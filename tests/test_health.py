import os, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from lib.db import ContextDB
from lib.health import health_summary, format_health_text, prune, _check_infrastructure


class TestHealthSummary:
    def setup_method(self):
        self.tmp = tempfile.mkdtemp()
        self.db = ContextDB(self.tmp)

    def teardown_method(self):
        self.db.close()

    def test_returns_none_when_healthy(self):
        """No issues = no summary (with events and commits present)."""
        self.db.insert_event(
            session_id="s1", category="tool_use", event_type="Read",
            priority=3, data="{}", project_dir="/p"
        )
        self.db.insert_commit(
            session_id="s1", commit_date="2026-03-15", hash="a"*40, short_hash="aaaaaaa",
            author="t@t.com", subject="feat: test", body="",
            files_changed="x.py", tags="feat", project_dir="/p"
        )
        result = health_summary(self.db, self.db, "/p", self.tmp, {})
        assert result is None

    def test_returns_dict_with_critical_and_warnings(self):
        """Return value should be a dict with critical and warnings keys."""
        self.db.insert_memo(from_agent="s1", subject="Test", content="content")
        result = health_summary(self.db, self.db, "/p", self.tmp, {})
        assert isinstance(result, dict)
        assert "critical" in result
        assert "warnings" in result

    def test_shows_unread_memos_as_warning(self):
        self.db.insert_memo(from_agent="s1", subject="Test", content="content")
        result = health_summary(self.db, self.db, "/p", self.tmp, {})
        assert result is not None
        text = format_health_text(result)
        assert "memo" in text.lower()
        # Memos are warnings, not critical
        assert any("memo" in w.lower() for w in result["warnings"])

    def test_shows_bug_gaps_as_warning(self):
        self.db.insert_commit(
            session_id="s1", commit_date="2026-03-15", hash="e"*40, short_hash="eeeeeee",
            author="t@t.com", subject="fix: BUG-999", body="",
            files_changed="x.py", tags="fix,BUG-999", project_dir="/p"
        )
        result = health_summary(self.db, self.db, "/p", self.tmp, {})
        assert result is not None
        text = format_health_text(result)
        assert "BUG" in text

    def test_disabled_returns_none(self):
        self.db.insert_memo(from_agent="s1", subject="Test", content="c")
        result = health_summary(self.db, self.db, "/p", self.tmp, {"nudge.health-summary": False})
        assert result is None

    def test_shows_stale_rules(self):
        """Rules not validated in 60+ days should appear."""
        self.db.execute(
            "INSERT INTO rule_validations (rule_name, rule_hash, last_validated, "
            "match_count, first_seen, status) VALUES (?, ?, ?, ?, ?, 'active')",
            ("Schema sync", "abc123", "2025-01-01T00:00:00", 5, "2025-01-01T00:00:00"),
        )
        result = health_summary(self.db, self.db, "/p", self.tmp, {})
        assert result is not None
        text = format_health_text(result)
        assert "Schema sync" in text

    def test_multiple_issues(self):
        """Should report all issues found."""
        self.db.insert_memo(from_agent="s1", subject="Test", content="c")
        self.db.insert_commit(
            session_id="s1", commit_date="2026-03-15", hash="f"*40, short_hash="fffffff",
            author="t@t.com", subject="fix: BUG-001", body="",
            files_changed="x.py", tags="fix,BUG-001", project_dir="/p"
        )
        result = health_summary(self.db, self.db, "/p", self.tmp, {})
        assert result is not None
        text = format_health_text(result)
        assert "memo" in text.lower()
        assert "BUG" in text


class TestInfrastructureChecks:
    """Tests for the new infrastructure health checks (memo #32)."""

    def setup_method(self):
        self.tmp = tempfile.mkdtemp()
        self.db = ContextDB(self.tmp)

    def teardown_method(self):
        self.db.close()

    def test_zero_events_is_critical(self):
        """Zero events total = hooks never fired."""
        issues = _check_infrastructure(self.db)
        assert any("ZERO events" in i for i in issues)

    def test_zero_commits_is_critical(self):
        """Zero commits = bootstrap never ran."""
        issues = _check_infrastructure(self.db)
        assert any("Commit index is empty" in i for i in issues)

    def test_healthy_infra_returns_empty(self):
        """Events + commits present = no issues."""
        self.db.insert_event(
            session_id="s1", category="tool_use", event_type="Read",
            priority=3, data="{}", project_dir="/p"
        )
        self.db.insert_commit(
            session_id="s1", commit_date="2026-03-15", hash="a"*40, short_hash="aaaaaaa",
            author="t@t.com", subject="feat: test", body="",
            files_changed="x.py", tags="feat", project_dir="/p"
        )
        issues = _check_infrastructure(self.db)
        assert len(issues) == 0

    def test_old_events_no_recent_is_critical(self):
        """Events exist but none in 24h = hooks stopped."""
        self.db.execute(
            "INSERT INTO events (session_id, category, event_type, priority, data, project_dir, timestamp) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("s1", "tool_use", "Read", 3, "{}", "/p", "2025-01-01T00:00:00"),
        )
        self.db.insert_commit(
            session_id="s1", commit_date="2026-03-15", hash="b"*40, short_hash="bbbbbbb",
            author="t@t.com", subject="feat: test", body="",
            files_changed="x.py", tags="feat", project_dir="/p"
        )
        issues = _check_infrastructure(self.db)
        assert any("No events in last 24 hours" in i for i in issues)
        # Should NOT report zero events (we have old ones)
        assert not any("ZERO events" in i for i in issues)

    def test_infra_issues_are_critical_in_health_summary(self):
        """Infrastructure issues go into the critical list, not warnings."""
        result = health_summary(self.db, self.db, "/p", self.tmp, {})
        assert result is not None
        assert len(result["critical"]) > 0
        assert any("event" in c.lower() or "commit" in c.lower()
                    for c in result["critical"])

    def test_events_present_commits_missing_only_commits_critical(self):
        """If events fire but no commits, only commit issue is critical."""
        self.db.insert_event(
            session_id="s1", category="tool_use", event_type="Read",
            priority=3, data="{}", project_dir="/p"
        )
        issues = _check_infrastructure(self.db)
        assert any("Commit index is empty" in i for i in issues)
        assert not any("ZERO events" in i for i in issues)


class TestFormatHealthText:
    def test_formats_critical_and_warnings(self):
        report = {
            "critical": ["* Zero events"],
            "warnings": ["* 2 unread memo(s)"],
        }
        text = format_health_text(report)
        assert "Zero events" in text
        assert "unread memo" in text
        assert text.startswith("Context Hooks:")

    def test_formats_empty_sections(self):
        report = {"critical": [], "warnings": ["* 1 unread memo(s)"]}
        text = format_health_text(report)
        assert "unread memo" in text


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
        report = prune(self.db, self.db, "/p", self.tmp, dry_run=True)
        assert "memo" in report.lower() or "prune" in report.lower()
        # Should still exist
        count = self.db.query("SELECT COUNT(*) FROM memos")[0][0]
        assert count == 1

    def test_prune_deletes_old_read_memos(self):
        self.db.insert_memo(from_agent="s1", subject="Old", content="c")
        self.db.execute("UPDATE memos SET read = 1, created_at = '2025-01-01T00:00:00'")
        prune(self.db, self.db, "/p", self.tmp, dry_run=False)
        count = self.db.query("SELECT COUNT(*) FROM memos")[0][0]
        assert count == 0

    def test_prune_keeps_unread_memos(self):
        """Unread memos should NOT be deleted even if old."""
        self.db.insert_memo(from_agent="s1", subject="Important", content="c")
        self.db.execute("UPDATE memos SET created_at = '2025-01-01T00:00:00'")
        prune(self.db, self.db, "/p", self.tmp, dry_run=False)
        count = self.db.query("SELECT COUNT(*) FROM memos")[0][0]
        assert count == 1

    def test_prune_marks_stale_rules(self):
        """Rules not validated in 60+ days should be marked stale."""
        self.db.execute(
            "INSERT INTO rule_validations (rule_name, rule_hash, last_validated, "
            "match_count, first_seen, status) VALUES (?, ?, ?, ?, ?, 'active')",
            ("Old rule", "hash1", "2025-01-01T00:00:00", 3, "2025-01-01T00:00:00"),
        )
        prune(self.db, self.db, "/p", self.tmp, dry_run=False)
        rows = self.db.query("SELECT status FROM rule_validations WHERE rule_hash = 'hash1'")
        assert rows[0][0] == "stale"

    def test_prune_report_always_returns_string(self):
        """Even with empty DB, prune should return a report string."""
        report = prune(self.db, self.db, "/p", self.tmp, dry_run=True)
        assert isinstance(report, str)
        assert "PRUNE REPORT" in report

    def test_prune_keeps_recent_read_memos(self):
        """Recently read memos (< 90 days) should be kept."""
        self.db.insert_memo(from_agent="s1", subject="Recent", content="c")
        self.db.execute("UPDATE memos SET read = 1")
        # created_at is recent (just now), so should not be pruned
        prune(self.db, self.db, "/p", self.tmp, dry_run=False)
        count = self.db.query("SELECT COUNT(*) FROM memos")[0][0]
        assert count == 1


class TestClusterHealthRouting:
    def test_health_summary_reads_memos_from_cluster_db(self):
        """health_summary should count unread memos from cluster_db."""
        master_root = tempfile.mkdtemp()
        from lib.db import data_dir
        master_dir = data_dir(master_root)
        cluster_db = ContextDB(master_dir)
        local_db = ContextDB(tempfile.mkdtemp())

        cluster_db.insert_memo(from_agent="x", subject="Unread", content="c")

        result = health_summary(local_db, cluster_db, "/fake/root", master_dir, {})
        assert result is not None
        text = format_health_text(result)
        assert "unread" in text.lower()
        local_db.close()
        cluster_db.close()

    def test_prune_deletes_memos_from_cluster_db(self):
        """prune should delete old memos from cluster_db, not local_db."""
        master_root = tempfile.mkdtemp()
        from lib.db import data_dir
        master_dir = data_dir(master_root)
        cluster_db = ContextDB(master_dir)
        local_db = ContextDB(tempfile.mkdtemp())

        cluster_db.execute(
            "INSERT INTO memos (from_agent, subject, content, created_at, read) VALUES (?,?,?,?,?)",
            ("x", "Old", "c", "2020-01-01T00:00:00", 1)
        )

        result = prune(local_db, cluster_db, "/fake/root", master_dir, dry_run=False)
        remaining = cluster_db.query("SELECT COUNT(*) FROM memos")
        assert remaining[0][0] == 0
        local_db.close()
        cluster_db.close()
