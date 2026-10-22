"""Config rules: the hub refuses a config it cannot enforce exactly."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest

import event_hub as e

BASE = json.loads(e.DEFAULT_CONFIG.read_text(encoding="utf-8"))


def load(tmp_path: Path, cfg: Any) -> dict:
    path = tmp_path / "config.json"
    path.write_text(json.dumps(cfg), encoding="utf-8")
    return e.load_config(path)


def code(tmp_path: Path, edit) -> str:
    cfg = copy.deepcopy(BASE)
    edit(cfg)
    with pytest.raises(e.Rejected) as caught:
        load(tmp_path, cfg)
    return str(caught.value)


def test_the_shipped_config_is_valid(tmp_path):
    cfg = load(tmp_path, BASE)
    assert set(cfg["sources"]) == {"sensor-demo", "app-demo"}
    assert [rule["id"] for rule in cfg["rules"]] == ["high-temperature"]


def test_unknown_top_level_keys_or_schema_version_are_invalid(tmp_path):
    assert code(tmp_path, lambda c: c.update(extra=1)) == "invalid_config"
    assert code(tmp_path, lambda c: c.update(schema_version="v2")) == "invalid_config"


def test_there_must_be_between_1_and_100_sources(tmp_path):
    assert code(tmp_path, lambda c: c.update(sources={})) == "invalid_sources"
    many = {f"s{i}": BASE["sources"]["sensor-demo"] for i in range(101)}
    assert code(tmp_path, lambda c: c.update(sources=many)) == "invalid_sources"


@pytest.mark.parametrize(
    "source",
    [
        {"enabled": "yes", "transport": "mqtt", "event_types": ["a"]},
        {"enabled": True, "transport": "websocket", "event_types": ["a"]},
        {"enabled": True, "transport": "mqtt", "event_types": ["a"], "secret": "x"},
    ],
)
def test_a_source_needs_a_boolean_switch_and_a_known_transport(tmp_path, source):
    assert (
        code(tmp_path, lambda c: c["sources"].update({"new-source": source}))
        == "invalid_source"
    )


def test_a_source_id_must_be_a_safe_identifier(tmp_path):
    source = BASE["sources"]["sensor-demo"]
    assert (
        code(tmp_path, lambda c: c["sources"].update({"Bad Id": source}))
        == "invalid_source"
    )


@pytest.mark.parametrize(
    "event_types", [[], [f"t{i}" for i in range(21)], ["Bad Type"]]
)
def test_a_source_declares_1_to_20_safe_event_types(tmp_path, event_types):
    def edit(c):
        c["sources"]["sensor-demo"]["event_types"] = event_types

    assert code(tmp_path, edit) == "invalid_event_types"


def rule(**changes: Any) -> dict:
    return {**BASE["rules"][0], **changes}


@pytest.mark.parametrize(
    ("bad_rule", "expected"),
    [
        ({**BASE["rules"][0], "extra": 1}, "invalid_rule"),
        (rule(id="Bad Id"), "invalid_rule_identity"),
        (rule(operator="between"), "invalid_rule_condition"),
        (rule(threshold="35"), "invalid_rule_condition"),
        (rule(threshold=float("nan")), "invalid_rule_condition"),
        (rule(enabled=1), "invalid_rule_condition"),
        (rule(action="webhook"), "unsupported_action"),
        (rule(cooldown_seconds=-1), "invalid_rule_window"),
        (rule(max_age_seconds=604801), "invalid_rule_window"),
        (rule(cooldown_seconds=1.5), "invalid_rule_window"),
    ],
)
def test_each_rule_is_exact_bounded_and_only_notifies_internally(
    tmp_path, bad_rule, expected
):
    assert code(tmp_path, lambda c: c.update(rules=[bad_rule])) == expected


def test_rule_ids_are_unique(tmp_path):
    assert (
        code(tmp_path, lambda c: c.update(rules=[rule(), rule()]))
        == "invalid_rule_identity"
    )


def test_at_most_100_rules_and_zero_is_allowed(tmp_path):
    assert load(tmp_path, {**BASE, "rules": []})["rules"] == []
    rules = [rule(id=f"r{i}") for i in range(101)]
    assert code(tmp_path, lambda c: c.update(rules=rules)) == "invalid_rules"


def test_window_bounds_are_inclusive(tmp_path):
    cfg = load(
        tmp_path, {**BASE, "rules": [rule(cooldown_seconds=0, max_age_seconds=604800)]}
    )
    assert cfg["rules"][0]["max_age_seconds"] == 604800
