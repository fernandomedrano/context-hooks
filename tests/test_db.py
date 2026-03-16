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


class TestSchemaMigration:
    """Tests for automatic schema migration when opening older DBs."""

    def _create_v1_db(self, tmp_dir):
        """Create a DB with v1 schema (no priority column, no shared_state table)."""
        import sqlite3
        db_path = os.path.join(tmp_dir, "context.db")
        conn = sqlite3.connect(db_path)
        conn.executescript("""
            CREATE TABLE events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                timestamp TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%S','now','localtime')),
                category TEXT NOT NULL,
                event_type TEXT NOT NULL,
                priority INTEGER NOT NULL DEFAULT 3,
                data TEXT NOT NULL,
                project_dir TEXT NOT NULL
            );
            CREATE TABLE commits (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                timestamp TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%S','now','localtime')),
                commit_date TEXT,
                hash TEXT NOT NULL UNIQUE,
                short_hash TEXT NOT NULL,
                author TEXT,
                subject TEXT NOT NULL,
                body TEXT,
                files_changed TEXT,
                tags TEXT,
                project_dir TEXT NOT NULL
            );
            CREATE TABLE knowledge (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                category TEXT NOT NULL,
                maturity TEXT DEFAULT 'signal',
                title TEXT NOT NULL,
                content TEXT NOT NULL,
                reasoning TEXT,
                status TEXT DEFAULT 'active',
                superseded_by INTEGER,
                bug_refs TEXT,
                file_refs TEXT,
                commit_refs TEXT,
                tags TEXT,
                evidence_count INTEGER DEFAULT 0,
                last_validated TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(title, status)
            );
            CREATE TABLE memos (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                from_agent TEXT NOT NULL,
                to_agent TEXT DEFAULT '*',
                subject TEXT NOT NULL,
                content TEXT NOT NULL,
                thread_id TEXT,
                created_at TEXT NOT NULL,
                read INTEGER DEFAULT 0,
                expires_at TEXT
            );
            CREATE TABLE rule_validations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                rule_name TEXT NOT NULL,
                rule_hash TEXT NOT NULL UNIQUE,
                last_validated TEXT,
                match_count INTEGER DEFAULT 0,
                first_seen TEXT NOT NULL,
                status TEXT DEFAULT 'active'
            );
            CREATE VIRTUAL TABLE knowledge_fts USING fts5(
                title, content, reasoning,
                content=knowledge, content_rowid=id
            );
        """)
        # Insert a memo without priority column to verify data survives migration
        conn.execute(
            "INSERT INTO memos (from_agent, to_agent, subject, content, created_at, read) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            ("agent-1", "*", "Old memo", "Pre-migration content", "2026-01-01T00:00:00", 0)
        )
        conn.commit()
        conn.close()

    def test_v1_db_gets_priority_column(self):
        """Opening a v1 DB should add the priority column to memos."""
        tmp = tempfile.mkdtemp()
        self._create_v1_db(tmp)
        db = ContextDB(tmp)
        # priority column should exist and default to 'normal'
        rows = db.query("SELECT priority FROM memos")
        assert rows[0][0] == "normal"
        db.close()

    def test_v1_db_gets_shared_state_table(self):
        """Opening a v1 DB should create the shared_state table."""
        tmp = tempfile.mkdtemp()
        self._create_v1_db(tmp)
        db = ContextDB(tmp)
        tables = db.query("SELECT name FROM sqlite_master WHERE type='table' AND name='shared_state'")
        assert len(tables) == 1
        db.close()

    def test_v1_memo_data_survives_migration(self):
        """Existing memo data should be intact after migration."""
        tmp = tempfile.mkdtemp()
        self._create_v1_db(tmp)
        db = ContextDB(tmp)
        rows = db.query("SELECT from_agent, subject, content FROM memos")
        assert len(rows) == 1
        assert rows[0] == ("agent-1", "Old memo", "Pre-migration content")
        db.close()

    def test_v1_db_can_insert_memo_with_priority_after_migration(self):
        """After migrating a v1 DB, insert_memo with priority should work."""
        tmp = tempfile.mkdtemp()
        self._create_v1_db(tmp)
        db = ContextDB(tmp)
        db.insert_memo(from_agent="agent-2", subject="New", content="Post-migration", priority="urgent")
        rows = db.query("SELECT subject, priority FROM memos WHERE from_agent='agent-2'")
        assert rows[0] == ("New", "urgent")
        db.close()

    def test_schema_version_tracked(self):
        """Fresh DB should have a schema_version table with current version."""
        tmp = tempfile.mkdtemp()
        db = ContextDB(tmp)
        rows = db.query("SELECT version FROM schema_version")
        assert len(rows) == 1
        assert rows[0][0] >= 2  # current version is at least 2
        db.close()

    def test_migration_is_idempotent(self):
        """Opening the same DB twice should not fail or change data."""
        tmp = tempfile.mkdtemp()
        self._create_v1_db(tmp)
        db1 = ContextDB(tmp)
        db1.insert_memo(from_agent="test", subject="Idem", content="Check")
        db1.close()
        db2 = ContextDB(tmp)
        rows = db2.query("SELECT COUNT(*) FROM memos")
        assert rows[0][0] == 2  # original + new
        db2.close()

    def test_fresh_db_no_migration_needed(self):
        """A fresh DB should start at current version without errors."""
        tmp = tempfile.mkdtemp()
        db = ContextDB(tmp)
        version = db.query("SELECT version FROM schema_version")[0][0]
        assert version >= 2
        db.close()


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
