import os, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from lib.db import ContextDB
from lib.knowledge import store, search, list_entries, promote, archive, restore, dismiss, supersede, send_memo, list_memos, read_memo

class TestKnowledgeStore:
    def setup_method(self):
        self.tmp = tempfile.mkdtemp()
        self.db = ContextDB(self.tmp)

    def teardown_method(self):
        self.db.close()

    def test_store_creates_entry(self):
        store(self.db, "architectural-decision", "Use SQLite", "We chose SQLite for zero deps.")
        entries = list_entries(self.db)
        assert len(entries) == 1
        assert entries[0]["title"] == "Use SQLite"
        assert entries[0]["maturity"] == "decision"

    def test_store_with_bug_refs(self):
        store(self.db, "failure-class", "Stack depth assumed", "Content", bug_refs="BUG-037,BUG-038")
        entries = list_entries(self.db, category="failure-class")
        assert entries[0]["bug_refs"] == "BUG-037,BUG-038"

    def test_search_fts(self):
        store(self.db, "coding-convention", "Parameterized queries", "All SQL writes must use ? placeholders to prevent injection.")
        results = search(self.db, "injection")
        assert len(results) >= 1
        assert "Parameterized" in results[0]["title"]

    def test_search_empty(self):
        results = search(self.db, "nonexistent term xyz123")
        assert len(results) == 0

    def test_list_by_category(self):
        store(self.db, "architectural-decision", "A", "content")
        store(self.db, "failure-class", "B", "content")
        decisions = list_entries(self.db, category="architectural-decision")
        assert len(decisions) == 1
        assert decisions[0]["title"] == "A"

    def test_promote_lifecycle(self):
        store(self.db, "coding-convention", "Test rule", "content")
        # Get the id
        entries = list_entries(self.db)
        eid = entries[0]["id"]
        # decision is the starting maturity for explicit stores
        assert entries[0]["maturity"] == "decision"
        # Promote to convention
        promote(self.db, eid)
        entries = list_entries(self.db)
        assert entries[0]["maturity"] == "convention"

    def test_promote_convention_errors(self):
        store(self.db, "coding-convention", "Max rule", "content")
        entries = list_entries(self.db)
        eid = entries[0]["id"]
        promote(self.db, eid)  # decision → convention
        # Convention → ??? should raise
        try:
            promote(self.db, eid)
            assert False, "Should have raised"
        except ValueError as e:
            assert "maximum maturity" in str(e).lower()

    def test_archive_and_restore(self):
        store(self.db, "reference", "Old doc", "content")
        entries = list_entries(self.db)
        eid = entries[0]["id"]
        archive(self.db, eid)
        # Should not appear in active list
        assert len(list_entries(self.db)) == 0
        # Restore
        restore(self.db, eid)
        assert len(list_entries(self.db)) == 1

    def test_dismiss(self):
        store(self.db, "reference", "Noise", "content")
        entries = list_entries(self.db)
        eid = entries[0]["id"]
        dismiss(self.db, eid)
        assert len(list_entries(self.db)) == 0
        # Check it's dismissed, not just archived
        rows = self.db.query("SELECT status FROM knowledge WHERE id = ?", (eid,))
        assert rows[0][0] == "dismissed"

    def test_supersede(self):
        store(self.db, "architectural-decision", "Use React", "V1 choice")
        entries = list_entries(self.db)
        old_id = entries[0]["id"]
        supersede(self.db, old_id, "architectural-decision", "Use React", "V2 choice with SSR", "Better performance")
        entries = list_entries(self.db)
        assert len(entries) == 1  # only the new active one
        assert "V2" in entries[0]["content"]
        # Old entry should be superseded
        old = self.db.query("SELECT status, superseded_by FROM knowledge WHERE id = ?", (old_id,))
        assert old[0][0] == "superseded"

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


class TestMemos:
    def setup_method(self):
        self.tmp = tempfile.mkdtemp()
        self.db = ContextDB(self.tmp)

    def teardown_method(self):
        self.db.close()

    def test_send_and_list(self):
        send_memo(self.db, "session-1", "Remember this", "Deploy needs cache bust")
        memos = list_memos(self.db)
        assert len(memos) == 1
        assert memos[0]["subject"] == "Remember this"
        assert memos[0]["read"] == 0

    def test_unread_filter(self):
        send_memo(self.db, "s1", "Unread", "content")
        send_memo(self.db, "s2", "Also unread", "content")
        # Mark one as read
        memos = list_memos(self.db)
        read_memo(self.db, memos[0]["id"])
        unread = list_memos(self.db, unread_only=True)
        assert len(unread) == 1
        assert unread[0]["subject"] == "Also unread"

    def test_read_returns_content(self):
        send_memo(self.db, "s1", "Test", "The actual content here")
        memos = list_memos(self.db)
        content = read_memo(self.db, memos[0]["id"])
        assert content["content"] == "The actual content here"
        # Should be marked read now
        updated = self.db.query("SELECT read FROM memos WHERE id = ?", (memos[0]["id"],))
        assert updated[0][0] == 1
