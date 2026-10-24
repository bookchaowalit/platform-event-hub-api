# SPEC-001 — L2 baseline: locked dependencies, strict types, domain rules under test

Standard: ADR-0004 — TDD + SDD (architecture-docs, `docs/architecture/decisions/ADR-0004-tdd-sdd-standard.md`).
Big Loop Plan Wave 2 (solo-empire `repository-catalog/roadmaps/BIG-LOOP-PLAN-2026-10-21.md`).

## Document control

| Field | Value |
|---|---|
| Spec ID / version | SPEC-001 / 1.0 |
| Status | in review (implemented; owner approval pending) |
| Repository / path | bookchaowalit/platform-event-hub-api / `specs/001-l2-baseline/spec.md` |
| Owner (maker) | Claude Code (Wave 2 cells session) |
| Reviewer (checker) | Fresh Claude reviewer session (no maker context) |
| Approver | Owner (bookchaowalit): pending, see the Wave 2 report owner actions |
| Requirement links | Big Loop DoD L2: `PRODUCT.md`, ≥ 3 test files, Sentry, type check; L1 lockfile |
| Main-only | yes: committed straight to `main`, so the red run is local (ADR-0004 rule 2, v1.1) |
| Created / approved | 2026-10-22 / pending owner |

## 1. Problem and intent

- **Problem:** The local MQTT/HTTP event hub lands events lake-first and evaluates threshold rules; it had no lockfile, type check, Sentry or coverage gate, its image could not import the hub, and its validation, config and rule-evaluation rules were only covered end to end.
- **Outcome:** the image installs exactly `uv.lock` (`uv sync --frozen`), CI runs Ruff, mypy
  (strict on `src/`) and a coverage gate on shared CI v1.1.1, and the cell's own rules have
  tests of their own beyond the generic contract suite.
- **Why now:** Wave 2 of the Big Loop Plan; milestone M1 is 2026-11-15.

## 2. Scope

| In scope | Out of scope (non-goals) |
|---|---|
| Lock file and frozen install, CI caller bump, Ruff/mypy config and fixes, rule tests, `PRODUCT.md` | New endpoints or fields; schema changes |

## 3. Behaviour — scenarios

### AC-01 — The image's files import the hub

- **Given** only the files the Dockerfile COPYs, laid out as /app
- **When** event_hub and event_hub_transport are imported, and the CMD script runs --help
- **Then** both import and the CLI parses its arguments
- **Test:** `tests/test_image_contents.py::test_the_copied_files_import_the_hub_and_its_transport` · level: unit / API with local fakes

### AC-02 — Every copied file passes the dockerignore

- **Given** the Dockerfile COPY sources and Dockerfile.dockerignore
- **When** each source is matched against the whitelist
- **Then** every file is allowed
- **Test:** `tests/test_image_contents.py::test_every_copied_file_is_allowed_by_the_dockerignore` · level: unit / API with local fakes

### AC-03 — Duplicate JSON keys keep their own code

- **Given** a payload with a repeated key
- **When** it is validated
- **Then** Rejected duplicate_json_key
- **Test:** `tests/test_event_validation.py::test_unreadable_json_is_rejected_with_a_stable_code` · level: unit / API with local fakes

### AC-04 — Source and transport must be allowed

- **Given** an unknown source, a source on the wrong transport, or a disabled source
- **When** an event is validated
- **Then** Rejected source_not_allowed
- **Test:** `tests/test_event_validation.py::test_an_unknown_source_or_the_wrong_transport_is_not_allowed` · level: unit / API with local fakes

### AC-05 — Payload is one finite number and a unit

- **Given** missing unit, extra field, string, bool, infinity or a list
- **When** validated
- **Then** Rejected invalid_measurement
- **Test:** `tests/test_event_validation.py::test_the_payload_is_one_finite_number_and_a_unit_and_nothing_else` · level: unit / API with local fakes

### AC-06 — Timestamps need a timezone

- **Given** a naive, unparsable, numeric or over-long occurred_at
- **When** validated
- **Then** timezone_required or invalid_timestamp
- **Test:** `tests/test_event_validation.py::test_occurred_at_must_be_a_short_iso_timestamp_with_a_timezone` · level: unit / API with local fakes

### AC-07 — Rules are exact and bounded

- **Given** a rule with an extra key, bad id, unknown operator, non-numeric or NaN threshold, external action or out-of-range window
- **When** the config loads
- **Then** the matching Rejected code
- **Test:** `tests/test_config_rules.py::test_each_rule_is_exact_bounded_and_only_notifies_internally` · level: unit / API with local fakes

### AC-08 — Source declarations are bounded

- **Given** 0 or 101 sources, a bad source id, or 0/21/unsafe event types
- **When** the config loads
- **Then** invalid_sources / invalid_source / invalid_event_types
- **Test:** `tests/test_config_rules.py::test_a_source_declares_1_to_20_safe_event_types` · level: unit / API with local fakes

### AC-09 — Cooldown suppresses repeat notifications

- **Given** a notified event
- **When** another match arrives inside, then after, the cooldown
- **Then** cooldown, then notified; two notifications
- **Test:** `tests/test_rule_evaluation.py::test_a_second_match_inside_the_cooldown_is_recorded_but_not_notified` · level: unit / API with local fakes

### AC-10 — Late, stale, future and wrong-unit events do not poison the watermark

- **Given** events outside the window or with another unit
- **When** evaluated
- **Then** stale_or_future / unit_mismatch / late, and a later in-window event still notifies
- **Test:** `tests/test_rule_evaluation.py::test_stale_or_future_events_and_wrong_units_do_not_move_the_watermark` · level: unit / API with local fakes

### AC-11 — Unexpected processing errors reach Sentry, rejections do not

- **Given** an ingest that raises RuntimeError, or Rejected
- **When** an event is received
- **Then** the receipt fails with a code; only the RuntimeError is captured
- **Test:** `tests/test_sentry.py::test_an_unexpected_processing_error_is_reported_and_kept_as_a_failure_code` · level: unit / API with local fakes

### AC-12 — Sentry is opt-in and scrubbed

- **Given** no SENTRY_DSN, or a DSN
- **When** init_sentry runs
- **Then** no-op, or errors only with send_default_pii False and the scrubber
- **Test:** `tests/test_sentry.py::test_dsn_initialises_errors_only_without_pii` · level: unit / API with local fakes

## 4. Interfaces and data

| Interface | Change | Contract |
|---|---|---|
| e | v | e |
| G | E | T |
| G | E | T |
| P | O | S |
| P | O | S |
| P | O | S |
| P | O | S |
| M | Q | T |

- **Data model / migration:** N/A — no schema change.
- **Backward compatibility:** a payload with duplicate JSON keys is still rejected, now with the code `duplicate_json_key` instead of `invalid_json`; the image now contains the modules the hub imports.

## 5. Non-functional requirements

| NFR | Target | How it is tested |
|---|---|---|
| Performance | N/A — no hot-path change beyond one comparison per declared range | — |
| Security / privacy | Authorization unchanged; Sentry sends no PII and scrubs tokens/URLs | existing auth tests, Sentry tests |
| Reliability / observability | Reproducible install from the lock file; opt-in Sentry error reporting | image rehearsal before push; Sentry tests |
| Accessibility / UX | N/A — API only | — |

## 6. Test plan (TDD)

- **Test levels:** unit and API tests with local fakes; no hosted service is called.
- **Test data:** generated in each test; no production data.
- **Coverage:** CI gate `coverage-threshold: "70"` (measured 72.25% on 2026-10-22, minus 2 points).
- **Not test-first (exceptions):** tests that pin behaviour the service already had
  (characterization tests, marked below) were written after the code by nature; tooling and
  config changes are covered by CI itself.

### Red / green log

| Scenario | Test | Red: commit / run + failure message | Green: commit / run | Refactor note |
|---|---|---|---|---|
| AC-01 | `tests/test_image_contents.py::test_the_copied_files_import_the_hub_and_its_transport` | c571a94 · `.venv/bin/python -m pytest -q tests/test_image_contents.py` · `AssertionError: ["ModuleNotFoundError: No module named 'cell_store'"]` | CI run of the pushed tip | none |
| AC-02 | `tests/test_image_contents.py::test_every_copied_file_is_allowed_by_the_dockerignore` | characterization (behaviour already present; passed on first run) | CI run of the pushed tip | none |
| AC-03 | `tests/test_event_validation.py::test_unreadable_json_is_rejected_with_a_stable_code` | c571a94 · `.venv/bin/python -m pytest -q tests/test_event_validation.py` · `assert 'invalid_json' == 'duplicate_json_key'` | CI run of the pushed tip | none |
| AC-04 | `tests/test_event_validation.py::test_an_unknown_source_or_the_wrong_transport_is_not_allowed` | characterization (behaviour already present; passed on first run) | CI run of the pushed tip | none |
| AC-05 | `tests/test_event_validation.py::test_the_payload_is_one_finite_number_and_a_unit_and_nothing_else` | characterization (behaviour already present; passed on first run) | CI run of the pushed tip | none |
| AC-06 | `tests/test_event_validation.py::test_occurred_at_must_be_a_short_iso_timestamp_with_a_timezone` | characterization (behaviour already present; passed on first run) | CI run of the pushed tip | none |
| AC-07 | `tests/test_config_rules.py::test_each_rule_is_exact_bounded_and_only_notifies_internally` | characterization (behaviour already present; passed on first run) | CI run of the pushed tip | none |
| AC-08 | `tests/test_config_rules.py::test_a_source_declares_1_to_20_safe_event_types` | characterization (behaviour already present; passed on first run) | CI run of the pushed tip | none |
| AC-09 | `tests/test_rule_evaluation.py::test_a_second_match_inside_the_cooldown_is_recorded_but_not_notified` | characterization (behaviour already present; passed on first run) | CI run of the pushed tip | none |
| AC-10 | `tests/test_rule_evaluation.py::test_stale_or_future_events_and_wrong_units_do_not_move_the_watermark` | characterization (behaviour already present; passed on first run) | CI run of the pushed tip | none |
| AC-11 | `tests/test_sentry.py::test_an_unexpected_processing_error_is_reported_and_kept_as_a_failure_code` | characterization (behaviour already present; passed on first run) | CI run of the pushed tip | none |
| AC-12 | `tests/test_sentry.py::test_dsn_initialises_errors_only_without_pii` | characterization (behaviour already present; passed on first run) | CI run of the pushed tip | none |

## 7. Risks and open questions

| # | Risk or question | Impact | Owner | Resolution / date |
|---|---|---|---|---|
| 1 | Stored rows written before a range rule existed may already be inverted | a patch touching one of those dates is refused until both are fixed | owner | patches that do not touch the range still work |

## 8. Change log

| Version | Date | Change | Scenarios affected | Approved by |
|---|---|---|---|---|
| 1.0 | 2026-10-22 | Implemented with the Wave 2 baseline | all | pending owner |

## Approval

| Gate | Role | Name / date | Decision | Evidence |
|---|---|---|---|---|
| Implemented (DoD) | Reviewer (checker) | Fresh Claude reviewer / 2026-10-22 | see Wave 2 report | CI run of the pushed tip |
| Implemented (DoD) | Approver | Owner | pending | Wave 2 report |

## Review (2026-10-24): changes requested

Checker: independent Claude reviewer subagent (`solo:solo-reviewer`, Sonnet, read-only, no maker context). Status unchanged until fixed.

- Tests run by the checker: .venv/bin/pytest -q: 96 passed, 9 subtests passed
- Gap: §4 Interfaces table is corrupted (single-letter rows). AC-01 cites the import test, but the --help check is a separate test. Regenerate §4 and fix the citation.
