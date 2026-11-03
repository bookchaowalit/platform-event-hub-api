# ADR-0003 — Intake cell API as the control plane; hub keeps only its own SQLite projection

- Status: accepted (retro-documented)
- Date: retro-documented 2026-11-04 (change landed in `24d5502`, 2026-10-11)

## Context
เดิม sources/intakes อยู่ใน SQLite ของ solo-empire. commit `24d5502` "retire the local SQLite control plane" ย้ายไปใช้ Intake cell.

## Decision
- Sources, intakes, intake-events อยู่ใน Intake cell (`open_cell_store("intake", "event-hub")`, resources `sources`, `intakes`, `intake-events`),
  เรียกผ่านคำสั่งใน `data-intake-cli.py` ที่โหลด in-process (`Hub.call`).
- Hub sync เฉพาะ sources ที่ `dataset=hub_events` (ไม่ pause sources ของระบบอื่น).
- `hub.db` (SQLite WAL, `synchronous=FULL`) เก็บเฉพาะ projection ของ hub: receipts, decisions, rule_state, notifications, rejections.
- ใช้ fenced lease (`claim` 1 นาที, `fail worker_interrupted`) และ optimistic `expected_version` ของ cell.

## Evidence
- `infra/scripts/services/event_hub.py` (`Hub.__init__`, `sync_sources`, `process`, `HUB_SCHEMA`)
- `infra/scripts/utils/cell_store.py`, `infra/scripts/cli/data-intake-cli.py` (`guarded_update`, `cmd_claim`, `cmd_fail`)
- git log `24d5502`, `48e3f8e`

## Consequences
- hub ต้องมี URL + automation token ของ Intake cell; ถ้าไม่มี `Hub()` สร้างไม่ได้ (`ClientConfigError`)
- `infra/event-hub/README.md` ยังบอกว่า `hub.db` มี "ตาราง intake" — ล้าสมัยหลัง `24d5502`
