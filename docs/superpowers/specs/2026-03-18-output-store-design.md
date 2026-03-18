# Tool Output Indexing + Progressive Search Throttling — Design Spec

**Date:** 2026-03-18
**Status:** Approved
**Author:** context-hooks agent + Fernando
**Inspiration:** context-mode (github.com/mksglu/context-mode)

## Problem

Large tool outputs (test suites, file reads, grep results) consume the agent's context window. After compaction, that content is gone entirely. The agent either re-reads files or operates without the information.

context-mode solves this by sandboxing execution and indexing outputs in FTS5. We can achieve the same context compression without a sandbox — intercept PostToolUse responses, chunk and index large outputs, return a summary, let the agent search on demand.

## Solution

### Output Store (`lib/output_store.py`)

New schema table + FTS5 virtual table:

```sql
CREATE TABLE output_chunks (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  session_id TEXT NOT NULL,
  source TEXT NOT NULL,
  chunk_index INTEGER NOT NULL,
  content TEXT NOT NULL,
  created_at TEXT NOT NULL
);

CREATE VIRTUAL TABLE output_chunks_fts USING fts5(
  source, content,
  content=output_chunks, content_rowid=id
);
```

**Chunking:**
- Markdown/code: split at heading boundaries, keep code blocks intact
- Plain text: split at paragraph boundaries (double newline), fallback to 100-line groups
- Max chunk size: 4096 bytes
- Only index if tool output > 4096 bytes (4KB threshold)

**Lifecycle:** Ephemeral per session. Cleaned up on session-start (startup source). FIFO eviction at 200 chunks per session.

### Hook Integration

In `hooks.py`, after event handling, check tool_response size:

```python
if output_size > 4096:
    chunk_count = index_output(db, session_id, source_label, output_text)
    summary = summarize_output(output_text, source_label, chunk_count)
    return {"additionalContext": summary}
```

**Indexed tools:** Bash (stdout), Read (file contents), Grep (results), Glob (file lists).

**Source labels:** `"Bash:git log --oneline"`, `"Read:/src/big_file.py"`, `"Grep:pattern"`.

### Search with Progressive Throttling

**CLI:** `context-hooks search-output <query> [--sources]`
**MCP tool:** `search_output`

**Throttling per session:**
- Calls 1-3: up to 5 results ranked by BM25
- Calls 4-8: 1 result per call
- Calls 9+: blocked — "Refine your query or list sources with --sources"

**Search fallback:** FTS5 MATCH first, then LIKE fallback for non-FTS-friendly queries.

**`--sources` flag:** Lists all indexed sources (tool + label) without searching content. Not throttled.

### Summary Format

Returned as `additionalContext` after indexing:

```
Output indexed: Bash "pytest tests/ -v" (47KB → 12 chunks).
Search with: context-hooks search-output <query>
```

### Schema Migration

v2 → v3: Add `output_chunks` table and `output_chunks_fts` virtual table.

## File Changes

| File | Change |
|------|--------|
| `lib/output_store.py` | **New** — chunking, indexing, search, throttle, cleanup |
| `lib/db.py` | Schema v3 migration |
| `lib/hooks.py` | Check output size after event, index + return summary |
| `lib/mcp_tools.py` | Register `search_output` and `list_output_sources` tools |
| `bin/context-hooks` | Add `search-output` CLI command |
| `tests/test_output_store.py` | **New** |

## What This Does NOT Do

- Does not sandbox or redirect tool execution — purely post-hoc indexing
- Does not modify tool responses — only adds additionalContext
- Does not persist across sessions — ephemeral working memory
- Does not replace the knowledge store — knowledge is curated, outputs are raw
