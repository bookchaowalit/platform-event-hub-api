# 18 — Git & release workflow

ส่วนใหญ่ commit ตรงเข้า `main` (main-only, SPEC-001 "Main-only: yes"); มี PR เดียวคือ #1 (`ci/devsecops-phase1`). `main` ไม่มี branch protection / ruleset.
ไม่มี deploy trigger: "release" = owner pull แล้ว `docker compose up -d --build` เอง.

```mermaid
gitGraph
    commit id: "1dac9cf"
    commit id: "6b9eb54 deps"
    commit id: "24d5502 retire SQLite control plane"
    commit id: "48e3f8e standalone repo"
    commit id: "04e175e decouple"
    commit id: "c0d7636 license"
    commit id: "499e3e4 renames"
    branch ci/devsecops-phase1
    commit id: "44dbdce shared CI"
    checkout main
    merge ci/devsecops-phase1 id: "d699350 PR 1"
    commit id: "c571a94 red tests"
    commit id: "4bc01cd fix, OSV red"
    commit id: "389b719 uv.lock, green"
    commit id: "a628046 spec review"
```

```mermaid
flowchart LR
    W["edit on main or a branch"] --> C["commit"]
    C --> PUSH["push to main or open PR"]
    PUSH --> CI["CI: event-hub + security"]
    CI -- red --> FIX["fix commit on main"]
    FIX --> CI
    CI -- green --> LOCAL["owner: git pull, docker compose up -d --build"]
    REN["Renovate weekly PR"] --> CI
```

Sources: `git log origin/main`, `gh pr list --state all` (1 PR, merged 2026-10-17), `gh api .../branches/main/protection` (404 Branch not protected), `gh api .../rulesets` (`[]`),
`.github/workflows/ci.yml`, `renovate.json`, `specs/001-l2-baseline/spec.md`.

## Open questions

- ไม่มี tag / release; ต้องการ versioning ของ image หรือไม่
