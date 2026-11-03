# 20 — Observability

```mermaid
flowchart LR
    subgraph hub["se-event-hub"]
        P["Hub.process exception, not Rejected"] --> CAP["_sentry.capture"]
        HT["HTTP 503 paths"] --> CAP
        MW["MQTT on_message exception"] --> CAP
        SNAP["Hub.snapshot: counts by status, last 50 events, notifications, rejections by code"]
        HEALTH["GET /health: MQTT ready and no last_error and snapshot healthy"]
        OUT["stdout: starting JSON line, stderr: sentry enabled line"]
    end
    CAP -- "only if SENTRY_DSN" --> SEN["Sentry: errors, scrubbed, tag service"]
    HEALTH --> DHC["Docker healthcheck every 15 s, 3 retries"]
    DHC --> PS["docker compose ps: health status"]
    SNAP --> API["GET /api/status"]
    API --> UI["operator page: MQTT connected, complete, failed counts"]
    MQ["Mosquitto log_type error and warning to stdout"] --> LOGS["docker logs"]
    OUT --> LOGS
    LIVE["event_hub_live_test.py"] --> EVID[".runtime/live-evidence.json"]
```

| Signal | Source | Who watches |
|---|---|---|
| Receipt counts (`complete`, `failed`), rejection counts by code | `/api/status` (`Hub.snapshot`) | operator page (manual) |
| Health `healthy` / `degraded` | `/health`, Docker healthcheck | `docker compose ps` (manual) |
| Unexpected exceptions | Sentry (opt-in), `environment` = `SENTRY_ENVIRONMENT` default `production` | unknown — see open questions |
| Broker errors / warnings | Mosquitto stdout | `docker logs` |
| HTTP access log | **disabled** (`log_message` is a no-op) | — |
| Live E2E evidence | `.runtime/live-evidence.json` | operator |

ไม่มี: uptime Worker, heartbeat, PostHog, metrics endpoint, tracing (`traces_sample_rate=None`). ระบบไม่ได้ hosted จึงไม่อยู่ใน uptime monitor ของ prod baseline.

Sources: `infra/scripts/services/_sentry.py`, `infra/scripts/services/event_hub.py` (`snapshot`, `process`), `infra/scripts/services/event_hub_transport.py` (`get_response`, `log_message`, `on_message`, `run`),
`infra/event-hub/compose.yml` (healthcheck), `infra/event-hub/mosquitto.conf`, `infra/event-hub/index.html`, `PRODUCT.md` Measures.

## Open questions

- มี Sentry project สำหรับ event hub และ alert rule ไปที่ใด (repo ไม่มีข้อมูล; ops records ไม่กล่าวถึง event hub)
- PRODUCT.md Measures: "duplicate notifications per event and rule (target 0)" และ "time from receipt to complete" ยังไม่มี query/metric
