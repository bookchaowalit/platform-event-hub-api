#!/usr/bin/env python3
"""Event-hub reliability, lake-first, auth, and policy regressions (offline)."""

import json
import sqlite3
import sys
import tempfile
import threading
import unittest
from datetime import datetime, timedelta, UTC
from http.server import HTTPServer
from pathlib import Path
from unittest.mock import patch
from urllib.request import Request, urlopen
from urllib.error import HTTPError

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "infra/scripts/services"))
sys.path.insert(0, str(ROOT / "infra/tests/support"))
import sqlite_cell_store  # noqa: E402 - the Intake cell double (also isolates the cell API client switches)
import event_hub as e  # noqa: E402 - after the sys.path setup above
import event_hub_transport as transport  # noqa: E402 - same
from data_lake.storage import ObjectStore, StorageError  # noqa: E402 - same


class HubTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.runtime = Path(self.temp.name)
        self.config = self.runtime / "config.json"
        self.config.write_bytes(e.DEFAULT_CONFIG.read_bytes())
        self.intake_db = sqlite3.connect(
            self.runtime / "intake-cell.db", check_same_thread=False
        )
        self.hub = self.new_hub()
        self.now = datetime.now(UTC).replace(microsecond=0)

    def new_hub(self):
        # The intake tables live in the Intake cell; the double keeps them across hub restarts.
        return e.Hub(
            self.runtime,
            self.config,
            intake_store=sqlite_cell_store.SqliteCellStore(self.intake_db, "intake"),
        )

    def tearDown(self):
        self.hub.close()
        self.intake_db.close()
        self.temp.cleanup()

    def event(self, value=38, event_id="event-1", seconds=0, source="sensor-demo"):
        obj = e.fixture(source, value, event_id)
        obj["occurred_at"] = (self.now + timedelta(seconds=seconds)).isoformat()
        return obj

    def send(self, obj):
        return self.hub.receive(
            e.canonical_json(obj).encode(),
            obj["source_id"],
            "http" if obj["source_id"] == "app-demo" else "mqtt",
        )

    def count(self, table):
        return self.hub.conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0]

    def change(self, edit):
        cfg = json.loads(self.config.read_text())
        edit(cfg)
        self.config.write_text(e.canonical_json(cfg))

    def test_exact_bytes_verified_intake_and_atomic_action(self):
        obj = self.event()
        raw = json.dumps(obj, indent=3).encode() + b"\n"
        result = self.hub.receive(raw, "sensor-demo", "mqtt")
        assert result["status"] == "complete", result
        assert result["notifications"] == 1
        row = e.intake.intake_by_identifier(
            self.hub.intake_store, "hub-" + result["identity"]
        )
        assert row["status"] == "verified"
        e.intake.verify_manifest(row, self.hub.lake_uri)
        assert self.hub.store.get_bytes(row["lake_raw_object_key"]) == raw
        assert self.count("hub_notifications") == 1

    def test_duplicate_whitespace_and_conflicting_identity(self):
        obj = self.event()
        assert self.send(obj)["status"] == "complete"
        assert (
            self.hub.receive(json.dumps(obj, indent=2).encode(), "sensor-demo", "mqtt")[
                "status"
            ]
            == "duplicate"
        )
        obj["payload"]["value"] = 50
        assert self.send(obj)["code"] == "event_id_conflict"
        assert self.count("hub_notifications") == 1

    def test_normal_threshold_cooldown_and_late_order(self):
        self.send(self.event(35, "normal", -1200))
        self.send(self.event(38, "high", -1100))
        self.send(self.event(40, "cooldown", -1050))
        self.send(self.event(40, "late", -1150))
        self.send(self.event(40, "new-alert", -400))
        assert self.count("hub_notifications") == 2
        outcomes = [
            r[0]
            for r in self.hub.conn.execute(
                "SELECT outcome FROM hub_decisions ORDER BY rowid"
            )
        ]
        assert outcomes == ["not_matched", "notified", "cooldown", "late", "notified"]

    def test_stale_future_and_unit_mismatch_cannot_poison_watermark(self):
        self.send(self.event(event_id="future", seconds=1000))
        self.send(self.event(event_id="stale", seconds=-90000))
        wrong = self.event(event_id="wrong-unit")
        wrong["payload"]["unit"] = "watts"
        self.send(wrong)
        assert self.send(self.event())["notifications"] == 1

    def test_source_scoping_and_disabled_policy(self):
        raw = e.canonical_json(self.event(source="app-demo")).encode()
        assert (
            self.hub.receive(raw, "sensor-demo", "mqtt")["code"]
            == "invalid_event_identity"
        )
        assert self.hub.receive(raw, "app-demo", "mqtt")["code"] == "source_not_allowed"
        self.change(lambda c: c["sources"]["sensor-demo"].update(enabled=False))
        assert self.send(self.event())["code"] == "source_not_allowed"
        assert self.count("hub_receipts") == 0

    def test_removed_source_is_paused_in_existing_registry(self):
        self.change(lambda c: c["sources"].pop("sensor-demo"))
        self.hub.sync_sources()
        assert (
            self.hub.intake_store.list("sources", source_key="sensor-demo")[0]["status"]
            == "paused"
        )

    def test_untrusted_shapes_rejected_without_raw_storage(self):
        bad = [b"{", b"[]", b'{"a":1,"a":2}', b" " * 4097]
        for field, value in [
            ("value", True),
            ("value", float("nan")),
            ("value", 10**400),
            ("unit", []),
        ]:
            obj = self.event()
            obj["payload"][field] = value
            bad.append(json.dumps(obj).encode())
        obj = self.event()
        obj["payload"]["password"] = "synthetic-sensitive-value"
        bad.append(json.dumps(obj).encode())
        for raw in bad:
            with self.subTest(raw_length=len(raw)):
                assert (
                    self.hub.receive(raw, "sensor-demo", "mqtt")["status"] == "rejected"
                )
        assert self.count("hub_receipts") == 0
        assert self.hub.store.list_keys("landing") == []
        assert "synthetic-sensitive-value" not in str(self.hub.snapshot())

    def test_lake_failure_blocks_actions_then_restart_recovers(self):
        with patch.object(
            e, "ingest_payload", side_effect=StorageError("private details")
        ):
            result = self.send(self.event())
        assert result["status"] == "failed"
        assert "private details" not in str(self.hub.snapshot())
        assert self.count("hub_notifications") == 0
        self.hub.close()
        self.hub = self.new_hub()
        recovered = self.hub.retry()
        assert recovered[0]["status"] == "complete", recovered
        assert self.count("hub_notifications") == 1
        assert self.hub.retry() == []

    def test_partial_manifest_failure_resumes_identical_ingest(self):
        original = ObjectStore.put_bytes

        def fail(store, key, data, **kwargs):
            if key.startswith("control/manifests/source="):
                raise StorageError("synthetic")
            return original(store, key, data, **kwargs)

        with patch.object(ObjectStore, "put_bytes", fail):
            assert self.send(self.event())["status"] == "failed"
        assert self.count("hub_notifications") == 0
        assert self.hub.retry()[0]["status"] == "complete"

    def test_action_failure_rolls_back_cooldown_and_notifications(self):
        original = self.hub.evaluate

        def fail(*args):
            original(*args)
            raise RuntimeError("synthetic")

        with patch.object(self.hub, "evaluate", fail):
            assert self.send(self.event())["status"] == "failed"
        for table in ["hub_notifications", "hub_decisions", "hub_rule_state"]:
            assert self.count(table) == 0
        assert self.hub.retry()[0]["notifications"] == 1

    def test_rule_snapshot_stable_on_retry_and_disable_stops_actions(self):
        with patch.object(e, "ingest_payload", side_effect=StorageError()):
            self.send(self.event())
        self.change(lambda c: c["rules"][0].update(threshold=100))
        assert self.hub.retry()[0]["notifications"] == 1
        with patch.object(e, "ingest_payload", side_effect=StorageError()):
            self.send(self.event(event_id="next", source="app-demo"))
        self.change(lambda c: c["rules"][0].update(enabled=False))
        assert self.hub.retry()[0]["notifications"] == 0

    def test_tampered_bronze_never_projects(self):
        with patch.object(self.hub, "evaluate", side_effect=RuntimeError()):
            result = self.send(self.event())
        row = e.intake.intake_by_identifier(
            self.hub.intake_store, "hub-" + result["identity"]
        )
        Path(self.hub.lake_uri, row["lake_bronze_object_key"]).write_bytes(b"corrupt")
        assert self.hub.retry()[0]["status"] == "failed"
        assert self.count("hub_notifications") == 0

    def test_independent_http_source_has_own_cooldown(self):
        self.send(self.event())
        assert self.send(self.event(source="app-demo"))["notifications"] == 1

    def test_single_runtime_writer(self):
        with pytest.raises(RuntimeError, match="hub_already_running"):
            self.new_hub()

    def test_automatic_retry_is_bounded_and_manual_retry_recovers(self):
        with patch.object(e, "ingest_payload", side_effect=StorageError()):
            self.send(self.event())
            assert self.hub.retry(automatic=True) == []
            for _ in range(4):
                self.hub.retry()
        assert self.hub.retry(automatic=True) == []
        exhausted = self.send(self.event())
        assert exhausted["retry_exhausted"]
        assert self.hub.retry()[0]["status"] == "complete"
        assert self.count("hub_notifications") == 1

    def test_history_reads_measurement_from_bronze(self):
        self.send(self.event(value=38.5))
        history = self.hub.snapshot()["events"][0]
        assert history["value"] == 38.5
        assert history["unit"] == "celsius"

    def test_onboard_creates_scoped_profile_and_acl_without_printing_secret(self):
        with patch.object(
            transport.subprocess,
            "run",
            return_value=type("Result", (), {"returncode": 0})(),
        ):
            result = transport.onboard(
                self.runtime,
                self.config,
                "living-room",
                "temperature.reading",
                "192.168.1.20",
                1884,
            )
        assert result["status"] == "created"
        assert "password" not in result
        profile_path = Path(result["profile_path"])
        assert profile_path.stat().st_mode & 63 == 0
        profile = json.loads(profile_path.read_text())
        assert profile["source_id"] == "living-room"
        assert profile["host"] == "192.168.1.20"
        assert profile["password"] != ""
        acl = (self.runtime / "auth/acl").read_text()
        assert "user living-room" in acl
        assert "topic write se/events/living-room" in acl
        assert (
            "topic write se/events/app-demo" not in acl.split("user living-room", 1)[-1]
        )
        cfg = e.load_config(self.runtime / "config.json")
        assert cfg["sources"]["living-room"]["transport"] == "mqtt"

    def test_onboard_rejects_duplicate_and_invalid_network_identity(self):
        with patch.object(
            transport.subprocess,
            "run",
            return_value=type("Result", (), {"returncode": 0})(),
        ):
            transport.onboard(
                self.runtime,
                self.config,
                "living-room",
                "temperature.reading",
                "192.168.1.20",
                1884,
            )
            with pytest.raises(e.Rejected, match="source_exists"):
                transport.onboard(
                    self.runtime,
                    self.config,
                    "living-room",
                    "temperature.reading",
                    "192.168.1.20",
                    1884,
                )
            with pytest.raises(e.Rejected, match="invalid_device_host"):
                transport.onboard(
                    self.runtime,
                    self.config,
                    "other-room",
                    "temperature.reading",
                    "user:pass@host",
                    1884,
                )
            with pytest.raises(e.Rejected, match="invalid_device_port"):
                transport.onboard(
                    self.runtime,
                    self.config,
                    "other-room",
                    "temperature.reading",
                    "192.168.1.20",
                    0,
                )

    def test_onboard_acl_reconciliation_preserves_existing_sources(self):
        with patch.object(
            transport.subprocess,
            "run",
            return_value=type("Result", (), {"returncode": 0})(),
        ):
            transport.onboard(
                self.runtime,
                self.config,
                "first-device",
                "temperature.reading",
                "127.0.0.1",
                1884,
            )
            transport.onboard(
                self.runtime,
                self.config,
                "second-device",
                "temperature.reading",
                "127.0.0.1",
                1884,
            )
        acl = (self.runtime / "auth/acl").read_text()
        assert acl.count("user event-hub") == 1
        assert acl.count("topic write") == 3
        assert "topic write se/events/first-device" in acl
        assert "topic write se/events/second-device" in acl

    def test_http_auth_origin_validation_and_config(self):
        values = {"operator": "fixture-operator", "http": {"app-demo": "fixture-app"}}
        server = HTTPServer(("127.0.0.1", 0), transport.handler_for(self.hub, values))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()

        def request(path, token=None, data=None, origin=None):
            headers = {"Content-Type": "application/json"}
            if token:
                headers["Authorization"] = "Bearer " + token
            if origin:
                headers["Origin"] = origin
            req = Request(
                f"http://127.0.0.1:{server.server_port}" + path,
                data=data,
                headers=headers,
            )
            try:
                with urlopen(req, timeout=5) as response:
                    return response.status, json.load(response)
            except HTTPError as exc:
                with exc:
                    return exc.code, json.load(exc)

        try:
            assert request("/api/status")[0] == 401
            assert request("/api/status", "fixture-app")[0] == 401
            assert request("/api/status", "fixture-operator")[0] == 200
            raw = e.canonical_json(self.event(source="app-demo")).encode()
            assert request("/api/events/app-demo", "fixture-operator", raw)[0] == 401
            assert (
                request(
                    "/api/events/app-demo", "fixture-app", raw, "https://other.invalid"
                )[0]
                == 403
            )
            assert (
                request("/api/events/app-demo", "fixture-app", raw)[1]["status"]
                == "complete"
            )
            assert request("/api/events/app-demo", "fixture-app", b" " * 4097)[0] == 400
            assert request("/api/config", "fixture-operator", b"{}")[0] == 400
            cfg = json.loads(self.config.read_text())
            cfg["rules"][0]["threshold"] = 40
            assert (
                request(
                    "/api/config", "fixture-operator", e.canonical_json(cfg).encode()
                )[0]
                == 200
            )
            assert e.load_config(self.config)["rules"][0]["threshold"] == 40
        finally:
            server.shutdown()
            thread.join()
            server.server_close()

    def test_mqtt_ack_only_after_completion_or_visible_rejection(self):
        from types import SimpleNamespace
        from unittest.mock import Mock

        worker = object.__new__(transport.MQTTWorker)
        worker.hub = self.hub
        worker.last_error = None
        client = Mock()
        message = SimpleNamespace(
            topic="se/events/sensor-demo",
            qos=1,
            retain=False,
            mid=42,
            payload=e.canonical_json(self.event()).encode(),
        )
        with patch.object(e, "ingest_payload", side_effect=StorageError()):
            worker.on_message(client, None, message)
        client.ack.assert_not_called()
        client.disconnect.assert_called_once()
        worker.on_message(client, None, message)
        client.ack.assert_called_once_with(42, 1)
        client.reset_mock()
        message.payload = b"invalid"
        worker.on_message(client, None, message)
        client.ack.assert_called_once_with(42, 1)
        assert self.count("hub_rejections") == 1


if __name__ == "__main__":
    unittest.main(verbosity=2)
