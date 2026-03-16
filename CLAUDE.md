# context-hooks — Agent Instructions

**Project:** context-hooks — Agent Context Intelligence System
**Status:** v0.1.0, active development
**License:** MIT

## Quick Context

Platform-agnostic context intelligence for AI coding agents. Tracks session events, indexes git history with auto-discovered patterns, maintains a structured knowledge store, survives context compaction, provides health checks. Zero external dependencies (bash + sqlite3 + python3).

**Origin:** Built during a KADE2 session. Started as compaction survival, evolved when cross-referencing memory layers surfaced hidden patterns (parallel path bugs, undocumented conventions, stale knowledge).

## Architecture

```
bin/context-hooks          ← Bash CLI dispatcher (routes to Python modules)
lib/
├── db.py                  ← SQLite layer. ALL writes use parameterized queries (? placeholders).
├── config.py              ← YAML config loader (no PyYAML — custom parser)
├── events.py              ← F1: PostToolUse event extraction + storage
├── snapshot.py            ← F2: PreCompact snapshot + SessionStart(compact) recovery
├── commits.py             ← F3: Git commit indexing + backfill
├── tags.py                ← F4: Tag engine + profile generation (parallel paths)
├── knowledge.py           ← F5: Knowledge store + memos + maturity lifecycle
├── hooks.py               ← Central hook router (dispatches by hook type + source)
├── xref.py                ← F6: Cross-reference report across all memory layers
├── health.py              ← F7+F10: Session-start health check + auto-hygiene + prune
├── nudge.py               ← F8+F9: Parity warnings + flywheel enforcement (opt-in)
├── queries.py             ← Query commands (parity, search, tag, file, bugs, related, recent)
└── status.py              ← Status display
adapters/claude-code/      ← Claude Code hook shim + slash command
install.sh                 ← Platform-detecting installer
uninstall.sh               ← Clean removal
```

**Data location:** `~/.context-hooks/projects/<hash>/context.db` — one SQLite DB per project, hash derived from git root path.

## Critical Rules

1. **NEVER use shell string interpolation for SQL.** All DB writes go through `db.py` methods with `?` placeholders. This is a security invariant — commit messages and error output can contain SQL injection payloads.

2. **Zero external dependencies.** Only Python stdlib (sqlite3, json, hashlib, subprocess, os, re, sys, datetime, argparse, itertools, collections). No pip install, no requirements.txt. If you need YAML parsing, use `config._parse_simple_yaml()`.

3. **Every module must work when called from any directory.** The CLI dispatcher sets `PYTHONPATH` to the project root. Imports use `from lib.x import y`. Tests use `sys.path.insert(0, ...)`.

4. **Platform agnostic.** Core modules know nothing about Claude Code, Gemini, or Cursor. Platform-specific logic lives only in `adapters/` and `install.sh`.

5. **Passive by default, active opt-in.** Features that observe and report are always on. Features that inject warnings into the agent workflow (`nudge.py`) require explicit `nudge enable <name>`.

## Running Tests

```bash
python3 -m pytest tests/ -v          # all 146 tests
python3 -m pytest tests/test_db.py   # specific module
```

Tests use `tempfile.mkdtemp()` for isolated DBs. No external services needed.

## Testing Changes

Before committing:
```bash
python3 -m pytest tests/ -v           # must pass
bin/context-hooks help                 # CLI must work
bin/context-hooks status               # must not crash (run from any git repo)
```

## Key Design Decisions

- **Single SQLite DB per project** (not one DB per table, not a global DB). Keeps data isolated, easy to delete, easy to backup.
- **Full 40-char commit hashes** in DB (not short hashes). Short hash stored separately for display. Prevents collision in large repos.
- **Knowledge maturity lifecycle** (signal → pattern → decision → convention). Each stage has a higher evidence bar. Promotion is guarded — you can't skip stages.
- **`UNIQUE(title, status)` on knowledge** — allows superseded entry + new active entry with the same title to coexist.
- **FTS5 for knowledge search** — ships with SQLite 3.9.0+, no external deps.
- **Profile stored in `~/.context-hooks/`**, NOT in the project repo. Prevents accidentally committing parallel path analysis to a public repo.

## Spec

Full design spec at: (originally at KADE2 project)
- Design principles, schema, feature specs (F1-F10), hook input schemas, install flow, security model

## What's Not Done Yet (v0.2 candidates)

- **Task 13: Migration from v1** — Import from `~/.claude/context-events/*.db` and `data/agent-bridge/knowledge/`. Schema differs (v1 lacks `short_hash`, `author`, `maturity` columns).
- **Gemini/Cursor/VS Code adapters** — Only Claude Code adapter exists. Others need hook format translation.
- **Flywheel factory** — Guided flow for defining project-specific flywheels.
- **Team sync** — Share knowledge entries via git-committed export.
- **Commit quality scoring** — Analyze commit message quality during bootstrap.

## File Change Patterns

If you're modifying the **schema** (`db.py`), you likely also need to update:
- `commits.py` or `knowledge.py` (the inserters)
- `xref.py` or `health.py` (the readers)
- `tests/test_db.py`

If you're adding a **new CLI command**, update:
- `bin/context-hooks` (the dispatcher)
- The relevant `lib/*.py` module's `main()` function
- `README.md` command table
