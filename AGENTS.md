# AGENTS.md — context-hooks

Agent handoff document. Any agent on any tool can pick up from here.

## Current Work
<!-- AGENT: This section is rewritten at every session wrap-up. It is the ground truth for current state. -->

**As of:** 2026-03-15

**Where we are:** v0.2 MCP server adapter is complete and tested (200 tests, 23 tools + 14 compat aliases). All core features (F1-F10) plus MCP server are implemented. Ready for real-world end-to-end testing.

**Immediate next task:** Configure `.mcp.json` and test the MCP server end-to-end with Claude Code after IDE restart.

**Blocked / waiting:**
- Agent-bridge memo to KADE2 — needs MCP connection from a session with agent-bridge configured

**Priority queue:**
1. End-to-end MCP server test with Claude Code
2. Task 13: Migration from v1 (import from old DB format + agent-bridge data)
3. Gemini/Cursor/VS Code adapters

---

## Project Overview

See `CLAUDE.md` for architecture, rules, and testing instructions.
