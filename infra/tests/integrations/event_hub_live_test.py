#!/usr/bin/env python3
"""Explicit local pilot E2E. Sends synthetic MQTT/HTTP events and restores config.

Run only against the event-hub pilot started from infra/event-hub/compose.yml.
Secrets are read in memory and never printed or passed as command arguments.
"""

import json
import sys
import subprocess
import time
import uuid
from datetime import datetime, timedelta, UTC
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "infra/scripts/services"))
import event_hub as e  # noqa: E402 - after the services directory is on sys.path
from event_hub_transport import read_secrets, mqtt_client  # noqa: E402 - same


def main():
    values = read_secrets(e.DEFAULT_RUNTIME)

    def request(path, data=None, token=None):
        headers = {
            "Content-Type": "application/json",
            "Authorization": "Bearer " + (token or values["operator"]),
        }
        req = Request("http://127.0.0.1:8112" + path, data=data, headers=headers)
        with urlopen(req, timeout=20) as response:
            return json.load(response)

    state = request("/api/status")
    original = {
        "schema_version": "event-hub.config.v1",
        "sources": state["sources"],
        "rules": state["rules"],
    }
    cfg = json.loads(e.canonical_json(original))
    suffix = uuid.uuid4().hex[:12]
    rule_id = "live-" + suffix
    rule = dict(
        cfg["rules"][0], id=rule_id, enabled=True, threshold=35, cooldown_seconds=600
    )
    cfg["rules"].append(rule)
    request("/api/config", e.canonical_json(cfg).encode())
    client = mqtt_client(values, "sensor-demo", client_id="live-" + suffix)
    client.connect("127.0.0.1", 1884, 30)
    client.loop_start()
    started = time.monotonic()
    base = datetime.now(UTC).replace(microsecond=0) - timedelta(seconds=10)

    def event(name, value, offset, source="sensor-demo"):
        obj = e.fixture(source, value, suffix + "-" + name)
        obj["occurred_at"] = (base + timedelta(seconds=offset)).isoformat()
        return obj

    def publish(obj, topic="se/events/sensor-demo"):
        info = client.publish(topic, e.canonical_json(obj).encode(), qos=1)
        info.wait_for_publish(timeout=10)
        assert info.is_published(), "mqtt_publish_timeout"

    def wait_for(event_id):
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            try:
                state = request("/api/status")
            except (URLError, OSError):
                time.sleep(0.2)
                continue
            rows = [r for r in state["events"] if r["event_id"] == event_id]
            if rows and rows[0]["status"] == "complete":
                return state
            time.sleep(0.2)
        raise AssertionError("event_not_completed")

    try:
        normal = event("normal", 30, 0)
        high = event("high", 38, 1)
        cooldown = event("cooldown", 40, 2)
        late = event("late", 40, -1)
        for obj in (normal, high, high, cooldown, late):
            publish(obj)
            wait_for(obj["event_id"])
        app = event("app", 39, 3, "app-demo")
        result = request(
            "/api/events/app-demo",
            e.canonical_json(app).encode(),
            values["http"]["app-demo"],
        )
        assert result["status"] == "complete", "http_event_failed"
        state = wait_for(app["event_id"])
        alerts = [n for n in state["notifications"] if n["rule_id"] == rule_id]
        assert len(alerts) == 2, "notification_reconciliation"
        assert {n["source"] for n in alerts} == {"sensor-demo", "app-demo"}
        # Publishing to another identity is rejected by the broker ACL. MQTT
        # 3.1.1 PUBACK alone cannot prove authorization, so inspect hub receipts.
        forbidden = event("forbidden", 40, 4, "app-demo")
        publish(forbidden, "se/events/app-demo")
        time.sleep(0.5)
        state = request("/api/status")
        assert all(r["event_id"] != forbidden["event_id"] for r in state["events"]), (
            "mqtt_acl_bypass"
        )
        try:
            request(
                "/api/events/app-demo",
                e.canonical_json(app).encode(),
                "invalid-fixture",
            )
        except HTTPError as exc:
            with exc:
                assert exc.code == 401  # noqa: PT017 - a script run by hand against the pilot, not under pytest
        else:
            raise AssertionError("http_auth_bypass")
        restart = "--restart" in sys.argv
        if restart:
            compose = [
                "docker",
                "compose",
                "-f",
                str(ROOT / "infra/event-hub/compose.yml"),
            ]
            subprocess.run(
                [*compose, "stop", "hub"], check=True, capture_output=True, timeout=40
            )
            queued = event("queued-during-restart", 40, 5)
            try:
                publish(queued)
            finally:
                subprocess.run(
                    [*compose, "start", "hub"],
                    check=True,
                    capture_output=True,
                    timeout=30,
                )
            state = wait_for(queued["event_id"])
            publish(high)  # Same identity after process restart must remain a no-op.
            state = wait_for(high["event_id"])
            assert (
                len([n for n in state["notifications"] if n["rule_id"] == rule_id]) == 2
            )
        evidence = {
            "status": "passed",
            "transport": ["mqtt", "http"],
            "unique_events": 5,
            "expected_notifications": 2,
            "actual_notifications": len(alerts),
            "duplicate_suppressed": True,
            "cooldown_and_late_suppressed": True,
            "mqtt_acl": "passed",
            "http_auth": "passed",
            "elapsed_seconds": round(time.monotonic() - started, 3),
        }
        if restart:
            evidence.update(
                unique_events=6, restart_recovery="passed", post_restart_dedup="passed"
            )
        (e.DEFAULT_RUNTIME / "live-evidence.json").write_text(
            e.canonical_json(evidence) + "\n"
        )
        print(e.canonical_json(evidence))
    finally:
        client.disconnect()
        client.loop_stop()
        request("/api/config", e.canonical_json(original).encode())


if __name__ == "__main__":
    main()
