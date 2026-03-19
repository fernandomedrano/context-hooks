# AGENTS.md — context-hooks

Agent handoff document. Any agent on any tool can pick up from here.

## Current Work
<!-- AGENT: This section is rewritten at every session wrap-up. It is the ground truth for current state. -->

**As of:** 2026-03-18 (session 7)

**Where we are:** v0.4+ shipped. Memo polling live — agents get unread memos injected automatically every 10 tool calls. HandNote onboarded (191 commits, cluster joined, agent-bridge migrated). Three projects in KADE cluster. Cross-project flywheel validated end-to-end. 376 tests, 24 MCP tools, schema v3.

**Immediate next task:** Validate edit-time proactive nudge in KADE — edit a file with a known parity companion and verify inline warning fires.

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
