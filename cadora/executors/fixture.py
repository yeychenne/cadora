"""Deterministic local executor for private demos and tests.

The fixture backend does not call an external agent or model service. It writes
small, predictable AI-DLC markdown artifacts so Cadora's runner, archive, gates,
and HITL/MCP review surfaces can be exercised in environments where a real
headless agent is unavailable or inappropriate for sensitive content.
"""

from __future__ import annotations

from pathlib import Path

from cadora.executors.base import ExecutionResult, NodeExecutor
from cadora.topology import Node


class FixtureExecutor(NodeExecutor):
    """Local no-model executor that emits deterministic review artifacts."""

    name = "fixture"

    def run(self, node: Node, prompt: str, *, cwd: str, env=None) -> ExecutionResult:
        workspace = Path(cwd)
        docs = workspace / "aidlc-docs" / node.phase
        revised = "Human review of your previous attempt" in prompt
        artifacts = _write_node_artifacts(docs, node, prompt, revised=revised)
        return ExecutionResult(
            node_id=node.id,
            ok=True,
            exit_code=0,
            text=(
                f"fixture completed {node.id}"
                + (" after review comments" if revised else "")
            ),
            model="local-fixture",
            meta={"external_model": False, "private": True, "artifacts": artifacts},
        )


def _write_node_artifacts(docs: Path, node: Node, prompt: str, *, revised: bool) -> list[str]:
    docs.mkdir(parents=True, exist_ok=True)
    prompt_excerpt = _excerpt(prompt)
    if node.id == "requirements":
        return _write_requirements(docs, prompt_excerpt, revised=revised)
    if node.id == "design":
        return _write_design(docs, prompt_excerpt, revised=revised)
    return [_write_generic(docs, node, prompt_excerpt, revised=revised)]


def _write_requirements(docs: Path, prompt_excerpt: str, *, revised: bool) -> list[str]:
    req_dir = docs / "requirements"
    plan_dir = docs / "plans"
    req_dir.mkdir(parents=True, exist_ok=True)
    plan_dir.mkdir(parents=True, exist_ok=True)
    review_controls = (
        "- Human reviewer identity, timestamp, rationale, and decision are recorded.\n"
        "- Request-changes decisions rerun the same stage before downstream work.\n"
        "- Artifact hashes and privacy boundaries are included before approval.\n"
        if revised
        else "- Human review is required before downstream work.\n"
          "- Reviewers can approve, request changes, or abort.\n"
    )
    req = req_dir / "requirements.md"
    plan = plan_dir / "execution-plan.md"
    req.write_text(
        f"""# Fixture Requirements

## Source Prompt
{prompt_excerpt}

## Review Controls
{review_controls}
## Acceptance Criteria
- Cadora pauses at this review gate when HITL is enabled.
- Approval releases the next topology node.
- Request changes revises this same node before proceeding.
""",
        encoding="utf-8",
    )
    plan.write_text(
        """# Fixture Execution Plan

1. Generate local requirements artifacts.
2. Pause for human review when the topology marks the node with `review: true`.
3. Apply review comments through Cadora's normal revision loop.
4. Continue only after approval.
""",
        encoding="utf-8",
    )
    return [str(req.relative_to(docs.parents[1])), str(plan.relative_to(docs.parents[1]))]


def _write_design(docs: Path, prompt_excerpt: str, *, revised: bool) -> list[str]:
    design_dir = docs / "application-design"
    design_dir.mkdir(parents=True, exist_ok=True)
    design = design_dir / "design.md"
    design.write_text(
        f"""# Fixture Design

## Source Prompt
{prompt_excerpt}

## Components
- Local fixture executor
- Cadora topology runner
- Human review channel
- Run archive

## Review Boundary
The fixture produces deterministic documents only; human approval remains the
boundary that lets downstream AI-DLC stages continue.

Revision applied: {'yes' if revised else 'no'}
""",
        encoding="utf-8",
    )
    return [str(design.relative_to(docs.parents[1]))]


def _write_generic(docs: Path, node: Node, prompt_excerpt: str, *, revised: bool) -> str:
    path = docs / f"{node.id}.md"
    path.write_text(
        f"""# Fixture Node: {node.id}

Role: {node.role or 'unspecified'}
Revision applied: {'yes' if revised else 'no'}

## Source Prompt
{prompt_excerpt}
""",
        encoding="utf-8",
    )
    return str(path.relative_to(docs.parents[1]))


def _excerpt(prompt: str, limit: int = 700) -> str:
    text = " ".join(prompt.strip().split())
    if not text:
        return "(empty prompt)"
    if len(text) <= limit:
        return text
    return text[: limit - 3].rstrip() + "..."
