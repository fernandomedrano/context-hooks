# Cross-Project Routing — Design Spec

**Date:** 2026-03-15
**Status:** Draft
**Scope:** Cluster-based routing for memos and knowledge across related projects

---

## Problem

context-hooks stores data per-project in isolated SQLite DBs (`~/.context-hooks/projects/<hash>/context.db`). Related projects (e.g., KADE2, Orbit/HandNote, context-hooks) need to share memos and knowledge. Current workarounds — manual path resolution, raw SQL to foreign DBs — are fragile and hit schema drift issues.

## Model

### Clusters

A **cluster** is a named group of projects that share memos and knowledge through a single hub.

- One project is the **master** — its DB holds all shared data (memos, knowledge, shared_state)
- Other projects are **satellites** — they route shared operations to the master's DB
- The master is also a satellite of itself (no special code path)
- All inter-project communication goes through the master's DB (hub-and-spoke, no peer routing)
- Projects without a cluster config remain standalone (current behavior, zero change)

### Data split

| Data | Where it lives | Why |
|------|---------------|-----|
| Events | Local DB | Session-specific, high-volume, no cross-project value |
| Commits | Local DB | Per-repo by definition |
| Snapshots | Local DB | Recovery is per-session |
| Tags, rule_validations | Local DB | Derived from local commits |
| **Memos** | **Master DB** | Cross-agent communication hub |
| **Knowledge** | **Master DB** | Shared institutional memory |
| **Shared state** | **Master DB** | Cross-agent coordination |

### Configuration

Each project in a cluster has `~/.context-hooks/projects/<hash>/cluster.yaml`:

```yaml
cluster: kade-ecosystem
master: /Users/fernando/Dev/KADE2
```

Two fields. The master project gets the same file (master points to itself).

The `master` value is a **git root path** (not a data dir hash). `resolve_cluster_db()` feeds it through `data_dir()` to get the master's data directory.

Members are not listed — discovery is unnecessary since all routing is hub-and-spoke to the master.

**Path quoting:** Paths with spaces must be quoted in YAML (`master: "/Users/fernando/Dev/My Project"`). The existing `_parse_simple_yaml()` parser strips quotes correctly.

## Resolution logic

### Type contract

The codebase has two path types that must not be confused:

- **git_root**: The project's repository root (e.g., `/Users/fernando/Dev/KADE2`)
- **project_dir** (aka data_dir): The context-hooks data directory (e.g., `~/.context-hooks/projects/e85e0ed8026d/`)

`resolve_cluster_db()` takes a **project_dir** and returns a **project_dir**. The `master` field in cluster.yaml is a **git_root**, which is converted internally via `data_dir()`.

### Function

New function in `db.py`:

```python
def resolve_cluster_db(project_dir: str) -> str:
    """Return the cluster master's data dir, or project_dir if standalone.

    Args:
        project_dir: The local project's data directory (not git root).

    Returns:
        The master's data directory if clustered, or project_dir if standalone.
    """
    cluster_path = os.path.join(project_dir, "cluster.yaml")
    if os.path.exists(cluster_path):
        from lib.config import _parse_simple_yaml
        with open(cluster_path) as f:
            config = _parse_simple_yaml(f.read())
        master_root = config.get("master")
        if master_root and master_root.strip():
            master_dir = data_dir(master_root)
            # Validate master DB exists — catch typo'd paths before silently creating empty DBs
            master_db = os.path.join(master_dir, "context.db")
            if not os.path.exists(master_db):
                import sys
                print(f"WARNING: cluster master DB not found at {master_db}. "
                      f"Check master path in {cluster_path}. Falling back to standalone.",
                      file=sys.stderr)
                return project_dir
            return master_dir
        # Empty/missing master field — fall back to standalone
    return project_dir
```

### Caller pattern

Modules that need both local and shared data open two DB handles:

```python
local_db = ContextDB(project_dir)                      # events, commits, snapshots, tags
cluster_db = ContextDB(resolve_cluster_db(project_dir)) # memos, knowledge, shared_state
```

For standalone projects, both resolve to the same directory. In WAL mode, two connections to the same file in the same process are safe for concurrent reads and sequential writes. No `BEGIN EXCLUSIVE` transactions should be used on either handle.

## Module changes

### No changes needed

| Module | Reason |
|--------|--------|
| `events.py` | Writes events to local DB only |
| `commits.py` | Writes commits to local DB only |
| `snapshot.py` | Reads events from local DB only |
| `tags.py` | Reads commits from local DB only |
| `config.py` | `_parse_simple_yaml()` already handles this format |

### Changes needed

| Module | What changes |
|--------|-------------|
| `db.py` | Add `resolve_cluster_db()` function |
| `hooks.py` | Open both DBs; pass cluster_db to health check and knowledge reads |
| `knowledge.py` | `main()` CLI entry point resolves cluster_db; all knowledge + memo operations use it |
| `mcp_tools.py` | Add `cluster_dir` to context; split `_open_db()` into `_open_local_db()` + `_open_cluster_db()` |
| `health.py` | `health_summary()` and `prune()` both receive cluster_db for memo/knowledge ops, local_db for events/rule_validations |
| `xref.py` | `run_xref()` receives two DB handles: cluster_db for knowledge reads, local_db for commits and rule_validation writes |
| `nudge.py` | cluster_db for knowledge (flywheel check) |
| `queries.py` | cluster_db for knowledge searches, local_db for commit queries |
| `status.py` | Both DBs; display cluster info if configured |
| `bin/context-hooks` | Add `cluster` command dispatch to new `lib/cluster.py` |

### Entry points that open DBs

Several modules open a DB directly. Each must be updated to resolve the cluster:

- `hooks.py:handle()` — the central router. Currently opens `db = ContextDB(project_dir)` and passes it to event handlers, health checks, and snapshot logic. Must compute `cluster_dir = resolve_cluster_db(project_dir)` and open a second `cluster_db` handle. Passes `cluster_db` to `health_summary()` for memo/knowledge checks. Passes `local_db` to event storage and snapshot logic. This is the hot path (every PostToolUse event), so both handles should be opened lazily — only open `cluster_db` when the code path actually needs it (health check on SessionStart, not on every event).
- `knowledge.py:main()` — currently `db = ContextDB(get_data_dir(git_root))`. Must compute `cluster_dir = resolve_cluster_db(project_dir)` and use `ContextDB(cluster_dir)` for all knowledge + memo operations.
- `health.py:main()` — must open both local_db and cluster_db, pass cluster_db to `health_summary()` and `prune()` for memo/knowledge ops.
- `xref.py:main()` — must open both, pass cluster_db for knowledge reads and local_db for rule_validation writes.
- `queries.py:main()` — must resolve cluster for knowledge-related queries.
- `nudge.py:main()` — must resolve cluster for flywheel knowledge checks.
- `status.py:main()` — must open both for complete status display.

### MCP server

`build_handlers(ctx)` gets two context keys:

```python
ctx = {
    "project_dir": project_dir,        # local data dir
    "cluster_dir": cluster_dir,         # shared data dir (= project_dir if standalone)
    "git_root": git_root,
    "config": config,
}
```

Two helper functions replace the current single `_open_db()`:

```python
def _open_local_db(ctx):
    return ContextDB(ctx["project_dir"])

def _open_cluster_db(ctx):
    return ContextDB(ctx["cluster_dir"])
```

Handler routing:

| Handler category | DB | Handlers |
|-----------------|-----|----------|
| Knowledge (store, search, get, list, promote, archive, restore, supersede) | `_open_cluster_db` | All reads/writes to shared knowledge |
| Memos (send, check, read, reply, broadcast, list_threads) | `_open_cluster_db` | All memo ops; reply must read+write on same handle |
| Shared state (set, get) | `_open_cluster_db` | Cross-agent coordination |
| Task (handoff_task) | `_open_cluster_db` | Handoff is a structured memo |
| Commits (query_commits) | `_open_local_db` | Per-repo commits |
| Parity (check_parity) | `_open_local_db` | Per-repo analysis |
| Profile (get_profile) | `_open_local_db` | Per-repo tag profile |
| Xref (run_xref) | Both | cluster_db for knowledge, local_db for commits + rule_validations |
| Health (get_health) | Both | cluster_db for memos/knowledge, local_db for events |
| Project context (get_project_context) | Both | cluster_db for memos/knowledge, local_db for commits |

**Note on `context_reply_memo`:** The read of the original memo, the insert of the reply, and the update to `thread_id` must all happen on the same `_open_cluster_db()` handle within a single call.

## New module: `lib/cluster.py`

Implements the CLI commands. Follows existing pattern: `main(args)` function dispatched from `bin/context-hooks`.

### CLI commands

```bash
bin/context-hooks cluster join --master /path/to/master --name cluster-name
bin/context-hooks cluster show
bin/context-hooks cluster leave
```

### `cluster join`

1. Resolve the current project's data dir via `data_dir(resolve_git_root(cwd))`
2. Validate master path is a git repository root: run `git -C <path> rev-parse --show-toplevel` and verify it succeeds and matches the given path. Reject non-git directories with a clear error.
3. Write `cluster.yaml` to the data dir
4. Print: "Joined cluster '<name>'. Memos and knowledge now route to master at <path>."
5. Print: "Existing local memos/knowledge in this project's DB will be ignored (not deleted)."

### `cluster show`

1. Read `cluster.yaml` from current project's data dir
2. Print cluster name, master path, and whether this project IS the master
3. If no cluster.yaml: print "Not in a cluster (standalone mode)."

### `cluster leave`

1. Check if `cluster.yaml` exists in current project's data dir
2. If not: print "Not in a cluster." and exit (no error)
3. Remove `cluster.yaml`
4. Print: "Left cluster. Memos and knowledge now use local DB. Data in master DB is unaffected."

## Cluster adoption — no auto-migration

When a satellite joins a cluster, its local memos/knowledge are **not** migrated to the master. Clean break.

Rationale:
- Satellite DBs are typically empty or near-empty
- The master already has the canonical data
- Merge logic would add complexity for no practical gain
- Local data is not deleted — it's just ignored

If needed in the future, a manual `cluster migrate-local` command could copy local data into the master with provenance tags. Not built now.

## Schema compatibility

Handled by existing infrastructure: `ContextDB.__init__` runs `_migrate()` on open. When a satellite opens the master's DB, auto-migration upgrades the schema if the satellite has newer code than the master's last opener. This is safe because migrations are additive (ALTER TABLE ADD COLUMN, CREATE TABLE IF NOT EXISTS) and idempotent.

## Knowledge durability (out of scope)

The KADE2 design constraint — knowledge must be git-tracked or cloud-backed — is a master-side concern handled by a separate dual-write feature. This spec only ensures knowledge routes to the master DB. What the master does with its knowledge (export to markdown, git-track) is designed separately.

## Security notes

- `cluster.yaml` lives in `~/.context-hooks/projects/<hash>/`, not in the repo — cluster membership is machine-local
- Opening another project's DB requires filesystem access to `~/.context-hooks/` (same user)
- The master path in `cluster.yaml` is a local filesystem path — no network, no remote access
- All DB writes still use parameterized queries (security invariant from CLAUDE.md rule #1)

## Testing strategy

### Unit tests

- `resolve_cluster_db()`: standalone (no cluster.yaml), with valid cluster.yaml, empty master field (fallback to standalone), missing cluster.yaml file, typo'd master path with no existing DB (fallback to standalone with warning)
- `cluster.py`: join writes cluster.yaml, show reads it, leave removes it, leave when not in cluster is a no-op

### Integration tests

- Satellite writes memo via `send_memo()` → memo appears in master DB, not in satellite's local DB
- Satellite reads knowledge via `search()` → returns entries from master DB
- Satellite `prune()` → deletes old memos from master DB, does not touch local events
- `run_xref()` on satellite → reads knowledge from master DB, writes rule_validations to local DB

### Regression tests

- Standalone project: all operations route to local DB (both handles resolve to same dir)
- Existing test suite (215 tests) must pass unchanged when no cluster.yaml exists

### MCP tests

- Handlers route to correct DB based on `cluster_dir` in context
- `context_reply_memo` reads+writes on cluster DB
- `context_get_project_context` reads memos/knowledge from cluster DB, commits from local DB
- `context_run_xref` passes both DBs correctly

### Prune regression

- `prune()` with cluster: deletes old memos from cluster_db, archives stale knowledge in cluster_db, does NOT accidentally modify local_db events or commits
