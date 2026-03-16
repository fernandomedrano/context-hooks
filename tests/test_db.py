import os
import tempfile
import pytest
import sys

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lib.db import ContextDB, project_hash


class TestProjectHash:
    def test_deterministic(self):
        assert project_hash("/Users/test/project") == project_hash("/Users/test/project")

    def test_different_paths(self):
        assert project_hash("/a") != project_hash("/b")

    def test_length(self):
        assert len(project_hash("/any/path")) == 12


class TestContextDB:
    def setup_method(self):
        self.tmp = tempfile.mkdtemp()
        self.db = ContextDB(self.tmp)

    def teardown_method(self):
        self.db.close()

    def test_creates_db_file(self):
        assert os.path.exists(os.path.join(self.tmp, "context.db"))

    def test_creates_all_tables(self):
        tables = self.db.query(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        )
        names = [r[0] for r in tables]
        for expected in ["events", "commits", "knowledge", "memos", "rule_validations"]:
            assert expected in names, f"Missing table: {expected}"

    def test_creates_fts_table(self):
        tables = self.db.query(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='knowledge_fts'"
        )
        assert len(tables) == 1

    def test_insert_event(self):
        self.db.insert_event(
            session_id="s1", category="file", event_type="file_edit",
            priority=1, data="/path/to/file.py", project_dir="/project"
        )
        rows = self.db.query("SELECT category, event_type, data FROM events")
        assert len(rows) == 1
        assert rows[0] == ("file", "file_edit", "/path/to/file.py")

    def test_insert_commit(self):
        self.db.insert_commit(
            session_id="s1", commit_date="2026-01-01T00:00:00",
            hash="a" * 40, short_hash="aaaaaaa", author="test@test.com",
            subject="fix: something", body="details", files_changed="a.py,b.py",
            tags="fix", project_dir="/p"
        )
        rows = self.db.query("SELECT hash, short_hash, subject, tags FROM commits")
        assert len(rows) == 1
        assert rows[0] == ("a" * 40, "aaaaaaa", "fix: something", "fix")

    def test_commit_dedup(self):
        for _ in range(3):
            self.db.insert_commit(
                session_id="s1", commit_date="2026-01-01",
                hash="b" * 40, short_hash="bbbbbbb", author="t@t.com",
                subject="dup", body="", files_changed="", tags="", project_dir="/p"
            )
        rows = self.db.query("SELECT COUNT(*) FROM commits")
        assert rows[0][0] == 1

    def test_sql_injection_safe(self):
        evil = "'); DROP TABLE commits; --"
        self.db.insert_commit(
            session_id="s1", commit_date="2026-01-01", hash="c" * 40,
            short_hash="ccccccc", author="t@t.com", subject=evil,
            body="", files_changed="", tags="", project_dir="/p"
        )
        rows = self.db.query("SELECT subject FROM commits")
        assert rows[0][0] == evil
        tables = self.db.query(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='commits'"
        )
        assert len(tables) == 1

    def test_fifo_eviction(self):
        for i in range(510):
            self.db.insert_event(
                session_id="s1", category="file", event_type="file_read",
                priority=3, data=f"/file{i}.py", project_dir="/p"
            )
        self.db.evict_events("s1", max_events=500)
        count = self.db.query("SELECT COUNT(*) FROM events WHERE session_id='s1'")[0][0]
        assert count == 500

    def test_insert_knowledge(self):
        self.db.insert_knowledge(
            category="architectural-decision", title="Use SQLite",
            content="We chose SQLite for zero deps.", reasoning="No server needed."
        )
        rows = self.db.query("SELECT title, maturity, status FROM knowledge")
        assert len(rows) == 1
        assert rows[0] == ("Use SQLite", "decision", "active")

    def test_knowledge_fts_search(self):
        self.db.insert_knowledge(
            category="coding-convention", title="Parameterized queries",
            content="All SQL writes must use ? placeholders to prevent injection.",
        )
        results = self.db.query(
            "SELECT title FROM knowledge_fts WHERE knowledge_fts MATCH ?", ("injection",)
        )
        assert len(results) == 1
        assert results[0][0] == "Parameterized queries"

    def test_insert_memo(self):
        self.db.insert_memo(
            from_agent="session-1", subject="Remember this",
            content="The deploy requires cache bust."
        )
        rows = self.db.query("SELECT subject, read FROM memos")
        assert len(rows) == 1
        assert rows[0] == ("Remember this", 0)

    def test_knowledge_unique_title_status(self):
        """Two active entries with same title should fail, but active + archived should work."""
        self.db.insert_knowledge(
            category="decision", title="Use React",
            content="Frontend framework choice."
        )
        # Same title, same status (active) should fail
        with pytest.raises(Exception):
            self.db.insert_knowledge(
                category="decision", title="Use React",
                content="Different content."
            )
        # Archive the first, then insert new active
        self.db.execute(
            "UPDATE knowledge SET status = 'archived' WHERE title = ?", ("Use React",)
        )
        self.db.insert_knowledge(
            category="decision", title="Use React",
            content="Updated framework choice."
        )
        rows = self.db.query("SELECT status FROM knowledge WHERE title = 'Use React' ORDER BY id")
        assert [r[0] for r in rows] == ["archived", "active"]

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


class TestConfig:
    def test_parse_simple_yaml(self):
        from lib.config import _parse_simple_yaml
        text = """
nudge.parity: true
nudge.flywheel: false
max_events: 500
project_name: my-project
tags:
  - fix
  - feat
"""
        result = _parse_simple_yaml(text)
        assert result["nudge.parity"] is True
        assert result["nudge.flywheel"] is False
        assert result["max_events"] == 500
        assert result["project_name"] == "my-project"
        assert result["tags"] == ["fix", "feat"]

    def test_load_config_missing_file(self):
        from lib.config import load_config
        config = load_config("/nonexistent/path")
        assert config == {}
