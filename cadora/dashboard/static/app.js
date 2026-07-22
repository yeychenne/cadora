let selectedNodeId = null;
let selectedTab = "activity";
let sinceWindow = "";
// While a review is pending on the open run, pause the auto-poll: re-rendering the panel under the
// reviewer would wipe an in-progress comment and race their click. The manual Refresh still works,
// and polling resumes once the decision advances the run.
let reviewPending = false;
let servedArchives = [];

const fmtTokens = (value) => {
  const n = Number(value || 0);
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(2)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}k`;
  return String(n);
};

// Timestamps: show a readable local HH:MM:SS, not the full ISO string with microseconds + tz.
const fmtTime = (ts) => {
  if (!ts) return "";
  const d = new Date(ts);
  return Number.isNaN(d.getTime())
    ? String(ts).slice(11, 19)
    : d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false });
};

// Durations: seconds under a minute, else "Xm Ys".
const fmtDuration = (seconds) => {
  if (seconds == null || Number.isNaN(Number(seconds))) return "—";
  const n = Number(seconds);
  if (n < 60) return `${n.toFixed(1)}s`;
  return `${Math.floor(n / 60)}m ${Math.round(n % 60)}s`;
};

const escapeHtml = (value) =>
  String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");

const previewKindFor = (path) =>
  ["md", "markdown"].includes(String(path).split(".").pop().toLowerCase()) ? "markdown" : "text";

// Minimal, XSS-safe markdown -> HTML: escape first, then emit only our own tags (no raw HTML from
// the artifact ever renders). Covers the common AI-DLC doc shapes: headings, bold, inline/fenced
// code, unordered lists, and http(s) links.
const mdInline = (s) =>
  s
    .replace(/`([^`]+)`/g, "<code>$1</code>")
    .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
    .replace(
      /\[([^\]]+)\]\((https?:\/\/[^)\s]+)\)/g,
      '<a href="$2" target="_blank" rel="noopener">$1</a>',
    );

// One pipe-table row -> its trimmed cells (drops the outer empty splits of "| a | b |").
const tableCells = (line) => {
  const t = line.trim().replace(/^\|/, "").replace(/\|$/, "");
  return t.split("|").map((c) => c.trim());
};
const isTableRow = (line) => /^\s*\|.*\|\s*$/.test(line);
const isTableSeparator = (line) => isTableRow(line) && /^[\s|:-]+$/.test(line.trim());

const renderMarkdown = (raw) => {
  const out = [];
  let inCode = false;
  let inList = false;
  let tableBuf = [];
  const closeList = () => {
    if (inList) {
      out.push("</ul>");
      inList = false;
    }
  };
  // AI-DLC docs are full of pipe tables (traceability matrices, decision tables) — they used to
  // fall through to <p> rows of raw "| … |" text. Buffer consecutive table rows; a separator as
  // the second row promotes the first to a header.
  const flushTable = () => {
    if (!tableBuf.length) return;
    const rows = [...tableBuf];
    tableBuf = [];
    if (rows.length === 1) {
      out.push(`<p class="md-p">${mdInline(rows[0])}</p>`); // a lone pipe line isn't a table
      return;
    }
    const hasHeader = isTableSeparator(rows[1]);
    const cells = (line, tag) =>
      `<tr>${tableCells(line).map((c) => `<${tag}>${mdInline(c)}</${tag}>`).join("")}</tr>`;
    out.push('<div class="md-table-wrap"><table class="md-table">');
    if (hasHeader) out.push(`<thead>${cells(rows[0], "th")}</thead>`);
    const body = rows.slice(hasHeader ? 2 : 0).filter((r) => !isTableSeparator(r));
    out.push(`<tbody>${body.map((r) => cells(r, "td")).join("")}</tbody>`);
    out.push("</table></div>");
  };
  for (const line of escapeHtml(raw).split("\n")) {
    if (line.trim().startsWith("```")) {
      if (inCode) out.push("</code></pre>");
      else {
        closeList();
        flushTable();
        out.push('<pre class="md-code"><code>');
      }
      inCode = !inCode;
      continue;
    }
    if (inCode) {
      out.push(line);
      continue;
    }
    if (isTableRow(line)) {
      closeList();
      tableBuf.push(line);
      continue;
    }
    flushTable();
    const heading = line.match(/^(#{1,6})\s+(.*)$/);
    if (heading) {
      closeList();
      const level = heading[1].length;
      out.push(`<h${level} class="md-h">${mdInline(heading[2])}</h${level}>`);
      continue;
    }
    const item = line.match(/^\s*[-*]\s+(.*)$/);
    if (item) {
      if (!inList) {
        out.push('<ul class="md-ul">');
        inList = true;
      }
      out.push(`<li>${mdInline(item[1])}</li>`);
      continue;
    }
    closeList();
    out.push(line.trim() === "" ? "" : `<p class="md-p">${mdInline(line)}</p>`);
  }
  if (inCode) out.push("</code></pre>");
  closeList();
  flushTable();
  return out.join("\n");
};

// ---- Full-screen document review with annotations -------------------------------------------
// The reviewer opens a gate document full screen, selects passages, attaches notes, and sends
// the collected annotations into the review comment box — where they drive a request_changes
// (same-stage revision) or a conversational Revise. Mounted on <body>, outside the re-rendered
// app container, so a rerender can never wipe an open review.
let docModal = null;
let docAnnotations = [];
let docModalPath = "";

const renderAnnotations = () => {
  const list = docModal.querySelector("#ann-list");
  list.innerHTML = docAnnotations.length
    ? docAnnotations
        .map(
          (a, i) => `
            <div class="ann-chip">
              <span class="ann-quote">“${escapeHtml(a.quote)}”</span>
              <span class="ann-note">${escapeHtml(a.note)}</span>
              <button class="ann-del" data-ann="${i}" title="remove">×</button>
            </div>`,
        )
        .join("")
    : '<span class="ann-empty">Select a passage in the document to attach a note.</span>';
  docModal.querySelector("#ann-apply").disabled = !docAnnotations.length;
  list.querySelectorAll(".ann-del").forEach((b) =>
    b.addEventListener("click", () => {
      docAnnotations.splice(Number(b.dataset.ann), 1);
      renderAnnotations();
    }),
  );
};

const ensureDocModal = () => {
  if (docModal) return docModal;
  docModal = document.createElement("div");
  docModal.className = "doc-modal";
  docModal.innerHTML = `
    <div class="doc-modal-card" role="dialog" aria-modal="true" aria-label="Document review">
      <div class="doc-modal-head">
        <strong id="doc-modal-path"></strong>
        <span class="doc-modal-hint">select text to annotate · Esc closes</span>
        <button id="doc-modal-close" title="close">✕</button>
      </div>
      <div class="doc-modal-body md-view" id="doc-modal-body"></div>
      <div class="ann-bar">
        <div class="ann-input-row" id="ann-input-row" hidden>
          <span class="ann-quote" id="ann-pending-quote"></span>
          <input id="ann-note-input" type="text" placeholder="Your note on this passage…">
          <button id="ann-save">Add note</button>
          <button id="ann-cancel">Cancel</button>
        </div>
        <div class="ann-list" id="ann-list"></div>
        <div class="ann-actions">
          <button id="ann-apply" disabled>Add annotations to review comments</button>
        </div>
      </div>
    </div>`;
  document.body.appendChild(docModal);

  const close = () => {
    docModal.classList.remove("open");
    document.removeEventListener("keydown", onKey);
  };
  const onKey = (e) => {
    if (e.key === "Escape") close();
  };
  docModal.addEventListener("keydown", onKey);
  docModal.querySelector("#doc-modal-close").addEventListener("click", close);
  docModal.addEventListener("click", (e) => {
    if (e.target === docModal) close(); // click on the backdrop
  });

  // Selection -> pending annotation: capture the selected text when the mouse settles.
  docModal.querySelector("#doc-modal-body").addEventListener("mouseup", () => {
    const sel = window.getSelection();
    const text = sel ? String(sel).trim().replace(/\s+/g, " ") : "";
    if (!text) return;
    const quote = text.length > 140 ? `${text.slice(0, 140)}…` : text;
    docModal.querySelector("#ann-pending-quote").textContent = `“${quote}”`;
    docModal.querySelector("#ann-input-row").hidden = false;
    docModal.querySelector("#ann-note-input").focus();
  });
  const savePending = () => {
    const note = docModal.querySelector("#ann-note-input").value.trim();
    const quoted = docModal.querySelector("#ann-pending-quote").textContent.replace(/^“|”$/g, "");
    if (!note || !quoted) return;
    docAnnotations.push({ quote: quoted, note });
    docModal.querySelector("#ann-note-input").value = "";
    docModal.querySelector("#ann-input-row").hidden = true;
    renderAnnotations();
  };
  docModal.querySelector("#ann-save").addEventListener("click", savePending);
  docModal.querySelector("#ann-note-input").addEventListener("keydown", (e) => {
    if (e.key === "Enter") savePending();
  });
  docModal.querySelector("#ann-cancel").addEventListener("click", () => {
    docModal.querySelector("#ann-input-row").hidden = true;
  });

  // Annotations -> the review comment box: one line per note, prefixed with the document path,
  // appended so the reviewer can still edit before deciding.
  docModal.querySelector("#ann-apply").addEventListener("click", () => {
    const comments = document.getElementById("review-comments");
    if (!comments) return;
    const lines = docAnnotations.map((a) => `${docModalPath}: “${a.quote}” — ${a.note}`);
    comments.value = [comments.value.trim(), ...lines].filter(Boolean).join("\n");
    docAnnotations = [];
    renderAnnotations();
    docModal.classList.remove("open");
    comments.scrollIntoView({ block: "center" });
    comments.focus();
  });
  return docModal;
};

const openDocModal = async (url, path) => {
  const modal = ensureDocModal();
  docModalPath = path;
  docAnnotations = [];
  modal.querySelector("#doc-modal-path").textContent = path;
  modal.querySelector("#ann-input-row").hidden = true;
  const body = modal.querySelector("#doc-modal-body");
  body.innerHTML = '<p class="empty">Loading…</p>';
  modal.classList.add("open");
  renderAnnotations();
  const response = await fetch(url);
  const text = response.ok ? await response.text() : "Could not load document";
  body.innerHTML =
    response.ok && previewKindFor(path) === "markdown"
      ? renderMarkdown(text)
      : `<pre>${escapeHtml(text)}</pre>`;
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") modal.classList.remove("open");
  });
};

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
    executor: node.executor,
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

// Prefer the server's normalized per-node costs: backends that report tokens but no dollars
// (Codex) are priced from the rate table at read time, flagged estimated — the same numbers
// usage/compare show. Falls back to raw manifest costs for older payload shapes.
const runCost = (run) => {
  const normalized = run.node_costs && Object.values(run.node_costs);
  if (normalized && normalized.length)
    return normalized.reduce((sum, c) => sum + Number(c.cost_usd || 0), 0);
  if (!run.manifest?.nodes) return 0;
  return run.manifest.nodes.reduce((sum, node) => sum + Number(node.cost_usd || 0), 0);
};

const runCostEstimated = (run) =>
  !!(run.node_costs && Object.values(run.node_costs).some((c) => c.estimated));

// Node-panel cost label from the normalized payload: "$0.4552 est." for a price-table figure,
// "12.5 credits" for Kiro, a plain dollar figure when backend-reported, "—" when unknown.
const nodeCostLabel = (run, node) => {
  const nc = run.node_costs && run.node_costs[nodeIdOf(node)];
  if (nc && nc.cost_usd != null)
    return `$${Number(nc.cost_usd).toFixed(4)}${nc.estimated ? " est." : ""}`;
  if (nc && nc.credits != null) return `${nc.credits} credits`;
  if (node.cost_usd != null) return `$${Number(node.cost_usd).toFixed(4)}`;
  return "—";
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

// An "active" run whose status file hasn't been touched for this long is probably a zombie —
// a SIGKILLed conductor leaves status "running" forever. 45 min sits above the 30-min default
// node timeout, so a healthy long generation can't trip it; a gate parked at a human review is
// exempt entirely (with --review-timeout 0 it legitimately writes nothing for hours).
const STALE_AFTER_SECONDS = 45 * 60;

const isStaleRun = (run) => {
  if (!run.active || run.status_age_seconds == null) return false;
  const waitingOnHuman = nodesOf(run).some((n) => n.status === "review_waiting");
  return !waitingOnHuman && run.status_age_seconds > STALE_AFTER_SECONDS;
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
  // With several archives on one dashboard, say which project a run belongs to.
  const archiveTag =
    servedArchives.length > 1 && run.archive
      ? `<span class="archive-tag" title="${escapeHtml(run.archive)}">${escapeHtml(run.archive.split("/").filter(Boolean).slice(-2).join("/"))}</span>`
      : "";
  return `
    <a class="run" href="/runs/${encodeURIComponent(run.run_id)}">
      <div class="run-head">
        <span class="run-id">${escapeHtml(run.run_id)}</span>
        ${archiveTag}
        ${
          isStaleRun(run)
            ? `<span class="pill stale" title="status.json untouched for ${fmtDuration(run.status_age_seconds)} — the conductor process may be dead (killed or crashed without finalizing)">stale?</span>`
            : ""
        }
        ${renderPill(status)}
      </div>
      <div class="segments">${segments}</div>
      <div class="run-meta muted">
        <span>${escapeHtml(executor)} / ${escapeHtml(topology)}</span>
        <span>${nodes.length} node${nodes.length === 1 ? "" : "s"} / $${runCost(run).toFixed(4)}${runCostEstimated(run) ? " est." : ""}</span>
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

  // Name the archives this dashboard serves — a reviewer must never wonder whether their
  // project's gates can even appear here (a claims-only dashboard once hid a citadel review).
  servedArchives = Array.isArray(runsData.archives) ? runsData.archives : [];
  const subtitle = document.querySelector(".topbar p");
  if (subtitle && Array.isArray(runsData.archives) && runsData.archives.length) {
    subtitle.textContent = `Serving ${runsData.archives.join(" · ")}`;
    subtitle.title = runsData.archives.join("\n");
  }

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
          <span>${escapeHtml(fmtTime(event.ts))}</span>
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

// --- Run input: the prompt(s) given at entry (+ vision if present) ---------
const renderRunInput = (input) => {
  if (!input || (!input.vision && !(input.roots || []).length)) return "";
  const vision = input.vision
    ? `<div class="input-vision"><span class="label">vision.md</span><pre>${escapeHtml(input.vision)}</pre></div>`
    : "";
  const roots = (input.roots || [])
    .map(
      (root) =>
        `<div class="root-prompt"><strong>${escapeHtml(root.node_id)}</strong><pre>${escapeHtml(root.prompt || "(no prompt)")}</pre></div>`,
    )
    .join("");
  return `
    <details class="panel run-input">
      <summary class="panel-title"><h2>Run input</h2><span>the prompt given at entry</span></summary>
      ${vision}
      ${roots ? `<div class="input-roots"><span class="label">Entry node prompt${(input.roots || []).length === 1 ? "" : "s"}</span>${roots}</div>` : ""}
    </details>`;
};

// --- Failure analysis: why a node failed (reason + gate detail + integrity) --
const nodeFailure = (node) => {
  if (String(node.status || "").toLowerCase() !== "failed") return "";
  const gate = node.gate;
  const gateDetail = gate && typeof gate === "object" && gate.passed === false ? gate.detail : null;
  const findings = (node.integrity && node.integrity.findings) || [];
  return `
    <div class="node-failure">
      <div class="fail-reason">✗ ${escapeHtml(node.error || "node failed")}</div>
      ${gateDetail ? `<div class="fail-block"><span class="label">gate ${escapeHtml(gateStatus(node) || "")} output</span><pre>${escapeHtml(String(gateDetail).slice(-2000))}</pre></div>` : ""}
      ${findings.length ? `<div class="fail-block"><span class="label">integrity findings</span><ul>${findings.map((finding) => `<li><strong>${escapeHtml(finding.rule || "")}</strong> ${escapeHtml(finding.detail || finding.path || "")}</li>`).join("")}</ul></div>` : ""}
    </div>`;
};

// --- HITL review: the interactive gate. Docs as links, a decision + comments back to the run. ---
const renderParkPanel = (park, runId) => {
  // Triage, not review: on a phone you decide gates you already understand — the full
  // reading/annotation experience stays on desktop. Decisions made here are stored in the
  // archive, bound to the documents' current SHA-256, and applied at `cadora resume`.
  if (!park || !park.pending || !park.pending.length) return "";
  const gates = park.pending
    .map((p) => {
      const docs = (p.documents || [])
        .map(
          (d) =>
            `<a href="/api/runs/${encodeURIComponent(runId)}/review/doc?path=${encodeURIComponent(d.path)}" target="_blank">${escapeHtml(d.path)}</a> <span class="park-kind">${escapeHtml(d.kind || "")}</span>`
        )
        .join("<br>");
      if (p.decided) {
        return `<div class="park-gate decided"><div class="park-head"><strong>${escapeHtml(p.node_id)}</strong><span class="park-done">✓ ${escapeHtml(p.decided)}${p.decided_by ? ` by ${escapeHtml(p.decided_by)}` : ""} — applies at resume</span></div></div>`;
      }
      return `
      <div class="park-gate" data-park-node="${escapeHtml(p.node_id)}">
        <div class="park-head"><strong>${escapeHtml(p.node_id)}</strong>
          <span>${p.cost_so_far != null ? `$${Number(p.cost_so_far).toFixed(4)} so far` : ""}</span></div>
        <div class="park-docs">${docs || '<span class="empty">no changed documents</span>'}</div>
        <textarea rows="2" class="park-comments" placeholder="Comments — required to request changes"></textarea>
        <div class="review-actions">
          <button class="review-btn approve" data-park-decision="approve">Approve</button>
          <button class="review-btn changes" data-park-decision="request_changes">Request changes</button>
          <button class="review-btn abort" data-park-decision="abort">Abort</button>
          <span class="review-msg park-msg"></span>
        </div>
      </div>`;
    })
    .join("");
  return `
    <div class="review-callout parked">
      <div class="review-head"><span class="review-flag">⏸ Parked</span><span>${park.pending.length} gate(s) await review — the run is not running; decisions apply at <code>cadora resume</code></span></div>
      <input id="park-reviewer" type="text" maxlength="80"
             placeholder="Your name — recorded in the evidence with this decision"
             value="${escapeHtml(localStorage.getItem("cadora-reviewer") || "")}" />
      ${gates}
    </div>`;
};

const wireParkPanel = (runId) => {
  document.querySelectorAll("[data-park-decision]").forEach((button) => {
    button.addEventListener("click", async () => {
      const gate = button.closest(".park-gate");
      const msg = gate.querySelector(".park-msg");
      const who = (document.getElementById("park-reviewer")?.value || "").trim();
      if (who) localStorage.setItem("cadora-reviewer", who);
      msg.textContent = "storing…";
      const response = await fetch(
        `/api/runs/${encodeURIComponent(runId)}/park/decision`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            node_id: gate.dataset.parkNode,
            decision: button.dataset.parkDecision,
            comments: gate.querySelector(".park-comments").value,
            reviewer: who,
          }),
        }
      );
      const result = await response.json().catch(() => ({}));
      if (result.stored) {
        msg.textContent = `✓ stored: ${result.stored} — applies at resume`;
        setTimeout(() => loadRunDetail(runId), 900);
      } else {
        msg.textContent = result.error || "failed to store the decision";
      }
    });
  });
};

const renderReviewPanel = (review) => {
  if (!review || !review.pending) return "";
  const docs = (review.documents || [])
    .map(
      (doc) => `
        <div class="review-doc">
          <a href="${escapeHtml(doc.url)}" target="_blank" rel="noopener">${escapeHtml(doc.path)}</a>
          <span class="doc-kind">${escapeHtml(doc.kind || "")}</span>
          <button class="doc-preview" data-doc="${escapeHtml(doc.url)}" data-path="${escapeHtml(doc.path)}">preview</button>
          <button class="doc-open" data-doc="${escapeHtml(doc.url)}" data-path="${escapeHtml(doc.path)}">full screen</button>
        </div>`,
    )
    .join("");
  const docOptions = (review.documents || [])
    .map((doc) => `<option value="${escapeHtml(doc.path)}">${escapeHtml(doc.path.split("/").pop())}</option>`)
    .join("");
  const convo = docOptions
    ? `
      <div class="review-convo">
        <div class="convo-row">
          <select id="convo-doc" aria-label="Document to ask about">${docOptions}</select>
          <input id="convo-input" type="text" placeholder="Ask about this document, or describe a revision…" aria-label="Message to the agent">
          <button class="convo-btn" data-msg="question">Ask ↩</button>
          <button class="convo-btn" data-msg="revision">Revise ↩</button>
        </div>
        <div class="review-reply md-view" id="review-reply"></div>
      </div>`
    : "";
  return `
    <div class="review-callout">
      <div class="review-head"><span class="review-flag">Review required</span><span><strong>${escapeHtml(review.node_id)}</strong> is waiting for your decision</span></div>
      <div class="review-docs">${docs || '<p class="empty">No changed documents surfaced</p>'}</div>
      <div class="review-preview md-view" id="review-preview"></div>
      ${convo}
      <textarea id="review-comments" rows="2" placeholder="Comments — required to request changes"></textarea>
      <input id="review-reviewer" type="text" maxlength="80"
             placeholder="Your name — recorded in the evidence with this decision"
             value="${escapeHtml(localStorage.getItem("cadora-reviewer") || "")}" />
      <div class="review-actions">
        <button class="review-btn approve" data-decision="approve">Approve</button>
        <button class="review-btn changes" data-decision="request_changes">Request changes</button>
        <button class="review-btn abort" data-decision="abort">Abort</button>
        <span class="review-msg" id="review-msg"></span>
      </div>
    </div>`;
};

const loadRunDetail = async (runId) => {
  const response = await fetch(`/api/runs/${encodeURIComponent(runId)}`);
  const data = await response.json();
  const inputResponse = await fetch(`/api/runs/${encodeURIComponent(runId)}/input`);
  const runInput = inputResponse.ok ? await inputResponse.json() : null;
  const reviewResponse = await fetch(`/api/runs/${encodeURIComponent(runId)}/review`);
  const review = reviewResponse.ok ? await reviewResponse.json() : null;
  // Pause auto-polling while ANY decision surface is open — the live review panel or a parked
  // gate awaiting triage. A rerender mid-thought wipes the reviewer's half-typed comment (the
  // exact bug #93 fixed for the live panel; the park panel earns the same protection).
  const parkUndecided = !!(data.park && (data.park.pending || []).some((p) => !p.decided));
  reviewPending = !!(review && review.pending) || parkUndecided;
  const nodes = nodesOf(data);
  const failedNode = nodes.find((n) => String(n.status || "").toLowerCase() === "failed");
  const runError =
    data.status?.error || data.manifest?.error || (failedNode ? failedNode.error : null);
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
      ${renderReviewPanel(review)}
      ${renderParkPanel(data.park, runId)}
      ${runError && (runStatus === "failed" || failedNode) ? `<div class="failure-banner">✗ <strong>${escapeHtml((failedNode && nodeIdOf(failedNode)) || "run")}</strong> — ${escapeHtml(runError)}</div>` : ""}
      ${renderRunInput(runInput)}
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
              <span>backend <strong>${escapeHtml(node.executor || "—")}</strong></span>
              <span>cost <strong>${nodeCostLabel(data, node)}</strong></span>
              ${node.credits != null ? `<span>credits <strong>${escapeHtml(node.credits)}</strong></span>` : ""}
              <span>duration <strong>${escapeHtml(fmtDuration(node.duration_seconds))}</strong></span>
              <span>context <strong>${fmtTokens(node.context_tokens)}</strong></span>
              <span>review <strong>${escapeHtml(node.review || "none")}</strong></span>
            </div>
            ${nodeFailure(node)}
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
      document.getElementById("tab-body").innerHTML =
        artifactResponse.ok && previewKindFor(path) === "markdown"
          ? `<div class="md-view">${renderMarkdown(text)}</div>`
          : `<pre>${escapeHtml(text)}</pre>`;
    });
  });
  document.querySelectorAll(".doc-preview").forEach((button) => {
    button.addEventListener("click", async () => {
      const docResponse = await fetch(button.dataset.doc);
      const text = docResponse.ok ? await docResponse.text() : "Could not load document";
      document.getElementById("review-preview").innerHTML =
        docResponse.ok && previewKindFor(button.dataset.path) === "markdown"
          ? renderMarkdown(text)
          : `<pre>${escapeHtml(text)}</pre>`;
    });
  });
  document.querySelectorAll(".doc-open").forEach((button) => {
    button.addEventListener("click", () => openDocModal(button.dataset.doc, button.dataset.path));
  });
  wireParkPanel(runId);
  document.querySelectorAll("[data-decision]").forEach((button) => {
    button.addEventListener("click", async () => {
      const msg = document.getElementById("review-msg");
      msg.textContent = "submitting…";
      const submitResponse = await fetch(`/api/runs/${encodeURIComponent(runId)}/review`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          decision: button.dataset.decision,
          comments: document.getElementById("review-comments").value,
          reviewer: (() => {
            const who = (document.getElementById("review-reviewer")?.value || "").trim();
            if (who) localStorage.setItem("cadora-reviewer", who);
            return who;
          })(),
        }),
      });
      const result = await submitResponse.json().catch(() => ({}));
      if (result.submitted) {
        msg.textContent = `submitted: ${result.submitted}`;
        setTimeout(() => loadRunDetail(runId), 900);
      } else {
        msg.textContent = result.error || "submission failed";
      }
    });
  });
  document.querySelectorAll(".convo-btn").forEach((button) => {
    button.addEventListener("click", async () => {
      const kind = button.dataset.msg;
      const message = document.getElementById("convo-input").value.trim();
      const path = document.getElementById("convo-doc").value;
      const reply = document.getElementById("review-reply");
      if (!message) {
        reply.textContent = "Type a question or a revision instruction first.";
        return;
      }
      reply.textContent = kind === "revision" ? "revising…" : "thinking…";
      const sent = await fetch(`/api/runs/${encodeURIComponent(runId)}/review/message`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ kind, message, path }),
      })
        .then((r) => r.json())
        .catch(() => ({}));
      if (!sent.sent) {
        reply.textContent = sent.error || "could not send";
        return;
      }
      // Poll for the run's reply — the parked node runs the executor to answer, then writes it.
      for (let i = 0; i < 120; i++) {
        const rep = await fetch(`/api/runs/${encodeURIComponent(runId)}/review/reply`)
          .then((r) => (r.ok ? r.json() : null))
          .catch(() => null);
        if (rep && rep.error) {
          reply.textContent = rep.error;
          return;
        }
        if (rep && rep.reply != null) {
          reply.innerHTML = renderMarkdown(rep.reply);
          if (kind === "revision") {
            reply.insertAdjacentHTML(
              "afterbegin",
              '<p class="convo-note">Revised in place — approve to keep it, or revise again.</p>',
            );
          }
          return;
        }
        await new Promise((r) => setTimeout(r, 1000));
      }
      reply.textContent = "no reply yet — the run may still be working";
    });
  });
};

const load = () => {
  const runId = runIdFromPath();
  if (runId) return loadRunDetail(runId);
  selectedNodeId = null;
  reviewPending = false;
  document.querySelector(".topbar h1").textContent = "Cadora";
  return loadOverview();
};

document.getElementById("refresh").addEventListener("click", load);
load();
setInterval(() => {
  if (!reviewPending) load();
}, 5000);
