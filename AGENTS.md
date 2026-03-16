# AGENTS.md — context-hooks

Agent handoff document. Any agent on any tool can pick up from here.

## Current Work
<!-- AGENT: This section is rewritten at every session wrap-up. It is the ground truth for current state. -->

**As of:** 2026-03-15

**Where we are:** v0.2 complete — MCP server + cross-project cluster routing (239 tests). Cluster model E2E verified: `cluster join` routes memos/knowledge to master DB, local data stays per-project. Schema auto-migration handles cross-project DB version drift.

**Immediate next task:** Knowledge durability — dual-write (SQLite for querying + markdown export in master repo for git-tracking). KADE2 design constraint: knowledge must survive catastrophic data loss.

**Blocked / waiting:**
- Nothing currently blocked

**Priority queue:**
1. Knowledge durability: dual-write (SQLite + git-tracked markdown)
2. End-to-end MCP server test with Claude Code
3. Task 13: Migration from v1 (import from old DB format + agent-bridge data)
4. Gemini/Cursor/VS Code adapters

---

## Project Overview

See `CLAUDE.md` for architecture, rules, and testing instructions.
