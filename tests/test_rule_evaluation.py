"""Rule evaluation: one decision per rule and event, with watermark, cooldown and age windows."""

from __future__ import annotations

import json
import sqlite3
from types import SimpleNamespace
from typing import Any

import pytest

import event_hub as e

RULE = {
    "id": "high-temperature",
    "enabled": True,
    "event_type": "temperature.reading",
    "unit": "celsius",
    "operator": "gt",
    "threshold": 35,
    "cooldown_seconds": 600,
    "max_age_seconds": 3600,
    "action": "internal_notification",
}


class Rules:
    """Evaluate events against a fresh hub database (no lake, no Intake cell needed)."""

    def __init__(self, *rules: dict[str, Any]) -> None:
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(e.HUB_SCHEMA)
        self.rules = list(rules) or [RULE]
        self.hub = SimpleNamespace(conn=self.conn)
        self.count = 0

    def send(
        self,
        value: float,
        at: str,
        *,
        unit: str = "celsius",
        received: str | None = None,
        active: list[dict[str, Any]] | None = None,
    ) -> str:
        self.count += 1
        identity = f"identity-{self.count}"
        receipt = {
            "identity": identity,
            "received_at": received or at,
            "rules_json": json.dumps(self.rules),
        }
        obj = {
            "source_id": "sensor-demo",
            "event_type": "temperature.reading",
            "occurred_at": at,
            "payload": {"value": value, "unit": unit},
        }
        cfg = {"rules": self.rules if active is None else active}
        self.evaluate(receipt, obj, cfg)
        (outcome,) = self.conn.execute(
            "SELECT outcome FROM hub_decisions WHERE identity=?", (identity,)
        ).fetchone()
        return str(outcome)

    def evaluate(
        self, receipt: dict[str, Any], obj: dict[str, Any], cfg: dict[str, Any]
    ) -> int:
        # A stand-in hub (evaluate only uses .conn) and a dict receipt in place of sqlite3.Row.
        return e.Hub.evaluate(self.hub, receipt, obj, cfg)  # type: ignore[arg-type]  # stand-in hub and dict receipt (see above)

    def notifications(self) -> int:
        return int(
            self.conn.execute("SELECT count(*) FROM hub_notifications").fetchone()[0]
        )


def test_a_value_over_the_threshold_notifies_and_one_under_does_not():
    rules = Rules()
    assert rules.send(36, "2026-10-22T08:00:00+00:00") == "notified"
    assert rules.send(30, "2026-10-22T09:00:00+00:00") == "not_matched"
    assert rules.notifications() == 1


@pytest.mark.parametrize(
    ("operator", "value", "outcome"),
    [
        ("gt", 35, "not_matched"),
        ("gte", 35, "notified"),
        ("lt", 34, "notified"),
        ("lte", 36, "not_matched"),
        ("eq", 35, "notified"),
    ],
)
def test_each_operator_compares_the_value_with_the_threshold(operator, value, outcome):
    rules = Rules({**RULE, "operator": operator})
    assert rules.send(value, "2026-10-22T08:00:00+00:00") == outcome


def test_a_second_match_inside_the_cooldown_is_recorded_but_not_notified():
    rules = Rules()
    assert rules.send(36, "2026-10-22T08:00:00+00:00") == "notified"
    assert rules.send(37, "2026-10-22T08:09:59+00:00") == "cooldown"
    assert rules.send(38, "2026-10-22T08:20:00+00:00") == "notified"
    assert rules.notifications() == 2


def test_an_event_at_or_before_the_watermark_is_late():
    rules = Rules()
    assert rules.send(30, "2026-10-22T08:00:00+00:00") == "not_matched"
    assert rules.send(40, "2026-10-22T08:00:00+00:00") == "late"
    assert rules.send(40, "2026-10-22T07:59:00+00:00") == "late"
    assert rules.notifications() == 0


def test_stale_or_future_events_and_wrong_units_do_not_move_the_watermark():
    rules = Rules()
    stale = rules.send(
        40, "2026-10-22T06:00:00+00:00", received="2026-10-22T08:00:00+00:00"
    )
    future = rules.send(
        40, "2026-10-22T08:02:00+00:00", received="2026-10-22T08:00:00+00:00"
    )
    assert (stale, future) == ("stale_or_future", "stale_or_future")
    assert (
        rules.send(40, "2026-10-22T07:30:00+00:00", unit="percent") == "unit_mismatch"
    )
    # None of them set a watermark, so an older but in-window event still counts.
    assert (
        rules.send(
            40, "2026-10-22T07:00:00+00:00", received="2026-10-22T07:30:00+00:00"
        )
        == "notified"
    )


def test_a_rule_disabled_now_records_disabled_even_if_it_was_enabled_at_receipt():
    rules = Rules()
    assert (
        rules.send(40, "2026-10-22T08:00:00+00:00", active=[{**RULE, "enabled": False}])
        == "disabled"
    )
    assert rules.notifications() == 0


def test_rules_for_other_event_types_make_no_decision():
    rules = Rules({**RULE, "event_type": "humidity.reading"})
    receipt = {
        "identity": "x",
        "received_at": "2026-10-22T08:00:00+00:00",
        "rules_json": json.dumps(rules.rules),
    }
    obj = {
        "source_id": "sensor-demo",
        "event_type": "temperature.reading",
        "occurred_at": "2026-10-22T08:00:00+00:00",
        "payload": {"value": 99, "unit": "celsius"},
    }
    assert rules.evaluate(receipt, obj, {"rules": rules.rules}) == 0
    assert rules.conn.execute("SELECT count(*) FROM hub_decisions").fetchone()[0] == 0
