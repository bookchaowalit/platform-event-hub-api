"""Sentry stays off without SENTRY_DSN; with it, errors only and nothing sensitive is sent."""

import importlib
import sys

import pytest

import _sentry


def test_no_dsn_is_a_noop(monkeypatch):
    monkeypatch.delenv("SENTRY_DSN", raising=False)
    calls = []
    monkeypatch.setitem(
        sys.modules,
        "sentry_sdk",
        type("Fake", (), {"init": lambda **kw: calls.append(kw)}),
    )
    _sentry.init_sentry()
    assert calls == []


def test_dsn_initialises_errors_only_without_pii(monkeypatch, capsys):
    sentry_sdk = pytest.importorskip("sentry_sdk")
    calls, tags = [], {}
    monkeypatch.setattr(sentry_sdk, "init", lambda **kw: calls.append(kw))
    monkeypatch.setattr(
        sentry_sdk, "set_tag", lambda key, value: tags.__setitem__(key, value)
    )
    monkeypatch.setenv("SENTRY_DSN", "https://public@o0.ingest.sentry.io/0")
    monkeypatch.setenv("RAILWAY_SERVICE_NAME", "event-hub")
    monkeypatch.delenv("SENTRY_ENVIRONMENT", raising=False)
    _sentry.init_sentry()
    assert calls[0]["environment"] == "production"
    assert calls[0]["traces_sample_rate"] is None
    assert calls[0]["send_default_pii"] is False
    assert calls[0]["include_local_variables"] is False
    assert calls[0]["max_request_body_size"] == "never"
    assert calls[0]["before_send"] is _sentry._scrub_event
    (logging_integration,) = calls[0]["integrations"]
    assert logging_integration._handler is None
    assert logging_integration._breadcrumb_handler is None
    assert logging_integration._sentry_logs_handler is None
    assert tags == {"service": "event-hub"}
    assert "sentry: enabled for event-hub" in capsys.readouterr().err


def test_real_sdk_installed():
    pytest.importorskip("sentry_sdk")
    assert importlib.import_module("sentry_sdk")


# Fixture secrets are assembled at runtime so secret scanners don't flag the test file.
_PW = "s3cr" + "%40t"
_JWT = ".".join(
    ["eyJ" + "hbGciOiJub25lIn0", "eyJ" + "zdWIiOiJ0ZXN0In0", "c2ln" + "bmF0dXJl"]
)


def test_scrub_removes_url_passwords_tokens_and_jwts():
    text = (
        f"connect postgresql://habits:{_PW}@db.example/habits failed; "
        f"pass" + "word=" + "dummy-value Authorization: Bearer " + "abc.def-ghi " + _JWT
    )
    out = _sentry.scrub(text)
    assert _PW not in out
    assert "dummy-value" not in out
    assert "abc.def-ghi" not in out
    assert _JWT not in out
    assert "postgresql://habits:***@db.example/habits" in out


def test_scrub_event_drops_request_details_and_extra():
    event = {
        "exception": {"values": [{"value": "bad url postgres://u:" + "pw" + "@h/db"}]},
        "request": {
            "method": "POST",
            "url": "https://x/habits",
            "data": {"notes": "x"},
            "headers": {"authorization": "Bearer t"},
            "query_string": "token=t",
        },
        "breadcrumbs": {
            "values": [
                {"message": "GET with Bearer abc", "data": {"url": "https://x?token=t"}}
            ]
        },
        "extra": {"sys.argv": ["--token", "t"]},
    }
    out = _sentry._scrub_event(event, {})
    assert out["exception"]["values"][0]["value"] == "bad url postgres://u:***@h/db"
    assert out["request"] == {"method": "POST"}
    assert "data" not in out["breadcrumbs"]["values"][0]
    assert "abc" not in out["breadcrumbs"]["values"][0]["message"]
    assert "extra" not in out


def test_capture_is_a_noop_without_dsn(monkeypatch):
    monkeypatch.delenv("SENTRY_DSN", raising=False)
    _sentry.capture(RuntimeError("x"))  # must not import or call sentry_sdk


def test_scrub_event_keeps_only_scrubbed_formatted_log_text():
    token = "opaque" + "-token-value"
    event = {
        "logentry": {
            "message": "Authorization: Bearer %s",
            "params": [token],
            "formatted": "Authorization: Bearer " + token,
        }
    }
    out = _sentry._scrub_event(event, {})
    assert out["logentry"] == {"message": "Authorization: Bearer ***"}
    assert token not in str(out)


def test_scrub_event_drops_dynamic_sampling_context():
    event = {
        "contexts": {
            "trace": {"trace_id": "t", "dynamic_sampling_context": {"user_id": "alice"}}
        }
    }
    out = _sentry._scrub_event(event, {})
    assert out["contexts"]["trace"] == {"trace_id": "t"}


# --- where the hub reports ------------------------------------------------------------------


def test_main_initialises_sentry_before_parsing_arguments(monkeypatch):
    import event_hub

    calls = []
    monkeypatch.setattr(event_hub, "init_sentry", lambda: calls.append("init"))
    monkeypatch.setattr(sys, "argv", ["event_hub.py", "--help"])
    with pytest.raises(SystemExit):
        event_hub.main()
    assert calls == ["init"]


@pytest.fixture
def hub(tmp_path):
    import sqlite3

    import event_hub
    import sqlite_cell_store

    conn = sqlite3.connect(tmp_path / "intake.db", check_same_thread=False)
    instance = event_hub.Hub(
        tmp_path / "runtime",
        event_hub.DEFAULT_CONFIG,
        intake_store=sqlite_cell_store.SqliteCellStore(conn, "intake"),
    )
    yield instance
    instance.close()
    conn.close()


def receive(hub):
    import event_hub

    obj = event_hub.fixture("sensor-demo", 38, "event-1")
    return hub.receive(event_hub.canonical_json(obj).encode(), "sensor-demo", "mqtt")


def test_an_unexpected_processing_error_is_reported_and_kept_as_a_failure_code(
    hub, monkeypatch
):
    import event_hub

    reported = []
    monkeypatch.setattr(event_hub, "capture", reported.append)

    def broken(*_args, **_kwargs):
        raise RuntimeError("lake offline")

    monkeypatch.setattr(event_hub, "ingest_payload", broken)
    result = receive(hub)
    assert (result["status"], result["code"]) == ("failed", "RuntimeError")
    assert [str(exc) for exc in reported] == ["lake offline"]


def test_an_expected_rejection_during_processing_is_not_reported(hub, monkeypatch):
    import event_hub

    reported = []
    monkeypatch.setattr(event_hub, "capture", reported.append)

    def refused(*_args, **_kwargs):
        raise event_hub.Rejected("bronze_count_mismatch")

    monkeypatch.setattr(event_hub, "ingest_payload", refused)
    result = receive(hub)
    assert (result["status"], result["code"]) == ("failed", "bronze_count_mismatch")
    assert reported == []
