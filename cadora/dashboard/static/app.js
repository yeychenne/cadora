let selectedNodeId = null;
let selectedTab = "activity";
let sinceWindow = "";

const fmtTokens = (value) => {
  const n = Number(value || 0);
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(2)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}k`;
  return String(n);
};

const escapeHtml = (value) =>
  String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");

const statusOf = (run) => {
  if (run.status?.status) return run.status.status;
  if (run.manifest?.ok === true) return "completed";
  if (run.manifest?.ok === false) return "failed";
  return "unknown";
};

const nodeIdOf = (node) => node.node_id || node.nodeId;

const nodesOf = (run) => {
  if (run.status?.nodes) return Object.values(run.status.nodes);
  return (run.manifest?.nodes || []).map((node, index, all) => ({
    node_id: node.node_id,
    role: node.node_id,
    phase: "",
    status: node.ok ? "completed" : "failed",
    depends_on: index > 0 ? [all[index - 1].node_id] : [],
    model: node.model,
    cost_usd: node.cost_usd,
    generation_tokens:
      Number(node.usage?.input_tokens || 0) + Number(node.usage?.output_tokens || 0),
    context_tokens:
      Number(node.usage?.input_tokens || 0) +
      Number(node.usage?.output_tokens || 0) +
      Number(node.usage?.cache_creation_input_tokens || 0) +
      Number(node.usage?.cache_read_input_tokens || 0),
    review: (node.human_reviews || []).at(-1)?.decision,
    gate: node.gate,
    integrity: node.integrity,
    error: node.ok ? null : node.meta?.error || "node failed",
  }));
};

const runCost = (run) => {
  if (!run.manifest?.nodes) return 0;
  return run.manifest.nodes.reduce((sum, node) => sum + Number(node.cost_usd || 0), 0);
};

const runIdFromPath = () => {
  const match = window.location.pathname.match(/^\/runs\/([^/]+)$/);
  return match ? decodeURIComponent(match[1]) : null;
};

const renderPill = (status) => `<span class="pill ${escapeHtml(status)}">${escapeHtml(status)}</span>`;

const costFmt = (value) => `$${Number(value || 0).toFixed(4)}`;

// --- Topology: per-node gate / integrity / review badges -------------------
const gateStatus = (node) => {
  const gate = node.gate;
  if (gate == null) return null;
  if (typeof gate === "string") return gate;
  return gate.status || (gate.passed === true ? "passed" : gate.passed === false ? "failed" : null);
};

const integrityStatus = (node) => {
  const integrity = node.integrity;
  if (integrity == null) return null;
  if (typeof integrity === "string") return integrity;
  if (typeof integrity.passed === "boolean") return integrity.passed ? "clean" : "flagged";
  if (typeof integrity.ok === "boolean") return integrity.ok ? "clean" : "flagged";
  return integrity.status || integrity.mode || null;
};

const badgeTone = (kind, label) => {
  const value = String(label).toLowerCase();
  if (kind === "gate") return value.includes("passed") ? "ok" : value.includes("fail") ? "bad" : "warn";
  if (kind === "integrity") return /clean|ok|off/.test(value) ? "ok" : "warn";
  if (kind === "review") return value === "approve" ? "ok" : /reject|change|abort/.test(value) ? "bad" : "warn";
  return "";
};

const nodeBadges = (node) => {
  const chips = [];
  const gate = gateStatus(node);
  if (gate) chips.push(["gate", `gate ${gate}`, badgeTone("gate", gate)]);
  const integrity = integrityStatus(node);
  if (integrity) chips.push(["integrity", integrity, badgeTone("integrity", integrity)]);
  if (node.review) chips.push(["review", node.review, badgeTone("review", node.review)]);
  return chips
    .map(
      ([kind, label, tone]) =>
        `<span class="badge ${tone}" title="${escapeHtml(kind)}">${escapeHtml(label)}</span>`,
    )
    .join("");
};

// --- FinOps: cost breakdowns, token split, daily trend ---------------------
const bar = (value, max) => {
  const pct = max > 0 ? Math.max(3, Math.round((Number(value || 0) / max) * 100)) : 0;
  return `<span class="bar"><span style="width:${pct}%"></span></span>`;
};

const costTable = (rows, key) => {
  const used = (rows || []).filter(
    (row) => Number(row.cost_usd || 0) > 0 || Number(row.context_tokens || 0) > 0,
  );
  if (!used.length) return `<p class="empty">No usage yet</p>`;
  const maxCost = Math.max(...used.map((row) => Number(row.cost_usd || 0)), 0.0001);
  return used
    .map(
      (row) => `
        <div class="cost-row">
          <strong>${escapeHtml(row[key] || "unknown")}</strong>
          ${bar(row.cost_usd, maxCost)}
          <span class="muted">${fmtTokens(row.context_tokens)} / ${costFmt(row.cost_usd)}</span>
        </div>`,
    )
    .join("");
};

const renderDaily = (series) => {
  if (!series.length) return `<p class="empty">No runs in window</p>`;
  const max = Math.max(...series.map((day) => day.cost_usd), 0.0001);
  return `<div class="daily">${series
    .map(
      (day) => `
      <div class="daily-col" title="${escapeHtml(day.day)}: ${costFmt(day.cost_usd)} (${day.run_count} run${day.run_count === 1 ? "" : "s"})">
        <span class="daily-fill" style="height:${Math.max(4, Math.round((day.cost_usd / max) * 100))}%"></span>
        <small>${escapeHtml(day.day === "undated" ? "n/a" : day.day.slice(5))}</small>
      </div>`,
    )
    .join("")}</div>`;
};

const renderFinops = (usage) => {
  const cacheTokens =
    Number(usage.cache_creation_input_tokens || 0) + Number(usage.cache_read_input_tokens || 0);
  const windows = ["", "30d", "7d"];
  return `
    <div class="panel-title">
      <h2>FinOps</h2>
      <div class="since-toggle">
        ${windows
          .map(
            (window) =>
              `<button class="${sinceWindow === window ? "active" : ""}" data-since="${window}">${window === "" ? "all" : window}</button>`,
          )
          .join("")}
      </div>
    </div>
    <div class="finops-top">
      <div class="finops-block">
        <span class="label">Token split</span>
        <div class="split">
          <span><em>in</em> ${fmtTokens(usage.input_tokens)}</span>
          <span><em>out</em> ${fmtTokens(usage.output_tokens)}</span>
          <span><em>cache</em> ${fmtTokens(cacheTokens)}</span>
        </div>
      </div>
      <div class="finops-block">
        <span class="label">Cost by day</span>
        ${renderDaily(usage.by_day || [])}
      </div>
    </div>
    <div class="finops-cols">
      <div><span class="label">By model</span><div class="table">${costTable(usage.by_model || [], "model")}</div></div>
      <div><span class="label">By executor</span><div class="table">${costTable(usage.by_executor || [], "executor")}</div></div>
      <div><span class="label">By funding</span><div class="table">${costTable(usage.by_funding || [], "funding")}</div></div>
    </div>
  `;
};

const renderRun = (run) => {
  const status = statusOf(run);
  const nodes = nodesOf(run);
  const segments = nodes.length
    ? nodes
        .map(
          (node) =>
            `<span class="segment ${node.status}" title="${escapeHtml(nodeIdOf(node))}: ${escapeHtml(node.status)}"></span>`,
        )
        .join("")
    : `<span class="segment idle"></span>`;
  const executor = run.manifest?.executor || run.status?.executor || "unknown";
  const topology = run.manifest?.topology || run.status?.topology || "unknown";
  return `
    <a class="run" href="/runs/${encodeURIComponent(run.run_id)}">
      <div class="run-head">
        <span class="run-id">${escapeHtml(run.run_id)}</span>
        ${renderPill(status)}
      </div>
      <div class="segments">${segments}</div>
      <div class="run-meta muted">
        <span>${escapeHtml(executor)} / ${escapeHtml(topology)}</span>
        <span>${nodes.length} node${nodes.length === 1 ? "" : "s"} / $${runCost(run).toFixed(4)}</span>
      </div>
    </a>
  `;
};

const overviewShell = () => {
  document.querySelector("main").innerHTML = `
    <section class="metrics" aria-label="Usage summary">
      <div class="metric"><span class="label">Runs</span><strong id="metric-runs">0</strong></div>
      <div class="metric"><span class="label">Generation Tokens</span><strong id="metric-generation">0</strong></div>
      <div class="metric"><span class="label">Context Tokens</span><strong id="metric-context">0</strong></div>
      <div class="metric"><span class="label">Cost</span><strong id="metric-cost">$0.0000</strong></div>
    </section>
    <section class="panel">
      <div class="panel-title"><h2>Active Runs</h2><span id="active-count">0 active</span></div>
      <div id="active-runs" class="run-list"></div>
    </section>
    <section class="panel">
      <div class="panel-title"><h2>Recent Runs</h2><span id="recent-count">0 runs</span></div>
      <div id="recent-runs" class="run-list"></div>
    </section>
    <section class="panel finops" id="finops"></section>
  `;
};

const loadOverview = async () => {
  overviewShell();
  const [runsResponse, usageResponse] = await Promise.all([
    fetch("/api/runs"),
    fetch(`/api/usage?since=${encodeURIComponent(sinceWindow)}`),
  ]);
  const runsData = await runsResponse.json();
  const usage = await usageResponse.json();
  const runs = runsData.runs || [];
  const active = runs.filter((run) => run.active);

  document.getElementById("metric-runs").textContent = usage.run_count || 0;
  document.getElementById("metric-generation").textContent = fmtTokens(usage.generation_tokens);
  document.getElementById("metric-context").textContent = fmtTokens(usage.context_tokens);
  document.getElementById("metric-cost").textContent = `$${Number(usage.cost_usd || 0).toFixed(4)}`;
  document.getElementById("active-count").textContent = `${active.length} active`;
  document.getElementById("recent-count").textContent = `${runs.length} runs`;
  document.getElementById("active-runs").innerHTML = active.length
    ? active.map(renderRun).join("")
    : `<p class="empty">No active runs</p>`;
  document.getElementById("recent-runs").innerHTML = runs.length
    ? runs.slice(0, 12).map(renderRun).join("")
    : `<p class="empty">No archived runs</p>`;
  document.getElementById("finops").innerHTML = renderFinops(usage);
  document.querySelectorAll("[data-since]").forEach((button) => {
    button.addEventListener("click", () => {
      sinceWindow = button.dataset.since;
      loadOverview();
    });
  });
};

const levelNodes = (nodes) => {
  const byId = new Map(nodes.map((node) => [nodeIdOf(node), node]));
  const depth = new Map();
  const compute = (node) => {
    const id = nodeIdOf(node);
    if (depth.has(id)) return depth.get(id);
    const deps = (node.depends_on || []).filter((dep) => byId.has(dep));
    const value = deps.length ? Math.max(...deps.map((dep) => compute(byId.get(dep)))) + 1 : 0;
    depth.set(id, value);
    return value;
  };
  nodes.forEach(compute);
  return nodes.map((node) => ({ ...node, depth: depth.get(nodeIdOf(node)) || 0 }));
};

const renderDag = (nodes) => {
  const leveled = levelNodes(nodes);
  const byDepth = new Map();
  leveled.forEach((node) => {
    const row = byDepth.get(node.depth) || [];
    row.push(node);
    byDepth.set(node.depth, row);
  });
  const nodeW = 170;
  const nodeH = 104;
  const colGap = 60;
  const rowGap = 42;
  const paddingX = 48;
  const paddingY = 48;
  const maxColumns = Math.max(1, ...Array.from(byDepth.values()).map((row) => row.length));
  const maxDepth = Math.max(0, ...leveled.map((node) => node.depth));
  const width = Math.max(360, paddingX * 2 + maxColumns * nodeW + (maxColumns - 1) * colGap);
  const height = Math.max(520, paddingY * 2 + (maxDepth + 1) * nodeH + maxDepth * rowGap);
  const positioned = leveled.map((node) => {
    const row = byDepth.get(node.depth) || [];
    const index = row.findIndex((candidate) => nodeIdOf(candidate) === nodeIdOf(node));
    const rowWidth = row.length * nodeW + (row.length - 1) * colGap;
    return {
      ...node,
      x: (width - rowWidth) / 2 + index * (nodeW + colGap),
      y: paddingY + node.depth * (nodeH + rowGap),
    };
  });
  const byId = new Map(positioned.map((node) => [nodeIdOf(node), node]));
  const edges = positioned.flatMap((node) =>
    (node.depends_on || [])
      .filter((dep) => byId.has(dep))
      .map((dep) => {
        const source = byId.get(dep);
        return {
          id: `${dep}->${nodeIdOf(node)}`,
          sx: source.x + nodeW / 2,
          sy: source.y + nodeH,
          tx: node.x + nodeW / 2,
          ty: node.y,
        };
      }),
  );
  return `
    <div class="dag" style="width:${width}px; min-height:${height}px">
      <svg class="dag-edges" viewBox="0 0 ${width} ${height}" aria-hidden="true">
        <defs>
          <marker id="dag-arrow" markerWidth="9" markerHeight="9" refX="7" refY="4.5" orient="auto" markerUnits="strokeWidth">
            <path d="M0,0 L9,4.5 L0,9 Z"></path>
          </marker>
        </defs>
        ${edges
          .map((edge) => {
            const midY = (edge.sy + edge.ty) / 2;
            return `<path class="dag-edge" d="M ${edge.sx} ${edge.sy} C ${edge.sx} ${midY}, ${edge.tx} ${midY}, ${edge.tx} ${edge.ty}" />`;
          })
          .join("")}
      </svg>
      ${positioned
        .map((node) => {
          const id = nodeIdOf(node);
          const selected = id === selectedNodeId ? "selected" : "";
          return `
            <button class="dag-node ${node.status} ${selected}" style="left:${node.x}px; top:${node.y}px" data-node="${escapeHtml(id)}">
              <span class="dag-title">${escapeHtml(id)}</span>
              <span class="dag-sub">${escapeHtml(node.role || node.phase || "")}</span>
              <div class="dag-badges">${nodeBadges(node)}</div>
              <div class="dag-foot"><span>${costFmt(node.cost_usd)}</span><span>${fmtTokens(node.context_tokens)} ctx</span></div>
            </button>
          `;
        })
        .join("")}
    </div>
  `;
};

const artifactList = async (runId, nodeId) => {
  const response = await fetch(`/api/runs/${encodeURIComponent(runId)}/nodes/${encodeURIComponent(nodeId)}/artifacts`);
  if (!response.ok) return [];
  return (await response.json()).artifacts || [];
};

const renderActivity = (events, nodeEvents) => {
  const all = [...(events || []), ...(nodeEvents || [])].slice(-200);
  if (!all.length) return `<p class="empty">No activity captured yet</p>`;
  return all
    .map((event) => {
      const type = event.type || event.raw?.type || "event";
      const payload = event.payload || event;
      return `
        <div class="activity-row">
          <span>${escapeHtml(event.ts || "")}</span>
          <strong>${escapeHtml(type)}</strong>
          <code>${escapeHtml(JSON.stringify(payload).slice(0, 500))}</code>
        </div>`;
    })
    .join("");
};

const renderArtifacts = (runId, nodeId, artifacts) => {
  if (!artifacts.length) return `<p class="empty">No artifacts for this node yet</p>`;
  return artifacts
    .map(
      (artifact) => `
        <button class="artifact" data-artifact="${escapeHtml(artifact.path)}" ${artifact.previewable ? "" : "disabled"}>
          <span>${escapeHtml(artifact.path)}</span>
          <small>${escapeHtml(artifact.kind)} / ${artifact.size} bytes</small>
        </button>`,
    )
    .join("");
};

const selectedNode = (nodes) => {
  if (!selectedNodeId && nodes.length) selectedNodeId = nodeIdOf(nodes[0]);
  return nodes.find((node) => nodeIdOf(node) === selectedNodeId) || nodes[0];
};

const loadRunDetail = async (runId) => {
  const response = await fetch(`/api/runs/${encodeURIComponent(runId)}`);
  const data = await response.json();
  const nodes = nodesOf(data);
  const node = selectedNode(nodes);
  const nodeId = node ? nodeIdOf(node) : "";
  const [nodeEventsResponse, outputResponse, artifacts] = nodeId
    ? await Promise.all([
        fetch(`/api/runs/${encodeURIComponent(runId)}/nodes/${encodeURIComponent(nodeId)}/events`),
        fetch(`/api/runs/${encodeURIComponent(runId)}/nodes/${encodeURIComponent(nodeId)}/output`),
        artifactList(runId, nodeId),
      ])
    : [null, null, []];
  const nodeEvents = nodeEventsResponse?.ok ? await nodeEventsResponse.json() : [];
  const output = outputResponse?.ok ? await outputResponse.text() : "";
  const runStatus = statusOf(data);
  const title = document.querySelector(".topbar h1");
  title.innerHTML = `<a href="/">Cadora</a> / ${escapeHtml(runId)}`;

  document.querySelector("main").innerHTML = `
    <section class="run-detail">
      <div class="run-hero panel">
        <div>
          <h2>${escapeHtml(runId)}</h2>
          <p>${escapeHtml(data.manifest?.executor || data.status?.executor || "unknown")} / ${escapeHtml(data.manifest?.topology || data.status?.topology || "unknown")}</p>
        </div>
        <div class="hero-metrics">
          ${renderPill(runStatus)}
          <span>$${runCost(data).toFixed(4)}</span>
          <span>${nodes.length} node${nodes.length === 1 ? "" : "s"}</span>
        </div>
      </div>
      <div class="run-workspace">
        <section class="panel canvas-panel">
          <div class="panel-title"><h2>DAG Progress</h2><span>click a node</span></div>
          ${renderDag(nodes)}
        </section>
        <aside class="panel node-panel">
          ${
            node
              ? `
            <div class="panel-title">
              <h2>${escapeHtml(nodeId)}</h2>
              ${renderPill(node.status || "idle")}
            </div>
            <div class="node-facts">
              <span>model <strong>${escapeHtml(node.model || "unknown")}</strong></span>
              <span>cost <strong>$${Number(node.cost_usd || 0).toFixed(4)}</strong></span>
              <span>context <strong>${fmtTokens(node.context_tokens)}</strong></span>
              <span>review <strong>${escapeHtml(node.review || "none")}</strong></span>
            </div>
            <div class="tabs">
              ${["activity", "output", "artifacts", "raw"].map((tab) => `<button class="${selectedTab === tab ? "active" : ""}" data-tab="${tab}">${tab}</button>`).join("")}
            </div>
            <div class="tab-body" id="tab-body">
              ${
                selectedTab === "activity"
                  ? renderActivity(data.events, nodeEvents)
                  : selectedTab === "output"
                    ? `<pre>${escapeHtml(output || "No output yet")}</pre>`
                    : selectedTab === "artifacts"
                      ? renderArtifacts(runId, nodeId, artifacts)
                      : `<pre>${escapeHtml(JSON.stringify(node, null, 2))}</pre>`
              }
            </div>`
              : `<p class="empty">No nodes in this run</p>`
          }
        </aside>
      </div>
    </section>
  `;

  document.querySelectorAll(".dag-node").forEach((button) => {
    button.addEventListener("click", () => {
      selectedNodeId = button.dataset.node;
      selectedTab = "activity";
      loadRunDetail(runId);
    });
  });
  document.querySelectorAll("[data-tab]").forEach((button) => {
    button.addEventListener("click", () => {
      selectedTab = button.dataset.tab;
      loadRunDetail(runId);
    });
  });
  document.querySelectorAll("[data-artifact]").forEach((button) => {
    button.addEventListener("click", async () => {
      const path = button.dataset.artifact;
      const artifactResponse = await fetch(
        `/api/runs/${encodeURIComponent(runId)}/nodes/${encodeURIComponent(nodeId)}/artifacts/${encodeURIComponent(path)}`,
      );
      const text = artifactResponse.ok ? await artifactResponse.text() : "Could not load artifact";
      document.getElementById("tab-body").innerHTML = `<pre>${escapeHtml(text)}</pre>`;
    });
  });
};

const load = () => {
  const runId = runIdFromPath();
  if (runId) return loadRunDetail(runId);
  selectedNodeId = null;
  document.querySelector(".topbar h1").textContent = "Cadora";
  return loadOverview();
};

document.getElementById("refresh").addEventListener("click", load);
load();
setInterval(load, 5000);
