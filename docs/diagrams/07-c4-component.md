# 07 — C4 Component (se-event-hub)

```mermaid
C4Component
    title se-event-hub - components
    Container_Boundary(hub, "se-event-hub process") {
        Component(cli, "main / run", "event_hub.main, event_hub_transport.run", "commands setup, serve, onboard, simulate, demo, status, retry")
        Component(http, "HTTP Handler", "handler_for", "local origin check, operator auth, per-source bearer, body limits")
        Component(worker, "MQTTWorker", "paho-mqtt v2, manual ack", "durable session se-event-hub-worker-v1")
        Component(timer, "Retry timer", "Server.service_actions", "every 15 s, 10 receipts, automatic backoff")
        Component(core, "Hub", "event_hub.Hub", "receive, process, evaluate, retry, snapshot, sync_sources")
        Component(val, "Validation", "validate, load_config, json_object", "event.v1 and event-hub.config.v1")
        Component(intakecli, "Intake commands", "data-intake-cli.py loaded in-process", "source register, enqueue, claim, land, verify, fail, retry")
        Component(ingest, "data_lake", "ingest_payload, ObjectStore", "landing, Bronze Parquet, manifests, checkpoints")
        Component(cell, "cell_store", "ApiCellStore", "stdlib HTTP client, no redirects")
        Component(sen, "_sentry", "init_sentry, capture, scrub", "opt-in error reporting")
        Component(prov, "Provisioning", "setup, onboard, write_acl, _password_stage", "secrets, ACL, password file")
    }
    ContainerDb(db, "hub.db")
    ContainerDb(lake, "Local lake")
    System_Ext(intake, "Intake cell API")
    System_Ext(broker, "Mosquitto")
    Rel(http, core, "receive, snapshot, retry")
    Rel(worker, core, "receive")
    Rel(timer, core, "retry automatic")
    Rel(core, val, "validate")
    Rel(core, intakecli, "call cmd_*")
    Rel(core, ingest, "ingest_payload")
    Rel(intakecli, cell, "list, create, update")
    Rel(cell, intake, "HTTPS /v1/*")
    Rel(core, db, "SQL")
    Rel(ingest, lake, "files")
    Rel(worker, broker, "subscribe, ack")
    Rel(core, sen, "capture")
    Rel(cli, prov, "setup, onboard")
```

Sources: `infra/scripts/services/event_hub.py`, `infra/scripts/services/event_hub_transport.py`, `infra/scripts/services/_sentry.py`,
`infra/scripts/cli/data-intake-cli.py`, `infra/scripts/utils/cell_store.py`, `infra/scripts/data_lake/ingest.py`, `infra/scripts/data_lake/storage.py`.

หมายเหตุ: `utils/*` และ `data_lake/*` เป็น vendored copies (README; `pyproject.toml` mypy exclude) — ต้นฉบับอยู่ที่ solo-empire / data-lake.
