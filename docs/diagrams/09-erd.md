# 09 — ERD

ข้อมูลที่ persist มี 3 ที่: (1) `hub.db` SQLite ของ hub, (2) Intake cell (hosted) — โครงสร้างตาม migration ที่ vendored ไว้,
(3) local lake (object keys). `infra/database/schema.sql` เป็น fixture ของ solo-empire DB สำหรับ test doubles เท่านั้น (ไม่ใช่ข้อมูลของ hub).

## hub.db (owned by this repo)

```mermaid
erDiagram
    hub_receipts ||--o{ hub_decisions : "has decision per rule"
    hub_receipts ||--o{ hub_notifications : "at most one per rule"
    hub_receipts {
        text identity PK "sha256 of source NUL event_id"
        text source "UNIQUE with event_id"
        text event_id
        text canonical_sha256
        text raw_key "landing/event-hub/... payload.json"
        text received_at
        text rules_json "rule snapshot at receipt"
        text status "pending, complete, failed"
        text error_code
        integer attempt_count
        text last_attempt_at
    }
    hub_decisions {
        text identity PK,FK
        text rule_id PK
        text outcome "notified, cooldown, not_matched, late, stale_or_future, unit_mismatch, disabled"
    }
    hub_notifications {
        text id PK "sha256 of identity NUL rule_id"
        text identity FK
        text rule_id "UNIQUE with identity"
        text created_at
        text message
    }
    hub_rule_state {
        text rule_id PK
        text source PK
        text event_type PK
        text watermark
        text last_action_at
    }
    hub_rejections {
        text digest PK "sha256 of raw bytes"
        text code PK
        text first_seen
        integer count
    }
```

Sources: `infra/scripts/services/event_hub.py` (`HUB_SCHEMA`, `Hub.__init__` column migration, `Hub.evaluate`, `Hub.reject`).

## Intake cell (external, schema per vendored migration 033)

```mermaid
erDiagram
    source_registry ||--o{ intake_queue : "source_id"
    intake_queue ||--o{ intake_events : "intake_id"
    source_registry {
        integer id PK
        text source_key UK "hub source id"
        text source_kind "application for hub"
        text domain "operations"
        text dataset "hub_events"
        text privacy_class "internal"
        text ingest_mode "stream"
        text status "planned, active, paused, retired"
        datetime last_ingested_at
    }
    intake_queue {
        integer id PK
        text intake_key UK "hub- plus identity"
        integer source_id FK
        text input_ref "event: plus identity"
        text idempotency_key UK
        text status "queued, processing, landed, verified, rejected, failed, cancelled"
        text lease_owner "event-hub"
        datetime lease_expires_at
        text raw_sha256
        text lake_run_id
        text lake_manifest_key
        text lake_raw_object_key
        text lake_bronze_object_key
        text error_code
        datetime verified_at
    }
    intake_events {
        integer id PK
        integer intake_id FK
        text event_type "queued, claimed, landed, verified, rejected, failed, retried, cancelled"
        text from_status
        text to_status
        text actor
    }
```

ความสัมพันธ์ข้าม store (logical, ไม่มี FK): `intake_queue.intake_key = "hub-" + hub_receipts.identity`; `source_registry.source_key = hub_receipts.source`.

Sources: `infra/database/migrations/033_source_registry_intake_queue.sql`, `infra/scripts/cli/data-intake-cli.py` (resources `sources`, `intakes`, `intake-events`),
`infra/scripts/services/event_hub.py` (`sync_sources`, `process`).

## Local lake object keys

| Key pattern | Written by | Content |
|---|---|---|
| `control/event-hub/identities/<identity>.json` | `Hub.receive` | `{"sha256": canonical checksum}` reservation (immutable) |
| `landing/event-hub/source=<s>/event=<identity>/payload.json` | `Hub.receive` | exact first request bytes (recovery copy) |
| `landing/source=<s>/batch_id=<id>/payload.json` | `ingest_payload` | exact bytes, shared landing contract |
| `bronze/domain=operations/dataset=hub_events/schema_version=1/ingest_date=<d>/source=<s>/part-<id>.parquet` | `ingest_payload` | one-row Bronze envelope (`payload_json`) |
| `control/manifests/source=<s>/batch_id=<id>.json` | `ingest_payload` | raw + Bronze sha256, lineage |
| `control/checkpoints/source=<s>/batch_id=<id>.json` | `ingest_payload` | resume point for partial writes |

Sources: `infra/scripts/services/event_hub.py`, `infra/scripts/data_lake/ingest.py`, `infra/scripts/data_lake/storage.py`.

## Runtime files (`infra/event-hub/.runtime/`, gitignored)

`secrets.json` (mqtt / http / operator), `config.json`, `auth/acl`, `auth/passwords`, `devices/<source>.json`, `hub.db`, `hub.lock`, `lake/`, `live-evidence.json`.

Sources: `infra/scripts/services/event_hub_transport.py`, `infra/event-hub/.gitignore`, `infra/event-hub/README.md`.

## Open questions

- schema จริงของ Intake cell อยู่ใน platform-intake-api; migration 033 ที่ vendored ไว้ยังตรงกับ hosted schema หรือไม่ (cell มี `version` ที่ `guarded_update` ใช้ แต่ไม่มีใน 033)
