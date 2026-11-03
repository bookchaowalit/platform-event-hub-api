# 21 — Incident response

ช่องทางแจ้งเตือนที่มีจริงในโค้ด: Docker healthcheck (สถานะ `unhealthy` เท่านั้น ไม่ restart อัตโนมัติ), `/health` 503, หน้า operator (`failed` count, error_code),
Sentry (เฉพาะเมื่อตั้ง `SENTRY_DSN`). ไม่มี Telegram / pager ที่ผูกกับ repo นี้.

```mermaid
flowchart TD
    A1["Docker health unhealthy or /health 503"] --> T
    A2["operator page: failed count, error_code, history_unavailable"] --> T
    A3["Sentry issue, if DSN set"] --> T
    A4["device or app reports 503 or no ack"] --> T
    T{"triage via GET /api/status"}
    T -- "mqtt_auth_failed or not connected" --> M["check broker: docker compose ps, logs, auth/passwords, ACL, restart mqtt"]
    T -- "receipts failed with CellStoreError or ClientConfigError" --> C["check Intake cell API, EMPIRE_INTAKE_API, automation token"]
    T -- "intake_not_verified, bronze mismatch, StorageError" --> L["inspect .runtime/lake manifest and Bronze objects"]
    T -- "rejections spike by code" --> R["fix producer payload, resend with new event_id"]
    T -- "hub_already_running" --> W["stop the second writer, host or container"]
    M --> FIX
    C --> FIX
    L --> FIX
    R --> FIX
    W --> FIX
    FIX["fix cause"] --> RT["POST /api/retry or event_hub.py retry, offline after stopping worker"]
    RT --> V{"counts failed = 0 and health healthy?"}
    V -- no --> T
    V -- yes --> PM["postmortem: no template or location in repo"]
```

Sources: `infra/scripts/services/event_hub_transport.py` (`MQTTWorker`, `get_response`, `do_POST`), `infra/scripts/services/event_hub.py` (`Hub.__init__` `hub_already_running`, `process` codes, `retry`, `main`),
`infra/scripts/utils/cell_store.py` (`CellStoreError`), `infra/event-hub/README.md`, `infra/event-hub/compose.yml`.

## Open questions

- severity / ผู้รับผิดชอบ / ที่เก็บ postmortem สำหรับ pilot นี้
- Sentry alert ส่งไปช่องทางใด
