# event-hub

Event Hub pilot (MQTT + HTTP ingestion, lake-first, action deduplication) พร้อม `data-intake-cli` ที่ใช้ส่ง intake.

ที่มา: ย้ายจาก `solo-empire/infra/event-hub/`, `infra/scripts/services/event_hub*.py`, `infra/scripts/cli/data-intake-cli.py` และ `infra/tests/integrations/event_hub*_test.py` (2026-10-11) พร้อมประวัติของ `infra/event-hub/` (git subtree split).

## โครงสร้าง

Repo นี้คงโครงสร้างแบบ `infra/` เดิมไว้เพื่อให้ path ใน Dockerfile/compose/tests ทำงานเหมือนเดิม:

- `infra/event-hub/` — Dockerfile, compose.yml, mosquitto, config.json, README ของ pilot
- `infra/scripts/services/event_hub*.py`, `infra/scripts/cli/data-intake-cli.py`
- `infra/scripts/utils/{_env,cell_store,task_store,recurring_step,secret_resolver,crm_ledger,decision_store,habits_store,notifications_store}.py` และ `infra/scripts/data_lake/{ingest,storage,parquet,iceberg}.py` — สำเนา vendored จาก solo-empire (ต้นฉบับ data lake อยู่ใน data-lake)
- `infra/database/migrations/033_source_registry_intake_queue.sql` — สำเนา migration ของ source registry/intake queue
- `infra/tests/` — event_hub_test.py, event_hub_live_test.py, sqlite_cell_store double
- `tests/` — กฎของ hub: event validation, config, rule evaluation, Sentry และไฟล์ที่ image ต้องมี (pytest)
- `pyproject.toml` — ค่าของ pytest/coverage/mypy เท่านั้น (ไม่ใช่ package); `ruff.toml` — กฎ lint

## ทดสอบ

ติดตั้ง dependency จาก lock ที่ pin hash ไว้ (pyarrow จำเป็น: event hub อ่าน/เขียน Parquet bronze; paho-mqtt ใช้เฉพาะ transport จริง):

```bash
uv venv -p 3.11 && uv pip install --require-hashes -r requirements.lock
uvx ruff@0.17.1 check . && uvx ruff@0.17.1 format --check .
.venv/bin/mypy .                                              # strict กับ infra/scripts/services/
.venv/bin/python -m pytest -q --cov                           # tests/ + event_hub_test.py; CI gate coverage
.venv/bin/python infra/tests/integrations/data_intake_test.py
```

Live test ต้องรัน `infra/event-hub/compose.yml` เท่านั้น.

## Dependencies และ lock

- `infra/event-hub/requirements.txt` — runtime ของ image; image ติดตั้งจาก `infra/event-hub/requirements.lock` ด้วย `pip install --require-hashes`
- `requirements.txt` — runtime + เครื่องมือทดสอบ (pytest, pytest-cov, mypy); CI ติดตั้งจาก `requirements.lock`

แก้ไฟล์ใดไฟล์หนึ่งแล้วสร้าง lock ใหม่ทั้งสองไฟล์ (คำสั่งอยู่บรรทัดแรกของแต่ละ lock):

```bash
uv pip compile --generate-hashes --python-version 3.11 --python-platform x86_64-manylinux_2_28 infra/event-hub/requirements.txt -o infra/event-hub/requirements.lock
uv pip compile --generate-hashes --python-version 3.11 --python-platform x86_64-manylinux_2_28 requirements.txt -o requirements.lock
```

## Sentry

ตั้ง `SENTRY_DSN` เพื่อเปิดการรายงาน error (ไม่ตั้ง = ปิด, ดู `infra/scripts/services/_sentry.py`): ส่งเฉพาะ error ที่ไม่คาดไว้ (receipt ที่ fail ด้วย exception ที่ไม่ใช่ `Rejected`, 503 ของ HTTP transport, MQTT handler) ไม่ส่ง PII, local variables, request body/header/URL และ scrub token/URL password ก่อนส่ง
