# 03 — User flows

## Operator: เข้าหน้าเว็บและจัดการกฎ

```mermaid
flowchart TD
    O["Open http://127.0.0.1:8112"] --> H{"Host and Origin local?"}
    H -- no --> X403["403 origin_not_allowed"]
    H -- yes --> PG["GET / returns index.html with CSP"]
    PG --> L["Enter operator secret in login form"]
    L --> S["POST /session with Authorization Bearer"]
    S -- wrong --> X401["401 unauthorized"]
    S -- ok --> CK["Set-Cookie hub_session, HttpOnly, SameSite Strict, Max-Age 3600"]
    CK --> ST["GET /api/status: health, sources, rules, events, notifications, rejections"]
    ST --> E1["Edit source switch, threshold, cooldown"]
    E1 --> SV["POST /api/config with event-hub.config.v1"]
    SV -- invalid --> X400["400 with Rejected code"]
    SV -- "not runtime config" --> X409["409 runtime_config_required"]
    SV -- ok --> SY["sync_sources to Intake cell, 200 saved"]
    ST --> RT["Click retry: POST /api/retry"]
    RT --> ST
```

Sources: `infra/event-hub/index.html`, `infra/scripts/services/event_hub_transport.py` (`get_response`, `do_POST`, `local_origin`, `operator`).

## Operator shell: onboarding อุปกรณ์

```mermaid
flowchart TD
    A["onboard --source-id --event-type --device-host --device-port"] --> B["setup runs first"]
    B --> C{"ids safe, source new, host and port valid?"}
    C -- no --> R["Rejected: invalid_source_id, source_exists, invalid_device_host ..."]
    C -- yes --> D["new password, stage passwords via docker run mosquitto_passwd"]
    D --> E["write secrets.json, runtime config.json, auth/passwords, auth/acl"]
    E --> F["write devices/source.json profile, mode 0600"]
    F --> G["result: restart_required true, no password printed"]
    G --> H["docker compose restart mqtt"]
```

Sources: `infra/scripts/services/event_hub_transport.py` (`onboard`, `_password_stage`, `write_acl`, `write_private_json`), `infra/event-hub/README.md`.

## Device / app: ส่งค่า

```mermaid
flowchart TD
    M["Device publishes QoS 1, retain false to se/events/source"] --> W["MQTTWorker.on_message"]
    W --> Q{"QoS 1 and not retained?"}
    Q -- no --> RJ["reject mqtt_qos_or_retained, ack"]
    Q -- yes --> RCV["Hub.receive"]
    RCV --> OUT{"result"}
    OUT -- "complete, duplicate, rejected, retry_exhausted" --> ACK["client.ack"]
    OUT -- "failed or paused" --> DC["disconnect, broker redelivers"]
    HP["App POST /api/events/source"] --> AU{"bearer matches source token?"}
    AU -- no --> U401["401"]
    AU -- yes --> BD{"one Content-Length, 1..4096 bytes, application/json"}
    BD -- no --> B400["400 code"]
    BD -- yes --> RCV2["Hub.receive"]
    RCV2 --> C200["200 complete or duplicate or paused"]
    RCV2 --> C422["422 rejected"]
    RCV2 --> C503["503 failed: resend same event_id and content"]
```

Sources: `infra/scripts/services/event_hub_transport.py` (`MQTTWorker.on_message`, `handler_for.body`, `do_POST`), `infra/event-hub/README.md`.

## Open questions

- `paused` ใน HTTP คืน 200 (ไม่ใช่ 503) ทั้งที่ยังไม่ complete — ตั้งใจหรือไม่ (โค้ด map เฉพาะ `rejected`→422, `failed`→503)
