"""Token and cost aggregation over Cadora run archives."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from cadora.archive import list_runs

# $/MTok (input, cached input, output) — for backends that report tokens but no dollar cost.
# Codex source: developers.openai.com/api/docs/pricing + /codex/pricing, 2026-07-03 (ChatGPT-plan
# credit-funded runs price identically: the credit rate card maps to API rates at $0.04/credit).
# GLM source: docs.z.ai/guides/overview/pricing, 2026-07-03 (glm-5.2 before glm-5 — prefix
# matching relies on insertion order).
_PRICE_PER_MTOK: dict[str, tuple[float, float, float]] = {
    "gpt-5.5": (5.00, 0.50, 30.00),
    "gpt-5.4": (2.50, 0.25, 15.00),
    "gpt-5.4-mini": (0.75, 0.075, 4.50),
    "gpt-5.4-nano": (0.20, 0.02, 1.25),
    "gpt-5.3-codex": (1.75, 0.175, 14.00),
    "glm-5.2": (1.40, 0.26, 4.40),
    "glm-5": (1.00, 0.20, 3.20),
    "glm-4.7": (0.60, 0.11, 2.20),
}


def estimate_cost_usd(
    model: str | None,
    *,
    input_tokens: int = 0,
    cache_read_input_tokens: int = 0,
    output_tokens: int = 0,
    cached_included_in_input: bool = True,
) -> float | None:
    """Price a node from the public rate table; None when the model is unknown.

    Wire semantics differ per vendor: on the OpenAI wire cached input is a SUBSET of
    ``input_tokens`` (``cached_included_in_input=True`` — the uncached remainder bills at the
    full rate); on the Anthropic wire (GLM via Z.ai) ``input_tokens`` EXCLUDES cache reads, so
    both bill additively (``cached_included_in_input=False``). Reasoning tokens are already
    included in ``output_tokens`` upstream and are not double-counted.
    """
    if not model:
        return None
    key = str(model)
    rates = _PRICE_PER_MTOK.get(key)
    if rates is None:  # tolerate dated/suffixed ids, e.g. "gpt-5.5-2026-04-23", "glm-5.2[1m]"
        # LONGEST prefix wins: "gpt-5.4-mini-2026…" must price as mini, never as gpt-5.4.
        for prefix in sorted(_PRICE_PER_MTOK, key=len, reverse=True):
            if key.startswith(prefix):
                rates = _PRICE_PER_MTOK[prefix]
                break
    if rates is None:
        return None
    in_rate, cached_rate, out_rate = rates
    if cached_included_in_input:
        uncached = max(input_tokens - cache_read_input_tokens, 0)
        cached = (
            min(cache_read_input_tokens, input_tokens)
            if input_tokens
            else cache_read_input_tokens
        )
    else:
        uncached, cached = input_tokens, cache_read_input_tokens
    return (uncached * in_rate + cached * cached_rate + output_tokens * out_rate) / 1_000_000


@dataclass
class NodeUsage:
    run_id: str
    node_id: str
    executor: str
    model: str | None = None
    funding: str = "unknown"
    input_tokens: int = 0
    output_tokens: int = 0
    cache_creation_input_tokens: int = 0
    cache_read_input_tokens: int = 0
    generation_tokens: int = 0
    context_tokens: int = 0
    reasoning_output_tokens: int = 0
    cost_usd: float | None = None
    cost_estimated: bool = False  # True when cost_usd came from the price table, not the backend
    credits: float | None = None  # Kiro subscription credits (Kiro reports no tokens/dollars)
    # Portion of cost_usd spent answering a reviewer's Ask/Revise at this node's parked gate —
    # already INCLUDED in cost_usd, surfaced separately so "what did human review cost?" has an
    # answer.
    review_cost_usd: float | None = None
    raw_usage: dict | None = None


@dataclass
class UsageSummary:
    since: str | None
    run_count: int
    node_count: int
    input_tokens: int
    output_tokens: int
    cache_creation_input_tokens: int
    cache_read_input_tokens: int
    generation_tokens: int
    context_tokens: int
    cost_usd: float
    review_cost_usd: float  # conversational-review portion of cost_usd (already included in it)
    credits: float  # Kiro subscription credits across all nodes
    estimated_cost_nodes: int  # nodes whose cost was computed from the price table
    by_model: list[dict]
    by_executor: list[dict]
    by_funding: list[dict]
    by_day: list[dict]
    nodes: list[NodeUsage]

    def to_dict(self) -> dict:
        data = asdict(self)
        data["nodes"] = [asdict(node) for node in self.nodes]
        return data


def summarize_usage(
    archive_root: str | Path | list[str | Path] = "runs",
    *,
    since: str | datetime | None = None,
) -> UsageSummary:
    """Aggregate token usage from every manifest under ``archive_root`` (one dir or several)."""
    cutoff = parse_since(since)
    roots = archive_root if isinstance(archive_root, list) else [archive_root]
    manifests = [
        manifest
        for root in roots
        for manifest in list_runs(root)
        if _include_manifest(manifest, cutoff)
    ]
    nodes: list[NodeUsage] = []
    days: dict[str, dict] = {}
    for manifest in manifests:
        run_nodes = normalize_manifest_usage(manifest)
        nodes.extend(run_nodes)
        key = _day_key(manifest)
        day = days.get(key)
        if day is None:
            day = days[key] = _empty_day(key)
        day["run_count"] += 1
        for node in run_nodes:
            _add_node_to_day(day, node)

    return UsageSummary(
        since=cutoff.isoformat() if cutoff else None,
        run_count=len(manifests),
        node_count=len(nodes),
        input_tokens=sum(n.input_tokens for n in nodes),
        output_tokens=sum(n.output_tokens for n in nodes),
        cache_creation_input_tokens=sum(n.cache_creation_input_tokens for n in nodes),
        cache_read_input_tokens=sum(n.cache_read_input_tokens for n in nodes),
        generation_tokens=sum(n.generation_tokens for n in nodes),
        context_tokens=sum(n.context_tokens for n in nodes),
        cost_usd=sum(n.cost_usd or 0.0 for n in nodes),
        review_cost_usd=round(sum(n.review_cost_usd or 0.0 for n in nodes), 6),
        credits=sum(n.credits or 0.0 for n in nodes),
        estimated_cost_nodes=sum(1 for n in nodes if n.cost_estimated),
        by_model=_group(nodes, "model"),
        by_executor=_group(nodes, "executor"),
        by_funding=_group(nodes, "funding"),
        by_day=[days[key] for key in sorted(days)],
        nodes=nodes,
    )


def normalize_manifest_usage(manifest: dict) -> list[NodeUsage]:
    """Normalize every node usage record in one run manifest."""
    run_id = str(manifest.get("run_id", ""))
    run_executor = str(manifest.get("executor") or "unknown")
    normalized = []
    for node in manifest.get("nodes", []):
        usage = node.get("usage") or {}
        meta = node.get("meta") or {}
        # Per-node executor: phase routing sends construction nodes to a second backend
        # (e.g. Codex); fall back to the run-level executor for manifests without it.
        node_executor = str(node.get("executor") or run_executor)
        input_tokens = _int(usage.get("input_tokens") or usage.get("inputTokens"))
        output_tokens = _int(usage.get("output_tokens") or usage.get("outputTokens"))
        cache_creation = _int(
            usage.get("cache_creation_input_tokens")
            or usage.get("cacheCreationInputTokens")
        )
        cache_read = _int(
            usage.get("cache_read_input_tokens")
            or usage.get("cacheReadInputTokens")
            or usage.get("cached_input_tokens")  # Codex reports cached prompt tokens here
            or usage.get("cachedInputTokens")
        )
        total = _int(usage.get("total_tokens") or usage.get("totalTokens"))
        if not input_tokens and not output_tokens and total:
            input_tokens = total
        reasoning = _int(
            usage.get("reasoning_output_tokens") or usage.get("reasoningOutputTokens")
        )
        # Kiro reports credits (subscription units), not tokens/dollars.
        credits = usage.get("credits")
        if credits is not None:
            try:
                credits = float(credits)
            except (TypeError, ValueError):
                credits = None

        # Backends that report tokens but no dollars (Codex) get a price-table estimate,
        # flagged as such. A backend-reported cost is always authoritative.
        cost_usd = node.get("cost_usd")
        cost_estimated = False
        if not cost_usd and (input_tokens or output_tokens):
            computed = estimate_cost_usd(
                node.get("model"),
                input_tokens=input_tokens,
                cache_read_input_tokens=cache_read,
                output_tokens=output_tokens,
                # GLM rides the Anthropic wire: cache reads are separate from input, not a subset.
                cached_included_in_input=node_executor != "glm",
            )
            if computed is not None:
                cost_usd = computed
                cost_estimated = True

        generation = input_tokens + output_tokens
        context = generation + cache_creation + cache_read
        normalized.append(
            NodeUsage(
                run_id=run_id,
                node_id=str(node.get("node_id", "")),
                executor=node_executor,
                model=node.get("model"),
                funding=meta.get("funding_resolved")
                or meta.get("funding")
                or ("kiro/credits" if credits is not None else "unknown"),
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                cache_creation_input_tokens=cache_creation,
                cache_read_input_tokens=cache_read,
                generation_tokens=generation,
                context_tokens=context if context else total,
                reasoning_output_tokens=reasoning,
                cost_usd=cost_usd,
                cost_estimated=cost_estimated,
                credits=credits,
                review_cost_usd=node.get("review_conversation_cost_usd"),
                raw_usage=usage or None,
            )
        )
    return normalized


def parse_since(value: str | datetime | None) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return _aware(value)
    text = str(value).strip()
    if text.endswith("d") and text[:-1].isdigit():
        return datetime.now(timezone.utc) - timedelta(days=int(text[:-1]))
    if text.endswith("h") and text[:-1].isdigit():
        return datetime.now(timezone.utc) - timedelta(hours=int(text[:-1]))
    try:
        return _aware(datetime.fromisoformat(text.replace("Z", "+00:00")))
    except ValueError as exc:
        raise ValueError(f"invalid --since value: {value!r}") from exc


def _include_manifest(manifest: dict, cutoff: datetime | None) -> bool:
    if cutoff is None:
        return True
    run_time = _manifest_time(manifest)
    return run_time is None or run_time >= cutoff


def _manifest_time(manifest: dict) -> datetime | None:
    for key in ("started_at", "startedAt", "completed_at", "completedAt"):
        if manifest.get(key):
            try:
                return _aware(datetime.fromisoformat(str(manifest[key]).replace("Z", "+00:00")))
            except ValueError:
                pass
    run_id = str(manifest.get("run_id", ""))
    if run_id.startswith("run-") and len(run_id) >= 19:
        try:
            return datetime.strptime(run_id[4:19], "%Y%m%d-%H%M%S").replace(
                tzinfo=timezone.utc
            )
        except ValueError:
            return None
    return None


def _group(nodes: list[NodeUsage], field: str) -> list[dict]:
    groups: dict[str, dict] = {}
    for node in nodes:
        key = str(getattr(node, field) or "unknown")
        bucket = groups.setdefault(
            key,
            {
                field: key,
                "node_count": 0,
                "input_tokens": 0,
                "output_tokens": 0,
                "generation_tokens": 0,
                "context_tokens": 0,
                "cost_usd": 0.0,
                "credits": 0.0,
            },
        )
        bucket["node_count"] += 1
        bucket["input_tokens"] += node.input_tokens
        bucket["output_tokens"] += node.output_tokens
        bucket["generation_tokens"] += node.generation_tokens
        bucket["context_tokens"] += node.context_tokens
        bucket["cost_usd"] += node.cost_usd or 0.0
        bucket["credits"] += node.credits or 0.0
    return sorted(groups.values(), key=lambda item: item["context_tokens"], reverse=True)


def _day_key(manifest: dict) -> str:
    moment = _manifest_time(manifest)
    return moment.date().isoformat() if moment else "undated"


def _empty_day(day: str) -> dict:
    return {
        "day": day,
        "run_count": 0,
        "node_count": 0,
        "generation_tokens": 0,
        "context_tokens": 0,
        "cost_usd": 0.0,
    }


def _add_node_to_day(day: dict, node: NodeUsage) -> None:
    day["node_count"] += 1
    day["generation_tokens"] += node.generation_tokens
    day["context_tokens"] += node.context_tokens
    day["cost_usd"] += node.cost_usd or 0.0


def _int(value) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)
