# ADR-0006 — Hash-pinned dependencies and SHA-pinned shared CI

- Status: accepted (retro-documented)
- Date: retro-documented 2026-11-04 (`d699350` 2026-10-17, `389b719` 2026-10-22)

## Decision
- `pyproject.toml` ประกาศ deps (`pyarrow`, `paho-mqtt==2.1.0`, `sentry-sdk`), `uv.lock` pin ทุกตัว; image ติดตั้ง `infra/event-hub/requirements.lock` ด้วย `pip install --require-hashes`
- CI ใช้ reusable workflows ของ `bookchaowalit/platform-ci-workflows` pin ที่ SHA `87dc5e1c...` (v1.1.1): `ci-python.yml` + `security.yml`; coverage gate 70
- Renovate: weekly Monday before 9am (Asia/Bangkok), lock maintenance รายเดือน, group `platform-ci-workflows`

## Evidence
- `pyproject.toml`, `uv.lock`, `infra/event-hub/requirements.lock`, `infra/event-hub/Dockerfile`, `.github/workflows/ci.yml`, `renovate.json`, `README.md`
- central rollout register [devsecops-phase1/rollout-register.md](https://github.com/bookchaowalit/architecture-docs/blob/main/operations/platform/devsecops-phase1/rollout-register.md) (row platform-event-hub-api)

## Consequences
- แก้ dependency ต้องสร้าง `uv.lock` และ `requirements.lock` ใหม่ทั้งคู่ (README)
