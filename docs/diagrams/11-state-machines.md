# 11 — State machines

## hub_receipts.status

```mermaid
stateDiagram-v2
    [*] --> pending: receive, new identity
    pending --> complete: process ok, verified Bronze, rules evaluated
    pending --> failed: process raised, error_code set
    pending --> pending: process returned paused, source disabled
    failed --> complete: retry ok
    failed --> failed: retry failed, attempt_count + 1
    failed --> failed: paused, no attempt
    complete --> complete: duplicate receive, no new action
    complete --> [*]
```

- Automatic retry เลือกเฉพาะ `status != 'complete' AND attempt_count < 5` และ backoff `min(300, 15 * 2^(attempt_count-1))` วินาที; หลังจากนั้น `receive` ของ event เดิมคืน `retry_exhausted` และต้องสั่ง manual retry (`Hub.retry()` ไม่มีเงื่อนไข attempt).

Sources: `infra/scripts/services/event_hub.py` (`HUB_SCHEMA`, `receive`, `process`, `retry`).

## Intake cell intake status (as driven by the hub)

```mermaid
stateDiagram-v2
    [*] --> queued: enqueue hub-identity
    queued --> processing: claim, worker event-hub, lease 1 min
    processing --> landed: land with lake keys
    landed --> verified: verify manifest
    processing --> failed: fail worker_interrupted, live lease of a dead worker
    processing --> queued: claim of an expired lease
    failed --> queued: retry
    rejected --> queued: retry
    queued --> rejected: reject, not used by hub
    processing --> rejected: reject, not used by hub
    landed --> rejected: reject, not used by hub
    verified --> [*]
```

Sources: `infra/scripts/cli/data-intake-cli.py` (`QUEUE_STATUSES`, `cmd_claim`, `cmd_land`, `cmd_verify`, `cmd_fail`, `cmd_retry`, `cmd_reject`),
`infra/scripts/services/event_hub.py` (`process`), `infra/database/migrations/033_source_registry_intake_queue.sql`.

## Source status in the Intake cell (hub sync)

```mermaid
stateDiagram-v2
    [*] --> active: register, enabled true
    [*] --> paused: register, enabled false
    active --> paused: disabled in config or removed from config
    paused --> active: enabled in config
    planned --> active: set-status
    active --> retired: set-status, not used by hub
    paused --> retired: set-status, not used by hub
```

Sources: `infra/scripts/services/event_hub.py` (`sync_sources`), `infra/scripts/cli/data-intake-cli.py` (`SOURCE_STATUSES`, `cmd_source_set_status`).

## Rule decision outcome per (event, rule)

```mermaid
stateDiagram-v2
    [*] --> disabled: rule disabled now or in snapshot
    [*] --> stale_or_future: age over max_age_seconds or more than 60 s in future
    [*] --> late: occurred_at at or before watermark
    [*] --> unit_mismatch: unit differs
    [*] --> not_matched: eligible, comparison false
    [*] --> cooldown: eligible, match inside cooldown
    [*] --> notified: eligible, match outside cooldown
    not_matched --> [*]: watermark moves
    cooldown --> [*]: watermark moves
    notified --> [*]: watermark and last_action_at move
```

Sources: `infra/scripts/services/event_hub.py` (`evaluate`), `tests/test_rule_evaluation.py`.

## MQTTWorker connection

```mermaid
stateDiagram-v2
    [*] --> connecting: start thread
    connecting --> connected: on_connect ok
    connecting --> auth_failed: reason code failure, last_error mqtt_auth_failed
    connected --> ready: on_subscribe ok, ready set
    connected --> sub_failed: last_error mqtt_subscription_failed
    ready --> disconnected: processing failed, client.disconnect
    ready --> disconnected: broker gone
    auth_failed --> disconnected
    sub_failed --> disconnected
    disconnected --> connecting: wait 3 s, reconnect
    ready --> [*]: close
```

Sources: `infra/scripts/services/event_hub_transport.py` (`MQTTWorker`). `/health` = 503 เมื่อไม่ `ready` หรือมี `last_error`.
