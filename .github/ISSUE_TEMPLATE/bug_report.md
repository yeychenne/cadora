---
name: Bug report
about: Something in cadora itself misbehaved (crash, wrong result, bad output)
labels: bug
---

**What happened / what did you expect?**

**Reproduce**
```bash
# the exact cadora command
```

**Environment** — paste the output of:
```bash
cadora doctor
python3 --version && pip show cadora | head -2
```

**Evidence (if a run is involved)** — from `runs/<run-id>/`:
- `manifest.json` — at least the failing node's entry (gate/integrity/cost fields included)
- relevant `cadora archive show <run-id>` output

Strip anything confidential — the manifest may contain your prompts and file paths.
