# 01 — Business process (As-Is / To-Be)

ระบบนี้คือ **local pilot** ของ Event Hub: รับค่าตัวเลข (telemetry) จากอุปกรณ์ผ่าน MQTT และจากแอปผ่าน HTTP,
เก็บ exact bytes ลง data lake, ตรวจ Bronze ให้ผ่านก่อน แล้วจึงประเมิน threshold rule เพื่อสร้าง internal notification
(ไม่มี external action). ไม่ได้ deploy แบบ hosted: registry ระบุ `host: local`, `status: exception`.

## As-Is (จากโค้ดวันนี้)

```mermaid
flowchart TD
    D["IoT device: MQTT publish se/events/source"] --> V
    A["App: POST /api/events/source with bearer"] --> V
    V{"validate event.v1 against config"}
    V -- "Rejected code" --> Q["hub_rejections: digest + reason code only"]
    V -- ok --> ID{"identity = sha256 of source + event_id"}
    ID -- "same id, other content" --> Q
    ID -- "already complete" --> DUP["return duplicate, no new action"]
    ID -- new --> RES["lake: control/event-hub/identities reservation + landing raw bytes"]
    RES --> RC["hub.db: hub_receipts row, rules_json snapshot"]
    RC --> P["process receipt"]
    DUP --> ACK
    P --> IN["Intake cell: enqueue, claim"]
    IN --> ING["data_lake.ingest_payload: landing + Bronze Parquet + manifest"]
    ING --> LAND["Intake cell: land, verify"]
    LAND --> BR{"manifest ok and Bronze row matches checksum?"}
    BR -- no --> F["hub_receipts status failed + error_code"]
    BR -- yes --> EV["evaluate rules: decisions, rule_state watermark, cooldown"]
    EV --> N["hub_notifications: one per event and rule"]
    EV --> C["hub_receipts status complete"]
    C --> ACK["MQTT ack or HTTP 200"]
    F --> R{"attempt_count below 5?"}
    R -- yes --> AR["automatic retry every 15 s with backoff"]
    AR --> P
    R -- no --> MR["operator: POST /api/retry or CLI retry"]
    MR --> P
    Q --> ACK2["MQTT ack or HTTP 422"]
```

Sources: `infra/scripts/services/event_hub.py` (`validate`, `Hub.receive`, `Hub.process`, `Hub.evaluate`, `Hub.retry`),
`infra/scripts/services/event_hub_transport.py` (`MQTTWorker.on_message`, `handler_for.do_POST`, `run.Server.service_actions`),
`infra/scripts/cli/data-intake-cli.py`, `infra/scripts/data_lake/ingest.py`, `PRODUCT.md`, `infra/event-hub/README.md`.

### งานของ operator (As-Is)

```mermaid
flowchart LR
    S["setup: secrets.json, runtime config, MQTT passwords, ACL"] --> UP["docker compose up -d --build"]
    UP --> ON["onboard: new MQTT identity + device profile"]
    ON --> RB["restart mqtt broker to load ACL"]
    UP --> UI["web page 127.0.0.1:8112: sources, rules, events, notifications"]
    UI --> CFG["POST /api/config: save sources and rules"]
    UI --> RT["POST /api/retry"]
    UP --> SIM["simulate --transport mqtt or http"]
```

Sources: `infra/event-hub/README.md`, `infra/scripts/services/event_hub_transport.py` (`setup`, `onboard`, `simulate`), `infra/event-hub/index.html`.

## To-Be (จากเอกสารของ repo เท่านั้น)

ไม่มี `docs/UPGRADE-PLAN.md`; To-Be ต่อไปนี้มาจาก `infra/event-hub/README.md` ส่วน "ขอบเขต pilot และขั้นถัดไป",
`PRODUCT.md` และ `DOCPACK/00-product-card.md` §8:

- ระบุ use case จริงอย่างน้อยหนึ่งรายการ และยืนยันอุปกรณ์จริง (รุ่น/เครือข่าย) — product card §8, PRODUCT.md `[ต้องยืนยัน]`
- เชื่อมจาก LAN ผ่าน tunnel หรือ listener ที่มี TLS/ACL ก่อนเปิดใช้งาน (README)
- เพิ่ม compaction/batching ก่อนใช้ข้อมูลถี่ (README: ตอนนี้ Parquet part ต่อ event)
- action adapter สำหรับข้อความภายนอก / actuator / API ภายนอก (README; PRODUCT.md ระบุว่า out of scope ตอนนี้)
- Redpanda เป็นทางเลือกในอนาคตเมื่อต้อง buffering/fan-out (README)

```mermaid
flowchart LR
    DEV["verified real device"] --> TLS["TLS or tunnel listener"]
    TLS --> HUB["event hub: same lake-first pipeline"]
    HUB --> BATCH["compaction or batching of Bronze parts"]
    HUB --> ACT["external action adapters, tested for side effects"]
    HUB -.-> RP["optional Redpanda buffer"]
```

Sources: `infra/event-hub/README.md`, `PRODUCT.md`, `DOCPACK/00-product-card.md`.

## Open questions

- ใครใช้ pilot อยู่จริงวันนี้ และอุปกรณ์ใด (PRODUCT.md `[ต้องยืนยัน]`; registry data-sources: "confirm which devices still post")
- To-Be ข้างบนมีลำดับความสำคัญหรือวันที่หรือไม่ — ไม่มี roadmap ใน repo
