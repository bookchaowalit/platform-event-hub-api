"""Event validation rules: only bounded event.v1 telemetry from an allowed source gets in."""

from __future__ import annotations

import json
from typing import Any

import pytest

import event_hub as e

CFG = e.load_config(e.DEFAULT_CONFIG)


def event(**changes: Any) -> dict[str, Any]:
    obj = {
        "schema_version": "event.v1",
        "source_id": "sensor-demo",
        "event_id": "event-1",
        "event_type": "temperature.reading",
        "occurred_at": "2026-10-22T08:00:00Z",
        "payload": {"value": 38, "unit": "celsius"},
    }
    obj.update(changes)
    return obj


def check(obj: Any, source: str = "sensor-demo", transport: str = "mqtt") -> dict:
    raw = obj if isinstance(obj, bytes) else json.dumps(obj).encode()
    return e.validate(raw, source, transport, CFG)


def reason(obj: Any, source: str = "sensor-demo", transport: str = "mqtt") -> str:
    with pytest.raises(e.Rejected) as caught:
        check(obj, source, transport)
    return str(caught.value)


def test_a_well_formed_event_from_an_allowed_source_is_accepted():
    assert check(event())["event_id"] == "event-1"
    assert (
        check(event(source_id="app-demo"), "app-demo", "http")["source_id"]
        == "app-demo"
    )


def test_payload_size_is_between_1_and_4096_bytes():
    assert reason(b"") == "payload_size"
    padded = json.dumps(event()).encode().ljust(e.MAX_BYTES + 1)
    assert reason(padded) == "payload_size"


@pytest.mark.parametrize(
    ("source", "transport"),
    [("unknown", "mqtt"), ("sensor-demo", "http"), ("app-demo", "mqtt")],
)
def test_an_unknown_source_or_the_wrong_transport_is_not_allowed(source, transport):
    assert reason(event(source_id=source), source, transport) == "source_not_allowed"


def test_a_disabled_source_is_not_allowed():
    cfg = json.loads(json.dumps(CFG))
    cfg["sources"]["sensor-demo"]["enabled"] = False
    with pytest.raises(e.Rejected, match="source_not_allowed"):
        e.validate(json.dumps(event()).encode(), "sensor-demo", "mqtt", cfg)


@pytest.mark.parametrize(
    ("raw", "code"),
    [
        (b"{not json", "invalid_json"),
        (b"[1, 2]", "object_required"),
        (b'{"event_id": "a", "event_id": "b"}', "duplicate_json_key"),
    ],
)
def test_unreadable_json_is_rejected_with_a_stable_code(raw, code):
    assert reason(raw) == code


def test_extra_or_missing_fields_are_rejected():
    assert reason({**event(), "token": "x"}) == "invalid_event_fields"
    obj = event()
    del obj["payload"]
    assert reason(obj) == "invalid_event_fields"


@pytest.mark.parametrize(
    "changes",
    [
        {"schema_version": "event.v2"},
        {"source_id": "app-demo"},
        {"event_id": "Not Valid"},
        {"event_id": "x" * 65},
    ],
)
def test_schema_source_and_event_id_must_match_exactly(changes):
    assert reason(event(**changes)) == "invalid_event_identity"


def test_an_event_type_the_source_does_not_declare_is_rejected():
    assert reason(event(event_type="humidity.reading")) == "event_type_not_allowed"


@pytest.mark.parametrize(
    ("occurred_at", "code"),
    [
        ("2026-10-22T08:00:00", "timezone_required"),
        ("yesterday", "invalid_timestamp"),
        (1729584000, "invalid_timestamp"),
        ("2026-10-22T08:00:00+00:00" + " " * 20, "invalid_timestamp"),
    ],
)
def test_occurred_at_must_be_a_short_iso_timestamp_with_a_timezone(occurred_at, code):
    assert reason(event(occurred_at=occurred_at)) == code


def test_a_z_suffix_and_an_offset_both_count_as_a_timezone():
    assert (
        e.timestamp("2026-10-22T08:00:00Z").isoformat() == "2026-10-22T08:00:00+00:00"
    )
    assert (
        e.timestamp("2026-10-22T15:00:00+07:00").isoformat()
        == "2026-10-22T08:00:00+00:00"
    )


@pytest.mark.parametrize(
    "payload",
    [
        {"value": 38},
        {"value": 38, "unit": "celsius", "note": "free text"},
        {"value": "38", "unit": "celsius"},
        {"value": True, "unit": "celsius"},
        {"value": float("inf"), "unit": "celsius"},
        [38, "celsius"],
    ],
)
def test_the_payload_is_one_finite_number_and_a_unit_and_nothing_else(payload):
    raw = json.dumps(event(payload=payload), allow_nan=True).encode()
    assert reason(raw) == "invalid_measurement"


def test_only_the_listed_units_are_accepted():
    assert reason(event(payload={"value": 1, "unit": "fahrenheit"})) == "invalid_unit"
    for unit in ("celsius", "percent", "count", "seconds", "watts", "volts"):
        assert (
            check(event(payload={"value": 1.5, "unit": unit}))["payload"]["unit"]
            == unit
        )
