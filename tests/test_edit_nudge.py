"""Tests for edit-time proactive nudges."""
import json
import os
import tempfile
import pytest
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lib.db import ContextDB
from lib.edit_nudge import (
    check_edit_nudges,
    load_session_cache,
    save_session_cache,
    cleanup_session_cache,
    _check_parity,
    _check_bug_history,
    _check_knowledge_refs,
    _check_hotfile,
    _check_convention,
)


# ── Fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture
def tmp_dir():
    return tempfile.mkdtemp()


@pytest.fixture
def db(tmp_dir):
    d = ContextDB(tmp_dir)
    yield d
    d.close()


@pytest.fixture
def profile_with_parity():
    return {
        "version": 1,
        "parallel_paths": [
            {
                "files": ["src/pipeline.py", "src/chat_service.py"],
                "name": "pipeline+chat-service",
                "together": 15,
                "co_pct": 72,
            },
            {
                "files": ["src/low_pair_a.py", "src/low_pair_b.py"],
                "name": "low-pair",
                "together": 3,
                "co_pct": 30,  # Below 60% threshold
            },
        ],
        "hot_files": {
            "src/pipeline.py": "pipeline",
            "src/config.py": "config",
        },
        "directory_tags": {},
    }


# ── Session cache tests ─────────────────────────────────────────────────────

class TestSessionCache:
    def test_load_empty(self, tmp_dir):
        cache = load_session_cache(tmp_dir)
        assert cache == {}

    def test_save_and_load(self, tmp_dir):
        cache = {"sess1": ["parity:a:b", "bug:c"]}
        save_session_cache(tmp_dir, cache)
        loaded = load_session_cache(tmp_dir)
        assert loaded == cache

    def test_cleanup_keeps_current(self, tmp_dir):
        cache = {
            "old-session": ["parity:a:b"],
            "current": ["bug:x"],
        }
        save_session_cache(tmp_dir, cache)
        cleanup_session_cache(tmp_dir, "current")
        loaded = load_session_cache(tmp_dir)
        assert "current" in loaded
        assert "old-session" not in loaded

    def test_cleanup_removes_all_if_no_current(self, tmp_dir):
        cache = {"old1": ["a"], "old2": ["b"]}
        save_session_cache(tmp_dir, cache)
        cleanup_session_cache(tmp_dir, "new-session")
        loaded = load_session_cache(tmp_dir)
        assert loaded == {}

    def test_corrupt_json_returns_empty(self, tmp_dir):
        path = os.path.join(tmp_dir, "session_nudge_cache.json")
        with open(path, "w") as f:
            f.write("NOT JSON{{{")
        cache = load_session_cache(tmp_dir)
        assert cache == {}


# ── Parity nudge tests ──────────────────────────────────────────────────────

class TestParityNudge:
    def test_fires_on_solo_edit(self, profile_with_parity):
        cache = {}
        result = _check_parity(
            "src/pipeline.py", profile_with_parity, cache, "sess1"
        )
        assert result is not None
        assert "chat_service.py" in result
        assert "72%" in result

    def test_fires_on_companion_side(self, profile_with_parity):
        cache = {}
        result = _check_parity(
            "src/chat_service.py", profile_with_parity, cache, "sess1"
        )
        assert result is not None
        assert "pipeline.py" in result

    def test_no_fire_below_threshold(self, profile_with_parity):
        cache = {}
        result = _check_parity(
            "src/low_pair_a.py", profile_with_parity, cache, "sess1"
        )
        assert result is None

    def test_dedup_same_session(self, profile_with_parity):
        cache = {}
        r1 = _check_parity("src/pipeline.py", profile_with_parity, cache, "s1")
        r2 = _check_parity("src/pipeline.py", profile_with_parity, cache, "s1")
        assert r1 is not None
        assert r2 is None  # Deduped

    def test_fires_with_absolute_path(self, profile_with_parity):
        cache = {}
        result = _check_parity(
            "/home/user/project/src/pipeline.py",
            profile_with_parity, cache, "sess1"
        )
        assert result is not None
        assert "chat_service.py" in result

    def test_no_fire_unrelated_file(self, profile_with_parity):
        cache = {}
        result = _check_parity(
            "src/unrelated.py", profile_with_parity, cache, "sess1"
        )
        assert result is None

    def test_no_profile(self):
        cache = {}
        result = _check_parity("src/pipeline.py", {}, cache, "sess1")
        assert result is None


# ── Bug history nudge tests ──────────────────────────────────────────────────

class TestBugHistoryNudge:
    _hash_counter = 0

    def _insert_bug_commits(self, db, basename, count, bug_tag="BUG-041"):
        for i in range(count):
            TestBugHistoryNudge._hash_counter += 1
            n = TestBugHistoryNudge._hash_counter
            db.execute(
                "INSERT INTO commits (session_id, hash, short_hash, subject, "
                "files_changed, tags, project_dir) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    "s1", f"{n:040d}", f"x{n:06d}",
                    f"fix: bug {n}", basename, bug_tag, "/tmp"
                ),
            )

    def test_fires_with_bug_history(self, db):
        self._insert_bug_commits(db, "pipeline.py", 3)
        cache = {}
        result = _check_bug_history("src/pipeline.py", db, cache, "sess1")
        assert result is not None
        assert "BUG-041" in result
        assert "3 bug-fix" in result

    def test_no_fire_single_bug(self, db):
        self._insert_bug_commits(db, "pipeline.py", 1)
        cache = {}
        result = _check_bug_history("src/pipeline.py", db, cache, "sess1")
        assert result is None  # Threshold is 2

    def test_no_fire_no_bugs(self, db):
        cache = {}
        result = _check_bug_history("src/clean.py", db, cache, "sess1")
        assert result is None

    def test_dedup(self, db):
        self._insert_bug_commits(db, "pipeline.py", 3)
        cache = {}
        r1 = _check_bug_history("src/pipeline.py", db, cache, "s1")
        r2 = _check_bug_history("src/pipeline.py", db, cache, "s1")
        assert r1 is not None
        assert r2 is None

    def test_multiple_bug_refs(self, db):
        self._insert_bug_commits(db, "pipeline.py", 2, "BUG-041")
        self._insert_bug_commits(db, "pipeline.py", 2, "BUG-038")
        cache = {}
        result = _check_bug_history("src/pipeline.py", db, cache, "sess1")
        assert result is not None
        assert "BUG-038" in result
        assert "BUG-041" in result


# ── Knowledge refs nudge tests ───────────────────────────────────────────────

class TestKnowledgeRefsNudge:
    def _insert_knowledge(self, db, title, file_refs, category="failure-class"):
        now = "2026-03-18T00:00:00"
        db.execute(
            "INSERT INTO knowledge (category, title, content, file_refs, "
            "status, created_at, updated_at) VALUES (?, ?, ?, ?, 'active', ?, ?)",
            (category, title, "content", file_refs, now, now),
        )

    def test_fires_with_knowledge_ref(self, db):
        self._insert_knowledge(db, "streaming flush rule", "pipeline.py,chat.py")
        cache = {}
        result = _check_knowledge_refs("src/pipeline.py", db, cache, "sess1")
        assert result is not None
        assert "streaming flush rule" in result

    def test_no_fire_unrelated(self, db):
        self._insert_knowledge(db, "some rule", "other.py")
        cache = {}
        result = _check_knowledge_refs("src/pipeline.py", db, cache, "sess1")
        assert result is None

    def test_dedup(self, db):
        self._insert_knowledge(db, "rule", "pipeline.py")
        cache = {}
        r1 = _check_knowledge_refs("src/pipeline.py", db, cache, "s1")
        r2 = _check_knowledge_refs("src/pipeline.py", db, cache, "s1")
        assert r1 is not None
        assert r2 is None

    def test_ignores_archived(self, db):
        now = "2026-03-18T00:00:00"
        db.execute(
            "INSERT INTO knowledge (category, title, content, file_refs, "
            "status, created_at, updated_at) VALUES (?, ?, ?, ?, 'archived', ?, ?)",
            ("failure-class", "old rule", "content", "pipeline.py", now, now),
        )
        cache = {}
        result = _check_knowledge_refs("src/pipeline.py", db, cache, "sess1")
        assert result is None


# ── Hot file nudge tests ─────────────────────────────────────────────────────

class TestHotfileNudge:
    def test_fires_when_enabled(self, profile_with_parity):
        config = {"nudge.edit-hotfile": True}
        cache = {}
        result = _check_hotfile(
            "src/pipeline.py", profile_with_parity, config, cache, "sess1"
        )
        assert result is not None
        assert "pipeline" in result

    def test_no_fire_when_disabled(self, profile_with_parity):
        config = {}
        cache = {}
        result = _check_hotfile(
            "src/pipeline.py", profile_with_parity, config, cache, "sess1"
        )
        assert result is None

    def test_no_fire_non_hot_file(self, profile_with_parity):
        config = {"nudge.edit-hotfile": True}
        cache = {}
        result = _check_hotfile(
            "src/random.py", profile_with_parity, config, cache, "sess1"
        )
        assert result is None


# ── Convention nudge tests ───────────────────────────────────────────────────

class TestConventionNudge:
    def _insert_convention(self, db, title, file_refs):
        now = "2026-03-18T00:00:00"
        db.execute(
            "INSERT INTO knowledge (category, title, content, file_refs, "
            "status, created_at, updated_at) VALUES (?, ?, ?, ?, 'active', ?, ?)",
            ("coding-convention", title, "content", file_refs, now, now),
        )

    def test_fires_when_enabled(self, db):
        self._insert_convention(db, "always use dataclass", "models.py")
        config = {"nudge.edit-convention": True}
        cache = {}
        result = _check_convention("src/models.py", db, config, cache, "sess1")
        assert result is not None
        assert "always use dataclass" in result

    def test_no_fire_when_disabled(self, db):
        self._insert_convention(db, "rule", "models.py")
        config = {}
        cache = {}
        result = _check_convention("src/models.py", db, config, cache, "sess1")
        assert result is None


# ── Integration: check_edit_nudges ───────────────────────────────────────────

class TestCheckEditNudges:
    def test_returns_multiple_nudges(self, db, tmp_dir, profile_with_parity):
        # Insert bug history for pipeline.py
        for i in range(3):
            db.execute(
                "INSERT INTO commits (session_id, hash, short_hash, subject, "
                "files_changed, tags, project_dir) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                ("s1", f"{'b' * 39}{i}", f"bbb{i:04d}",
                 "fix: bug", "pipeline.py", "BUG-099", "/tmp"),
            )

        config = {}
        nudges = check_edit_nudges(
            file_path="src/pipeline.py",
            db=db,
            profile=profile_with_parity,
            config=config,
            project_data_dir=tmp_dir,
            session_id="sess1",
        )
        # Should get parity + bug history
        assert len(nudges) >= 2
        assert any("Parity" in n for n in nudges)
        assert any("Bug history" in n for n in nudges)

    def test_empty_when_no_matches(self, db, tmp_dir):
        config = {}
        nudges = check_edit_nudges(
            file_path="src/totally_new.py",
            db=db,
            profile=None,
            config=config,
            project_data_dir=tmp_dir,
            session_id="sess1",
        )
        assert nudges == []

    def test_dedup_across_calls(self, db, tmp_dir, profile_with_parity):
        config = {}
        n1 = check_edit_nudges(
            "src/pipeline.py", db, profile_with_parity,
            config, tmp_dir, "sess1",
        )
        n2 = check_edit_nudges(
            "src/pipeline.py", db, profile_with_parity,
            config, tmp_dir, "sess1",
        )
        assert len(n1) >= 1  # At least parity
        assert len(n2) == 0  # All deduped

    def test_no_profile_still_checks_db(self, db, tmp_dir):
        # Insert knowledge ref
        now = "2026-03-18T00:00:00"
        db.execute(
            "INSERT INTO knowledge (category, title, content, file_refs, "
            "status, created_at, updated_at) VALUES (?, ?, ?, ?, 'active', ?, ?)",
            ("failure-class", "flush rule", "content", "handler.py", now, now),
        )
        config = {}
        nudges = check_edit_nudges(
            "src/handler.py", db, None, config, tmp_dir, "sess1",
        )
        assert len(nudges) == 1
        assert "flush rule" in nudges[0]
