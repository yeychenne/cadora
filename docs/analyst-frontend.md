# Analyst front-end (FE-builder + PDF)

`examples/analyst-frontend.topology.yaml` is a reusable, **domain-agnostic** Cadora topology that turns
any deterministic case-scoring engine into **engine + analyst GUI + PDF**, contract-first.

Over an existing engine it builds:
- a **FastAPI analyst API** — queue, full detail (per-method/rule breakdown + findings), network graph, a
  deterministic **audit/explainability** trail, the actions the engine supports, and a report route;
- a **Vite/React GUI** — Queue → Detail → NetworkGraph → Audit panel → actions → Download report;
- a **WeasyPrint PDF** report renderer (lazy import, HTML fallback);
- backend tests for every route + a renderer test, run under the engine's existing virtualenv.

## Run it

```bash
cadora run examples/analyst-frontend.topology.yaml \
  --cwd <engine-repo> \
  --executor codex --model <model> \
  --gate-cmd ".venv/bin/python -m ruff check . && .venv/bin/python -m pytest -q" \
  --gate-setup off
```

The node **discovers** the engine — its result/score models, any existing FastAPI app, and the domain's
own word for a "case" (claim, trade, alert, transaction, …) — and builds against *those*. Nothing is
hardcoded to a domain.

## Optional contract — `frontend.manifest.yaml`

Drop this at the engine repo root to **steer** the build instead of relying on inference (the
contract-first path). All fields are optional; anything omitted is inferred from the engine's models.

```yaml
entity: trade alert            # the domain's word for a "case"
id_field: alert_id             # the identifier field on the result model
queue_columns:                 # columns for the queue table
  - { key: alert_id,    label: "Alert" }
  - { key: final_score, label: "Score" }
  - { key: level,       label: "Risk" }
detail_sections: [breakdown, network, decision, audit]
graph:
  nodes: entities              # where the graph nodes come from on the result
  edges: relationships         # rendered as react-force-graph `links`
actions: [suspend, escalate]   # only actions the engine actually supports
report_title: "Trade Surveillance — Alert Report"
```

## Lessons baked in (so you don't re-hit them)

- **vite proxy → `http://127.0.0.1:<port>`** (IPv4, not `localhost`): a VM/container proxy can squat `::1`
  and make the API 401. Keep it env-overridable.
- **react-force-graph needs `links`**, not `edges` — the GUI maps `edges → links` (forgetting this renders
  a blank page with no error).
- **Heavy deps imported lazily** (WeasyPrint, boto3): the engine and its tests run with neither installed;
  the PDF path falls back to HTML.
- **Deterministic explainability** is the strength of a deterministic engine — the audit panel and the
  "Ask about this case" box answer *from the report*, with a clearly-marked seam for a real LLM responder
  later.

## Proven generic

Modeled on one engine's bespoke analyst GUI, then driven **unchanged** against a second, independent
engine — a claims-fraud scorer and a trade-surveillance engine — to reproduce a full analyst GUI using
each engine's own vocabulary, with no per-domain edits. One generic topology reproducing a bespoke
analyst GUI on a second engine is the productization test.
