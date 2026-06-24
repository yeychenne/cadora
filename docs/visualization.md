# Visualization requirements

Cadora must make both *itself* and the *projects it builds* legible at a glance. This specifies the
required visual artifacts and the roadmap for generating them.

## 1. Cadora's own diagrams (canonical — `docs/diagrams/`)

The documentation MUST include and keep current three views:

| Diagram | File | Answers |
|---|---|---|
| **Architecture** | `docs/diagrams/architecture.svg` | What Cadora is: inputs → conductor → backend → outputs; gates + integrity + the MCP review surface |
| **User journey** | `docs/diagrams/user-journey.svg` | How an operator uses it: Define → Run → Build → Verify → Inspect/Deploy, including the HITL review gates |
| **Inputs & outputs** | `docs/diagrams/inputs-outputs.svg` | What goes in (documents, agent, workspace) and what comes back (code, aidlc-docs, archive, integrity, deployed app) |

### Requirements
- **R-VIS-1** — the three diagrams are maintained as standalone SVGs that render on GitHub and embed
  in the docs / beta package.
- **R-VIS-2** — each diagram carries a one-line `<title>`/`<desc>` for accessibility (SVG `role="img"`).
- **R-VIS-3** — when the architecture, the run flow, or the I/O contract changes, the matching diagram
  is updated in the same PR.
- **R-VIS-4** — the user-journey diagram shows the **HITL review gates**, reviewable in the terminal
  or any MCP client (the AI-DLC method favors human review of the documents at key steps).

## 2. Per-project visualization (roadmap — `cadora visualize`)

Beyond Cadora's own docs, Cadora SHOULD generate visualizations of **the project it builds**, derived
from a run's `aidlc-docs/`:

- **Architecture** of the built app — components / services from `inception/application-design/`.
- **User journey** of the built app — from `inception/user-stories/`.
- **Inputs / outputs** of the built app — its APIs, data models, and files.

Candidate surface: a `cadora visualize <run>` command that renders SVG / Mermaid from the aidlc-docs
artifacts, so every run yields the same three legible views of what was built. Post-v0.1.0; tracks
with the per-stage DAG and `cadora eval` (the generated diagrams become another captured, gradable
artifact in the run archive).

## Why this is a first-class requirement

"A clear visual representation of the architecture, the user journey, the inputs and the outputs" is
how an operator — and a beta tester, and a client — understands a run *without reading every
document*. Treating it as a requirement (not an afterthought) keeps Cadora legible as it grows.
