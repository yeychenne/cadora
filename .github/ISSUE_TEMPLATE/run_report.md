---
name: Run report
about: A conducted run behaved unexpectedly — gate verdict, integrity finding, cost attribution, or a backend CLI drift
labels: run-report
---

**Which part looks wrong?** (gate verdict / integrity finding / HITL flow / cost attribution / backend drive)

**Backend + model** (e.g. `claude` / `codex gpt-5.5`) and the exact `cadora run` command:
```bash
```

**What the run recorded** — from `runs/<run-id>/manifest.json`: the node's `gate`, `integrity`,
`usage`, `cost_usd` fields (strip confidential content first).

**What you expected instead**

**Backend CLI versions** — paste `cadora doctor` output (backend CLIs ship weekly; contract
drift is exactly what we want to catch here).
