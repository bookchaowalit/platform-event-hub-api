# ADR-0004 — Bounded event.v1 contract and per-source credentials

- Status: accepted (retro-documented)
- Date: retro-documented 2026-11-04

## Decision
- `event.v1` มี field ตายตัว 6 ตัว, payload = `value` (finite number) + `unit` (6 ค่า), ≤4096 bytes, ห้าม duplicate JSON key — ไม่มีช่องให้ credential/free text
- Rejected payload เก็บเพียง SHA-256 digest + reason code (`hub_rejections`)
- Credentials แยกต่อ source: MQTT username/password + ACL `topic write se/events/<source>`; HTTP bearer ต่อ source; operator secret แยก; ทั้งหมดใน `.runtime/secrets.json` mode 0600
- HTTP ต้องมี local `Host`/`Origin`

## Evidence
- `infra/scripts/services/event_hub.py` (`validate`, `json_object`, `Hub.reject`)
- `infra/scripts/services/event_hub_transport.py` (`read_secrets` ตรวจ mode, `write_acl`, `local_origin`, `operator`, `hmac.compare_digest`)
- `tests/test_event_validation.py`, `infra/tests/integrations/event_hub_test.py::test_http_auth_origin_validation_and_config`
- `PRODUCT.md` Positioning

## Consequences
- รองรับเฉพาะ telemetry ตัวเลข; เพิ่มชนิดข้อมูลต้องเปลี่ยน contract
- เพิ่ม source ใหม่ต้อง provision credential + ACL และ restart broker
