"""Tests for smart context surfacing (session briefing, file intel, test intel)."""
import os
import tempfile
import pytest
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lib.db import ContextDB
from lib.context_briefing import session_briefing, file_briefing, check_testrun_briefing


@pytest.fixture
def tmp_dir():
    return tempfile.mkdtemp()


@pytest.fixture
def db(tmp_dir):
    d = ContextDB(tmp_dir)
    yield d
    d.close()


def _insert_knowledge(db, title, category="failure-class", file_refs=None, recent=True):
    ts = "2026-03-18T00:00:00" if recent else "2025-01-01T00:00:00"
    db.execute(
        "INSERT INTO knowledge (category, title, content, file_refs, "
        "status, created_at, updated_at) VALUES (?, ?, ?, ?, 'active', ?, ?)",
        (category, title, "content", file_refs, ts, ts),
    )


def _insert_bug_commit(db, basename, bug_tag="BUG-050", n=0):
    db.execute(
        "INSERT INTO commits (session_id, hash, short_hash, subject, "
        "files_changed, tags, project_dir) VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("s1", f"{n:040d}", f"z{n:06d}", f"fix: bug {n}", basename, bug_tag, "/tmp"),
    )


def _insert_error_event(db, error_data):
    db.execute(
        "INSERT INTO events (session_id, category, event_type, priority, data, project_dir) "
        "VALUES (?, 'error', 'error_bash', 1, ?, '/tmp')",
        ("s1", error_data),
    )


# ── Session briefing tests ───────────────────────────────────────────────────

class TestSessionBriefing:
    def test_returns_empty_for_empty_db(self, db, tmp_dir):
        lines = session_briefing(db, db, tmp_dir, {})
        # No profile, no knowledge, no errors — might be empty
        assert isinstance(lines, list)

    def test_surfaces_recent_knowledge(self, db, tmp_dir):
        _insert_knowledge(db, "Race condition in queue", recent=True)
        _insert_knowledge(db, "Auth token expiry", recent=True)
        lines = session_briefing(db, db, tmp_dir, {})
        knowledge_lines = [l for l in lines if "Recent knowledge" in l]
        assert len(knowledge_lines) == 1
        assert "Race condition" in knowledge_lines[0]

    def test_surfaces_recent_errors(self, db, tmp_dir):
        _insert_error_event(db, "pytest tests/ -v\n---\nAssertionError")
        lines = session_briefing(db, db, tmp_dir, {})
        error_lines = [l for l in lines if "Recent errors" in l]
        assert len(error_lines) == 1
        assert "pytest" in error_lines[0]

    def test_no_old_knowledge(self, db, tmp_dir):
        _insert_knowledge(db, "Old entry", recent=False)
        lines = session_briefing(db, db, tmp_dir, {})
        knowledge_lines = [l for l in lines if "Recent knowledge" in l]
        assert len(knowledge_lines) == 0


# ── File briefing tests ─────────────────────────────────────────────────────

class TestFileBriefing:
    def test_surfaces_bug_history(self, db):
        _insert_bug_commit(db, "handler.py", "BUG-050", n=1)
        _insert_bug_commit(db, "handler.py", "BUG-051", n=2)
        cache = {}
        lines = file_briefing("src/handler.py", db, None, cache, "sess1")
        assert len(lines) >= 1
        assert any("bug-fix" in l.lower() or "BUG-" in l for l in lines)

    def test_surfaces_knowledge_refs(self, db):
        _insert_knowledge(db, "Handler flush rule", file_refs="handler.py")
        cache = {}
        lines = file_briefing("src/handler.py", db, None, cache, "sess1")
        assert any("Handler flush rule" in l for l in lines)

    def test_surfaces_parity_companion(self, db):
        profile = {
            "parallel_paths": [
                {"files": ["src/pipeline.py", "src/chat.py"], "name": "p+c", "co_pct": 72}
            ]
        }
        cache = {}
        lines = file_briefing("src/pipeline.py", db, profile, cache, "sess1")
        assert any("chat.py" in l for l in lines)

    def test_dedup_across_calls(self, db):
        _insert_knowledge(db, "rule", file_refs="handler.py")
        cache = {}
        l1 = file_briefing("src/handler.py", db, None, cache, "s1")
        l2 = file_briefing("src/handler.py", db, None, cache, "s1")
        assert len(l1) >= 1
        assert len(l2) == 0  # Deduped

    def test_no_fire_for_unrelated_file(self, db):
        _insert_knowledge(db, "rule", file_refs="other.py")
        cache = {}
        lines = file_briefing("src/handler.py", db, None, cache, "sess1")
        assert len(lines) == 0

    def test_no_bug_history_below_threshold(self, db):
        _insert_bug_commit(db, "handler.py", "BUG-050", n=1)
        # Only 1 bug commit — threshold is 2
        cache = {}
        lines = file_briefing("src/handler.py", db, None, cache, "sess1")
        bug_lines = [l for l in lines if "bug" in l.lower()]
        assert len(bug_lines) == 0


# ── Test briefing tests ──────────────────────────────────────────────────────

class TestTestBriefing:
    def test_surfaces_failure_class_knowledge(self, db):
        _insert_knowledge(db, "Race condition", category="failure-class")
        cache = {}
        lines = check_testrun_briefing("pytest tests/ -v", db, cache, "sess1")
        assert len(lines) >= 1
        assert any("failure-class" in l for l in lines)

    def test_fires_once_per_session(self, db):
        _insert_knowledge(db, "Race condition", category="failure-class")
        cache = {}
        l1 = check_testrun_briefing("pytest tests/", db, cache, "s1")
        l2 = check_testrun_briefing("pytest tests/", db, cache, "s1")
        assert len(l1) >= 1
        assert len(l2) == 0  # Deduped

    def test_no_fire_without_failure_class(self, db):
        _insert_knowledge(db, "Some reference", category="reference")
        cache = {}
        lines = check_testrun_briefing("pytest tests/", db, cache, "sess1")
        assert len(lines) == 0

    def test_empty_db(self, db):
        cache = {}
        lines = check_testrun_briefing("pytest tests/", db, cache, "sess1")
        assert lines == []
