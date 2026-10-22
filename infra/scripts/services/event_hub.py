#!/usr/bin/env python3
"""Local MQTT/HTTP event hub: exact landing → verified Bronze → internal actions."""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import importlib.util
import io
import json
import math
import operator
import re
import sqlite3
import sys
import threading
from datetime import datetime, UTC
from pathlib import Path
from typing import Any

INFRA = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(INFRA / "scripts"))
sys.path.insert(0, str(INFRA / "scripts" / "utils"))
from cell_store import open_cell_store  # noqa: E402 - after the sys.path setup above
from data_lake.ingest import canonical_json, ingest_payload, utc_now  # noqa: E402 - same
from data_lake.storage import ObjectStore  # noqa: E402 - same

from _sentry import capture, init_sentry  # noqa: E402 - same

spec = importlib.util.spec_from_file_location(
    "event_hub_intake", INFRA / "scripts/cli/data-intake-cli.py"
)
if spec is None or spec.loader is None:
    raise ImportError("data-intake-cli.py could not be loaded")
# Any: the CLI is loaded from a file path, so mypy cannot see its functions.
intake: Any = importlib.util.module_from_spec(spec)
spec.loader.exec_module(intake)
# This private module instance uses the existing command implementations without
# emitting their CLI output (or redirecting process-wide stdout in HTTP threads).
intake.json_print = lambda *_args, **_kwargs: None

MAX_BYTES = 4096
IDENTIFIER = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
OPS = {
    "gt": operator.gt,
    "gte": operator.ge,
    "lt": operator.lt,
    "lte": operator.le,
    "eq": operator.eq,
}
DEFAULT_CONFIG = INFRA / "event-hub/config.json"
DEFAULT_RUNTIME = INFRA / "event-hub/.runtime"
AUTOMATION_ID = "event-hub"


class Rejected(ValueError):
    """A stable reason code, never source bytes or secret values."""


def digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def identifier(value: Any) -> bool:
    return isinstance(value, str) and IDENTIFIER.fullmatch(value) is not None


def timestamp(value: Any) -> datetime:
    if not isinstance(value, str) or len(value) > 40:
        raise Rejected("invalid_timestamp")
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))  # noqa: FURB162 - keep the explicit "Z" handling; lint pass is behaviour-neutral
    except ValueError as exc:
        raise Rejected("invalid_timestamp") from exc
    if dt.tzinfo is None:
        raise Rejected("timezone_required")
    return dt.astimezone(UTC)


def finite(value: Any) -> bool:
    try:
        return type(value) in (int, float) and math.isfinite(value)
    except OverflowError:
        return False


def json_object(raw: bytes) -> dict[str, Any]:
    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        obj: dict[str, Any] = {}
        for key, value in items:
            if key in obj:
                raise Rejected("duplicate_json_key")
            obj[key] = value
        return obj

    try:
        value = json.loads(raw, object_pairs_hook=pairs)
    except Rejected:
        raise  # duplicate_json_key from the hook keeps its own code (Rejected is a ValueError)
    except (ValueError, UnicodeDecodeError, RecursionError) as exc:
        raise Rejected("invalid_json") from exc
    if not isinstance(value, dict):
        raise Rejected("object_required")
    return value


def load_config(path: Path) -> dict[str, Any]:
    cfg = json_object(path.read_bytes())
    if (
        set(cfg) != {"schema_version", "sources", "rules"}
        or cfg["schema_version"] != "event-hub.config.v1"
    ):
        raise Rejected("invalid_config")
    sources, rules = cfg["sources"], cfg["rules"]
    if not isinstance(sources, dict) or not 1 <= len(sources) <= 100:
        raise Rejected("invalid_sources")
    for key, source in sources.items():
        if (
            not identifier(key)
            or not isinstance(source, dict)
            or set(source) != {"enabled", "transport", "event_types"}
        ):
            raise Rejected("invalid_source")
        if type(source["enabled"]) is not bool or source["transport"] not in (
            "http",
            "mqtt",
        ):
            raise Rejected("invalid_source")
        if (
            not isinstance(source["event_types"], list)
            or not 1 <= len(source["event_types"]) <= 20
            or not all(identifier(t) for t in source["event_types"])
        ):
            raise Rejected("invalid_event_types")
    if not isinstance(rules, list) or len(rules) > 100:
        raise Rejected("invalid_rules")
    seen = set()
    for rule in rules:
        fields = {
            "id",
            "enabled",
            "event_type",
            "unit",
            "operator",
            "threshold",
            "cooldown_seconds",
            "max_age_seconds",
            "action",
        }
        if not isinstance(rule, dict) or set(rule) != fields:
            raise Rejected("invalid_rule")
        if (
            not all(identifier(rule[k]) for k in ("id", "event_type", "unit"))
            or rule["id"] in seen
        ):
            raise Rejected("invalid_rule_identity")
        seen.add(rule["id"])
        if (
            type(rule["enabled"]) is not bool
            or not isinstance(rule["operator"], str)
            or rule["operator"] not in OPS
            or not finite(rule["threshold"])
        ):
            raise Rejected("invalid_rule_condition")
        if rule["action"] != "internal_notification":
            raise Rejected("unsupported_action")
        if any(
            type(rule[k]) is not int or not 0 <= rule[k] <= 604800
            for k in ("cooldown_seconds", "max_age_seconds")
        ):
            raise Rejected("invalid_rule_window")
    return cfg


def validate(
    raw: bytes, source: str, transport: str, cfg: dict[str, Any]
) -> dict[str, Any]:
    if not 0 < len(raw) <= MAX_BYTES:
        raise Rejected("payload_size")
    policy = cfg["sources"].get(source)
    if not policy or not policy["enabled"] or policy["transport"] != transport:
        raise Rejected("source_not_allowed")
    obj = json_object(raw)
    if set(obj) != {
        "event_id",
        "source_id",
        "event_type",
        "occurred_at",
        "schema_version",
        "payload",
    }:
        raise Rejected("invalid_event_fields")
    if (
        obj["schema_version"] != "event.v1"
        or obj["source_id"] != source
        or not identifier(obj["event_id"])
    ):
        raise Rejected("invalid_event_identity")
    if obj["event_type"] not in policy["event_types"]:
        raise Rejected("event_type_not_allowed")
    timestamp(obj["occurred_at"])
    payload = obj["payload"]
    # Deliberately bounded telemetry v1: credentials and free text have no field.
    if (
        not isinstance(payload, dict)
        or set(payload) != {"value", "unit"}
        or not finite(payload["value"])
    ):
        raise Rejected("invalid_measurement")
    if payload["unit"] not in (
        "celsius",
        "percent",
        "count",
        "seconds",
        "watts",
        "volts",
    ):
        raise Rejected("invalid_unit")
    return obj


HUB_SCHEMA = """
CREATE TABLE IF NOT EXISTS hub_receipts (
 identity TEXT PRIMARY KEY, source TEXT NOT NULL, event_id TEXT NOT NULL,
 canonical_sha256 TEXT NOT NULL, raw_key TEXT NOT NULL,
 received_at TEXT NOT NULL, rules_json TEXT NOT NULL,
 status TEXT NOT NULL DEFAULT 'pending', error_code TEXT,
 attempt_count INTEGER NOT NULL DEFAULT 0, last_attempt_at TEXT,
 UNIQUE(source, event_id)
);
CREATE TABLE IF NOT EXISTS hub_decisions (
 identity TEXT NOT NULL REFERENCES hub_receipts(identity), rule_id TEXT NOT NULL,
 outcome TEXT NOT NULL, PRIMARY KEY(identity, rule_id)
);
CREATE TABLE IF NOT EXISTS hub_rule_state (
 rule_id TEXT NOT NULL, source TEXT NOT NULL, event_type TEXT NOT NULL,
 watermark TEXT NOT NULL, last_action_at TEXT,
 PRIMARY KEY(rule_id, source, event_type)
);
CREATE TABLE IF NOT EXISTS hub_notifications (
 id TEXT PRIMARY KEY, identity TEXT NOT NULL REFERENCES hub_receipts(identity),
 rule_id TEXT NOT NULL, created_at TEXT NOT NULL, message TEXT NOT NULL,
 UNIQUE(identity, rule_id)
);
CREATE TABLE IF NOT EXISTS hub_rejections (
 digest TEXT NOT NULL, code TEXT NOT NULL, first_seen TEXT NOT NULL,
 count INTEGER NOT NULL DEFAULT 1, PRIMARY KEY(digest, code)
);
"""


class Hub:
    def __init__(
        self,
        runtime: Path = DEFAULT_RUNTIME,
        config: Path = DEFAULT_CONFIG,
        intake_store: Any = None,  # an Intake cell store (CellStore or its SQLite test double)
    ) -> None:
        self.runtime = runtime.resolve()
        self.runtime.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.config_path = (
            self.runtime / "config.json"
            if config == DEFAULT_CONFIG and (self.runtime / "config.json").exists()
            else config
        )
        load_config(self.config_path)
        self.lock = threading.RLock()
        self._file_lock = (self.runtime / "hub.lock").open("a")
        try:
            fcntl.flock(self._file_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            self._file_lock.close()
            raise RuntimeError("hub_already_running") from exc
        self.lake_uri = str(self.runtime / "lake")
        self.store = ObjectStore(self.lake_uri)
        self.conn = sqlite3.connect(self.runtime / "hub.db", check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA synchronous=FULL")
        self.conn.executescript(HUB_SCHEMA)
        columns = {r[1] for r in self.conn.execute("PRAGMA table_info(hub_receipts)")}
        for name, declaration in [
            ("attempt_count", "INTEGER NOT NULL DEFAULT 0"),
            ("last_attempt_at", "TEXT"),
        ]:
            if name not in columns:
                # nosemgrep: python.lang.security.audit.formatted-sql-query.formatted-sql-query, python.sqlalchemy.security.sqlalchemy-execute-raw-query.sqlalchemy-execute-raw-query -- local SQLite migration: column name and declaration come from the fixed list just above
                self.conn.execute(
                    f"ALTER TABLE hub_receipts ADD COLUMN {name} {declaration}"
                )
        self.conn.commit()
        # Sources, intakes and intake events live in the Intake cell, not in the hub's local SQLite.
        self.intake_store = (
            intake_store
            if intake_store is not None
            else open_cell_store("intake", AUTOMATION_ID)
        )
        self.sync_sources()

    def call(self, name: str, argv: list[str]) -> None:
        args = intake.build_parser().parse_args(argv)
        getattr(intake, "cmd_" + name)(args, self.intake_store)

    def sync_sources(self) -> dict[str, Any]:
        cfg = load_config(self.config_path)
        # Only the hub's own sources (dataset hub_events): the Intake cell also holds other systems' sources
        # (finance files, diary files, LINE), which the hub must never pause.
        for row in self.intake_store.list(
            "sources", status="active", dataset="hub_events"
        ):
            if row["source_key"] not in cfg["sources"]:
                self.call(
                    "source_set_status",
                    ["source", "set-status", row["source_key"], "paused"],
                )
        for key, policy in cfg["sources"].items():
            rows = self.intake_store.list("sources", source_key=key)
            row = rows[0] if rows else None
            status = "active" if policy["enabled"] else "paused"
            if row is None:
                self.call(
                    "source_register",
                    [
                        "source",
                        "register",
                        "--key",
                        key,
                        "--name",
                        key,
                        "--kind",
                        "application",
                        "--domain",
                        "operations",
                        "--dataset",
                        "hub_events",
                        "--privacy-class",
                        "internal",
                        "--ingest-mode",
                        "stream",
                        "--status",
                        status,
                    ],
                )
            elif row["status"] != status:
                self.call("source_set_status", ["source", "set-status", key, status])
        return cfg

    def reject(self, raw: bytes, code: str) -> dict[str, Any]:
        # Invalid payloads may contain secrets. Quarantine only digest/reason;
        # the producer retains the rejected bytes for correction/resubmission.
        with self.lock, self.conn:
            self.conn.execute(
                """INSERT INTO hub_rejections(digest,code,first_seen) VALUES(?,?,?)
                ON CONFLICT(digest,code) DO UPDATE SET count=count+1""",
                (digest(raw), code, utc_now()),
            )
        return {"status": "rejected", "code": code}

    def receive(self, raw: bytes, source: str, transport: str) -> dict[str, Any]:
        with self.lock:
            cfg = self.sync_sources()
            try:
                obj = validate(raw, source, transport, cfg)
            except Rejected as exc:
                return self.reject(raw, str(exc))
            identity = digest((source + "\0" + obj["event_id"]).encode())
            checksum = digest(canonical_json(obj).encode())
            row = self.conn.execute(
                "SELECT * FROM hub_receipts WHERE identity=?", (identity,)
            ).fetchone()
            if row is not None and row["canonical_sha256"] != checksum:
                return self.reject(raw, "event_id_conflict")
            if row is None:
                raw_key = (
                    f"landing/event-hub/source={source}/event={identity}/payload.json"
                )
                # Canonical identity reservation survives a crash before SQLite.
                reservation = f"control/event-hub/identities/{identity}.json"
                from data_lake.storage import ObjectNotFound, StorageConflict

                try:
                    self.store.put_bytes(
                        reservation, canonical_json({"sha256": checksum}).encode()
                    )
                except StorageConflict:
                    return self.reject(raw, "event_id_conflict")
                try:
                    original = self.store.get_bytes(raw_key)
                except ObjectNotFound:
                    self.store.put_bytes(raw_key, raw, content_type="application/json")
                else:
                    if (
                        digest(canonical_json(json_object(original)).encode())
                        != checksum
                    ):
                        return self.reject(raw, "event_id_conflict")
                with self.conn:
                    self.conn.execute(
                        """INSERT INTO hub_receipts
                        (identity,source,event_id,canonical_sha256,raw_key,received_at,rules_json)
                        VALUES(?,?,?,?,?,?,?)""",
                        (
                            identity,
                            source,
                            obj["event_id"],
                            checksum,
                            raw_key,
                            utc_now(),
                            canonical_json(cfg["rules"]),
                        ),
                    )
            elif row["status"] == "complete":
                return {"status": "duplicate", "identity": identity}
            elif row["attempt_count"] >= 5:
                return {
                    "status": "failed",
                    "identity": identity,
                    "code": "retry_exhausted",
                    "retry_exhausted": True,
                }
            return self.process(identity)

    def process(self, identity: str) -> dict[str, Any]:
        with self.lock:
            row = self.conn.execute(
                "SELECT * FROM hub_receipts WHERE identity=?", (identity,)
            ).fetchone()
            if row is None:
                raise Rejected("receipt_not_found")
            cfg = self.sync_sources()
            policy = cfg["sources"].get(row["source"])
            if not policy or not policy["enabled"]:
                return {"status": "paused", "identity": identity}
            with self.conn:
                self.conn.execute(
                    "UPDATE hub_receipts SET attempt_count=attempt_count+1,last_attempt_at=? WHERE identity=?",
                    (utc_now(), identity),
                )
            key = "hub-" + identity
            try:
                raw = self.store.get_bytes(row["raw_key"])
                obj = validate(raw, row["source"], policy["transport"], cfg)
                if digest(canonical_json(obj).encode()) != row["canonical_sha256"]:
                    raise Rejected("receipt_checksum_mismatch")
                found = self.intake_store.list("intakes", intake_key=key)
                if not found:
                    self.call(
                        "enqueue",
                        [
                            "enqueue",
                            "--source",
                            row["source"],
                            "--input-ref",
                            "event:" + identity,
                            "--idempotency-key",
                            key,
                            "--intake-key",
                            key,
                            "--payload-kind",
                            "json",
                            "--requested-by",
                            "automation",
                        ],
                    )
                found = intake.intake_by_identifier(self.intake_store, key)
                if found["status"] == "processing":
                    # The exclusive process lock proves the previous worker is
                    # gone. Close its lease through the existing fenced API.
                    expires = intake.lease_expiry(found)
                    if expires and expires > datetime.now(UTC):
                        self.call(
                            "fail",
                            [
                                "fail",
                                key,
                                "--worker",
                                found["lease_owner"],
                                "--error-code",
                                "worker_interrupted",
                                "--error-message",
                                "worker_interrupted",
                            ],
                        )
                        found = intake.intake_by_identifier(self.intake_store, key)
                if found["status"] == "failed":
                    self.call("retry", ["retry", key])
                    found = intake.intake_by_identifier(self.intake_store, key)
                if found["status"] in {"queued", "processing"}:
                    self.call(
                        "claim",
                        ["claim", key, "--worker", "event-hub", "--lease-minutes", "1"],
                    )
                    result = ingest_payload(
                        raw,
                        [obj],
                        source=row["source"],
                        domain="operations",
                        dataset="hub_events",
                        data_lake_uri=self.lake_uri,
                        source_record_id_field="event_id",
                        event_time_field="occurred_at",
                        privacy_class="internal",
                    )
                    self.call(
                        "land",
                        [
                            "land",
                            key,
                            "--worker",
                            "event-hub",
                            "--lake-run-id",
                            result["run_id"],
                            "--manifest-key",
                            result["manifest_key"],
                            "--raw-key",
                            result["raw_key"],
                            "--bronze-key",
                            result["bronze_key"],
                            "--raw-sha256",
                            digest(raw),
                            "--record-count",
                            "1",
                        ],
                    )
                found = intake.intake_by_identifier(self.intake_store, key)
                if found["status"] == "landed":
                    self.call(
                        "verify", ["verify", key, "--data-lake-uri", self.lake_uri]
                    )
                found = intake.intake_by_identifier(self.intake_store, key)
                if found["status"] != "verified":
                    raise Rejected("intake_not_verified")
                intake.verify_manifest(found, self.lake_uri)
                import pyarrow.parquet as pq

                records = (
                    pq.ParquetFile(
                        io.BytesIO(
                            self.store.get_bytes(found["lake_bronze_object_key"])
                        )
                    )
                    .read()
                    .to_pylist()
                )
                if len(records) != 1:
                    raise Rejected("bronze_count_mismatch")
                bronze_event = json_object(records[0]["payload_json"].encode())
                if (
                    digest(canonical_json(bronze_event).encode())
                    != row["canonical_sha256"]
                ):
                    raise Rejected("bronze_identity_mismatch")
                with self.conn:
                    count = self.evaluate(row, bronze_event, cfg)
                    self.conn.execute(
                        "UPDATE hub_receipts SET status='complete',error_code=NULL WHERE identity=?",
                        (identity,),
                    )
                return {
                    "status": "complete",
                    "identity": identity,
                    "notifications": count,
                }
            except Exception as exc:
                self.conn.rollback()
                if not isinstance(
                    exc, Rejected
                ):  # Rejected codes are expected outcomes
                    capture(exc)  # no-op unless SENTRY_DSN is set
                code = str(exc) if isinstance(exc, Rejected) else type(exc).__name__
                with self.conn:
                    self.conn.execute(
                        "UPDATE hub_receipts SET status='failed',error_code=? WHERE identity=?",
                        (code, identity),
                    )
                return {
                    "status": "failed",
                    "identity": identity,
                    "code": code,
                    "retry_exhausted": row["attempt_count"] + 1 >= 5,
                }

    def evaluate(
        self, receipt: sqlite3.Row, obj: dict[str, Any], cfg: dict[str, Any]
    ) -> int:
        """Consume verified Bronze, atomically record decisions/actions/cooldown."""
        count = 0
        when = timestamp(obj["occurred_at"])
        when_text = when.isoformat()
        age = (timestamp(receipt["received_at"]) - when).total_seconds()
        active = {r["id"] for r in cfg["rules"] if r["enabled"]}
        for rule in json.loads(receipt["rules_json"]):
            if rule["event_type"] != obj["event_type"]:
                continue
            if self.conn.execute(
                "SELECT 1 FROM hub_decisions WHERE identity=? AND rule_id=?",
                (receipt["identity"], rule["id"]),
            ).fetchone():
                continue
            state = self.conn.execute(
                "SELECT * FROM hub_rule_state WHERE rule_id=? AND source=? AND event_type=?",
                (rule["id"], obj["source_id"], obj["event_type"]),
            ).fetchone()
            last_action = state["last_action_at"] if state else None
            outcome = "not_matched"
            eligible = False
            if not rule["enabled"] or rule["id"] not in active:
                outcome = "disabled"
            elif age < -60 or age > rule["max_age_seconds"]:
                outcome = "stale_or_future"
            elif state and when <= timestamp(state["watermark"]):
                outcome = "late"
            elif obj["payload"]["unit"] != rule["unit"]:
                outcome = "unit_mismatch"
            else:
                eligible = True
                if OPS[rule["operator"]](obj["payload"]["value"], rule["threshold"]):
                    outcome = (
                        "cooldown"
                        if last_action
                        and (when - timestamp(last_action)).total_seconds()
                        < rule["cooldown_seconds"]
                        else "notified"
                    )
            if outcome == "notified":
                action_id = digest((receipt["identity"] + "\0" + rule["id"]).encode())
                self.conn.execute(
                    "INSERT INTO hub_notifications VALUES(?,?,?,?,?)",
                    (
                        action_id,
                        receipt["identity"],
                        rule["id"],
                        receipt["received_at"],
                        "ค่าจากแหล่งข้อมูลเข้าเงื่อนไขการแจ้งเตือน",
                    ),
                )
                last_action = when_text
                count += 1
            if eligible:
                self.conn.execute(
                    """INSERT INTO hub_rule_state VALUES(?,?,?,?,?)
                    ON CONFLICT(rule_id,source,event_type) DO UPDATE SET
                    watermark=excluded.watermark,last_action_at=excluded.last_action_at""",
                    (
                        rule["id"],
                        obj["source_id"],
                        obj["event_type"],
                        when_text,
                        last_action,
                    ),
                )
            self.conn.execute(
                "INSERT INTO hub_decisions VALUES(?,?,?)",
                (receipt["identity"], rule["id"], outcome),
            )
        return count

    def retry(
        self, limit: int = 100, *, automatic: bool = False
    ) -> list[dict[str, Any]]:
        if not 1 <= limit <= 100:
            raise Rejected("invalid_limit")
        with self.lock:
            where = "status!='complete'"
            if automatic:
                where += " AND attempt_count<5 AND (last_attempt_at IS NULL OR (julianday('now')-julianday(last_attempt_at))*86400 >= min(300,15*(1 << max(0,attempt_count-1))))"
            # nosemgrep: python.sqlalchemy.security.sqlalchemy-execute-raw-query.sqlalchemy-execute-raw-query -- the WHERE text is chosen from two fixed literals; the limit is bound as a ? parameter
            rows = self.conn.execute(
                f"SELECT identity FROM hub_receipts WHERE {where} ORDER BY received_at,rowid LIMIT ?",
                (limit,),
            ).fetchall()
            return [self.process(r["identity"]) for r in rows]

    def snapshot(self) -> dict[str, Any]:
        with self.lock:
            cfg = load_config(self.config_path)
            counts = {
                r[0]: r[1]
                for r in self.conn.execute(
                    "SELECT status,count(*) FROM hub_receipts GROUP BY status"
                )
            }
            events = [
                dict(r)
                for r in self.conn.execute("""SELECT identity,source,event_id,status,error_code,received_at,attempt_count
                FROM hub_receipts ORDER BY rowid DESC LIMIT 50""")
            ]
            import pyarrow.parquet as pq

            for row in events:
                identity = row.pop("identity")
                if row["status"] != "complete":
                    continue
                try:
                    entry = intake.intake_by_identifier(
                        self.intake_store, "hub-" + identity
                    )
                    raw = self.store.get_bytes(entry["lake_bronze_object_key"])
                    reading = json.loads(
                        pq.ParquetFile(io.BytesIO(raw))
                        .read(columns=["payload_json"])
                        .to_pylist()[0]["payload_json"]
                    )
                    row.update(
                        value=reading["payload"]["value"],
                        unit=reading["payload"]["unit"],
                    )
                except Exception:
                    row["error_code"] = "history_unavailable"
            notices = [
                dict(r)
                for r in self.conn.execute("""SELECT n.id,n.rule_id,n.message,n.created_at,r.source,r.event_id
                FROM hub_notifications n JOIN hub_receipts r ON n.identity=r.identity ORDER BY n.rowid DESC LIMIT 50""")
            ]
            rejections = [
                dict(r)
                for r in self.conn.execute(
                    "SELECT code,sum(count) AS count FROM hub_rejections GROUP BY code"
                )
            ]
            return {
                "status": "degraded"
                if counts.get("failed") or any(r["error_code"] for r in events)
                else "healthy",
                "counts": counts,
                "sources": cfg["sources"],
                "rules": cfg["rules"],
                "events": events,
                "notifications": notices,
                "rejections": rejections,
            }

    def close(self) -> None:
        self.conn.close()
        self._file_lock.close()


def fixture(
    source: str = "sensor-demo", value: float = 38, event_id: str | None = None
) -> dict[str, Any]:
    import uuid

    return {
        "schema_version": "event.v1",
        "source_id": source,
        "event_id": event_id or "demo-" + uuid.uuid4().hex,
        "event_type": "temperature.reading",
        "occurred_at": utc_now(),
        "payload": {"value": value, "unit": "celsius"},
    }


def main() -> None:
    init_sentry()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command",
        choices=["demo", "status", "retry", "serve", "setup", "simulate", "onboard"],
    )
    parser.add_argument("--runtime", type=Path, default=DEFAULT_RUNTIME)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--port", type=int, default=8112)
    parser.add_argument("--bind", default="127.0.0.1")
    parser.add_argument("--mqtt-host", default="127.0.0.1")
    parser.add_argument("--mqtt-port", type=int, default=1884)
    parser.add_argument("--transport", choices=["mqtt", "http"], default="mqtt")
    parser.add_argument("--value", type=float, default=38)
    parser.add_argument("--source-id")
    parser.add_argument("--event-type", default="temperature.reading")
    parser.add_argument("--device-host", default="127.0.0.1")
    parser.add_argument("--device-port", type=int, default=1884)
    args = parser.parse_args()
    if args.command in {"serve", "setup", "simulate", "onboard"}:
        from event_hub_transport import run

        run(args)
        return
    hub = Hub(args.runtime, args.config)
    result: Any  # a dict for demo and status, a list of receipts for retry
    try:
        if args.command == "demo":
            obj = fixture()
            raw = canonical_json(obj).encode()
            result = {
                "first": hub.receive(raw, "sensor-demo", "mqtt"),
                "duplicate": hub.receive(raw, "sensor-demo", "mqtt"),
            }
        elif args.command == "retry":
            result = hub.retry()
        else:
            result = hub.snapshot()
        print(canonical_json(result))
        if args.command == "demo" and result["first"]["status"] != "complete":
            raise SystemExit(1)
        if args.command == "retry" and any(r["status"] == "failed" for r in result):
            raise SystemExit(1)
    finally:
        hub.close()


if __name__ == "__main__":
    main()
