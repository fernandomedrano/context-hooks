# MCP Server Adapter Design

**Date:** 2026-03-15
**Status:** Approved
**Scope:** v0.2 — Full MCP server for context-hooks

## Summary

Add a universal MCP (Model Context Protocol) stdio server to context-hooks, exposing its knowledge store, memo system, commit queries, and analysis tools to any MCP-speaking agent (Claude Code, Codex, Gemini, Cursor, etc.). Includes agent-bridge compatibility mode for drop-in replacement in KADE2.

## Decisions

1. **Full surface** — Expose all agent-bridge tools (14) plus all context-hooks unique capabilities (9 additional tools). Total: 23 tools.
2. **Per-project** — One MCP server instance per project. Project resolved from cwd/git root at startup. No per-call project parameter.
3. **Zero external dependencies** — Thin MCP shim using only Python stdlib. No FastMCP, no pip.
4. **`context_` prefix** for native tool names. Agent-bridge aliases (`store_knowledge`, `send_memo`, etc.) registered behind `--compat=agent-bridge` flag.
5. **Approach B** — Protocol shim (`lib/mcp.py`) + tool registry (`lib/mcp_tools.py`). Clean separation of protocol vs. domain.

## Architecture

```
bin/context-hooks mcp [--compat=agent-bridge] [--project=/path]
        │
        ▼
  lib/mcp.py          ← JSON-RPC 2.0 stdio loop (~200 lines)
        │                 initialize, tools/list, tools/call, ping
        │                 Tool registry: dict of name → {schema, handler}
        ▼
  lib/mcp_tools.py    ← Tool definitions + handlers (~500 lines)
        │                 Each handler: parse args → open DB → call lib/* → return result
        ▼
  lib/knowledge.py    ← Existing: store, search, list, promote, archive, memo ops
  lib/queries.py      ← Existing: commit search, tag, file, parity, stats
  lib/xref.py         ← Existing: cross-reference report
  lib/health.py       ← Existing: health summary
  lib/tags.py         ← Existing: profile generation
  lib/db.py           ← Existing + new shared_state table
```

## Protocol Layer — `lib/mcp.py`

Minimal MCP stdio server implementing JSON-RPC 2.0:

- Uses **Content-Length header framing** (LSP-style), not newline-delimited JSON. This matches the MCP stdio transport spec that Claude Code, Codex, and other MCP clients expect. Format: `Content-Length: N\r\n\r\n{json}`.
- Dispatches to registered tool handlers
- Writes JSON responses to stdout (same Content-Length framing)
- Logs diagnostics to stderr (stdout is protocol-only)

Supported methods:
- `initialize` — Server info + capabilities
- `notifications/initialized` — Client ready acknowledgment (no response)
- `tools/list` — Returns all registered tool schemas
- `tools/call` — Dispatches to handler, returns `{content: [{type: "text", text: ...}]}` or `{isError: true, content: [{type: "text", text: "error message"}]}`
- `ping` — Heartbeat (returns `{}`)

No async. No framework. Synchronous `while True` loop reading Content-Length framed messages from stdin.

The shim exposes a registration API:
```python
def register_tool(name, description, input_schema, handler):
    """Register a tool. handler(args: dict) -> str"""
```

## Tool Registry — `lib/mcp_tools.py`

### Handler initialization pattern

All handlers receive a shared context object initialized at server startup:

```python
ctx = {
    "project_dir": data_dir(git_root),   # resolved once at startup
    "git_root": git_root,                 # for queries, xref, health
    "config": load_config(project_dir),   # for health, nudges
}
```

**DB connection lifecycle:** Open a fresh `ContextDB` per tool call, close after. This avoids WAL lock contention with concurrent CLI or hook processes writing to the same DB. The overhead is negligible (~1ms per open) compared to the MCP round-trip latency.

### Knowledge tools (8)

| Tool name | Compat alias | Description | Handler |
|---|---|---|---|
| `context_store_knowledge` | `store_knowledge` | Store a knowledge entry with category, title, content, optional reasoning/tags | `knowledge.store()` |
| `context_search_knowledge` | `search_knowledge` | FTS5 search over knowledge entries | `knowledge.search()` |
| `context_get_knowledge` | `get_knowledge` | Get a specific entry by title (active entries only) | New: direct DB query by title |
| `context_list_knowledge` | `list_knowledge` | List all entries, optionally by category | `knowledge.list_entries()` |
| `context_promote_knowledge` | — | Advance maturity: signal → pattern → decision → convention | `knowledge.promote()` |
| `context_archive_knowledge` | — | Archive an entry (soft delete) | `knowledge.archive()` |
| `context_restore_knowledge` | — | Restore an archived or dismissed entry to active | `knowledge.restore()` |
| `context_supersede_knowledge` | — | Replace an entry with a new one, preserving lineage | `knowledge.supersede()` |

#### `context_store_knowledge` schema

Entries are created at `maturity=decision` by default. Use `context_promote_knowledge` to advance through signal → pattern → decision → convention. The `maturity` parameter allows agents to store signals or patterns explicitly when the evidence bar is lower.

```json
{
  "type": "object",
  "properties": {
    "category": {
      "type": "string",
      "enum": ["architectural-decision", "coding-convention", "failure-class", "reference", "rejected-approach"],
      "description": "Knowledge category"
    },
    "title": { "type": "string", "description": "Short, descriptive title" },
    "content": { "type": "string", "description": "Full content (markdown supported)" },
    "reasoning": { "type": "string", "description": "Why this knowledge matters" },
    "maturity": {
      "type": "string",
      "enum": ["signal", "pattern", "decision", "convention"],
      "default": "decision",
      "description": "Evidence level (default: decision)"
    },
    "bug_refs": { "type": "string", "description": "Comma-separated bug IDs (e.g. BUG-082,BUG-091)" },
    "file_refs": { "type": "string", "description": "Comma-separated file paths" },
    "tags": { "type": "string", "description": "Comma-separated tags" }
  },
  "required": ["category", "title", "content"]
}
```

#### `context_search_knowledge` schema
```json
{
  "type": "object",
  "properties": {
    "query": { "type": "string", "description": "Search query (FTS5 syntax supported)" },
    "limit": { "type": "integer", "description": "Max results (default 10)", "default": 10 }
  },
  "required": ["query"]
}
```

#### `context_get_knowledge` schema

Retrieves a specific entry by exact title match. Returns only active entries (use `context_list_knowledge` with `status` param to find archived/superseded). Implemented as a direct DB query: `SELECT ... FROM knowledge WHERE title = ? AND status = 'active'`, not via `list_entries()`.

```json
{
  "type": "object",
  "properties": {
    "category": { "type": "string", "description": "Category to filter by (optional, narrows search)" },
    "title": { "type": "string", "description": "Exact title to retrieve" }
  },
  "required": ["title"]
}
```

#### `context_list_knowledge` schema
```json
{
  "type": "object",
  "properties": {
    "category": { "type": "string", "description": "Filter by category (optional)" },
    "status": { "type": "string", "enum": ["active", "archived", "superseded", "dismissed"], "default": "active" }
  }
}
```

#### `context_promote_knowledge` schema
```json
{
  "type": "object",
  "properties": {
    "id": { "type": "integer", "description": "Knowledge entry ID to promote" }
  },
  "required": ["id"]
}
```

#### `context_archive_knowledge` schema
```json
{
  "type": "object",
  "properties": {
    "id": { "type": "integer", "description": "Knowledge entry ID to archive" }
  },
  "required": ["id"]
}
```

#### `context_restore_knowledge` schema
```json
{
  "type": "object",
  "properties": {
    "id": { "type": "integer", "description": "Knowledge entry ID to restore to active" }
  },
  "required": ["id"]
}
```

#### `context_supersede_knowledge` schema
```json
{
  "type": "object",
  "properties": {
    "old_id": { "type": "integer", "description": "ID of the entry being replaced" },
    "category": {
      "type": "string",
      "enum": ["architectural-decision", "coding-convention", "failure-class", "reference", "rejected-approach"],
      "description": "Category of the replacement entry"
    },
    "title": { "type": "string", "description": "Title of the replacement entry" },
    "content": { "type": "string", "description": "Content of the replacement entry" },
    "reasoning": { "type": "string", "description": "Why this supersedes the old entry" }
  },
  "required": ["old_id", "category", "title", "content"]
}
```

### Memo tools (6)

| Tool name | Compat alias | Description | Handler |
|---|---|---|---|
| `context_send_memo` | `send_memo` | Send a memo to a specific agent | `knowledge.send_memo()` |
| `context_check_memos` | `check_memos` | List memos, optionally unread only | `knowledge.list_memos()` |
| `context_read_memo` | `read_memo` | Read a memo and mark as read | `knowledge.read_memo()` |
| `context_reply_memo` | `reply_memo` | Reply to a memo (continues thread) | New: insert memo with matching `thread_id` |
| `context_broadcast` | `broadcast` | Send memo to all agents with priority | `send_memo(to_agent='*')` + priority field |
| `context_list_threads` | `list_threads` | List conversation threads with summary | New: group memos by `thread_id` |

#### `context_send_memo` schema
```json
{
  "type": "object",
  "properties": {
    "from_agent": { "type": "string", "description": "Sender agent name" },
    "to_agent": { "type": "string", "description": "Recipient agent name" },
    "subject": { "type": "string", "description": "Memo subject line" },
    "content": { "type": "string", "description": "Memo body" },
    "expires_at": { "type": "string", "description": "ISO datetime expiry (optional)" }
  },
  "required": ["from_agent", "to_agent", "subject", "content"]
}
```

#### `context_check_memos` schema

The `to_agent` filter is implemented as a new query in the handler (the existing `list_memos()` only supports `unread_only`). Query: `SELECT ... FROM memos WHERE to_agent = ? OR to_agent = '*'` with optional `AND read = 0`.

```json
{
  "type": "object",
  "properties": {
    "unread_only": { "type": "boolean", "description": "Only show unread memos", "default": false },
    "to_agent": { "type": "string", "description": "Filter by recipient — includes direct + broadcast memos (optional)" }
  }
}
```

#### `context_read_memo` schema
```json
{
  "type": "object",
  "properties": {
    "id": { "type": "integer", "description": "Memo ID to read" }
  },
  "required": ["id"]
}
```

#### `context_reply_memo` schema

**Thread mechanics:** When replying to memo N:
1. Look up memo N's `thread_id`. If it has one, reuse it.
2. If memo N has no `thread_id`, generate one: `thread-{memo_N_id}` and retroactively update memo N's `thread_id` to this value.
3. Insert the reply memo with the same `thread_id`, `to_agent` = original memo's `from_agent`, `subject` = `"Re: {original_subject}"`.

This ensures threads are created lazily — a memo only gets a `thread_id` when someone replies to it.

```json
{
  "type": "object",
  "properties": {
    "memo_id": { "type": "integer", "description": "Memo ID to reply to" },
    "from_agent": { "type": "string", "description": "Sender agent name" },
    "content": { "type": "string", "description": "Reply content" }
  },
  "required": ["memo_id", "from_agent", "content"]
}
```

#### `context_broadcast` schema

Priority is stored by adding a `priority` column to the `memos` table (see Schema Changes below). Broadcasts are sent with `to_agent='*'`.

```json
{
  "type": "object",
  "properties": {
    "from_agent": { "type": "string", "description": "Sender agent name" },
    "subject": { "type": "string", "description": "Broadcast subject" },
    "content": { "type": "string", "description": "Broadcast body" },
    "priority": { "type": "string", "enum": ["normal", "high", "urgent"], "default": "normal" }
  },
  "required": ["from_agent", "subject", "content"]
}
```

#### `context_list_threads` schema

Groups memos by `thread_id` (non-null only). Returns each thread's ID, subject (from first memo), participant list, message count, and last activity timestamp. Query: `SELECT thread_id, MIN(subject), GROUP_CONCAT(DISTINCT from_agent), COUNT(*), MAX(created_at) FROM memos WHERE thread_id IS NOT NULL GROUP BY thread_id ORDER BY MAX(created_at) DESC LIMIT ?`.

```json
{
  "type": "object",
  "properties": {
    "limit": { "type": "integer", "description": "Max threads to return", "default": 20 }
  }
}
```

### Task & state tools (3)

| Tool name | Compat alias | Description | Handler |
|---|---|---|---|
| `context_handoff_task` | `handoff_task` | Structured task handoff between agents | New: memo with structured JSON content |
| `context_set_shared_state` | `set_shared_state` | Set key-value state visible to all agents | New: `shared_state` table |
| `context_get_shared_state` | `get_shared_state` | Get shared state by key or all | New: `shared_state` table |

#### `context_handoff_task` schema
```json
{
  "type": "object",
  "properties": {
    "from_agent": { "type": "string", "description": "Agent handing off" },
    "to_agent": { "type": "string", "description": "Agent receiving the task" },
    "title": { "type": "string", "description": "Task title" },
    "description": { "type": "string", "description": "What needs to be done" },
    "relevant_files": { "type": "string", "description": "Comma-separated file paths" },
    "context": { "type": "string", "description": "Additional context or constraints" },
    "blockers": { "type": "string", "description": "Known blockers or dependencies" },
    "priority": { "type": "string", "enum": ["low", "normal", "high", "urgent"], "default": "normal" }
  },
  "required": ["from_agent", "to_agent", "title", "description"]
}
```

#### `context_set_shared_state` schema
```json
{
  "type": "object",
  "properties": {
    "key": { "type": "string", "description": "State key" },
    "value": { "type": "string", "description": "State value (any string, JSON-encode complex values)" },
    "updated_by": { "type": "string", "description": "Agent setting the state" }
  },
  "required": ["key", "value", "updated_by"]
}
```

#### `context_get_shared_state` schema
```json
{
  "type": "object",
  "properties": {
    "key": { "type": "string", "description": "State key to retrieve (omit for all state)" }
  }
}
```

### Query & analysis tools (6)

| Tool name | Compat alias | Description | Handler |
|---|---|---|---|
| `context_query_commits` | — | Search commits by term, tag, file, or list recent | `queries.*` functions |
| `context_check_parity` | — | Show parallel path alerts (solo edits) | `queries.query_parity()` |
| `context_run_xref` | — | Cross-reference report across memory layers | `xref` module |
| `context_get_health` | — | Session health summary | `health.health_summary()` |
| `context_get_profile` | — | Auto-discovered file pair patterns | `tags.load_profile()` |
| `context_get_project_context` | `get_context_for_project` | Composite: health + unread memos + recent knowledge | Composite handler |

#### `context_query_commits` schema

Mode-specific `term` requirements:
- `search` — **required**: search string
- `tag` — **required**: tag name
- `file` — **required**: file path (partial match)
- `related` — **required**: commit hash prefix
- `bugs`, `recent`, `stats` — `term` ignored

Returns error if `term` is missing for modes that require it.

```json
{
  "type": "object",
  "properties": {
    "mode": {
      "type": "string",
      "enum": ["search", "tag", "file", "bugs", "related", "recent", "stats"],
      "description": "Query mode"
    },
    "term": { "type": "string", "description": "Search term, tag name, file path, or commit hash (required for search/tag/file/related modes)" },
    "limit": { "type": "integer", "description": "Max results (for recent mode)", "default": 20 }
  },
  "required": ["mode"]
}
```

#### `context_check_parity` schema
```json
{
  "type": "object",
  "properties": {}
}
```

#### `context_run_xref` schema
```json
{
  "type": "object",
  "properties": {}
}
```

#### `context_get_health` schema
```json
{
  "type": "object",
  "properties": {}
}
```

#### `context_get_profile` schema

This tool **regenerates** the profile by calling `tags.generate_profile()` (which runs `git log`), not `load_profile()` (which reads a cached file). This ensures fresh data but may take a few seconds in large repos. The profile is also saved to disk as a side effect.

```json
{
  "type": "object",
  "properties": {
    "days": { "type": "integer", "description": "Days of git history to analyze", "default": 30 }
  }
}
```

#### `context_get_project_context` schema
```json
{
  "type": "object",
  "properties": {
    "include_health": { "type": "boolean", "default": true },
    "include_memos": { "type": "boolean", "default": true },
    "include_knowledge": { "type": "boolean", "default": true },
    "knowledge_limit": { "type": "integer", "default": 10 }
  }
}
```

## Schema Changes — `lib/db.py`

### New table: `shared_state`

```sql
CREATE TABLE IF NOT EXISTS shared_state (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL,
  updated_by TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
```

### Modified table: `memos`

Add `priority` column:

```sql
ALTER TABLE memos ADD COLUMN priority TEXT DEFAULT 'normal';
```

Note: Since SQLite doesn't support `ALTER TABLE ADD COLUMN IF NOT EXISTS`, the migration must check if the column already exists before adding it. Use `PRAGMA table_info(memos)` to check.

### New methods on `ContextDB`:

```python
def upsert_shared_state(self, *, key, value, updated_by):
    """Set or update a shared state key."""

def get_shared_state(self, key=None):
    """Get one key or all shared state."""

def delete_shared_state(self, key):
    """Remove a shared state key."""
```

### Modified methods:

- `insert_memo()` — Accept optional `priority` parameter (default `'normal'`)
- `knowledge.store()` — Accept optional `maturity` parameter (default `'decision'`)

## Entry Point — `bin/context-hooks`

New case in the dispatcher:

```bash
mcp)  python3 "$SCRIPT_DIR/lib/mcp_tools.py" "$@" ;;
```

`lib/mcp_tools.py` has a `__main__` block that:
1. Parses `--compat` and `--project` flags
2. Resolves project: `--project` takes precedence over cwd. If `--project` is given, use it directly (no git root detection). If omitted, resolve git root from cwd.
3. Registers all tools (+ aliases if compat mode)
4. Calls `mcp.serve()` to start the stdio loop

## Agent-Bridge Compatibility

When `--compat=agent-bridge` is passed:

- All 14 agent-bridge tool names are registered as aliases pointing to the same handlers
- `store_knowledge` → `context_store_knowledge` handler
- `send_memo` → `context_send_memo` handler
- etc.

Both native and alias names appear in `tools/list`. An agent calling `store_knowledge` gets the same result as calling `context_store_knowledge`.

## `.mcp.json` Examples

### Standard usage
```json
{
  "context-hooks": {
    "command": "/path/to/bin/context-hooks",
    "args": ["mcp"],
    "cwd": "/path/to/project"
  }
}
```

### KADE2 drop-in replacement
```json
{
  "context-hooks": {
    "command": "/path/to/bin/context-hooks",
    "args": ["mcp", "--compat=agent-bridge"],
    "cwd": "/Users/fernando/Dev/KADE2"
  }
}
```

## Testing Strategy

- **`tests/test_mcp.py`** — Unit tests for protocol layer: feed JSON-RPC strings, assert correct responses. Malformed input, unknown methods, missing params.
- **`tests/test_mcp_tools.py`** — Unit tests for each tool handler: call with temp DB, assert DB mutations and return values.
- **`tests/test_db.py`** — New tests for `shared_state` table and methods.
- No end-to-end MCP client tests (too complex for zero-dep).

## Files Changed

| File | Change type |
|---|---|
| `lib/mcp.py` | **New** — Protocol shim (~200 lines) |
| `lib/mcp_tools.py` | **New** — Tool registry + handlers (~600-700 lines) |
| `lib/db.py` | **Modified** — Add `shared_state` table, `priority` column on memos, new methods |
| `lib/knowledge.py` | **Modified** — Accept optional `maturity` param in `store()` |
| `bin/context-hooks` | **Modified** — Add `mcp)` case |
| `tests/test_mcp.py` | **New** — Protocol tests |
| `tests/test_mcp_tools.py` | **New** — Tool handler tests |
| `tests/test_db.py` | **Modified** — Add shared_state + priority tests |

No existing behavior changes. All additions are additive. The `maturity` param in `store()` defaults to `'decision'` preserving current behavior.
