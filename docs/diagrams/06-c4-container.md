# 06 — C4 Container

```mermaid
C4Container
    title Event Hub pilot - containers
    Person(op, "Operator")
    System_Ext(dev, "IoT device")
    System_Ext(app, "HTTP app")
    System_Boundary(local, "Host machine, docker compose project se-event-hub") {
        Container(hub, "se-event-hub", "Python 3.11, http.server + paho-mqtt", "HTTP API and page, MQTT worker, rule engine, retry timer")
        Container(mqtt, "se-event-hub-mqtt", "eclipse-mosquitto 2.0.18", "password file + ACL, QoS 1, persistence autosave 10 s")
        ContainerDb(hubdb, "hub.db", "SQLite WAL", "receipts, decisions, rule_state, notifications, rejections")
        ContainerDb(lake, "Local lake", "files under .runtime/lake", "landing, bronze Parquet, control manifests")
        ContainerDb(rt, ".runtime files", "JSON, mode 0600", "secrets, config, auth acl and passwords, device profiles")
        ContainerDb(mq, "mqtt-data volume", "Docker volume", "broker persistence")
    }
    System_Ext(intake, "Intake cell API")
    System_Ext(sentry, "Sentry")
    Rel(op, hub, "page, /session, /api/*", "HTTP 127.0.0.1:8112")
    Rel(app, hub, "POST /api/events/source", "HTTP")
    Rel(dev, mqtt, "publish se/events/source", "MQTT 127.0.0.1:1884")
    Rel(hub, mqtt, "subscribe se/events/+ as event-hub", "MQTT 1883 in compose network")
    Rel(hub, hubdb, "read and write")
    Rel(hub, lake, "put_bytes, get_bytes")
    Rel(hub, rt, "read secrets and config")
    Rel(mqtt, mq, "persist")
    Rel(hub, intake, "cell API calls", "HTTPS")
    Rel(hub, sentry, "capture_exception", "HTTPS")
```

Sources: `infra/event-hub/compose.yml`, `infra/event-hub/Dockerfile`, `infra/event-hub/mosquitto.conf`, `infra/event-hub/start-mqtt.sh`,
`infra/scripts/services/event_hub.py` (`HUB_SCHEMA`, `Hub.__init__`), `infra/scripts/services/event_hub_transport.py` (`run`, `MQTTWorker`).

หมายเหตุ: host mode (`event_hub.py serve` บน host + broker ใน compose) ใช้ runtime เดียวกันได้ แต่ห้ามรันพร้อม container worker; `fcntl.flock` บน `hub.lock` กันไว้ (`infra/event-hub/README.md`).
