# 10 — Sequences

## 1. MQTT reading → complete → ack

```mermaid
sequenceDiagram
    participant D as Device
    participant B as Mosquitto
    participant W as MQTTWorker
    participant H as Hub
    participant L as Local lake
    participant I as Intake cell API
    participant S as hub.db
    D->>B: PUBLISH se/events/sensor-demo QoS 1
    B->>W: deliver, manual_ack session
    W->>H: receive(payload, source, mqtt)
    H->>I: sync_sources: list sources dataset hub_events
    H->>H: validate event.v1
    H->>L: put identity reservation, put landing payload.json
    H->>S: INSERT hub_receipts with rules_json
    H->>S: attempt_count + 1
    H->>I: enqueue hub-identity, claim worker event-hub lease 1 min
    H->>L: ingest_payload: landing, Bronze Parquet, manifest
    H->>I: land with lake keys, verify
    H->>L: verify_manifest, read Bronze row
    H->>S: BEGIN evaluate rules, insert decisions and notifications, status complete, COMMIT
    H-->>W: status complete, notifications n
    W->>B: PUBACK via client.ack
```

## 2. HTTP reading with per-source bearer

```mermaid
sequenceDiagram
    participant A as App
    participant X as HTTP Handler
    participant H as Hub
    A->>X: POST /api/events/app-demo, Bearer token, JSON body
    X->>X: local_origin check
    alt bearer does not match source token
        X-->>A: 401 unauthorized
    else body not one Content-Length or not JSON or over 4096
        X-->>A: 400 code
    else accepted
        X->>H: receive(body, app-demo, http)
        alt rejected
            X-->>A: 422 rejected with code
        else failed
            X-->>A: 503 failed, resend same event_id
        else complete or duplicate or paused
            X-->>A: 200 result
        end
    end
```

## 3. Failure then automatic retry

```mermaid
sequenceDiagram
    participant T as Retry timer
    participant H as Hub
    participant S as hub.db
    participant I as Intake cell API
    Note over H: lake or cell error inside process
    H->>S: rollback, status failed, error_code = exception class
    H->>H: capture to Sentry if DSN set
    loop every 15 s
        T->>H: retry limit 10 automatic
        H->>S: select not complete, attempt_count below 5, backoff elapsed
        H->>I: intake status, fail stale lease or retry failed intake, claim again
        H->>S: complete or failed again
    end
    Note over H: after 5 attempts only manual retry, POST /api/retry or CLI retry
```

## 4. Operator saves config

```mermaid
sequenceDiagram
    participant O as Operator page
    participant X as HTTP Handler
    participant H as Hub
    participant I as Intake cell API
    O->>X: POST /session Bearer operator secret
    X-->>O: 200, Set-Cookie hub_session
    O->>X: POST /api/config, cookie, body up to 65536 bytes
    X->>X: runtime config path check
    X->>H: lock, write config.staging.json, load_config
    alt invalid
        X-->>O: 400 Rejected code
    else valid
        X->>H: replace config.json, sync_sources
        H->>I: register new sources, set-status active or paused
        X-->>O: 200 saved
    end
```

Sources: `infra/scripts/services/event_hub.py` (`receive`, `process`, `evaluate`, `retry`, `sync_sources`),
`infra/scripts/services/event_hub_transport.py` (`MQTTWorker`, `handler_for`, `run`), `infra/scripts/cli/data-intake-cli.py`, `infra/event-hub/index.html`.
