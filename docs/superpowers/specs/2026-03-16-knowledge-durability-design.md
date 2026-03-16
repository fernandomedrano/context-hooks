# Knowledge Durability — Dual-Write Design

**Date:** 2026-03-16
**Status:** Draft
**Scope:** v0.3 — Knowledge persistence beyond SQLite

## Summary

Knowledge entries must survive catastrophic data loss (DB corruption, accidental deletion, machine migration). The dual-write approach writes each knowledge mutation to both SQLite (for querying) and a git-tracked markdown directory in the master project's repo (for durability). Memos remain SQLite-only — they're ephemeral operational chatter.

## Design Constraints

These come from KADE2 memos and the project's design history:

1. **Knowledge is durable, memos are ephemeral.** Only knowledge gets dual-write.
2. **Markdown export = concise index with file_refs, not content dumps.** Knowledge entries are short pointers (1-2 sentence summary + file path + grep key), not long-form documents.
3. **Export lives in the master project's repo** (`data/knowledge/` by default). In a cluster, satellites route through the master — the master's repo is the single source of truth.
4. **Zero external dependencies.** No pip, no third-party YAML/markdown libraries.
5. **Idempotent.** Re-exporting produces identical files. No timestamp churn in git diffs.

## Decisions

1. **Export location:** `<git-root>/data/knowledge/` in the master project. Configurable via `config.yaml` key `knowledge_export_dir`.
2. **File naming:** `<category>/<slug>.md` where slug is the title kebab-cased. Example: `failure-class/context-correction-ignored.md`. Superseded entries with the same title as their replacement get `-superseded-<id>` appended to avoid overwriting.
3. **Export trigger:** Synchronous on every write mutation (store, promote, archive, restore, dismiss, supersede). Not a separate sync command.
4. **Index file:** `data/knowledge/index.md` — flat list of all active entries with one-line summaries. Regenerated on each mutation.
5. **Standalone projects** (no cluster): export to their own repo's `data/knowledge/`.
6. **Import direction:** SQLite is authoritative. Markdown is a read-only export. No markdown→SQLite import path in v0.3 (avoids merge conflicts and dual-source-of-truth problems).
7. **Opt-in:** Export is off by default. Enabled via `config.yaml` key `knowledge_export: true` or `--export` flag on CLI. This respects the "passive by default" principle.

## Architecture

```
knowledge.store() / promote() / archive() / supersede() / ...
        │
        ├──▶ db.insert_knowledge() / db.execute(UPDATE...)   ← SQLite (query path)
        │
        └──▶ export.write_entry(db, entry_id, export_dir)    ← Markdown (durability path)
                │
                ├── data/knowledge/<category>/<slug>.md       ← Individual entry file
                └── data/knowledge/index.md                   ← Regenerated index
```

### New module: `lib/export.py`

Single module, ~150 lines. Four public functions:

- `write_entry(db, entry_id, export_dir)` — Write/update one entry's markdown file. Called after every mutation. Fetches the entry from DB to get current state. Writes to the slug-derived path; does NOT handle renames (caller's responsibility for supersede collision).
- `remove_entry(category, slug, export_dir)` — Delete a file when entry is dismissed. Called from `dismiss()`.
- `write_index(db, export_dir)` — Regenerate `index.md` from all active entries. Called after every mutation.
- `export_all(db, export_dir)` — Bulk re-export all active + archived entries and regenerate index.

### Markdown File Format

```markdown
---
id: 7
category: failure-class
maturity: decision
status: active
title: Context correction ignored
file_refs: fact_graph.py, deterministic_extract_impl.py
commit_refs: 3e3aa9f
bug_refs: BUG-073
tags: routing, extraction
created: 2026-03-08T02:16:27
updated: 2026-03-10T14:30:00
---

Context correction phrases ("you already told me") contain hand tokens that fire
hero_hand_present before the LLM can classify as correction. Fix: added
is_context_correction fact to routing layer.
```

Design notes on the format:
- **YAML frontmatter** is written by `export.py` using simple string formatting (key: value lines between `---` fences). Reading frontmatter back (for future import) requires a fence-stripping step: split on `---`, pass the middle section to `config._parse_simple_yaml()`, and treat everything after the second fence as the body. `_parse_simple_yaml()` does NOT understand `---` fences — callers must strip them first.
- **Body is the `content` field** — expected to be 1-3 sentences per the pointer design. Not the full reasoning.
- **`reasoning` field omitted from export.** It's operational context for the agent, not institutional knowledge. Keeps the markdown files concise and git-diff-friendly.
- **`id` is included** for cross-reference but is NOT authoritative — SQLite assigns IDs.
- **Timestamps use ISO 8601 without timezone** — matches the SQLite storage format. No timezone churn in diffs.
- **Non-ASCII handling:** `_slugify()` strips non-ASCII characters. Titles with Unicode (e.g., accented characters) will produce lossy slugs. This is acceptable for v0.3 — all current knowledge titles are ASCII. Document as a known limitation.

### Index File Format

```markdown
# Knowledge Index

*Auto-generated. Do not edit — regenerated on each knowledge mutation.*

## failure-class (3 active)

- [Context correction ignored](failure-class/context-correction-ignored.md) — routing, extraction
- [Venue tendency hallucination](failure-class/venue-tendency-hallucination.md) — humanizer
- [Memory context bleed](failure-class/memory-context-bleed.md) — memory

## coding-convention (2 active)

- [Always use parameterized queries](coding-convention/always-use-parameterized-queries.md) — security, db
```

Grouped by category, sorted alphabetically within each group. Tags shown inline for scannability. Only **active** entries appear in the index — archived and superseded entries retain their individual files but are excluded from the index.

## Mutation Flow

Each mutation function in `knowledge.py` calls `_safe_export()` after the SQLite write. Export failures log a warning but never block the SQLite path.

### store()
1. `db.insert_knowledge(...)` — SQLite write
2. Fetch `new_id` from DB (existing pattern — `SELECT id WHERE title = ? AND status = 'active'`)
3. `export.write_entry(db, new_id, export_dir)` — write `<category>/<slug>.md`
4. `export.write_index(db, export_dir)` — regenerate index

### promote() / restore()
1. `db.execute(UPDATE...)` — SQLite update
2. `export.write_entry(db, entry_id, export_dir)` — update frontmatter (maturity/status change)
3. `export.write_index(db, export_dir)` — regenerate index

### archive()
1. `db.execute(UPDATE...)` — SQLite update (status → archived)
2. `export.write_entry(db, entry_id, export_dir)` — update frontmatter (status: archived). **File is retained**, not deleted. Archived entries may be restored later.
3. `export.write_index(db, export_dir)` — regenerate index (archived entries excluded)

### dismiss()
1. Fetch `category`, `title` from DB (needed for slug computation and file path)
2. `db.execute(UPDATE...)` — SQLite update (status → dismissed)
3. `export.remove_entry(category, _slugify(title), export_dir)` — **delete file**
4. `export.write_index(db, export_dir)` — regenerate index

### supersede()
1. Fetch old entry's `category`, `title` from DB (for slug computation)
2. Mark old entry superseded in SQLite
3. Insert new entry in SQLite
4. If old and new titles produce the same slug, rename old file to `<slug>-superseded-<old_id>.md` via `os.rename()` in the `supersede()` caller (not inside `write_entry`). This rename happens BEFORE step 5.
5. `export.write_entry(db, old_id, export_dir)` — update old file at its (possibly renamed) path (status: superseded, superseded_by: new_id)
6. `export.write_entry(db, new_id, export_dir)` — write new file at `<slug>.md`
7. `export.write_index(db, export_dir)` — regenerate index

**Note on supersede and field inheritance:** `supersede()` does NOT automatically inherit `bug_refs`, `file_refs`, `commit_refs`, or `tags` from the old entry. The new entry starts fresh — the caller must explicitly pass these fields if they want continuity. This is intentional: supersede means the old knowledge is being replaced, potentially with different file references.

**Required MCP change:** The `context_supersede_knowledge` tool handler in `mcp_tools.py` and its `TOOL_SCHEMAS` entry must be updated to accept optional `bug_refs`, `file_refs`, `commit_refs`, and `tags` parameters, and pass them through to `supersede()`. Currently only `old_id`, `category`, `title`, `content`, and `reasoning` are exposed.

## Cluster Integration

In a clustered setup:
- Knowledge lives in the master's SQLite DB (existing behavior via `resolve_cluster_db()`)
- The export directory is resolved from the **master's git root**, not the satellite's
- Satellites writing knowledge automatically export to the master project's repo

The master project owner commits and pushes the knowledge directory.

## Resolving the Export Directory

The `git_root` is passed explicitly through the call chain (Option B). It's already resolved in `knowledge.main()` and available in `mcp_tools.py`'s `ctx["git_root"]`.

For clustered satellites, the master's git root must be resolved. The cluster config already stores the master's path in `cluster.yaml` (`master: /path/to/repo`). This is the git root itself.

```python
def resolve_export_dir(git_root: str, project_dir: str) -> str | None:
    """Return the export directory, or None if export is disabled.

    Args:
        git_root: The local project's git root.
        project_dir: The local project's data directory (~/.context-hooks/projects/<hash>).

    Returns:
        Absolute path to the export directory, or None if export is disabled.
    """
    config = load_project_config(project_dir)
    if not config.get('knowledge_export'):
        return None

    # In a cluster, export goes to the master's repo
    cluster_path = os.path.join(project_dir, "cluster.yaml")
    effective_git_root = git_root
    if os.path.exists(cluster_path):
        from lib.config import _parse_simple_yaml
        with open(cluster_path) as f:
            cluster_config = _parse_simple_yaml(f.read())
        master_root = cluster_config.get("master")
        if master_root and master_root.strip():
            effective_git_root = master_root

    custom = config.get('knowledge_export_dir')
    if custom:
        return os.path.join(effective_git_root, custom)
    return os.path.join(effective_git_root, 'data', 'knowledge')
```

## Slug Generation

```python
def _slugify(title: str, entry_id: int = 0) -> str:
    """Convert title to filesystem-safe kebab-case slug.

    Note: strips non-ASCII characters. Titles with Unicode (e.g., accented
    characters) produce lossy slugs. Falls back to 'entry-<id>' if the slug
    is empty after stripping. All current knowledge titles are ASCII.
    """
    slug = title.lower()
    slug = re.sub(r'[^a-z0-9\s-]', '', slug)
    slug = re.sub(r'[\s_]+', '-', slug)
    slug = re.sub(r'-+', '-', slug)
    slug = slug.strip('-')[:80]  # cap at 80 chars for filesystem safety
    return slug if slug else f'entry-{entry_id}'  # fallback for all-non-ASCII titles
```

Slug collisions: The `UNIQUE(title, status)` constraint on the knowledge table means two active entries can't have the same title — so slugs derived from titles are unique among active entries. For same-title supersede (old entry superseded, new entry active with identical title), the old file is renamed to `<slug>-superseded-<old_id>.md` before the new file is written. This preserves both entries' durability records.

## Error Handling

Export failures must NOT block the SQLite write. The SQLite path is the authoritative data store.

```python
def _safe_export(fn, *args, **kwargs):
    """Call export function, logging but not raising on failure."""
    try:
        fn(*args, **kwargs)
    except Exception as e:
        import sys
        print(f"WARNING: knowledge export failed: {e}", file=sys.stderr)
```

Failure modes:
- **Read-only filesystem:** Warning, SQLite write succeeds. User can re-export later via `knowledge export`.
- **Missing export dir:** `write_entry` creates it (including category subdirectories).
- **Disk full:** Warning. SQLite write likely also fails, but that's handled separately.

## CLI Surface

One new utility command for bulk re-export (useful after enabling export on an existing DB):

```bash
context-hooks knowledge export          # Re-export all active + archived entries + regenerate index
context-hooks knowledge export --dry-run # Show what would be written without writing
```

All existing mutation commands (`store`, `promote`, `archive`, `restore`, `dismiss`, `supersede`) trigger export automatically when `knowledge_export: true` is set in config. No changes to their CLI interface.

To enable:
```bash
# In the project's config.yaml (at ~/.context-hooks/projects/<hash>/config.yaml)
knowledge_export: true

# Or with custom directory
knowledge_export: true
knowledge_export_dir: docs/knowledge
```

## MCP Integration

The MCP tool handlers in `mcp_tools.py` already call `knowledge.store()`, `knowledge.promote()`, etc. Since the export is wired into these functions, MCP tools get dual-write automatically. `ctx["git_root"]` is already present in the MCP context dict — no changes to `mcp_tools.py` needed beyond passing `git_root` through to the knowledge functions that call `resolve_export_dir`.

## Testing Strategy

1. **Unit tests for `export.py`:** write_entry, remove_entry, write_index, export_all with temp dirs. Verify file contents, frontmatter format, slug generation, category subdirectory creation.
2. **Integration tests:** store() → verify both DB row and markdown file exist with matching content. promote() → verify frontmatter updated. dismiss() → verify file deleted. archive() → verify file retained with status: archived, excluded from index.
3. **Supersede tests:** same-title supersede → verify old file renamed to `<slug>-superseded-<id>.md`, new file at `<slug>.md`. Different-title supersede → both files at their respective slugs.
4. **Cluster tests:** satellite stores knowledge → verify export lands in master's repo dir.
5. **Idempotency test:** export same entry twice → files identical (no timestamp churn).
6. **Error resilience:** read-only export dir → SQLite write succeeds, warning printed.
7. **Bulk re-export test:** create entries, enable export, run `knowledge export` → all files created.
8. **Slug tests:** edge cases — very long titles (80 char cap), special characters, empty-after-strip.

## What This Does NOT Do

- **No markdown→SQLite import.** The export is one-way. To restore from markdown after data loss, a future `knowledge import` command could parse the frontmatter (strip `---` fences, pass middle section to `_parse_simple_yaml()`, body = content field), but that's v0.4 scope.
- **No git commit automation.** The export writes files; the user (or their workflow) commits them. We don't run `git add` or `git commit` from the export path.
- **No conflict resolution.** If someone manually edits an exported markdown file, the next export overwrites it. SQLite is authoritative.
- **No memo export.** Memos are ephemeral by design.
- **No Unicode slug support.** Non-ASCII characters are stripped from slugs. Known limitation for v0.3.
