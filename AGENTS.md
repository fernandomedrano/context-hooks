# AGENTS.md — context-hooks

Agent handoff document. Any agent on any tool can pick up from here.

## Current Work
<!-- AGENT: This section is rewritten at every session wrap-up. It is the ground truth for current state. -->

**As of:** 2026-03-15

**Where we are:** v0.2 MCP server + dogfooding fixes complete (215 tests). Schema auto-migration and CLI flag syntax for cross-project memo send shipped. Cross-agent memo exchange proven between KADE2 and context-hooks.

**Immediate next task:** Design cross-project memo routing layer — formalize address discovery, replace manual path resolution.

**Blocked / waiting:**
- Nothing currently blocked

**Priority queue:**
1. Cross-project memo routing design
2. Knowledge durability: dual-write (SQLite + git-tracked markdown)
3. End-to-end MCP server test with Claude Code
4. Task 13: Migration from v1

---

## Project Overview

See `CLAUDE.md` for architecture, rules, and testing instructions.
