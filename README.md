# context-hooks

Agent context intelligence for AI coding tools. Two modules, one install:

- **Git Intelligence** -- Tracks session activity, indexes git history with auto-discovered patterns, detects parallel paths, surfaces proactive nudges on file edits, survives context compaction
- **Agent Messaging** -- Asynchronous memos between agent instances, cross-project routing, shared state, threaded conversations

Use both together or either one independently. Zero dependencies. Pure Python. Works with any AI coding agent.

## The Problem

AI coding agents lose their working state after ~30 minutes. Context compaction destroys what files were being edited, what patterns exist in the codebase, and what was decided in previous sessions. Every session starts from zero.

Meanwhile, agents working on related projects can't talk to each other. Knowledge discovered in one session dies there. Bug patterns repeat because there's no institutional memory.

## What It Does

```
                     ┌─────────────────────────────────────────┐
                     │            context-hooks                 │
                     │                                         │
                     │  ┌─ Git Intelligence ─────────────────┐ │
  Agent tool call ──>│  │ Events → Commits → Tags → Profile  │ │
                     │  │ Parallel Paths, Edit Nudges         │ │
                     │  │ Compaction Survival, Health Checks  │ │
                     │  └─────────────────────────────────────┘ │
                     │                                         │
                     │  ┌─ Agent Messaging ──────────────────┐ │
                     │  │ Memos, Threads, Shared State       │ │
                     │  │ Cross-Project Routing (Clusters)   │ │
                     │  └─────────────────────────────────────┘ │
                     │                                         │
                     │  ┌─ Shared ───────────────────────────┐ │
                     │  │ Knowledge Store (+ Markdown Export) │ │
                     │  │ MCP Server (24 tools)              │ │
                     │  │ Cross-Reference Report              │ │
                     │  └─────────────────────────────────────┘ │
                     └─────────────────────────────────────────┘
```

### Git Intelligence

- **Compaction survival** -- Snapshots session state before compaction, restores it after. The agent picks up where it left off without asking "what were we doing?"
- **Commit indexing with auto-tagging** -- Every commit indexed with conventional-commit type, BUG/ADR/issue refs, file-type categories, and structural tags from an auto-generated profile
- **Parallel path detection** -- Discovers files usually edited together (e.g., `chat.py` and `chat_service.py`). Flags when you edit one without the other
- **Proactive edit nudges** -- When you edit a file, surfaces parity companions, bug history, and relevant knowledge entries in real time
- **Active nudges (opt-in)** -- Parity warnings on commit, flywheel enforcement
- **Tool output indexing** -- Large outputs (test results, file reads, grep results) are chunked and FTS5-indexed. The agent gets a tiny summary now and can search the full content on demand. Progressive throttling forces precise queries
- **Cross-reference report** -- Six-section analysis across MEMORY.md, knowledge store, and commit index

### Agent Messaging

- **Cross-session memos** -- Messages between agent instances across sessions. Agent A leaves a warning about an incomplete migration; Agent B sees it on next startup
- **Threaded conversations** -- Reply chains with auto-generated thread IDs
- **Shared state** -- Key-value store for inter-agent coordination (task handoff, deploy status, feature flags)
- **Cross-project routing** -- Cluster model routes memos and knowledge through a master database. Multiple repos, one conversation

### Shared

- **Knowledge store with durability** -- Structured entries with maturity lifecycle (signal -> pattern -> decision -> convention), full-text search, supersede chains. Optionally dual-writes to git-tracked markdown
- **MCP server** -- 24 tools exposed via Model Context Protocol. Drop-in replacement for agent-bridge with `--compat` mode

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
| MCP | Full | 24 tools via stdio JSON-RPC 2.0 |
| Generic | Manual | CLI commands, no automatic hooks |

All platforms share the same core library. Platform adapters translate hook formats into the unified event schema.

## Workflows

### Git Intelligence

#### Bug Fix Flywheel

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

# 4. Next time someone touches queue.py, the edit nudge surfaces this entry
```

#### Proactive Edit Nudges

When you edit a file, context-hooks checks it against known patterns and surfaces relevant context before you move on. This catches parity misses, reminds you of bug history, and surfaces relevant knowledge -- all at edit time, not commit time.

```bash
# You edit src/pipeline.py. The system immediately returns:
#   "Parity: pipeline.py is usually edited with chat_service.py
#    (72% co-occurrence). Verify companion was updated."
#   "Bug history: 3 bug-fix commits touched pipeline.py
#    (BUG-041, BUG-038). Extra care advised."

# Knowledge entries that reference edited files are surfaced too:
#   "Knowledge: "streaming responses must flush before return"
#    (coding-convention) references this file."

# Nudges are deduped per session -- you'll only see each one once.
# No configuration needed for parity, bug history, and knowledge nudges.

# Optional nudges (enable if you want them):
context-hooks nudge enable edit-hotfile     # warns on high-churn files
context-hooks nudge enable edit-convention  # surfaces coding conventions
```

**Always-on** (high confidence, no config needed):
- Parity pairs with 60%+ co-occurrence from your git history
- Files with 2+ bug-fix commits
- Files referenced by active knowledge entries

**Opt-in** (enable via `nudge enable`):
- Hot file warnings (high churn from profile)
- Coding convention reminders (from knowledge store)

#### Parallel Path Detection

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

#### Compaction Survival

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

#### Tool Output Indexing

Large tool outputs eat the context window. context-hooks intercepts them, indexes the full content in FTS5, and returns a tiny summary. The agent can search on demand — nothing is lost, but context stays small.

```bash
# The agent runs a test suite that produces 47KB of output.
# Instead of all 47KB going into context, the agent sees:
#   "Output indexed: Bash:pytest tests/ -v (47KB -> 12 chunks).
#    Search with: context-hooks search-output <query>"

# Later, the agent needs to find the failing test:
context-hooks search-output "FAILED"
# → --- Bash:pytest tests/ -v (chunk 8) ---
# → FAILED tests/test_auth.py::test_login_expired_token
# → AssertionError: expected 401 got 200

# List all indexed sources this session:
context-hooks search-output --sources
# → Bash:pytest tests/ -v (12 chunks, 47283 bytes)
# → Read:big_file.py (3 chunks, 15420 bytes)
# → Grep:TODO (2 chunks, 8192 bytes)
```

**What gets indexed:** Bash output, Read file contents, Grep/Glob results — anything over 4KB.

**Progressive throttling:** First 3 searches return up to 5 results. Calls 4-8 return 1 result each. After 8 searches, the agent is blocked — forcing better queries instead of brute-force scanning.

**Ephemeral:** Output chunks are session-scoped. They're cleaned up on next session start — this is working memory, not institutional knowledge.

### Agent Messaging

#### Cross-Session Memo Exchange

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

#### Cross-Project Routing

Multiple projects can share a single knowledge base and memo bus through the cluster model.

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

### Shared

#### Knowledge Durability

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

**Why this matters:**
- `git log data/knowledge/` shows the evolution of your team's institutional memory
- `git blame` on a knowledge file shows when decisions were made
- Other tools (grep, editors, scripts) can read knowledge without context-hooks
- If your SQLite DB is lost, knowledge entries survive in git history

#### MCP Server (Tool Access for Any Agent)

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

24 tools available: knowledge CRUD, memo operations, commit queries, health checks, cross-reference reports, and more.

## Commands

### General

| Command | Description |
|---------|-------------|
| `context-hooks status` | Show project info, table counts, last event/commit |
| `context-hooks health` | Session-start health check (both modules) |
| `context-hooks prune` | Hygiene report: stale knowledge, old memos |
| `context-hooks prune --apply` | Same as above but actually clean up |
| `context-hooks mcp` | Start MCP server (24 tools, all modules) |

### Git Intelligence

| Command | Description |
|---------|-------------|
| `context-hooks bootstrap` | Index last 30 days of git commits |
| `context-hooks profile` | Generate tag profile (parallel paths, hot files, directory tags) |
| `context-hooks xref` | Run the 6-section cross-reference report |
| `context-hooks query recent [N]` | Last N indexed commits (default 20) |
| `context-hooks query search <term>` | Full-text search over commits |
| `context-hooks query tag <tag>` | Find commits by tag |
| `context-hooks query file <path>` | Find commits touching a file |
| `context-hooks query bugs` | List bug-fix commits |
| `context-hooks query related <hash>` | Find commits touching the same files |
| `context-hooks query parity` | Show parallel path alerts (solo edits) |
| `context-hooks query stats` | Tag distribution across all commits |
| `context-hooks nudge list` | Show available nudges and their state |
| `context-hooks nudge enable <name>` | Enable a nudge |
| `context-hooks nudge disable <name>` | Disable a nudge |

| `context-hooks search-output <query>` | Search indexed tool outputs (FTS5) |
| `context-hooks search-output --sources` | List all indexed output sources |

Nudges: `parity`, `flywheel`, `health-summary`, `edit-hotfile`, `edit-convention`

### Agent Messaging

| Command | Description |
|---------|-------------|
| `context-hooks memo send --from X --subject Y --content Z` | Send a memo |
| `context-hooks memo send --from X --subject Y --content -` | Read content from stdin |
| `context-hooks memo list [--unread]` | List memos |
| `context-hooks memo read <id>` | Read and mark as read |
| `context-hooks cluster join --master /path --name <name>` | Join a cluster |
| `context-hooks cluster show` | Show cluster status |
| `context-hooks cluster leave` | Leave cluster, return to standalone |

Cross-project: add `--project /path/to/repo` to send to another project's database.

### Knowledge Store (shared by both modules)

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

**Git Intelligence:**
- **events** -- tool calls during sessions (file reads/edits, git ops, test runs, errors). FIFO-evicted at 500 per session.
- **commits** -- indexed git commits with computed tags. Deduplicated by hash.
- **output_chunks** -- FTS5-indexed tool outputs (>4KB). Session-scoped, ephemeral. FIFO-evicted at 200 chunks.
- **rule_validations** -- tracks which MEMORY.md rules have recent commit evidence.

**Agent Messaging:**
- **memos** -- cross-session messages between agent instances. Threaded conversations.
- **shared_state** -- key-value pairs for inter-agent coordination.

**Shared:**
- **knowledge** -- structured entries with maturity lifecycle and FTS5 search.

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
python3 -m pytest tests/ -q    # 357 tests, runs in ~3 seconds
```

## Architecture

```
bin/context-hooks          ← Bash CLI dispatcher

lib/                       ── Shared ──
├── db.py                  ← SQLite layer, schema, migrations
├── config.py              ← YAML config loader (no PyYAML)
├── hooks.py               ← Central hook router (dispatches to both modules)
├── knowledge.py           ← Knowledge store + maturity lifecycle
├── export.py              ← Knowledge durability (dual-write markdown)
├── mcp.py                 ← JSON-RPC 2.0 MCP protocol shim
├── mcp_tools.py           ← 23 MCP tool handlers + schemas
├── health.py              ← Health check + auto-hygiene (both modules)
├── status.py              ← Status display
│
│                          ── Git Intelligence ──
├── events.py              ← PostToolUse event extraction + storage
├── snapshot.py            ← PreCompact snapshot + recovery
├── commits.py             ← Git commit indexing + backfill
├── tags.py                ← Tag engine + profile generation
├── nudge.py               ← Commit-time parity warnings + flywheel enforcement
├── edit_nudge.py          ← Edit-time proactive nudges (parity, bugs, knowledge)
├── output_store.py        ← Tool output indexing + FTS5 search + throttling
├── queries.py             ← Query commands (search, parity, stats)
├── xref.py                ← Cross-reference report
│
│                          ── Agent Messaging ──
└── cluster.py             ← Cross-project routing (join/show/leave)
                             (memo operations live in knowledge.py)

adapters/claude-code/      ← Claude Code hook shim
tests/                     ← One test file per module (357 tests)
```

The two modules share one SQLite database and one MCP server but have no code dependencies on each other. Git intelligence works without messaging; messaging works without git indexing. Knowledge store is shared -- git intelligence writes failure-class entries and references; messaging routes them across projects.

## Contributing

1. Fork the repository
2. Create a feature branch (`git checkout -b feat/my-feature`)
3. Write tests first, then implement
4. Run the full test suite: `python3 -m pytest tests/ -q`
5. Use conventional commit messages: `feat:`, `fix:`, `docs:`, `test:`
6. Open a pull request

## License

MIT -- see [LICENSE](LICENSE).
