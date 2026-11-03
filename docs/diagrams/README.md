# Core 22 SDLC diagram pack — platform-event-hub-api

ชุดภาพ 22 รายการตาม SDLC Diagram Catalog v2 (knowledge-base `knowledge/tech/core-cs/sdlc-diagram-catalog.md`)
วาดจากโค้ดและ config ของ repo นี้ ณ `origin/main` `a628046` (2026-10-24), เขียนเมื่อ 2026-11-04.
repo นี้คือ **local pilot** (Docker Compose: hub + Mosquitto) ไม่มี hosted runtime — registry `event-hub` = `host: local`, `status: exception`.
Control plane (sources / intakes) อยู่ใน Intake cell API ภายนอก; hub เก็บ projection ของตัวเองใน SQLite `hub.db` และ lake บน filesystem.
ทุก diagram มี `Sources:`; สิ่งที่ไม่รู้อยู่ใน "Open questions" ของไฟล์นั้น.

| # | Item | Status | File | Sources read |
|---|---|---|---|---|
| 01 | Business process (As-Is / To-Be) | done (To-Be from README / PRODUCT / product card) | [01-business-process.md](01-business-process.md) | event_hub.py, event_hub_transport.py, data-intake-cli.py, ingest.py, PRODUCT.md, infra/event-hub/README.md, DOCPACK |
| 02 | Use cases | done | [02-use-cases.md](02-use-cases.md) | event_hub_transport.py, event_hub.py, compose.yml, mosquitto.acl, cell_store.py |
| 03 | User flows | done | [03-user-flows.md](03-user-flows.md) | index.html, event_hub_transport.py, README |
| 04 | Traceability (RTM) | done | [04-traceability.md](04-traceability.md) | PRODUCT.md, specs/001-l2-baseline/spec.md, tests/, infra/tests/, pyproject.toml, ci.yml |
| 05 | C4 Context | done | [05-c4-context.md](05-c4-context.md) | event_hub.py, cell_store.py, _sentry.py, compose.yml, ops record api-gateway-edge |
| 06 | C4 Container | done | [06-c4-container.md](06-c4-container.md) | compose.yml, Dockerfile, mosquitto.conf, start-mqtt.sh, event_hub*.py |
| 07 | C4 Component | done | [07-c4-component.md](07-c4-component.md) | infra/scripts/services/*, cli/data-intake-cli.py, utils/cell_store.py, data_lake/* |
| 08 | ADRs (retro-documented) | done | [08-adr/](08-adr/) — [0001](08-adr/ADR-0001-lake-first-verified-bronze-before-actions.md), [0002](08-adr/ADR-0002-local-loopback-pilot-docker-compose.md), [0003](08-adr/ADR-0003-intake-cell-api-as-control-plane.md), [0004](08-adr/ADR-0004-bounded-event-contract-and-scoped-credentials.md), [0005](08-adr/ADR-0005-opt-in-scrubbed-sentry.md), [0006](08-adr/ADR-0006-hash-pinned-deps-and-shared-ci.md) | code, git log, PRODUCT.md, spec, registry, central docs |
| 09 | ERD | done | [09-erd.md](09-erd.md) | event_hub.py HUB_SCHEMA, migrations/033, data-intake-cli.py, ingest.py |
| 10 | Sequences | done | [10-sequences.md](10-sequences.md) | event_hub.py, event_hub_transport.py, data-intake-cli.py, index.html |
| 11 | State machines | done | [11-state-machines.md](11-state-machines.md) | event_hub.py, data-intake-cli.py, migrations/033, event_hub_transport.py |
| 12 | API & integrations | done | [12-api-integration.md](12-api-integration.md) | event_hub_transport.py, cell_store.py, task_store.py, mosquitto.conf/.acl, _sentry.py |
| 13 | Threat model | done | [13-threat-model.md](13-threat-model.md) | services/*, compose.yml, mosquitto.conf, index.html, ci.yml, branch protection API |
| 14 | Backup & DR | done (no backup exists; RTO/RPO not defined) | [14-backup-dr.md](14-backup-dr.md) | event_hub*.py, ingest.py, compose.yml, README, central ingestion spec |
| 15 | CI/CD pipeline | done | [15-cicd-pipeline.md](15-cicd-pipeline.md) | ci.yml, renovate.json, platform-ci-workflows @87dc5e1, gh run list |
| 16 | Deployment | done (local compose only) | [16-deployment.md](16-deployment.md) | compose.yml, Dockerfile, start-mqtt.sh, README, online-systems.yaml |
| 17 | Test coverage matrix | done | [17-test-coverage-matrix.md](17-test-coverage-matrix.md) | tests/, infra/tests/integrations/, pyproject.toml, spec |
| 18 | Git & release workflow | done | [18-git-release-workflow.md](18-git-release-workflow.md) | git log, gh pr list, branch protection + rulesets API, renovate.json |
| 19 | Service dependency map | done | [19-service-dependency-map.md](19-service-dependency-map.md) | pyproject.toml, uv.lock, requirements.lock, compose.yml, cell_store.py |
| 20 | Observability | done | [20-observability.md](20-observability.md) | _sentry.py, event_hub*.py, compose.yml, mosquitto.conf, index.html, PRODUCT.md |
| 21 | Incident response | done (local alert channels only; no postmortem process) | [21-incident-response.md](21-incident-response.md) | event_hub*.py, cell_store.py, README, compose.yml |
| 22 | DORA | done (proxy: no deployments; rework rate cannot compute) | [22-dora.md](22-dora.md) | git log, gh run list, gh run view, gh pr list |

## Reused docs (ลิงก์ ไม่คัดลอก)

- Product contract, constraints, measures: [PRODUCT.md](../../PRODUCT.md) — ใช้ใน 01–04, 08, 20
- Pilot runbook + recovery semantics: [infra/event-hub/README.md](../../infra/event-hub/README.md) — ใช้ใน 01, 03, 14, 16, 21
- L2 baseline spec + red/green log: [specs/001-l2-baseline/spec.md](../../specs/001-l2-baseline/spec.md) — ใช้ใน 04, 08, 17
- Product card: [DOCPACK/00-product-card.md](../../DOCPACK/00-product-card.md) — ใช้ใน 01
- Central: [event-hub-ingestion-spec.md](https://github.com/bookchaowalit/architecture-docs/blob/main/systems/ai-data/event-hub-ingestion-spec.md),
  [source-registry-and-intake.md](https://github.com/bookchaowalit/architecture-docs/blob/main/systems/ai-data/source-registry-and-intake.md),
  [devsecops-phase1 rollout register](https://github.com/bookchaowalit/architecture-docs/blob/main/operations/platform/devsecops-phase1/rollout-register.md)

## Open questions สำหรับ owner (รวม)

1. Container `se-event-hub` ได้ `EMPIRE_INTAKE_API` + token `event-hub` จากไหน (compose ไม่ส่ง env/mount) — ถ้าไม่มี hub start ไม่ได้ (16)
2. ใครใช้ pilot อยู่ และอุปกรณ์จริงตัวใด (01)
3. Automation `event-hub` มี grant `intake.*` ใน identity หรือไม่ (12)
4. TLS / tunnel ก่อนเปิด LAN; cookie `hub_session` = operator secret ควรเปลี่ยนเป็น session id หรือไม่ (13)
5. Backup ของ `.runtime/`, RTO/RPO, ผู้ลบตาม retention 30 วัน (14)
6. Sentry project / alert routing (20, 21)
7. Branch protection บน `main` ของ repo public (13, 18)
8. `infra/event-hub/README.md` ล้าสมัย (`task event-hub`, `requirements.txt`, hub.db มีตาราง intake) และ SPEC-001 §4 ยังเสีย (04, ADR-0003)
