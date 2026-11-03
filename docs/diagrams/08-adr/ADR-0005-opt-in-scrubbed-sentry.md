# ADR-0005 — Opt-in, scrubbed Sentry for unexpected errors only

- Status: accepted (retro-documented)
- Date: retro-documented 2026-11-04 (SPEC-001, 2026-10-22)

## Decision
`init_sentry` เป็น no-op ถ้าไม่มี `SENTRY_DSN`. เมื่อเปิด: `send_default_pii=False`, `include_local_variables=False`, `max_request_body_size="never"`,
`traces_sample_rate=None`, logging integration ปิด, `before_send=_scrub_event` (ลบ URL password, bearer, JWT, request เหลือแค่ method, ลบ extra/breadcrumb data).
`capture` เรียกเฉพาะ exception ที่ไม่ใช่ `Rejected` (ใน `Hub.process`), HTTP 503, และ MQTT handler.

## Evidence
- `infra/scripts/services/_sentry.py`, `infra/scripts/services/event_hub.py` (`process` `capture(exc)`), `infra/scripts/services/event_hub_transport.py`
- `tests/test_sentry.py`; `specs/001-l2-baseline/spec.md` AC-11, AC-12

## Consequences
- ไม่มี tracing/performance data; rejections ไม่ถูกนับใน Sentry (ดูจาก `/api/status` แทน)
