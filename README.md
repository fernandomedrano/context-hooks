# context-hooks

Agent context intelligence for AI coding tools. Tracks session activity, indexes git history with auto-discovered patterns, maintains a structured knowledge store, and survives context compaction.

## The Problem

AI coding agents lose their working state after ~30 minutes. Context compaction destroys what files were being edited, what patterns exist in the codebase, and what was decided in previous sessions. Every session starts from zero.

Existing solutions are either too aggressive (blocking tool calls, redirecting reads, imposing limits) or too manual (requiring you to maintain config files listing which files matter).

## What It Does

- **Compaction survival** -- snapshots session state before compaction, restores it after. The agent picks up where it left off without asking "what were we doing?"
- **Commit indexing with auto-tagging** -- every commit is indexed with conventional-commit type, BUG/ADR/issue refs, file-type categories, and structural tags from an auto-generated profile
- **Parallel path detection** -- discovers files that are usually edited together (e.g., `chat.py` and `chat_service.py`). Flags when you edit one without the other. This found real bugs: streaming/sync parity misses that caused production failures
- **Knowledge store** -- structured entries with a maturity lifecycle (signal -> pattern -> decision -> convention), full-text search, supersede chains, and cross-referencing
- **Cross-reference report** -- six-section analysis across MEMORY.md, knowledge store, and commit index. Finds stale rules, undocumented patterns, bug-fix knowledge gaps, emerging parallel paths, and memory layer overlap
- **Session-start health check** -- surfaces actionable items when a new session begins: missing failure-class docs, stale rules, unread memos
- **Active nudges (opt-in)** -- parity warnings on commit, flywheel enforcement (e.g., "this bug fix has no failure-class knowledge entry")

## Quick Start

```bash
git clone https://github.com/fernandomedrano/context-hooks
cd context-hooks
# TODO: install.sh coming soon

# For Claude Code, add to your project's .claude/settings.json:
# (adapter setup instructions will be in install.sh)
```

After installation, run bootstrap to index your git history:

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
| Gemini CLI | Beta | Adapter shim |
| Cursor | Beta | Adapter shim |
| Generic | Manual | CLI commands, no automatic hooks |

All platforms share the same core library. Platform adapters translate hook formats into the unified event schema.

## How It Works

```
Agent Tool Call
    |
    v
Platform Adapter (translates hook format)
    |
    v
Hook Router (lib/hooks.py)
    |
    v
Event Extraction --> SQLite (per-project)
    |                   |
    +-- Commit Index    +-- Knowledge Store
    |                   |
    +-- Tag Engine      +-- Memos
    |                   |
    +-- Snapshot        +-- Rule Validations
    |
    v
Analysis (xref, health, nudges)
    |
    v
additionalContext injection (agent reads it)
```

Each git repository gets its own SQLite database at `~/.context-hooks/projects/<hash>/context.db`. All writes use parameterized queries. Database files are created with `0o700` permissions on the parent directory.

## Commands

### Core

| Command | Description |
|---------|-------------|
| `context-hooks status` | Show project info, table counts, last event/commit |
| `context-hooks bootstrap` | Index last 30 days of git commits |
| `context-hooks profile` | Generate tag profile from git history (parallel paths, hot files, directory tags) |
| `context-hooks xref` | Run the 6-section cross-reference report |
| `context-hooks prune` | Hygiene report: stale knowledge, old memos, auto-signal candidates |
| `context-hooks prune --apply` | Same as above but actually clean up |

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

### Knowledge Store

| Command | Description |
|---------|-------------|
| `context-hooks knowledge store <category> <title> <content>` | Store a new entry |
| `context-hooks knowledge search <query>` | FTS5 search over knowledge |
| `context-hooks knowledge list [category]` | List active entries |
| `context-hooks knowledge promote <id>` | Advance maturity (signal -> pattern -> decision -> convention) |
| `context-hooks knowledge archive <id>` | Archive an entry |
| `context-hooks knowledge supersede <old_id> <cat> <title> <content>` | Replace with a new entry |

Categories: `architectural-decision`, `coding-convention`, `failure-class`, `reference`, `rejected-approach`

### Memos

| Command | Description |
|---------|-------------|
| `context-hooks knowledge memo send <from> <subject> <content>` | Send a cross-session memo |
| `context-hooks knowledge memo list [--unread]` | List memos |
| `context-hooks knowledge memo read <id>` | Read and mark as read |

### Nudges

| Command | Description |
|---------|-------------|
| `context-hooks nudge list` | Show available nudges and their state |
| `context-hooks nudge enable <name>` | Enable a nudge |
| `context-hooks nudge disable <name>` | Disable a nudge |

Available nudges: `parity`, `flywheel`, `health-summary`

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

## Configuration

All configuration is optional. The system works with zero config.

### Global Config

`~/.context-hooks/config.yaml`:

```yaml
# Enable active nudges (all off by default)
nudge.parity: true
nudge.flywheel: true
nudge.health-summary: true

# Flywheel rules (warn when a pattern commit has no matching knowledge)
flywheels:
  - bug-to-failure-class:BUG-\d+:bug_refs:failure-class
```

### Per-Project Overrides

`~/.context-hooks/projects/<hash>/config.yaml` -- same format, overrides global settings.

### Auto-Generated Profile

`~/.context-hooks/projects/<hash>/profile.yaml` -- created by `context-hooks profile`. Contains:

- **directory_tags**: top-level directories appearing in 5%+ of commits
- **hot_files**: files touched in 8+ commits
- **parallel_paths**: file pairs with 30%+ co-occurrence and solo edits on both sides

Regenerate periodically as your codebase evolves.

## Data Storage

```
~/.context-hooks/                    # 0o700 permissions
  config.yaml                        # global config (optional)
  projects/
    <sha256-hash>/                   # one per git repo
      context.db                     # SQLite (WAL mode)
      profile.yaml                   # auto-generated tag profile
      config.yaml                    # per-project config overrides
      snapshot.xml                   # last pre-compaction snapshot (0o600)
```

### What's Stored

- **events**: tool calls during sessions (file reads/edits, git ops, test runs, errors). FIFO-evicted at 500 per session.
- **commits**: indexed git commits with computed tags. Deduplicated by hash.
- **knowledge**: structured entries with maturity lifecycle and FTS5 search.
- **memos**: cross-session messages between agent instances.
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

## Dependencies

None beyond what ships with macOS and Linux:

- `bash`
- `python3` (3.10+)
- `sqlite3` (via Python's built-in `sqlite3` module)
- `git`

No pip install. No node_modules. No Docker. No external services.

## Testing

```bash
python3 -m pytest tests/ -q    # 146 tests, runs in <1 second
```

## Contributing

1. Fork the repository
2. Create a feature branch (`git checkout -b feat/my-feature`)
3. Write tests first, then implement
4. Run the full test suite: `python3 -m pytest tests/ -q`
5. Use conventional commit messages: `feat:`, `fix:`, `docs:`, `test:`
6. Open a pull request

### Architecture Notes

- `lib/` -- core library (no CLI concerns, no platform specifics)
- `adapters/` -- platform-specific hook shims (Claude Code, Gemini CLI, Cursor, generic)
- `bin/` -- CLI entry points
- `tests/` -- mirrors `lib/` structure, one test file per module

All modules are pure Python with zero external dependencies. Keep it that way.

## License

MIT -- see [LICENSE](LICENSE).
