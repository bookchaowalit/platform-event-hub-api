# 19 — Service dependency map (runtime)

```mermaid
flowchart LR
    HUB["se-event-hub"]
    HUB --> MQ["Mosquitto 2.0.18 broker, compose service mqtt"]
    HUB --> IC["Intake cell API, platform-intake-api"]
    IC -.-> GW["gateway.bookchaowalit.com/v1/intake on owner client.env"]
    HUB -.-> SEN["Sentry, optional"]
    HUB --> FS[("host filesystem .runtime: hub.db, lake, secrets")]
    HUB --> PY["python:3.11-slim base"]
    HUB --> LIB1["pyarrow: Bronze Parquet read and write"]
    HUB --> LIB2["paho-mqtt 2.1.0: MQTT client"]
    HUB --> LIB3["sentry-sdk 2.x"]
    SETUP["setup and onboard commands"] --> DK["Docker CLI + eclipse-mosquitto image for mosquitto_passwd"]
    DEV["devices and apps"] --> MQ
    DEV --> HUB
```

| Dependency | Kind | Required for | Failure effect |
|---|---|---|---|
| Mosquitto | runtime service | MQTT ingest | `/health` 503 (worker not ready); HTTP ingest still works |
| Intake cell API | hosted API | every `receive` / `process` (`sync_sources`, enqueue … verify), startup | receipt `failed` with error class (e.g. `CellStoreError`), retried; startup fails without config |
| Local filesystem | storage | lake, hub.db, secrets | failure code, no action (lake-first) |
| Sentry | SaaS | error reporting | none (opt-in) |
| Docker CLI | host tool | `setup`, `onboard` password hashing | `mqtt_password_setup_failed` |
| No CDN, no external DB | — | — | — |

Sources: `pyproject.toml`, `uv.lock`, `infra/event-hub/requirements.lock`, `infra/event-hub/compose.yml`, `infra/event-hub/Dockerfile`,
`infra/scripts/services/event_hub.py`, `infra/scripts/services/event_hub_transport.py`, `infra/scripts/utils/cell_store.py`, ops record: api-gateway-edge (2026-11-03).
