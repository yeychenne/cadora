"""Token and cost aggregation over Cadora run archives."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from cadora.archive import list_runs


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
    cost_usd: float | None = None
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
    by_model: list[dict]
    by_executor: list[dict]
    nodes: list[NodeUsage]

    def to_dict(self) -> dict:
        data = asdict(self)
        data["nodes"] = [asdict(node) for node in self.nodes]
        return data


def summarize_usage(
    archive_root: str | Path = "runs",
    *,
    since: str | datetime | None = None,
) -> UsageSummary:
    """Aggregate token usage from every manifest under ``archive_root``."""
    cutoff = parse_since(since)
    manifests = [
        manifest
        for manifest in list_runs(archive_root)
        if _include_manifest(manifest, cutoff)
    ]
    nodes = [node for manifest in manifests for node in normalize_manifest_usage(manifest)]

    by_model = _group(nodes, "model")
    by_executor = _group(nodes, "executor")
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
        by_model=by_model,
        by_executor=by_executor,
        nodes=nodes,
    )


def normalize_manifest_usage(manifest: dict) -> list[NodeUsage]:
    """Normalize every node usage record in one run manifest."""
    run_id = str(manifest.get("run_id", ""))
    executor = str(manifest.get("executor") or "unknown")
    normalized = []
    for node in manifest.get("nodes", []):
        usage = node.get("usage") or {}
        meta = node.get("meta") or {}
        input_tokens = _int(usage.get("input_tokens") or usage.get("inputTokens"))
        output_tokens = _int(usage.get("output_tokens") or usage.get("outputTokens"))
        cache_creation = _int(
            usage.get("cache_creation_input_tokens")
            or usage.get("cacheCreationInputTokens")
        )
        cache_read = _int(
            usage.get("cache_read_input_tokens") or usage.get("cacheReadInputTokens")
        )
        total = _int(usage.get("total_tokens") or usage.get("totalTokens"))
        if not input_tokens and not output_tokens and total:
            input_tokens = total

        generation = input_tokens + output_tokens
        context = generation + cache_creation + cache_read
        normalized.append(
            NodeUsage(
                run_id=run_id,
                node_id=str(node.get("node_id", "")),
                executor=executor,
                model=node.get("model"),
                funding=meta.get("funding_resolved") or meta.get("funding") or "unknown",
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                cache_creation_input_tokens=cache_creation,
                cache_read_input_tokens=cache_read,
                generation_tokens=generation,
                context_tokens=context if context else total,
                cost_usd=node.get("cost_usd"),
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
            },
        )
        bucket["node_count"] += 1
        bucket["input_tokens"] += node.input_tokens
        bucket["output_tokens"] += node.output_tokens
        bucket["generation_tokens"] += node.generation_tokens
        bucket["context_tokens"] += node.context_tokens
        bucket["cost_usd"] += node.cost_usd or 0.0
    return sorted(groups.values(), key=lambda item: item["context_tokens"], reverse=True)


def _int(value) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)
