# AGENTS.md — context-hooks

Agent handoff document. Any agent on any tool can pick up from here.

## Current Work
<!-- AGENT: This section is rewritten at every session wrap-up. It is the ground truth for current state. -->

**As of:** 2026-03-20 (session 8)

**Where we are:** v0.5 shipped. PreToolUse hook surfaces context BEFORE tool execution — file intel on Read/Edit/Write, failure-class knowledge before test runs. CLAUDE.md snippet auto-injected during install. 388 tests, 24 MCP tools, schema v3. Three projects in KADE cluster.

**Immediate next task:** Validate edit-time parity nudge fires proactively in KADE after IDE restart — edit humanizer/__init__.py and verify inline warning.

**Blocked / waiting:**
- Parity nudge validation blocked on IDE restart (user + KADE agent testing)

**Priority queue:**
1. Dogfood: validate PreToolUse parity nudge in KADE
2. Fix install.sh --no-bootstrap for non-interactive agents
3. Platform adapters: Gemini CLI, Cursor, VS Code
4. Task 13: Migration from v1 (import from old DB format + agent-bridge data)

---

## Project Overview

See `CLAUDE.md` for architecture, rules, and testing instructions.
