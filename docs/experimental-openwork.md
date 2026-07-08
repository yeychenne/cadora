# Experimental: drive Cadora from a desktop GUI (OpenWork)

> **Experimental.** OpenWork is young and moving fast; treat this as a config-only experiment, not a
> supported surface. Cadora's own local dashboard remains the run-inspection UI.

Cadora already exposes an MCP server (`cadora mcp`), so a desktop agent shell that is an MCP client
can drive runs conversationally. [OpenWork](https://github.com/different-ai/openwork) — a Tauri
desktop app over the OpenCode engine — is one such shell (an open alternative to a "cowork"-style
desktop assistant, comparable to Amazon Quick Desktop).

## Wire it in (config only)

OpenWork/OpenCode reads MCP servers from `opencode.json`. Add Cadora as a local stdio server:

```json
{
  "mcp": {
    "cadora": {
      "type": "local",
      "command": ["cadora", "mcp"],
      "enabled": true
    }
  }
}
```

The agent inside OpenWork can then call Cadora's MCP tools — `start_run`, `run_status`,
`review_gate`, `submit_review`, `get_artifact` — to launch a run, poll it, approve gates, and pull
artifacts from the desktop.

## What you get, and the gap

- **You get:** a desktop, chat-driven way to launch and steer Cadora runs — near-zero effort.
- **You don't get (yet):** Cadora's DAG view, gate-diff, evidence-pack, and cost dashboards. MCP
  tools return text/JSON, so keep `cadora dashboard` open alongside for the visual run inspection.

If a purpose-built native shell ever becomes worth it, the lower-risk path is a thin Tauri app that
wraps the Cadora CLI and embeds the existing localhost dashboard — keeping the DAG/gate/evidence UI
intact rather than rebuilding it inside a fast-moving third-party app.
