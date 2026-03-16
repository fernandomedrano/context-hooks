import os, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from lib.db import ContextDB
from lib.health import health_summary, prune


class TestHealthSummary:
    def setup_method(self):
        self.tmp = tempfile.mkdtemp()
        self.db = ContextDB(self.tmp)

    def teardown_method(self):
        self.db.close()

    def test_returns_none_when_healthy(self):
        """No issues = no summary."""
        result = health_summary(self.db, "/p", self.tmp, {})
        assert result is None

    def test_shows_unread_memos(self):
        self.db.insert_memo(from_agent="s1", subject="Test", content="content")
        result = health_summary(self.db, "/p", self.tmp, {})
        assert result is not None
        assert "memo" in result.lower()

    def test_shows_bug_gaps(self):
        self.db.insert_commit(
            session_id="s1", commit_date="2026-03-15", hash="e"*40, short_hash="eeeeeee",
            author="t@t.com", subject="fix: BUG-999", body="",
            files_changed="x.py", tags="fix,BUG-999", project_dir="/p"
        )
        result = health_summary(self.db, "/p", self.tmp, {})
        assert result is not None
        assert "BUG" in result

    def test_disabled_returns_none(self):
        self.db.insert_memo(from_agent="s1", subject="Test", content="c")
        result = health_summary(self.db, "/p", self.tmp, {"nudge.health-summary": False})
        assert result is None

    def test_shows_stale_rules(self):
        """Rules not validated in 60+ days should appear."""
        self.db.execute(
            "INSERT INTO rule_validations (rule_name, rule_hash, last_validated, "
            "match_count, first_seen, status) VALUES (?, ?, ?, ?, ?, 'active')",
            ("Schema sync", "abc123", "2025-01-01T00:00:00", 5, "2025-01-01T00:00:00"),
        )
        result = health_summary(self.db, "/p", self.tmp, {})
        assert result is not None
        assert "Schema sync" in result

    def test_multiple_issues(self):
        """Should report all issues found."""
        self.db.insert_memo(from_agent="s1", subject="Test", content="c")
        self.db.insert_commit(
            session_id="s1", commit_date="2026-03-15", hash="f"*40, short_hash="fffffff",
            author="t@t.com", subject="fix: BUG-001", body="",
            files_changed="x.py", tags="fix,BUG-001", project_dir="/p"
        )
        result = health_summary(self.db, "/p", self.tmp, {})
        assert result is not None
        assert "memo" in result.lower()
        assert "BUG" in result


class TestPrune:
    def setup_method(self):
        self.tmp = tempfile.mkdtemp()
        self.db = ContextDB(self.tmp)

    def teardown_method(self):
        self.db.close()

    def test_dry_run_does_not_modify(self):
        self.db.insert_memo(from_agent="s1", subject="Old", content="c")
        # Mark as read and backdate
        self.db.execute("UPDATE memos SET read = 1, created_at = '2025-01-01T00:00:00'")
        report = prune(self.db, "/p", self.tmp, dry_run=True)
        assert "memo" in report.lower() or "prune" in report.lower()
        # Should still exist
        count = self.db.query("SELECT COUNT(*) FROM memos")[0][0]
        assert count == 1

    def test_prune_deletes_old_read_memos(self):
        self.db.insert_memo(from_agent="s1", subject="Old", content="c")
        self.db.execute("UPDATE memos SET read = 1, created_at = '2025-01-01T00:00:00'")
        prune(self.db, "/p", self.tmp, dry_run=False)
        count = self.db.query("SELECT COUNT(*) FROM memos")[0][0]
        assert count == 0

    def test_prune_keeps_unread_memos(self):
        """Unread memos should NOT be deleted even if old."""
        self.db.insert_memo(from_agent="s1", subject="Important", content="c")
        self.db.execute("UPDATE memos SET created_at = '2025-01-01T00:00:00'")
        prune(self.db, "/p", self.tmp, dry_run=False)
        count = self.db.query("SELECT COUNT(*) FROM memos")[0][0]
        assert count == 1

    def test_prune_marks_stale_rules(self):
        """Rules not validated in 60+ days should be marked stale."""
        self.db.execute(
            "INSERT INTO rule_validations (rule_name, rule_hash, last_validated, "
            "match_count, first_seen, status) VALUES (?, ?, ?, ?, ?, 'active')",
            ("Old rule", "hash1", "2025-01-01T00:00:00", 3, "2025-01-01T00:00:00"),
        )
        prune(self.db, "/p", self.tmp, dry_run=False)
        rows = self.db.query("SELECT status FROM rule_validations WHERE rule_hash = 'hash1'")
        assert rows[0][0] == "stale"

    def test_prune_report_always_returns_string(self):
        """Even with empty DB, prune should return a report string."""
        report = prune(self.db, "/p", self.tmp, dry_run=True)
        assert isinstance(report, str)
        assert "PRUNE REPORT" in report

    def test_prune_keeps_recent_read_memos(self):
        """Recently read memos (< 90 days) should be kept."""
        self.db.insert_memo(from_agent="s1", subject="Recent", content="c")
        self.db.execute("UPDATE memos SET read = 1")
        # created_at is recent (just now), so should not be pruned
        prune(self.db, "/p", self.tmp, dry_run=False)
        count = self.db.query("SELECT COUNT(*) FROM memos")[0][0]
        assert count == 1
