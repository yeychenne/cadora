"""Cadora MCP interface seam — expose runs + HITL review to any MCP client.

The HITL review surface becomes pluggable (Claude Desktop, Codex/ChatGPT, or
the terminal), mirroring how ``NodeExecutor`` makes the coding-agent backend pluggable. See
``cadora/mcp/channel.py`` (interface-agnostic review channel), ``cadora/mcp/session.py`` (threaded
run driver), ``cadora/mcp/server.py`` (the MCP transport), and the usage guide ``docs/hitl-mcp.md``.
"""
