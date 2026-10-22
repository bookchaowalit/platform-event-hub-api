# Product

<!-- impeccable:product-schema 1 -->

## Platform

api

## Users

- The Solo Empire owner, as the operator: the local web page at `http://127.0.0.1:8112` (sources,
  rules, events, notifications, retry), signed in with the `operator` value from the private
  `.runtime/secrets.json`.
- IoT devices that publish over MQTT (one scoped identity and topic per device, created with
  `onboard`) and apps that post over HTTP with a per-source bearer token.
- The Intake cell, which holds the hub's sources, intakes and intake events
  (`data-intake-cli.py` commands, run in-process).

## Product Purpose

Take numeric readings from devices and apps, land the exact bytes in the data lake, verify them
into Bronze, and only then evaluate threshold rules that create internal notifications, once per
event and rule, with cooldowns. A crash or lake failure never produces a notification without a
verified Bronze record.

Success: every accepted event ends `complete` (or visibly `failed` with a code and retried), no
duplicate notification for the same event and rule, and rejected payloads leave only a digest and
a reason code.

## Positioning

A local pilot, not a hosted service: MQTT and HTTP on loopback by default, a lake-first pipeline
(landing, Bronze, then actions) and internal notifications only (no webhooks or external
messages). Telemetry is deliberately narrow (one finite value and a unit), so credentials and free
text have no field to travel in.

## Operating Context

- Not deployed: the registry lists it as a local pilot (`host: local`). It runs with
  `infra/event-hub/compose.yml` (hub image plus Mosquitto 2.0.18), hub on `127.0.0.1:8112`, MQTT
  on `127.0.0.1:1884` unless `EVENT_HUB_MQTT_BIND`/`EVENT_HUB_MQTT_PORT` say otherwise.
- Runtime state lives in `infra/event-hub/.runtime/` (private files, mode 0600): secrets, runtime
  config, the hub's SQLite receipts, the local lake, device profiles.
- Errors go to Sentry only when `SENTRY_DSN` is set, without personal data.
- Who uses the pilot today, and with which devices [ต้องยืนยัน].

## Capabilities and Constraints

- Commands (`event_hub.py`): `setup`, `serve`, `onboard`, `simulate`, `demo`, `status`, `retry`.
- HTTP: `GET /health` (200 healthy, 503 degraded); `GET /api/status` and `POST /api/retry`,
  `/api/config`, `/session` for the operator; `POST /api/events/<source>` for HTTP sources (200
  done, 422 rejected, 503 failed). Requests must come from a local `Host`/`Origin`.
- MQTT: topic `se/events/<source>`, QoS 1, not retained; a message is acknowledged only after it is
  complete, a duplicate, rejected, or out of retries.
- Event contract `event.v1`: exactly `event_id`, `source_id`, `event_type`, `occurred_at` (ISO with
  a timezone), `schema_version`, `payload` (`value` finite number, `unit` one of celsius, percent,
  count, seconds, watts, volts); at most 4,096 bytes; the source must be enabled for that
  transport and event type. Anything else is rejected with a stable code (`invalid_json`,
  `duplicate_json_key`, `invalid_event_fields`, `invalid_measurement`, ...).
- Config `event-hub.config.v1`: 1–100 sources, up to 100 rules; a rule compares the value with
  `gt`, `gte`, `lt`, `lte` or `eq`, has a 0–604,800 second cooldown and maximum age, and its only
  action is `internal_notification`.
- Rules use the snapshot taken when the event was received; per rule and source a watermark makes
  late events `late`, events outside the age window `stale_or_future`, and wrong units
  `unit_mismatch`, without moving the watermark.
- Retries: automatic with backoff up to 5 attempts, then manual `retry`.
- Out of scope: hosted deployment, external actions, free-text or credential fields in events.

## Measures

- Receipts by status (`complete`, `failed`) and rejection counts by code, from `/api/status`.
- Notifications per rule, and duplicate notifications for one event and rule (target: 0).
- Time from receipt to `complete` [ต้องยืนยัน: not measured today].
