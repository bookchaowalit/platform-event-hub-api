# 15 — CI/CD pipeline

```mermaid
flowchart TD
    P["push to main"] --> CI
    PR["pull_request"] --> CI
    WD["workflow_dispatch"] --> CI
    CI["workflow CI, concurrency ci-ref, cancel in progress, contents read"]
    CI --> J1["job event-hub: ci-python.yml at 87dc5e1 v1.1.1"]
    CI --> J2["job security: security.yml at 87dc5e1 v1.1.1"]
    subgraph py["event-hub on ubuntu-24.04, Python 3.11"]
        S1["checkout"] --> S2["setup uv"]
        S2 --> S3["install: uv sync --frozen --all-extras"]
        S3 --> S4["lint: Ruff"]
        S4 --> S5["typecheck: mypy"]
        S5 --> S6["test: pytest --cov, then data_intake_test.py"]
        S6 --> S7["coverage gate 70"]
    end
    subgraph sec["security"]
        G["gitleaks"]
        SG["semgrep"]
        O["osv-scanner on uv.lock"]
        TV["trivy-fs"]
    end
    J1 --> py
    J2 --> sec
    S7 --> END["no deploy job: local pilot"]
```

- `event_hub_live_test.py` ไม่อยู่ใน CI (ต้องใช้ compose stack) — README, ci.yml comment
- Renovate เปิด PR dependency ทุกวันจันทร์ก่อน 9 โมง (Asia/Bangkok) และ lock maintenance วันที่ 1 ของเดือน
- Runs ล่าสุด (`gh run list`, 2026-11-04): 5 runs, 4 success, 1 failure (`4bc01cd`, `security / osv-scanner` step Scan, 2026-10-22)

Sources: `.github/workflows/ci.yml`, `renovate.json`, platform-ci-workflows `.github/workflows/ci-python.yml` และ `security.yml` ที่ commit `87dc5e1c7e0416886a6371d5a101bb2ccba38edd` (local clone),
`gh run list -R bookchaowalit/platform-event-hub-api`, `gh run view 39707588455`.
