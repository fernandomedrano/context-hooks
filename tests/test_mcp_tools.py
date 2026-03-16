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
