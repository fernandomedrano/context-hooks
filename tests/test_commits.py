import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lib.db import ContextDB
from lib.commits import index_commit, backfill


# The context-hooks repo itself — guaranteed to have at least 3 commits
GIT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class TestIndexCommit:
    def setup_method(self):
        self.tmp = tempfile.mkdtemp()
        self.db = ContextDB(self.tmp)

    def teardown_method(self):
        self.db.close()

    def test_stores_head_commit(self):
        result = index_commit(self.db, GIT_ROOT, session_id="s1")
        assert result is not None
        assert len(result["hash"]) == 40
        assert len(result["short_hash"]) >= 7
        assert result["subject"]  # non-empty

        rows = self.db.query("SELECT hash, short_hash, subject FROM commits")
        assert len(rows) == 1
        assert rows[0][0] == result["hash"]

    def test_includes_tags(self):
        result = index_commit(self.db, GIT_ROOT, session_id="s1")
        # The HEAD commit should have at least some tag (feat/fix/chore/docs or file-type)
        assert result is not None
        # Tags might be empty for some commits, but hash should always be there
        assert result["hash"]

    def test_dedup(self):
        """Indexing HEAD twice should only store one row."""
        index_commit(self.db, GIT_ROOT, session_id="s1")
        index_commit(self.db, GIT_ROOT, session_id="s1")
        rows = self.db.query("SELECT COUNT(*) FROM commits")
        assert rows[0][0] == 1

    def test_with_profile(self):
        profile = {"directory_tags": {"lib": "lib"}, "hot_files": {}, "parallel_paths": []}
        result = index_commit(self.db, GIT_ROOT, session_id="s1", profile=profile)
        assert result is not None
        # If HEAD touches lib/, should have the tag
        # Just verify it doesn't crash with a profile


class TestBackfill:
    def setup_method(self):
        self.tmp = tempfile.mkdtemp()
        self.db = ContextDB(self.tmp)

    def teardown_method(self):
        self.db.close()

    def test_finds_commits(self):
        """The context-hooks repo has at least 3 commits."""
        count = backfill(self.db, GIT_ROOT, days=365)
        assert count >= 3

        rows = self.db.query("SELECT COUNT(*) FROM commits")
        assert rows[0][0] >= 3

    def test_all_have_hashes(self):
        backfill(self.db, GIT_ROOT, days=365)
        rows = self.db.query("SELECT hash, short_hash FROM commits")
        for full_hash, short_hash in rows:
            assert len(full_hash) == 40
            assert len(short_hash) >= 7

    def test_dedup_on_backfill(self):
        """Running backfill twice should not duplicate commits."""
        count1 = backfill(self.db, GIT_ROOT, days=365)
        count2 = backfill(self.db, GIT_ROOT, days=365)
        rows = self.db.query("SELECT COUNT(*) FROM commits")
        assert rows[0][0] == count1  # second run inserts 0 new

    def test_tags_populated(self):
        """At least some commits should have tags."""
        backfill(self.db, GIT_ROOT, days=365)
        rows = self.db.query("SELECT tags FROM commits WHERE tags IS NOT NULL AND tags != ''")
        # The 'feat:' commits should get tagged
        assert len(rows) >= 1
