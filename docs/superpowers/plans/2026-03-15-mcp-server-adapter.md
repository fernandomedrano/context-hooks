# MCP Server Adapter Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a universal MCP stdio server exposing 23 tools for knowledge, memos, tasks, shared state, commit queries, and analysis — with agent-bridge compatibility mode.

**Architecture:** Two new files: `lib/mcp.py` (Content-Length framed JSON-RPC 2.0 protocol shim with tool registry) and `lib/mcp_tools.py` (23 tool definitions + handlers calling existing `lib/` modules). Schema additions to `lib/db.py` (shared_state table, priority column on memos). Minor modifications to `lib/knowledge.py` (maturity param) and `bin/context-hooks` (mcp dispatcher).

**Tech Stack:** Python 3 stdlib only (json, sys, os, re, argparse, sqlite3, datetime, hashlib, subprocess). Zero external deps.

**Spec:** `docs/superpowers/specs/2026-03-15-mcp-server-adapter-design.md`

**Test runner:** `python3.12 -m pytest tests/ -v`

---

## Chunk 1: Schema changes + existing module modifications

### Task 1: Add `shared_state` table and methods to `db.py`

**Files:**
- Modify: `lib/db.py:6-93` (SCHEMA string) and `:130-207` (ContextDB class)
- Test: `tests/test_db.py`

- [ ] **Step 1: Write failing tests for shared_state**

Add to `tests/test_db.py` at end of `TestContextDB` class:

```python
def test_upsert_shared_state(self):
    self.db.upsert_shared_state(key="current_task", value="implement MCP", updated_by="agent-1")
    rows = self.db.query("SELECT key, value, updated_by FROM shared_state")
    assert len(rows) == 1
    assert rows[0] == ("current_task", "implement MCP", "agent-1")

def test_upsert_shared_state_overwrite(self):
    self.db.upsert_shared_state(key="status", value="draft", updated_by="agent-1")
    self.db.upsert_shared_state(key="status", value="published", updated_by="agent-2")
    rows = self.db.query("SELECT value, updated_by FROM shared_state WHERE key = 'status'")
    assert len(rows) == 1
    assert rows[0] == ("published", "agent-2")

def test_get_shared_state_single(self):
    self.db.upsert_shared_state(key="mode", value="debug", updated_by="agent-1")
    result = self.db.get_shared_state("mode")
    assert result == [("mode", "debug", "agent-1", result[0][3])]  # updated_at is dynamic

def test_get_shared_state_all(self):
    self.db.upsert_shared_state(key="a", value="1", updated_by="x")
    self.db.upsert_shared_state(key="b", value="2", updated_by="y")
    result = self.db.get_shared_state()
    assert len(result) == 2

def test_get_shared_state_missing(self):
    result = self.db.get_shared_state("nonexistent")
    assert result == []

def test_delete_shared_state(self):
    self.db.upsert_shared_state(key="temp", value="val", updated_by="x")
    self.db.delete_shared_state("temp")
    result = self.db.get_shared_state("temp")
    assert result == []

def test_shared_state_table_exists(self):
    tables = self.db.query(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='shared_state'"
    )
    assert len(tables) == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3.12 -m pytest tests/test_db.py -v -k "shared_state"`
Expected: FAIL — `AttributeError: 'ContextDB' object has no attribute 'upsert_shared_state'`

- [ ] **Step 3: Add shared_state table to SCHEMA**

In `lib/db.py`, add after the `rule_validations` table (after line 73, before the FTS table):

```sql
CREATE TABLE IF NOT EXISTS shared_state (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL,
  updated_by TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
```

- [ ] **Step 4: Add shared_state methods to ContextDB**

In `lib/db.py`, add after `evict_events()` (after line 204, before `close()`):

```python
def upsert_shared_state(self, *, key, value, updated_by):
    """Set or update a shared state key."""
    from datetime import datetime
    now = datetime.now().isoformat()
    self.execute(
        "INSERT INTO shared_state (key, value, updated_by, updated_at) "
        "VALUES (?, ?, ?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value=?, updated_by=?, updated_at=?",
        (key, value, updated_by, now, value, updated_by, now)
    )

def get_shared_state(self, key=None):
    """Get one key or all shared state. Returns list of tuples."""
    if key:
        return self.query(
            "SELECT key, value, updated_by, updated_at FROM shared_state WHERE key = ?",
            (key,)
        )
    return self.query("SELECT key, value, updated_by, updated_at FROM shared_state ORDER BY key")

def delete_shared_state(self, key):
    """Remove a shared state key."""
    self.execute("DELETE FROM shared_state WHERE key = ?", (key,))
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `python3.12 -m pytest tests/test_db.py -v -k "shared_state"`
Expected: All 7 new tests PASS

- [ ] **Step 6: Run full test suite to verify no regressions**

Run: `python3.12 -m pytest tests/ -v`
Expected: All 146+ tests PASS

- [ ] **Step 7: Commit**

```bash
git add lib/db.py tests/test_db.py
git commit -m "feat: add shared_state table and methods to db.py"
```

---

### Task 2: Add `priority` column to memos + update `insert_memo`

**Files:**
- Modify: `lib/db.py:53-63` (memos schema) and `:190-196` (insert_memo)
- Test: `tests/test_db.py`

- [ ] **Step 1: Write failing tests for priority**

Add to `tests/test_db.py` at end of `TestContextDB`:

```python
def test_insert_memo_with_priority(self):
    self.db.insert_memo(
        from_agent="agent-1", subject="Urgent", content="Deploy now",
        priority="urgent"
    )
    rows = self.db.query("SELECT subject, priority FROM memos")
    assert len(rows) == 1
    assert rows[0] == ("Urgent", "urgent")

def test_insert_memo_default_priority(self):
    self.db.insert_memo(
        from_agent="agent-1", subject="FYI", content="No rush"
    )
    rows = self.db.query("SELECT subject, priority FROM memos")
    assert len(rows) == 1
    assert rows[0] == ("FYI", "normal")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3.12 -m pytest tests/test_db.py -v -k "priority"`
Expected: FAIL — `OperationalError: table memos has no column named priority`

- [ ] **Step 3: Add priority column to memos schema**

In `lib/db.py`, modify the memos CREATE TABLE to add `priority` after `expires_at` (line 62):

```sql
CREATE TABLE IF NOT EXISTS memos (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  from_agent TEXT NOT NULL,
  to_agent TEXT DEFAULT '*',
  subject TEXT NOT NULL,
  content TEXT NOT NULL,
  thread_id TEXT,
  created_at TEXT NOT NULL,
  read INTEGER DEFAULT 0,
  expires_at TEXT,
  priority TEXT DEFAULT 'normal'
);
```

- [ ] **Step 4: Update `insert_memo` to accept priority**

In `lib/db.py`, modify `insert_memo()` (line 190):

```python
def insert_memo(self, *, from_agent, subject, content, to_agent='*',
                thread_id=None, expires_at=None, priority='normal'):
    from datetime import datetime
    self.execute(
        "INSERT INTO memos (from_agent, to_agent, subject, content, thread_id, created_at, expires_at, priority) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (from_agent, to_agent, subject, content, thread_id, datetime.now().isoformat(), expires_at, priority)
    )
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `python3.12 -m pytest tests/test_db.py -v -k "priority"`
Expected: Both tests PASS

- [ ] **Step 6: Run full test suite**

Run: `python3.12 -m pytest tests/ -v`
Expected: All tests PASS (existing memo tests still work since priority defaults to 'normal')

- [ ] **Step 7: Commit**

```bash
git add lib/db.py tests/test_db.py
git commit -m "feat: add priority column to memos table"
```

---

### Task 3: Add optional `maturity` param to `knowledge.store()`

**Files:**
- Modify: `lib/knowledge.py:12-18` (store function)
- Test: `tests/test_knowledge.py`

- [ ] **Step 1: Write failing test**

Add to `tests/test_knowledge.py` (find the store test class/section):

```python
def test_store_with_custom_maturity(self):
    from lib.knowledge import store
    store(self.db, "reference", "Low-confidence signal", "Might be a pattern", maturity="signal")
    rows = self.db.query("SELECT title, maturity FROM knowledge WHERE title = 'Low-confidence signal'")
    assert len(rows) == 1
    assert rows[0][1] == "signal"

def test_store_default_maturity_is_decision(self):
    from lib.knowledge import store
    store(self.db, "reference", "High-confidence fact", "This is decided")
    rows = self.db.query("SELECT title, maturity FROM knowledge WHERE title = 'High-confidence fact'")
    assert len(rows) == 1
    assert rows[0][1] == "decision"
```

- [ ] **Step 2: Run tests to verify the custom maturity test fails**

Run: `python3.12 -m pytest tests/test_knowledge.py -v -k "maturity"`
Expected: `test_store_with_custom_maturity` FAILS — `TypeError: store() got an unexpected keyword argument 'maturity'`

- [ ] **Step 3: Add maturity param to store()**

In `lib/knowledge.py`, modify `store()` (line 12):

```python
def store(db, category, title, content, reasoning=None, bug_refs=None, file_refs=None, tags=None, maturity='decision'):
    """Store a new knowledge entry. Maturity defaults to 'decision'."""
    db.insert_knowledge(
        category=category, title=title, content=content,
        reasoning=reasoning, maturity=maturity,
        bug_refs=bug_refs, file_refs=file_refs, tags=tags
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3.12 -m pytest tests/test_knowledge.py -v -k "maturity"`
Expected: Both PASS

- [ ] **Step 5: Run full test suite**

Run: `python3.12 -m pytest tests/ -v`
Expected: All tests PASS

- [ ] **Step 6: Commit**

```bash
git add lib/knowledge.py tests/test_knowledge.py
git commit -m "feat: add optional maturity param to knowledge.store()"
```

---

## Chunk 2: MCP protocol shim — `lib/mcp.py`

### Task 4: Create the MCP protocol shim

**Files:**
- Create: `lib/mcp.py`
- Create: `tests/test_mcp.py`

This is the JSON-RPC 2.0 stdio server with Content-Length framing. It knows nothing about knowledge/memos/etc — it just dispatches to registered tool handlers.

- [ ] **Step 1: Write failing tests for the protocol layer**

Create `tests/test_mcp.py`:

```python
"""Tests for lib/mcp.py — MCP JSON-RPC protocol shim."""
import io
import json
import os
import sys
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lib.mcp import MCPServer


def make_message(obj):
    """Encode a JSON-RPC message with Content-Length framing."""
    body = json.dumps(obj)
    return f"Content-Length: {len(body)}\r\n\r\n{body}"


def send_and_receive(server, messages):
    """Send framed messages to server, collect responses."""
    input_data = "".join(make_message(m) for m in messages)
    stdin = io.StringIO(input_data)
    stdout = io.StringIO()
    server.run(stdin=stdin, stdout=stdout)
    # Parse responses from stdout
    output = stdout.getvalue()
    responses = []
    while output:
        if not output.startswith("Content-Length:"):
            break
        header_end = output.index("\r\n\r\n")
        length = int(output[len("Content-Length: "):header_end])
        body_start = header_end + 4
        body = output[body_start:body_start + length]
        responses.append(json.loads(body))
        output = output[body_start + length:]
    return responses


class TestMCPServer:
    def setup_method(self):
        self.server = MCPServer("test-server", "0.1.0")

    def test_initialize(self):
        msgs = [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize",
             "params": {"protocolVersion": "2024-11-05",
                        "capabilities": {},
                        "clientInfo": {"name": "test", "version": "1.0"}}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
        ]
        responses = send_and_receive(self.server, msgs)
        assert len(responses) == 1  # notification gets no response
        r = responses[0]
        assert r["id"] == 1
        assert r["result"]["serverInfo"]["name"] == "test-server"
        assert "tools" in r["result"]["capabilities"]

    def test_tools_list_empty(self):
        msgs = [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize",
             "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                        "clientInfo": {"name": "t", "version": "1"}}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
        ]
        responses = send_and_receive(self.server, msgs)
        tools_resp = responses[1]
        assert tools_resp["id"] == 2
        assert tools_resp["result"]["tools"] == []

    def test_tools_list_with_registered_tool(self):
        self.server.register_tool(
            name="echo",
            description="Echo back the input",
            input_schema={"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]},
            handler=lambda args: args["text"]
        )
        msgs = [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize",
             "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                        "clientInfo": {"name": "t", "version": "1"}}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
        ]
        responses = send_and_receive(self.server, msgs)
        tools = responses[1]["result"]["tools"]
        assert len(tools) == 1
        assert tools[0]["name"] == "echo"
        assert tools[0]["description"] == "Echo back the input"

    def test_tools_call_success(self):
        self.server.register_tool(
            name="greet",
            description="Greet someone",
            input_schema={"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]},
            handler=lambda args: f"Hello, {args['name']}!"
        )
        msgs = [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize",
             "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                        "clientInfo": {"name": "t", "version": "1"}}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
             "params": {"name": "greet", "arguments": {"name": "World"}}},
        ]
        responses = send_and_receive(self.server, msgs)
        r = responses[1]
        assert r["id"] == 2
        assert r["result"]["content"][0]["type"] == "text"
        assert r["result"]["content"][0]["text"] == "Hello, World!"

    def test_tools_call_error(self):
        def fail_handler(args):
            raise ValueError("Something broke")

        self.server.register_tool(
            name="fail", description="Always fails",
            input_schema={"type": "object", "properties": {}},
            handler=fail_handler
        )
        msgs = [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize",
             "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                        "clientInfo": {"name": "t", "version": "1"}}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
             "params": {"name": "fail", "arguments": {}}},
        ]
        responses = send_and_receive(self.server, msgs)
        r = responses[1]
        assert r["result"]["isError"] is True
        assert "Something broke" in r["result"]["content"][0]["text"]

    def test_tools_call_unknown_tool(self):
        msgs = [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize",
             "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                        "clientInfo": {"name": "t", "version": "1"}}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
             "params": {"name": "nonexistent", "arguments": {}}},
        ]
        responses = send_and_receive(self.server, msgs)
        r = responses[1]
        assert "error" in r  # JSON-RPC error, not tool error

    def test_ping(self):
        msgs = [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize",
             "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                        "clientInfo": {"name": "t", "version": "1"}}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 99, "method": "ping"},
        ]
        responses = send_and_receive(self.server, msgs)
        r = responses[1]
        assert r["id"] == 99
        assert r["result"] == {}

    def test_unknown_method(self):
        msgs = [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize",
             "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                        "clientInfo": {"name": "t", "version": "1"}}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "bogus/method", "params": {}},
        ]
        responses = send_and_receive(self.server, msgs)
        r = responses[1]
        assert "error" in r
        assert r["error"]["code"] == -32601  # Method not found
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3.12 -m pytest tests/test_mcp.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'lib.mcp'`

- [ ] **Step 3: Implement `lib/mcp.py`**

Create `lib/mcp.py`:

```python
"""Minimal MCP stdio server — JSON-RPC 2.0 with Content-Length framing.

Zero external dependencies. Knows nothing about knowledge/memos/etc.
Tools register via register_tool() and the server dispatches calls.
"""
import json
import sys


class MCPServer:
    """MCP protocol handler with tool registry."""

    def __init__(self, name: str, version: str):
        self.name = name
        self.version = version
        self._tools = {}  # name -> {description, input_schema, handler}

    def register_tool(self, *, name, description, input_schema, handler):
        """Register a tool. handler(args: dict) -> str"""
        self._tools[name] = {
            "description": description,
            "inputSchema": input_schema,
            "handler": handler,
        }

    def run(self, *, stdin=None, stdout=None):
        """Main loop: read JSON-RPC messages, dispatch, respond."""
        _in = stdin or sys.stdin
        _out = stdout or sys.stdout

        while True:
            message = self._read_message(_in)
            if message is None:
                break

            response = self._handle(message)
            if response is not None:
                self._write_message(response, _out)

    def _read_message(self, stream):
        """Read a Content-Length framed JSON-RPC message."""
        # Read headers
        headers = {}
        while True:
            line = stream.readline()
            if not line:
                return None  # EOF
            line = line.strip()
            if line == "":
                break  # End of headers
            if ":" in line:
                key, value = line.split(":", 1)
                headers[key.strip()] = value.strip()

        content_length = int(headers.get("Content-Length", 0))
        if content_length == 0:
            return None

        body = stream.read(content_length)
        if not body:
            return None

        return json.loads(body)

    def _write_message(self, obj, stream):
        """Write a Content-Length framed JSON-RPC response."""
        body = json.dumps(obj)
        stream.write(f"Content-Length: {len(body)}\r\n\r\n{body}")
        stream.flush()

    def _handle(self, message):
        """Dispatch a JSON-RPC message. Returns response dict or None."""
        method = message.get("method", "")
        msg_id = message.get("id")
        params = message.get("params", {})

        # Notifications (no id) get no response
        if msg_id is None:
            return None

        if method == "initialize":
            return self._respond(msg_id, {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": self.name, "version": self.version},
            })

        if method == "ping":
            return self._respond(msg_id, {})

        if method == "tools/list":
            tools = [
                {"name": name, "description": t["description"], "inputSchema": t["inputSchema"]}
                for name, t in self._tools.items()
            ]
            return self._respond(msg_id, {"tools": tools})

        if method == "tools/call":
            tool_name = params.get("name", "")
            arguments = params.get("arguments", {})

            if tool_name not in self._tools:
                return self._error(msg_id, -32602, f"Unknown tool: {tool_name}")

            tool = self._tools[tool_name]
            try:
                result_text = tool["handler"](arguments)
                return self._respond(msg_id, {
                    "content": [{"type": "text", "text": str(result_text)}]
                })
            except Exception as e:
                return self._respond(msg_id, {
                    "isError": True,
                    "content": [{"type": "text", "text": f"Error: {e}"}]
                })

        return self._error(msg_id, -32601, f"Method not found: {method}")

    def _respond(self, msg_id, result):
        return {"jsonrpc": "2.0", "id": msg_id, "result": result}

    def _error(self, msg_id, code, message):
        return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": code, "message": message}}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3.12 -m pytest tests/test_mcp.py -v`
Expected: All 9 tests PASS

- [ ] **Step 5: Run full test suite**

Run: `python3.12 -m pytest tests/ -v`
Expected: All tests PASS

- [ ] **Step 6: Commit**

```bash
git add lib/mcp.py tests/test_mcp.py
git commit -m "feat: MCP protocol shim with Content-Length framing"
```

---

## Chunk 3: Tool handlers — knowledge + memo tools

### Task 5: Create `lib/mcp_tools.py` with knowledge tool handlers

**Files:**
- Create: `lib/mcp_tools.py`
- Create: `tests/test_mcp_tools.py`

Start with just the knowledge tools. We'll add more tool groups in subsequent tasks.

- [ ] **Step 1: Write failing tests for knowledge tool handlers**

Create `tests/test_mcp_tools.py`:

```python
"""Tests for lib/mcp_tools.py — MCP tool handlers."""
import json
import os
import sys
import tempfile
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lib.db import ContextDB


class TestKnowledgeTools:
    """Test knowledge tool handlers directly (no MCP protocol)."""

    def setup_method(self):
        self.tmp = tempfile.mkdtemp()
        self.db = ContextDB(self.tmp)
        self.ctx = {
            "project_dir": self.tmp,
            "git_root": self.tmp,
            "config": {},
        }

    def teardown_method(self):
        self.db.close()

    def _handler(self, name):
        """Get a handler function by tool name."""
        from lib.mcp_tools import build_handlers
        handlers = build_handlers(self.ctx)
        return handlers[name]

    def test_store_knowledge(self):
        h = self._handler("context_store_knowledge")
        result = h({"category": "reference", "title": "Test entry", "content": "Some content"})
        assert "Stored" in result
        rows = self.db.query("SELECT title, maturity FROM knowledge")
        assert rows[0] == ("Test entry", "decision")

    def test_store_knowledge_with_maturity(self):
        h = self._handler("context_store_knowledge")
        h({"category": "reference", "title": "Signal", "content": "Maybe", "maturity": "signal"})
        rows = self.db.query("SELECT maturity FROM knowledge WHERE title = 'Signal'")
        assert rows[0][0] == "signal"

    def test_search_knowledge(self):
        self.db.insert_knowledge(category="reference", title="SQLite tips", content="Use WAL mode for concurrency")
        h = self._handler("context_search_knowledge")
        result = h({"query": "WAL concurrency"})
        parsed = json.loads(result)
        assert len(parsed) >= 1
        assert parsed[0]["title"] == "SQLite tips"

    def test_get_knowledge(self):
        self.db.insert_knowledge(category="reference", title="Exact match", content="Found it")
        h = self._handler("context_get_knowledge")
        result = json.loads(h({"title": "Exact match"}))
        assert result["title"] == "Exact match"
        assert result["content"] == "Found it"

    def test_get_knowledge_not_found(self):
        h = self._handler("context_get_knowledge")
        result = h({"title": "Nonexistent"})
        assert "not found" in result.lower()

    def test_list_knowledge(self):
        self.db.insert_knowledge(category="reference", title="A", content="a")
        self.db.insert_knowledge(category="coding-convention", title="B", content="b")
        h = self._handler("context_list_knowledge")
        result = json.loads(h({}))
        assert len(result) == 2

    def test_list_knowledge_by_category(self):
        self.db.insert_knowledge(category="reference", title="A", content="a")
        self.db.insert_knowledge(category="coding-convention", title="B", content="b")
        h = self._handler("context_list_knowledge")
        result = json.loads(h({"category": "reference"}))
        assert len(result) == 1
        assert result[0]["category"] == "reference"

    def test_promote_knowledge(self):
        self.db.insert_knowledge(category="reference", title="P", content="p", maturity="signal")
        entry_id = self.db.query("SELECT id FROM knowledge WHERE title = 'P'")[0][0]
        h = self._handler("context_promote_knowledge")
        result = h({"id": entry_id})
        assert "Promoted" in result
        new_maturity = self.db.query("SELECT maturity FROM knowledge WHERE id = ?", (entry_id,))[0][0]
        assert new_maturity == "pattern"

    def test_archive_knowledge(self):
        self.db.insert_knowledge(category="reference", title="Arch", content="c")
        entry_id = self.db.query("SELECT id FROM knowledge WHERE title = 'Arch'")[0][0]
        h = self._handler("context_archive_knowledge")
        h({"id": entry_id})
        status = self.db.query("SELECT status FROM knowledge WHERE id = ?", (entry_id,))[0][0]
        assert status == "archived"

    def test_restore_knowledge(self):
        self.db.insert_knowledge(category="reference", title="Rest", content="c")
        entry_id = self.db.query("SELECT id FROM knowledge WHERE title = 'Rest'")[0][0]
        self.db.execute("UPDATE knowledge SET status = 'archived' WHERE id = ?", (entry_id,))
        h = self._handler("context_restore_knowledge")
        h({"id": entry_id})
        status = self.db.query("SELECT status FROM knowledge WHERE id = ?", (entry_id,))[0][0]
        assert status == "active"

    def test_supersede_knowledge(self):
        self.db.insert_knowledge(category="reference", title="Old", content="old content")
        old_id = self.db.query("SELECT id FROM knowledge WHERE title = 'Old'")[0][0]
        h = self._handler("context_supersede_knowledge")
        h({"old_id": old_id, "category": "reference", "title": "New", "content": "new content"})
        old_status = self.db.query("SELECT status FROM knowledge WHERE id = ?", (old_id,))[0][0]
        assert old_status == "superseded"
        new_rows = self.db.query("SELECT title, status FROM knowledge WHERE title = 'New'")
        assert new_rows[0] == ("New", "active")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3.12 -m pytest tests/test_mcp_tools.py -v -k "Knowledge"`
Expected: FAIL — `ModuleNotFoundError: No module named 'lib.mcp_tools'`

- [ ] **Step 3: Implement knowledge handlers in `lib/mcp_tools.py`**

Create `lib/mcp_tools.py`:

```python
"""MCP tool registry — defines all 23 tools + agent-bridge compat aliases.

Each handler: receives args dict, opens DB, calls lib/*, returns string result.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lib.db import ContextDB
from lib import knowledge


def _open_db(ctx):
    """Open a fresh DB connection for this tool call."""
    return ContextDB(ctx["project_dir"])


def build_handlers(ctx):
    """Build all tool handlers closed over the shared context. Returns dict of name -> handler."""
    handlers = {}

    # ── Knowledge tools ──────────────────────────────────────────────────

    def context_store_knowledge(args):
        db = _open_db(ctx)
        try:
            knowledge.store(
                db, args["category"], args["title"], args["content"],
                reasoning=args.get("reasoning"),
                bug_refs=args.get("bug_refs"),
                file_refs=args.get("file_refs"),
                tags=args.get("tags"),
                maturity=args.get("maturity", "decision"),
            )
            return f"Stored: {args['title']}"
        finally:
            db.close()

    def context_search_knowledge(args):
        db = _open_db(ctx)
        try:
            results = knowledge.search(db, args["query"], limit=args.get("limit", 10))
            return json.dumps(results)
        finally:
            db.close()

    def context_get_knowledge(args):
        db = _open_db(ctx)
        try:
            title = args["title"]
            category = args.get("category")
            sql = "SELECT id, category, maturity, title, content, reasoning, status, bug_refs, file_refs, tags, created_at FROM knowledge WHERE title = ? AND status = 'active'"
            params = [title]
            if category:
                sql += " AND category = ?"
                params.append(category)
            rows = db.query(sql, tuple(params))
            if not rows:
                return f"Not found: {title}"
            r = rows[0]
            return json.dumps({
                "id": r[0], "category": r[1], "maturity": r[2], "title": r[3],
                "content": r[4], "reasoning": r[5], "status": r[6],
                "bug_refs": r[7], "file_refs": r[8], "tags": r[9], "created_at": r[10],
            })
        finally:
            db.close()

    def context_list_knowledge(args):
        db = _open_db(ctx)
        try:
            entries = knowledge.list_entries(
                db, category=args.get("category"), status=args.get("status", "active")
            )
            return json.dumps(entries)
        finally:
            db.close()

    def context_promote_knowledge(args):
        db = _open_db(ctx)
        try:
            knowledge.promote(db, args["id"])
            return f"Promoted entry {args['id']}"
        finally:
            db.close()

    def context_archive_knowledge(args):
        db = _open_db(ctx)
        try:
            knowledge.archive(db, args["id"])
            return f"Archived entry {args['id']}"
        finally:
            db.close()

    def context_restore_knowledge(args):
        db = _open_db(ctx)
        try:
            knowledge.restore(db, args["id"])
            return f"Restored entry {args['id']}"
        finally:
            db.close()

    def context_supersede_knowledge(args):
        db = _open_db(ctx)
        try:
            knowledge.supersede(
                db, args["old_id"], args["category"], args["title"],
                args["content"], args.get("reasoning")
            )
            return f"Superseded entry {args['old_id']} with '{args['title']}'"
        finally:
            db.close()

    handlers["context_store_knowledge"] = context_store_knowledge
    handlers["context_search_knowledge"] = context_search_knowledge
    handlers["context_get_knowledge"] = context_get_knowledge
    handlers["context_list_knowledge"] = context_list_knowledge
    handlers["context_promote_knowledge"] = context_promote_knowledge
    handlers["context_archive_knowledge"] = context_archive_knowledge
    handlers["context_restore_knowledge"] = context_restore_knowledge
    handlers["context_supersede_knowledge"] = context_supersede_knowledge

    return handlers
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3.12 -m pytest tests/test_mcp_tools.py -v -k "Knowledge"`
Expected: All 12 tests PASS

- [ ] **Step 5: Commit**

```bash
git add lib/mcp_tools.py tests/test_mcp_tools.py
git commit -m "feat: knowledge tool handlers for MCP server"
```

---

### Task 6: Add memo tool handlers

**Files:**
- Modify: `lib/mcp_tools.py`
- Modify: `tests/test_mcp_tools.py`

- [ ] **Step 1: Write failing tests for memo handlers**

Add to `tests/test_mcp_tools.py`:

```python
class TestMemoTools:
    def setup_method(self):
        self.tmp = tempfile.mkdtemp()
        self.db = ContextDB(self.tmp)
        self.ctx = {"project_dir": self.tmp, "git_root": self.tmp, "config": {}}

    def teardown_method(self):
        self.db.close()

    def _handler(self, name):
        from lib.mcp_tools import build_handlers
        return build_handlers(self.ctx)[name]

    def test_send_memo(self):
        h = self._handler("context_send_memo")
        result = h({"from_agent": "a1", "to_agent": "a2", "subject": "Hi", "content": "Hello"})
        assert "sent" in result.lower()
        rows = self.db.query("SELECT from_agent, to_agent, subject FROM memos")
        assert rows[0] == ("a1", "a2", "Hi")

    def test_check_memos_all(self):
        self.db.insert_memo(from_agent="a1", subject="S1", content="C1")
        self.db.insert_memo(from_agent="a2", subject="S2", content="C2")
        h = self._handler("context_check_memos")
        result = json.loads(h({}))
        assert len(result) == 2

    def test_check_memos_unread(self):
        self.db.insert_memo(from_agent="a1", subject="S1", content="C1")
        self.db.insert_memo(from_agent="a2", subject="S2", content="C2")
        self.db.execute("UPDATE memos SET read = 1 WHERE subject = 'S1'")
        h = self._handler("context_check_memos")
        result = json.loads(h({"unread_only": True}))
        assert len(result) == 1
        assert result[0]["subject"] == "S2"

    def test_check_memos_to_agent(self):
        self.db.insert_memo(from_agent="a1", to_agent="a2", subject="Direct", content="C")
        self.db.insert_memo(from_agent="a1", to_agent="*", subject="Broadcast", content="C")
        self.db.insert_memo(from_agent="a1", to_agent="a3", subject="Other", content="C")
        h = self._handler("context_check_memos")
        result = json.loads(h({"to_agent": "a2"}))
        subjects = [m["subject"] for m in result]
        assert "Direct" in subjects
        assert "Broadcast" in subjects
        assert "Other" not in subjects

    def test_read_memo(self):
        self.db.insert_memo(from_agent="a1", subject="Read me", content="Body text")
        memo_id = self.db.query("SELECT id FROM memos")[0][0]
        h = self._handler("context_read_memo")
        result = json.loads(h({"id": memo_id}))
        assert result["subject"] == "Read me"
        assert result["content"] == "Body text"
        # Verify marked as read
        read_flag = self.db.query("SELECT read FROM memos WHERE id = ?", (memo_id,))[0][0]
        assert read_flag == 1

    def test_reply_memo_creates_thread(self):
        self.db.insert_memo(from_agent="a1", to_agent="a2", subject="Original", content="Hello")
        memo_id = self.db.query("SELECT id FROM memos")[0][0]
        h = self._handler("context_reply_memo")
        h({"memo_id": memo_id, "from_agent": "a2", "content": "Reply here"})
        # Original should now have a thread_id
        orig = self.db.query("SELECT thread_id FROM memos WHERE id = ?", (memo_id,))[0][0]
        assert orig == f"thread-{memo_id}"
        # Reply should share the thread_id
        reply = self.db.query("SELECT thread_id, subject, to_agent FROM memos WHERE id != ?", (memo_id,))[0]
        assert reply[0] == f"thread-{memo_id}"
        assert reply[1] == "Re: Original"
        assert reply[2] == "a1"  # Reply goes to original sender

    def test_reply_memo_reuses_existing_thread(self):
        self.db.insert_memo(from_agent="a1", to_agent="a2", subject="Threaded",
                           content="Start", thread_id="thread-existing")
        memo_id = self.db.query("SELECT id FROM memos")[0][0]
        h = self._handler("context_reply_memo")
        h({"memo_id": memo_id, "from_agent": "a2", "content": "Continue"})
        reply_thread = self.db.query(
            "SELECT thread_id FROM memos WHERE content = 'Continue'"
        )[0][0]
        assert reply_thread == "thread-existing"

    def test_broadcast(self):
        h = self._handler("context_broadcast")
        h({"from_agent": "a1", "subject": "Alert", "content": "Deploy soon", "priority": "urgent"})
        rows = self.db.query("SELECT to_agent, priority FROM memos")
        assert rows[0] == ("*", "urgent")

    def test_list_threads(self):
        self.db.insert_memo(from_agent="a1", to_agent="a2", subject="T1",
                           content="msg1", thread_id="thread-1")
        self.db.insert_memo(from_agent="a2", to_agent="a1", subject="Re: T1",
                           content="msg2", thread_id="thread-1")
        self.db.insert_memo(from_agent="a3", to_agent="a1", subject="T2",
                           content="msg3", thread_id="thread-2")
        h = self._handler("context_list_threads")
        result = json.loads(h({}))
        assert len(result) == 2
        # Each thread has id, subject, participants, count, last_activity
        t1 = next(t for t in result if t["thread_id"] == "thread-1")
        assert t1["message_count"] == 2
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3.12 -m pytest tests/test_mcp_tools.py -v -k "Memo"`
Expected: FAIL — `KeyError: 'context_send_memo'`

- [ ] **Step 3: Add memo handlers to `build_handlers()` in `lib/mcp_tools.py`**

Add after the knowledge handlers section inside `build_handlers()`:

```python
    # ── Memo tools ───────────────────────────────────────────────────────

    def context_send_memo(args):
        db = _open_db(ctx)
        try:
            knowledge.send_memo(
                db, args["from_agent"], args["subject"], args["content"],
                to_agent=args.get("to_agent", "*"),
                expires_at=args.get("expires_at"),
            )
            return f"Memo sent: {args['subject']}"
        finally:
            db.close()

    def context_check_memos(args):
        db = _open_db(ctx)
        try:
            to_agent = args.get("to_agent")
            unread_only = args.get("unread_only", False)
            if to_agent:
                sql = ("SELECT id, from_agent, to_agent, subject, content, created_at, read, expires_at "
                       "FROM memos WHERE (to_agent = ? OR to_agent = '*')")
                params = [to_agent]
                if unread_only:
                    sql += " AND read = 0"
                sql += " ORDER BY id ASC"
                rows = db.query(sql, tuple(params))
                result = [knowledge._memo_to_dict(r) for r in rows]
            else:
                result = knowledge.list_memos(db, unread_only=unread_only)
            return json.dumps(result)
        finally:
            db.close()

    def context_read_memo(args):
        db = _open_db(ctx)
        try:
            memo = knowledge.read_memo(db, args["id"])
            return json.dumps(memo)
        finally:
            db.close()

    def context_reply_memo(args):
        db = _open_db(ctx)
        try:
            memo_id = args["memo_id"]
            # Look up original memo
            rows = db.query(
                "SELECT id, from_agent, to_agent, subject, thread_id FROM memos WHERE id = ?",
                (memo_id,)
            )
            if not rows:
                raise ValueError(f"Memo {memo_id} not found")
            orig = rows[0]
            orig_from = orig[1]
            orig_subject = orig[3]
            thread_id = orig[4]
            # Lazy thread creation
            if not thread_id:
                thread_id = f"thread-{memo_id}"
                db.execute("UPDATE memos SET thread_id = ? WHERE id = ?", (thread_id, memo_id))
            # Insert reply
            db.insert_memo(
                from_agent=args["from_agent"],
                to_agent=orig_from,
                subject=f"Re: {orig_subject}",
                content=args["content"],
                thread_id=thread_id,
            )
            return f"Replied to memo {memo_id} in {thread_id}"
        finally:
            db.close()

    def context_broadcast(args):
        db = _open_db(ctx)
        try:
            db.insert_memo(
                from_agent=args["from_agent"],
                to_agent="*",
                subject=args["subject"],
                content=args["content"],
                priority=args.get("priority", "normal"),
            )
            return f"Broadcast sent: {args['subject']}"
        finally:
            db.close()

    def context_list_threads(args):
        db = _open_db(ctx)
        try:
            limit = args.get("limit", 20)
            rows = db.query(
                "SELECT thread_id, MIN(subject), GROUP_CONCAT(DISTINCT from_agent), "
                "COUNT(*), MAX(created_at) "
                "FROM memos WHERE thread_id IS NOT NULL "
                "GROUP BY thread_id ORDER BY MAX(created_at) DESC LIMIT ?",
                (limit,)
            )
            result = [
                {"thread_id": r[0], "subject": r[1], "participants": r[2].split(","),
                 "message_count": r[3], "last_activity": r[4]}
                for r in rows
            ]
            return json.dumps(result)
        finally:
            db.close()

    handlers["context_send_memo"] = context_send_memo
    handlers["context_check_memos"] = context_check_memos
    handlers["context_read_memo"] = context_read_memo
    handlers["context_reply_memo"] = context_reply_memo
    handlers["context_broadcast"] = context_broadcast
    handlers["context_list_threads"] = context_list_threads
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3.12 -m pytest tests/test_mcp_tools.py -v -k "Memo"`
Expected: All 10 tests PASS

- [ ] **Step 5: Run full test suite**

Run: `python3.12 -m pytest tests/ -v`
Expected: All tests PASS

- [ ] **Step 6: Commit**

```bash
git add lib/mcp_tools.py tests/test_mcp_tools.py
git commit -m "feat: memo tool handlers with thread mechanics"
```

---

### Task 7: Add task/state + query/analysis tool handlers

**Files:**
- Modify: `lib/mcp_tools.py`
- Modify: `tests/test_mcp_tools.py`

- [ ] **Step 1: Write failing tests for task/state and query handlers**

Add to `tests/test_mcp_tools.py`:

```python
class TestTaskStateTools:
    def setup_method(self):
        self.tmp = tempfile.mkdtemp()
        self.db = ContextDB(self.tmp)
        self.ctx = {"project_dir": self.tmp, "git_root": self.tmp, "config": {}}

    def teardown_method(self):
        self.db.close()

    def _handler(self, name):
        from lib.mcp_tools import build_handlers
        return build_handlers(self.ctx)[name]

    def test_handoff_task(self):
        h = self._handler("context_handoff_task")
        h({"from_agent": "a1", "to_agent": "a2", "title": "Deploy v2",
           "description": "Run deploy script", "priority": "high"})
        rows = self.db.query("SELECT subject, content, to_agent FROM memos")
        assert len(rows) == 1
        assert rows[0][0] == "[TASK] Deploy v2"
        content = json.loads(rows[0][1])
        assert content["description"] == "Run deploy script"
        assert content["priority"] == "high"

    def test_set_and_get_shared_state(self):
        set_h = self._handler("context_set_shared_state")
        get_h = self._handler("context_get_shared_state")
        set_h({"key": "deploy_status", "value": "in_progress", "updated_by": "agent-1"})
        result = json.loads(get_h({"key": "deploy_status"}))
        assert result["key"] == "deploy_status"
        assert result["value"] == "in_progress"

    def test_get_shared_state_all(self):
        set_h = self._handler("context_set_shared_state")
        get_h = self._handler("context_get_shared_state")
        set_h({"key": "a", "value": "1", "updated_by": "x"})
        set_h({"key": "b", "value": "2", "updated_by": "y"})
        result = json.loads(get_h({}))
        assert len(result) == 2

    def test_get_shared_state_missing(self):
        get_h = self._handler("context_get_shared_state")
        result = get_h({"key": "nonexistent"})
        assert "not found" in result.lower() or "null" in result.lower()


class TestQueryTools:
    def setup_method(self):
        self.tmp = tempfile.mkdtemp()
        self.db = ContextDB(self.tmp)
        self.ctx = {"project_dir": self.tmp, "git_root": self.tmp, "config": {}}

    def teardown_method(self):
        self.db.close()

    def _handler(self, name):
        from lib.mcp_tools import build_handlers
        return build_handlers(self.ctx)[name]

    def test_query_commits_recent(self):
        self.db.insert_commit(
            session_id="s1", commit_date="2026-01-01", hash="a" * 40,
            short_hash="aaaaaaa", author="t@t.com", subject="fix: test",
            body="", files_changed="a.py", tags="fix", project_dir="/p"
        )
        h = self._handler("context_query_commits")
        result = h({"mode": "recent", "limit": 5})
        assert "aaaaaaa" in result

    def test_query_commits_search(self):
        self.db.insert_commit(
            session_id="s1", commit_date="2026-01-01", hash="b" * 40,
            short_hash="bbbbbbb", author="t@t.com", subject="feat: add MCP",
            body="", files_changed="mcp.py", tags="feat", project_dir="/p"
        )
        h = self._handler("context_query_commits")
        result = h({"mode": "search", "term": "MCP"})
        assert "bbbbbbb" in result

    def test_query_commits_missing_term(self):
        h = self._handler("context_query_commits")
        # search mode without term should return error
        try:
            result = h({"mode": "search"})
            assert "required" in result.lower() or "error" in result.lower()
        except (ValueError, KeyError):
            pass  # Also acceptable

    def test_check_parity(self):
        h = self._handler("context_check_parity")
        result = h({})
        assert "PARALLEL PATH" in result.upper() or "parity" in result.lower() or "alerts" in result.lower()

    def test_get_health(self):
        h = self._handler("context_get_health")
        result = h({})
        # May return "No issues" or actual health data
        assert isinstance(result, str)

    def test_get_project_context(self):
        self.db.insert_knowledge(category="reference", title="Test", content="c")
        self.db.insert_memo(from_agent="a", subject="S", content="C")
        h = self._handler("context_get_project_context")
        result = json.loads(h({}))
        assert "knowledge" in result
        assert "memos" in result
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3.12 -m pytest tests/test_mcp_tools.py -v -k "TaskState or Query"`
Expected: FAIL — `KeyError: 'context_handoff_task'`

- [ ] **Step 3: Add task/state handlers to `build_handlers()`**

Add after the memo handlers section:

```python
    # ── Task & state tools ───────────────────────────────────────────────

    def context_handoff_task(args):
        db = _open_db(ctx)
        try:
            task_content = json.dumps({
                "description": args["description"],
                "relevant_files": args.get("relevant_files", ""),
                "context": args.get("context", ""),
                "blockers": args.get("blockers", ""),
                "priority": args.get("priority", "normal"),
            })
            db.insert_memo(
                from_agent=args["from_agent"],
                to_agent=args["to_agent"],
                subject=f"[TASK] {args['title']}",
                content=task_content,
                priority=args.get("priority", "normal"),
            )
            return f"Task handed off: {args['title']} → {args['to_agent']}"
        finally:
            db.close()

    def context_set_shared_state(args):
        db = _open_db(ctx)
        try:
            db.upsert_shared_state(
                key=args["key"], value=args["value"], updated_by=args["updated_by"]
            )
            return f"State set: {args['key']} = {args['value']}"
        finally:
            db.close()

    def context_get_shared_state(args):
        db = _open_db(ctx)
        try:
            key = args.get("key")
            rows = db.get_shared_state(key)
            if key:
                if not rows:
                    return f"Not found: {key}"
                r = rows[0]
                return json.dumps({"key": r[0], "value": r[1], "updated_by": r[2], "updated_at": r[3]})
            return json.dumps([
                {"key": r[0], "value": r[1], "updated_by": r[2], "updated_at": r[3]}
                for r in rows
            ])
        finally:
            db.close()

    handlers["context_handoff_task"] = context_handoff_task
    handlers["context_set_shared_state"] = context_set_shared_state
    handlers["context_get_shared_state"] = context_get_shared_state
```

- [ ] **Step 4: Add query/analysis handlers to `build_handlers()`**

Add after the task/state handlers:

```python
    # ── Query & analysis tools ───────────────────────────────────────────

    from lib import queries

    _TERM_REQUIRED_MODES = {"search", "tag", "file", "related"}

    def context_query_commits(args):
        db = _open_db(ctx)
        try:
            mode = args["mode"]
            term = args.get("term")
            limit = args.get("limit", 20)

            if mode in _TERM_REQUIRED_MODES and not term:
                raise ValueError(f"'term' is required for mode '{mode}'")

            if mode == "search":
                return queries.query_search(db, term)
            elif mode == "tag":
                return queries.query_tag(db, term)
            elif mode == "file":
                return queries.query_file(db, term)
            elif mode == "bugs":
                return queries.query_bugs(db)
            elif mode == "related":
                return queries.query_related(db, term)
            elif mode == "recent":
                return queries.query_recent(db, limit)
            elif mode == "stats":
                return queries.query_stats(db)
            else:
                raise ValueError(f"Unknown mode: {mode}")
        finally:
            db.close()

    def context_check_parity(args):
        db = _open_db(ctx)
        try:
            return queries.query_parity(db)
        finally:
            db.close()

    def context_run_xref(args):
        db = _open_db(ctx)
        try:
            from lib.xref import run_xref
            return run_xref(db, ctx["git_root"], ctx["project_dir"])
        finally:
            db.close()

    def context_get_health(args):
        db = _open_db(ctx)
        try:
            from lib.health import health_summary
            result = health_summary(db, ctx["git_root"], ctx["project_dir"], ctx["config"])
            return result or "No health issues detected."
        finally:
            db.close()

    def context_get_profile(args):
        from lib.tags import generate_profile, save_profile
        days = args.get("days", 30)
        profile = generate_profile(ctx["git_root"], days=days)
        save_profile(ctx["project_dir"], profile)
        return json.dumps(profile)

    def context_get_project_context(args):
        db = _open_db(ctx)
        try:
            result = {}
            if args.get("include_health", True):
                from lib.health import health_summary
                result["health"] = health_summary(db, ctx["git_root"], ctx["project_dir"], ctx["config"]) or "OK"
            if args.get("include_memos", True):
                result["memos"] = knowledge.list_memos(db, unread_only=True)
            if args.get("include_knowledge", True):
                limit = args.get("knowledge_limit", 10)
                entries = knowledge.list_entries(db)
                result["knowledge"] = entries[:limit]
            return json.dumps(result)
        finally:
            db.close()

    handlers["context_query_commits"] = context_query_commits
    handlers["context_check_parity"] = context_check_parity
    handlers["context_run_xref"] = context_run_xref
    handlers["context_get_health"] = context_get_health
    handlers["context_get_profile"] = context_get_profile
    handlers["context_get_project_context"] = context_get_project_context
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `python3.12 -m pytest tests/test_mcp_tools.py -v`
Expected: All tests PASS

- [ ] **Step 6: Run full test suite**

Run: `python3.12 -m pytest tests/ -v`
Expected: All tests PASS

- [ ] **Step 7: Commit**

```bash
git add lib/mcp_tools.py tests/test_mcp_tools.py
git commit -m "feat: task/state + query/analysis tool handlers"
```

---

## Chunk 4: Tool registration, compat mode, CLI entry point

### Task 8: Add tool schema registration + agent-bridge compat aliases

**Files:**
- Modify: `lib/mcp_tools.py`
- Modify: `tests/test_mcp_tools.py`

- [ ] **Step 1: Write failing tests for tool registration**

Add to `tests/test_mcp_tools.py`:

```python
class TestToolRegistration:
    def setup_method(self):
        self.tmp = tempfile.mkdtemp()
        self.ctx = {"project_dir": self.tmp, "git_root": self.tmp, "config": {}}

    def test_register_all_tools(self):
        from lib.mcp import MCPServer
        from lib.mcp_tools import register_all_tools
        server = MCPServer("test", "0.1")
        register_all_tools(server, self.ctx)
        assert len(server._tools) == 23

    def test_register_with_compat(self):
        from lib.mcp import MCPServer
        from lib.mcp_tools import register_all_tools
        server = MCPServer("test", "0.1")
        register_all_tools(server, self.ctx, compat="agent-bridge")
        # 23 native + 14 aliases = 37
        assert len(server._tools) == 37

    def test_compat_alias_calls_same_handler(self):
        from lib.mcp import MCPServer
        from lib.mcp_tools import register_all_tools
        server = MCPServer("test", "0.1")
        register_all_tools(server, self.ctx, compat="agent-bridge")
        # Both names should resolve to same handler
        assert server._tools["store_knowledge"]["handler"] is server._tools["context_store_knowledge"]["handler"]
        assert server._tools["send_memo"]["handler"] is server._tools["context_send_memo"]["handler"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3.12 -m pytest tests/test_mcp_tools.py -v -k "Registration"`
Expected: FAIL — `ImportError: cannot import name 'register_all_tools'`

- [ ] **Step 3: Add `register_all_tools()` to `lib/mcp_tools.py`**

Add after `build_handlers()`:

```python
# ── Tool schemas ─────────────────────────────────────────────────────────────
# Each entry: (native_name, compat_alias_or_None, description, input_schema)

TOOL_SCHEMAS = [
    # Knowledge tools
    ("context_store_knowledge", "store_knowledge", "Store a knowledge entry with category, title, content, optional reasoning/tags/maturity",
     {"type": "object", "properties": {"category": {"type": "string", "enum": ["architectural-decision", "coding-convention", "failure-class", "reference", "rejected-approach"]}, "title": {"type": "string"}, "content": {"type": "string"}, "reasoning": {"type": "string"}, "maturity": {"type": "string", "enum": ["signal", "pattern", "decision", "convention"], "default": "decision"}, "bug_refs": {"type": "string"}, "file_refs": {"type": "string"}, "tags": {"type": "string"}}, "required": ["category", "title", "content"]}),
    ("context_search_knowledge", "search_knowledge", "FTS5 search over knowledge entries",
     {"type": "object", "properties": {"query": {"type": "string"}, "limit": {"type": "integer", "default": 10}}, "required": ["query"]}),
    ("context_get_knowledge", "get_knowledge", "Get a specific knowledge entry by exact title (active only)",
     {"type": "object", "properties": {"title": {"type": "string"}, "category": {"type": "string"}}, "required": ["title"]}),
    ("context_list_knowledge", "list_knowledge", "List knowledge entries, optionally filtered by category",
     {"type": "object", "properties": {"category": {"type": "string"}, "status": {"type": "string", "enum": ["active", "archived", "superseded", "dismissed"], "default": "active"}}}),
    ("context_promote_knowledge", None, "Advance maturity: signal -> pattern -> decision -> convention",
     {"type": "object", "properties": {"id": {"type": "integer"}}, "required": ["id"]}),
    ("context_archive_knowledge", None, "Archive a knowledge entry (soft delete)",
     {"type": "object", "properties": {"id": {"type": "integer"}}, "required": ["id"]}),
    ("context_restore_knowledge", None, "Restore an archived or dismissed entry to active",
     {"type": "object", "properties": {"id": {"type": "integer"}}, "required": ["id"]}),
    ("context_supersede_knowledge", None, "Replace a knowledge entry with a new one, preserving lineage",
     {"type": "object", "properties": {"old_id": {"type": "integer"}, "category": {"type": "string", "enum": ["architectural-decision", "coding-convention", "failure-class", "reference", "rejected-approach"]}, "title": {"type": "string"}, "content": {"type": "string"}, "reasoning": {"type": "string"}}, "required": ["old_id", "category", "title", "content"]}),
    # Memo tools
    ("context_send_memo", "send_memo", "Send a memo to a specific agent",
     {"type": "object", "properties": {"from_agent": {"type": "string"}, "to_agent": {"type": "string"}, "subject": {"type": "string"}, "content": {"type": "string"}, "expires_at": {"type": "string"}}, "required": ["from_agent", "to_agent", "subject", "content"]}),
    ("context_check_memos", "check_memos", "List memos, optionally filtered to unread or by recipient",
     {"type": "object", "properties": {"unread_only": {"type": "boolean", "default": False}, "to_agent": {"type": "string"}}}),
    ("context_read_memo", "read_memo", "Read a memo and mark it as read",
     {"type": "object", "properties": {"id": {"type": "integer"}}, "required": ["id"]}),
    ("context_reply_memo", "reply_memo", "Reply to a memo (creates/continues thread)",
     {"type": "object", "properties": {"memo_id": {"type": "integer"}, "from_agent": {"type": "string"}, "content": {"type": "string"}}, "required": ["memo_id", "from_agent", "content"]}),
    ("context_broadcast", "broadcast", "Send a broadcast memo to all agents with priority",
     {"type": "object", "properties": {"from_agent": {"type": "string"}, "subject": {"type": "string"}, "content": {"type": "string"}, "priority": {"type": "string", "enum": ["normal", "high", "urgent"], "default": "normal"}}, "required": ["from_agent", "subject", "content"]}),
    ("context_list_threads", "list_threads", "List conversation threads with summary",
     {"type": "object", "properties": {"limit": {"type": "integer", "default": 20}}}),
    # Task & state tools
    ("context_handoff_task", "handoff_task", "Structured task handoff between agents",
     {"type": "object", "properties": {"from_agent": {"type": "string"}, "to_agent": {"type": "string"}, "title": {"type": "string"}, "description": {"type": "string"}, "relevant_files": {"type": "string"}, "context": {"type": "string"}, "blockers": {"type": "string"}, "priority": {"type": "string", "enum": ["low", "normal", "high", "urgent"], "default": "normal"}}, "required": ["from_agent", "to_agent", "title", "description"]}),
    ("context_set_shared_state", "set_shared_state", "Set key-value state visible to all agents",
     {"type": "object", "properties": {"key": {"type": "string"}, "value": {"type": "string"}, "updated_by": {"type": "string"}}, "required": ["key", "value", "updated_by"]}),
    ("context_get_shared_state", "get_shared_state", "Get shared state by key or all state",
     {"type": "object", "properties": {"key": {"type": "string"}}}),
    # Query & analysis tools
    ("context_query_commits", None, "Search indexed commits by term, tag, file, or list recent/bugs/stats",
     {"type": "object", "properties": {"mode": {"type": "string", "enum": ["search", "tag", "file", "bugs", "related", "recent", "stats"]}, "term": {"type": "string"}, "limit": {"type": "integer", "default": 20}}, "required": ["mode"]}),
    ("context_check_parity", None, "Show parallel path alerts (solo edits without companion)",
     {"type": "object", "properties": {}}),
    ("context_run_xref", None, "Cross-reference report across all memory layers",
     {"type": "object", "properties": {}}),
    ("context_get_health", None, "Session health summary",
     {"type": "object", "properties": {}}),
    ("context_get_profile", None, "Regenerate and return auto-discovered file pair patterns",
     {"type": "object", "properties": {"days": {"type": "integer", "default": 30}}}),
    ("context_get_project_context", "get_context_for_project", "Composite: health + unread memos + recent knowledge",
     {"type": "object", "properties": {"include_health": {"type": "boolean", "default": True}, "include_memos": {"type": "boolean", "default": True}, "include_knowledge": {"type": "boolean", "default": True}, "knowledge_limit": {"type": "integer", "default": 10}}}),
]


def register_all_tools(server, ctx, compat=None):
    """Register all tools on an MCPServer. If compat='agent-bridge', also register aliases."""
    handlers = build_handlers(ctx)

    for native_name, alias, description, schema in TOOL_SCHEMAS:
        handler = handlers[native_name]
        server.register_tool(
            name=native_name, description=description,
            input_schema=schema, handler=handler,
        )
        if compat == "agent-bridge" and alias:
            server.register_tool(
                name=alias, description=description,
                input_schema=schema, handler=handler,
            )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3.12 -m pytest tests/test_mcp_tools.py -v -k "Registration"`
Expected: All 3 tests PASS

- [ ] **Step 5: Commit**

```bash
git add lib/mcp_tools.py tests/test_mcp_tools.py
git commit -m "feat: tool schema registration with agent-bridge compat aliases"
```

---

### Task 9: Add `__main__` block + CLI entry point

**Files:**
- Modify: `lib/mcp_tools.py` (add `__main__` block)
- Modify: `bin/context-hooks` (add `mcp)` case)

- [ ] **Step 1: Add `__main__` block to `lib/mcp_tools.py`**

Add at end of file:

```python
# ── CLI entry point ──────────────────────────────────────────────────────────

def main():
    """Start the MCP server. Called by: bin/context-hooks mcp [flags]"""
    import argparse
    from lib.db import data_dir, resolve_git_root
    from lib.config import load_config
    from lib.mcp import MCPServer

    parser = argparse.ArgumentParser(description="context-hooks MCP server")
    parser.add_argument("--compat", choices=["agent-bridge"], default=None,
                        help="Register compatibility aliases")
    parser.add_argument("--project", default=None,
                        help="Project directory (default: resolve from cwd)")
    args = parser.parse_args()

    if args.project:
        git_root = args.project
    else:
        git_root = resolve_git_root(os.getcwd())

    project_dir = data_dir(git_root)
    config = load_config(project_dir)

    ctx = {
        "project_dir": project_dir,
        "git_root": git_root,
        "config": config,
    }

    server = MCPServer("context-hooks", "0.2.0")
    register_all_tools(server, ctx, compat=args.compat)
    server.run()


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Add `mcp)` case to `bin/context-hooks`**

In `bin/context-hooks`, add before the `version)` case:

```bash
  mcp)        python3 "$SCRIPT_DIR/lib/mcp_tools.py" "$@" ;;
```

- [ ] **Step 3: Verify CLI help works**

Run: `bin/context-hooks help`
Expected: Output includes existing commands (should not crash)

- [ ] **Step 4: Verify MCP entry point parses args**

Run: `bin/context-hooks mcp --help`
Expected: Shows argparse help with `--compat` and `--project` options

- [ ] **Step 5: Run full test suite**

Run: `python3.12 -m pytest tests/ -v`
Expected: All tests PASS

- [ ] **Step 6: Commit**

```bash
git add lib/mcp_tools.py bin/context-hooks
git commit -m "feat: MCP server CLI entry point with --compat and --project flags"
```

---

### Task 10: Update CLI help text

**Files:**
- Modify: `bin/context-hooks`

- [ ] **Step 1: Add MCP to help text**

In `bin/context-hooks`, update the help heredoc to include:

```
MCP:       mcp [--compat=agent-bridge] [--project=/path]
```

Add this line after the `Maintain:` line.

- [ ] **Step 2: Verify**

Run: `bin/context-hooks help`
Expected: Output includes the new MCP line

- [ ] **Step 3: Commit**

```bash
git add bin/context-hooks
git commit -m "docs: add MCP command to CLI help text"
```

---

## Chunk 5: Final verification

### Task 11: Full integration test + verify all 146+ tests pass

- [ ] **Step 1: Run entire test suite**

Run: `python3.12 -m pytest tests/ -v`
Expected: All tests PASS (original 146 + new tests)

- [ ] **Step 2: Run CLI smoke tests**

```bash
bin/context-hooks help
bin/context-hooks status
bin/context-hooks mcp --help
```

Expected: All three commands succeed without errors

- [ ] **Step 3: Verify tool count**

Run: `python3.12 -c "
import tempfile, sys, os
sys.path.insert(0, '.')
from lib.mcp import MCPServer
from lib.mcp_tools import register_all_tools
from lib.db import data_dir
tmp = tempfile.mkdtemp()
ctx = {'project_dir': tmp, 'git_root': tmp, 'config': {}}
s = MCPServer('test', '0.1')
register_all_tools(s, ctx)
print(f'Native tools: {len(s._tools)}')
s2 = MCPServer('test', '0.1')
register_all_tools(s2, ctx, compat='agent-bridge')
print(f'With compat: {len(s2._tools)}')
"`
Expected: `Native tools: 23` and `With compat: 37`

- [ ] **Step 4: Final commit if any loose changes**

```bash
git status
# If clean, skip. Otherwise stage and commit.
```
