# context-hooks

Agent context intelligence for AI coding tools. Tracks session activity, indexes git history with auto-discovered patterns, maintains a durable knowledge store, and survives context compaction.

**Zero dependencies. Pure Python. Works with any AI coding agent.**

## The Problem

AI coding agents lose their working state after ~30 minutes. Context compaction destroys what files were being edited, what patterns exist in the codebase, and what was decided in previous sessions. Every session starts from zero.

Existing solutions are either too aggressive (blocking tool calls, redirecting reads, imposing limits) or too manual (requiring you to maintain config files listing which files matter).

## What It Does

```
                     ┌─────────────────────────────────────────┐
                     │            context-hooks                 │
                     ├─────────────────────────────────────────┤
  Agent tool call ──>│  Hook Router ──> Event Extraction        │
                     │       │              │                   │
                     │       v              v                   │
                     │  ┌──────────┐  ┌───────────┐            │
                     │  │ Commit   │  │ Knowledge │──> Markdown │
                     │  │ Index    │  │ Store     │   (durable) │
                     │  └──────────┘  └───────────┘            │
                     │       │              │                   │
                     │       v              v                   │
                     │  Tag Engine    Cross-Session Memos       │
                     │  Parallel Paths   MCP Server             │
                     │       │                                  │
                     │       v                                  │
                     │  Analysis: xref, health, nudges          │
                     └─────────────────────────────────────────┘
```

### Core Features

- **Compaction survival** -- Snapshots session state before compaction, restores it after. The agent picks up where it left off without asking "what were we doing?"
- **Commit indexing with auto-tagging** -- Every commit indexed with conventional-commit type, BUG/ADR/issue refs, file-type categories, and structural tags from an auto-generated profile
- **Parallel path detection** -- Discovers files usually edited together (e.g., `chat.py` and `chat_service.py`). Flags when you edit one without the other. Found real bugs: streaming/sync parity misses that caused production failures
- **Knowledge store with durability** -- Structured entries with maturity lifecycle, full-text search, supersede chains. Optionally dual-writes to git-tracked markdown for disaster recovery
- **Cross-project routing** -- Cluster model lets multiple projects share knowledge and memos through a master database
- **MCP server** -- 23 tools exposed via Model Context Protocol. Drop-in replacement for agent-bridge with `--compat` mode
- **Cross-session memos** -- Messages between agent instances across sessions and projects
- **Cross-reference report** -- Six-section analysis across MEMORY.md, knowledge store, and commit index
- **Active nudges (opt-in)** -- Parity warnings on commit, flywheel enforcement

## Quick Start

```bash
git clone https://github.com/fernandomedrano/context-hooks
cd context-hooks
bash install.sh --adapter=claude-code  # or: gemini, cursor, generic
```

Bootstrap your git history and verify:

```bash
context-hooks bootstrap    # indexes last 30 days of commits
context-hooks profile      # generates tag profile from git history
context-hooks status       # verify everything is working
```

That's it. The system starts observing immediately. Patterns emerge from your commit history -- no configuration needed.

## Platform Support

| Platform | Status | Hook Integration |
|----------|--------|------------------|
| Claude Code | Full | Native hooks API (PostToolUse, PreCompact, SessionStart) |
| Gemini CLI | Planned | Adapter shim |
| Cursor | Planned | Adapter shim |
| MCP | Full | 23 tools via stdio JSON-RPC 2.0 |
| Generic | Manual | CLI commands, no automatic hooks |

All platforms share the same core library. Platform adapters translate hook formats into the unified event schema.

## Workflows

### Workflow 1: Bug Fix Flywheel

When you fix a bug, the system can enforce that you also document it as institutional knowledge -- preventing the team from re-learning the same lessons.

```bash
# 1. Fix the bug and commit
git commit -m "fix: resolve race condition in message queue — BUG-042"

# 2. The flywheel nudge fires (if enabled):
#    "BUG-042 has no failure-class knowledge entry. Consider documenting."

# 3. Store the knowledge
context-hooks knowledge store failure-class \
    "Race condition in message queue" \
    "Consumer reads before producer commits. Fix: added read-after-write fence." \
    --bug-refs BUG-042 \
    --file-refs "queue.py, consumer.py" \
    --tags "concurrency, messaging"

# 4. Next time someone touches queue.py, the health check surfaces this entry
```

### Workflow 2: Cross-Project Knowledge Sharing

Multiple projects can share a single knowledge base through the cluster model.

```bash
# On the satellite project, join the master
context-hooks cluster join --master /path/to/main-project --name satellite-1

# Now knowledge and memos route to the master's database
context-hooks knowledge store reference \
    "API rate limit is 100 req/min" \
    "Discovered during load test. Affects all services."

# The master project sees it immediately
cd /path/to/main-project
context-hooks knowledge search "rate limit"
# → [1] API rate limit is 100 req/min (decision) — reference

# Send memos between projects
context-hooks memo send --from agent-1 --subject "Deploy blocked" \
    --content "Waiting on rate limit increase before deploying batch service"
```

### Workflow 3: Knowledge Durability

Knowledge entries can be dual-written to SQLite (for querying) and git-tracked markdown (for durability). Your institutional memory survives database corruption, machine migration, or accidental deletion.

```bash
# Enable export for this project
# Add to ~/.context-hooks/projects/<hash>/config.yaml:
#   knowledge_export: true

# Every knowledge mutation now writes both SQLite and markdown:
context-hooks knowledge store failure-class \
    "Context correction ignored" \
    "Correction phrases contain hand tokens that fire hero_hand_present." \
    --bug-refs BUG-073 \
    --file-refs "fact_graph.py, policy_table.yaml"

# Check the exported files:
ls data/knowledge/failure-class/
# → context-correction-ignored.md

cat data/knowledge/index.md
# → ## failure-class (1 active)
# → - [Context correction ignored](failure-class/context-correction-ignored.md) — ...

# Bulk re-export existing entries after enabling:
context-hooks knowledge export
# → Exported 14 entries to data/knowledge/

# Preview without writing:
context-hooks knowledge export --dry-run
```

The exported markdown files use YAML frontmatter for metadata and concise content:

```markdown
---
id: 7
category: failure-class
maturity: decision
status: active
title: Context correction ignored
file_refs: fact_graph.py, policy_table.yaml
bug_refs: BUG-073
tags: routing, extraction
created: 2026-03-08T02:16:27
updated: 2026-03-10T14:30:00
---

Correction phrases contain hand tokens that fire hero_hand_present
before the LLM can classify as correction. Fix: added is_context_correction.
```

**Why this matters:**
- `git log data/knowledge/` shows the evolution of your team's institutional memory
- `git blame` on a knowledge file shows when decisions were made
- Other tools (grep, editors, scripts) can read knowledge without context-hooks
- If your SQLite DB is lost, knowledge entries survive in git history

### Workflow 4: Cross-Session Memo Exchange

Agents in different sessions can communicate asynchronously through memos.

```bash
# Agent A sends a memo at end of session
context-hooks memo send --from agent-a --subject "Migration incomplete" \
    --content "Schema v3 migration is half done. Table users_v3 exists but FK constraints not added yet. Do NOT run the app until constraints are in place."

# Agent B checks on next session start
context-hooks memo list --unread
# → [1] Migration incomplete (from: agent-a) [unread]

context-hooks memo read 1
# → From: agent-a
# → Subject: Migration incomplete
# → Content: Schema v3 migration is half done...

# Send memos to other projects
context-hooks memo send --from agent-b --subject "Ready to deploy" \
    --content "All tests passing, migration complete." \
    --project /path/to/other-repo
```

### Workflow 5: Parallel Path Detection

The system discovers which files are usually edited together and warns when you miss one.

```bash
# After bootstrapping, generate the profile
context-hooks profile

# The profile discovers parallel paths from your git history:
#   chat.py <-> chat_service.py (72% co-occurrence, 3 solo edits)
#   api/routes.py <-> api/middleware.py (45% co-occurrence, 5 solo edits)

# When you edit chat.py without chat_service.py, the parity nudge fires:
#   "⚠ chat.py edited without chat_service.py (parallel path, 72% co-occurrence)"

# Check parity status manually
context-hooks query parity
```

### Workflow 6: Compaction Survival

When your AI agent's context window fills up and compacts, context-hooks preserves and restores state.

```bash
# Before compaction (automatic via PreCompact hook):
# → Snapshot saved: 12 events, 3 file edits, current task context

# After compaction (automatic via SessionStart hook):
# → Snapshot restored from 2 minutes ago
# → Files being edited: lib/knowledge.py, tests/test_knowledge.py
# → Last action: running pytest (3 failures)
# → The agent continues exactly where it left off
```

### Workflow 7: MCP Server (Tool Access for Any Agent)

Expose context-hooks as an MCP server for any MCP-speaking agent.

```bash
# Start the MCP server
context-hooks mcp

# With agent-bridge compatibility (drop-in replacement)
context-hooks mcp --compat=agent-bridge

# Add to .mcp.json for automatic startup:
{
  "mcpServers": {
    "context-hooks": {
      "command": "/path/to/context-hooks",
      "args": ["mcp"]
    }
  }
}
```

23 tools available: knowledge CRUD, memo operations, commit queries, health checks, cross-reference reports, and more.

## Commands

### Core

| Command | Description |
|---------|-------------|
| `context-hooks status` | Show project info, table counts, last event/commit |
| `context-hooks bootstrap` | Index last 30 days of git commits |
| `context-hooks profile` | Generate tag profile (parallel paths, hot files, directory tags) |
| `context-hooks xref` | Run the 6-section cross-reference report |
| `context-hooks health` | Session-start health check |
| `context-hooks prune` | Hygiene report: stale knowledge, old memos |
| `context-hooks prune --apply` | Same as above but actually clean up |

### Knowledge Store

| Command | Description |
|---------|-------------|
| `context-hooks knowledge store <cat> <title> <content>` | Store a new entry |
| `context-hooks knowledge search <query>` | FTS5 full-text search |
| `context-hooks knowledge list [category]` | List active entries |
| `context-hooks knowledge promote <id>` | Advance maturity (signal -> pattern -> decision -> convention) |
| `context-hooks knowledge archive <id>` | Archive an entry |
| `context-hooks knowledge restore <id>` | Restore an archived entry |
| `context-hooks knowledge dismiss <id>` | Dismiss an entry permanently |
| `context-hooks knowledge supersede <old_id> <cat> <title> <content>` | Replace with a new entry |
| `context-hooks knowledge export` | Re-export all entries to markdown |
| `context-hooks knowledge export --dry-run` | Preview what would be exported |

Categories: `architectural-decision`, `coding-convention`, `failure-class`, `reference`, `rejected-approach`

### Memos

| Command | Description |
|---------|-------------|
| `context-hooks memo send --from X --subject Y --content Z` | Send a memo |
| `context-hooks memo send --from X --subject Y --content -` | Read content from stdin |
| `context-hooks memo list [--unread]` | List memos |
| `context-hooks memo read <id>` | Read and mark as read |

Cross-project: add `--project /path/to/repo` to send to another project's database.

### Queries

| Command | Description |
|---------|-------------|
| `context-hooks query recent [N]` | Last N indexed commits (default 20) |
| `context-hooks query search <term>` | Full-text search over commits |
| `context-hooks query tag <tag>` | Find commits by tag |
| `context-hooks query file <path>` | Find commits touching a file |
| `context-hooks query bugs` | List bug-fix commits |
| `context-hooks query related <hash>` | Find commits touching the same files |
| `context-hooks query parity` | Show parallel path alerts (solo edits) |
| `context-hooks query stats` | Tag distribution across all commits |

### Cluster

| Command | Description |
|---------|-------------|
| `context-hooks cluster join --master /path --name <name>` | Join a cluster |
| `context-hooks cluster show` | Show cluster status |
| `context-hooks cluster leave` | Leave cluster, return to standalone |

### Nudges

| Command | Description |
|---------|-------------|
| `context-hooks nudge list` | Show available nudges and their state |
| `context-hooks nudge enable <name>` | Enable a nudge |
| `context-hooks nudge disable <name>` | Disable a nudge |

Available: `parity`, `flywheel`, `health-summary`

## Configuration

All configuration is optional. The system works with zero config.

### Global Config

`~/.context-hooks/config.yaml`:

```yaml
# Active nudges (all off by default)
nudge.parity: true
nudge.flywheel: true
nudge.health-summary: true

# Flywheel rules (warn when a pattern commit has no matching knowledge)
flywheels:
  - bug-to-failure-class:BUG-\d+:bug_refs:failure-class
```

### Per-Project Config

`~/.context-hooks/projects/<hash>/config.yaml` -- same format, overrides global:

```yaml
# Knowledge durability (dual-write to markdown)
knowledge_export: true
knowledge_export_dir: docs/knowledge    # default: data/knowledge

# Nudge overrides for this project
nudge.parity: true
```

### Auto-Generated Profile

`~/.context-hooks/projects/<hash>/profile.yaml` -- created by `context-hooks profile`:

- **directory_tags**: top-level directories appearing in 5%+ of commits
- **hot_files**: files touched in 8+ commits
- **parallel_paths**: file pairs with 30%+ co-occurrence and solo edits on both sides

Regenerate periodically as your codebase evolves.

## Git Commit Quality

The system works at two tiers depending on your commit discipline.

### Without Conventional Commits (works immediately)

- File-type tags: `tests`, `docs`, `migration`, `infra`, `ci`
- BUG-NNN, ADR-NNN, and #NNN references extracted from any commit message
- Parallel path detection from co-occurrence analysis
- Full-text search over all commit messages and file lists

### With Conventional Commits (unlocks more)

Using prefixes like `fix:`, `feat:`, `refactor:`, `docs:`, `test:`, `chore:` enables:

- Commit-type tags for filtering and statistics
- Scoped tags from `feat(router):` style messages
- Better flywheel enforcement (e.g., every `fix:` referencing BUG-NNN should have a failure-class knowledge entry)

## Data Storage

```
~/.context-hooks/                    # 0o700 permissions
  config.yaml                        # global config (optional)
  projects/
    <sha256-hash>/                   # one per git repo
      context.db                     # SQLite (WAL mode)
      profile.yaml                   # auto-generated tag profile
      config.yaml                    # per-project config overrides
      cluster.yaml                   # cluster membership (if joined)
      snapshot.xml                   # last pre-compaction snapshot (0o600)

<your-repo>/data/knowledge/          # when knowledge_export: true
  index.md                           # auto-generated knowledge index
  failure-class/                     # one directory per category
    context-correction-ignored.md
  coding-convention/
    always-use-parameterized-queries.md
```

### What's Stored

- **events**: tool calls during sessions (file reads/edits, git ops, test runs, errors). FIFO-evicted at 500 per session.
- **commits**: indexed git commits with computed tags. Deduplicated by hash.
- **knowledge**: structured entries with maturity lifecycle and FTS5 search.
- **memos**: cross-session messages between agent instances.
- **shared_state**: key-value pairs for inter-agent coordination.
- **rule_validations**: tracks which MEMORY.md rules have recent commit evidence.

### What's NOT Stored

- File contents
- Conversation transcripts
- API keys or credentials
- Anything outside the git repository

## Security

- All SQL uses parameterized queries (no string interpolation)
- Database directory permissions: `0o700`
- Snapshot file permissions: `0o600`
- No network calls -- everything is local SQLite
- No secrets are read or stored
- Schema auto-migration on DB open (v1 -> v2 handled transparently)

## Dependencies

None beyond what ships with macOS and Linux:

- `bash`
- `python3` (3.10+)
- `sqlite3` (via Python's built-in `sqlite3` module)
- `git`

No pip install. No node_modules. No Docker. No external services.

## Testing

```bash
python3 -m pytest tests/ -q    # 279 tests, runs in ~2 seconds
```

## Architecture

```
bin/context-hooks          ← Bash CLI dispatcher
lib/
├── db.py                  ← SQLite layer, schema, migrations
├── config.py              ← YAML config loader (no PyYAML)
├── events.py              ← PostToolUse event extraction + storage
├── snapshot.py            ← PreCompact snapshot + recovery
├── commits.py             ← Git commit indexing + backfill
├── tags.py                ← Tag engine + profile generation
├── knowledge.py           ← Knowledge store + memos + maturity lifecycle
├── export.py              ← Knowledge durability (dual-write markdown)
├── hooks.py               ← Central hook router
├── xref.py                ← Cross-reference report
├── health.py              ← Health check + auto-hygiene
├── nudge.py               ← Parity warnings + flywheel enforcement
├── queries.py             ← Query commands
├── status.py              ← Status display
├── cluster.py             ← Cross-project routing (join/show/leave)
├── mcp.py                 ← JSON-RPC 2.0 MCP protocol shim
└── mcp_tools.py           ← 23 MCP tool handlers + schemas
adapters/claude-code/      ← Claude Code hook shim
tests/                     ← One test file per module (279 tests)
```

All modules are pure Python with zero external dependencies. Keep it that way.

## Contributing

1. Fork the repository
2. Create a feature branch (`git checkout -b feat/my-feature`)
3. Write tests first, then implement
4. Run the full test suite: `python3 -m pytest tests/ -q`
5. Use conventional commit messages: `feat:`, `fix:`, `docs:`, `test:`
6. Open a pull request

## License

MIT -- see [LICENSE](LICENSE).
