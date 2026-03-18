# AGENTS.md — context-hooks

Agent handoff document. Any agent on any tool can pick up from here.

## Current Work
<!-- AGENT: This section is rewritten at every session wrap-up. It is the ground truth for current state. -->

**As of:** 2026-03-18

**Where we are:** v0.4 shipped — proactive intelligence is live. Edit nudges, file intel on Read, test briefing, tool output indexing with progressive throttling, session briefing on startup. Hooks installed globally via install.sh (v0.1 shell scripts migrated to shim). KADE dogfooding validated parity detection on first indexed commit. 371 tests, 24 MCP tools, schema v3.

**Immediate next task:** Validate edit-time proactive nudge in KADE — edit a file with a known parity companion and verify the inline warning fires without `query parity`.

**Blocked / waiting:**
- Nothing currently blocked

**Priority queue:**
1. Dogfood: validate edit-time nudge fires proactively in KADE
2. Platform adapters: Gemini CLI, Cursor, VS Code — hook format translation
3. Task 13: Migration from v1 (import from old DB format + agent-bridge data)
4. E2E MCP server test with Claude Code via .mcp.json

---

## Project Overview

See `CLAUDE.md` for architecture, rules, and testing instructions.
