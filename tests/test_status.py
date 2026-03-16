import os, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from lib.db import ContextDB
from lib.status import show_status

class TestStatus:
    def setup_method(self):
        self.tmp = tempfile.mkdtemp()
        self.db = ContextDB(self.tmp)

    def teardown_method(self):
        self.db.close()

    def test_status_empty_db(self):
        result = show_status(self.db, self.tmp, "/fake/project")
        assert "context-hooks status" in result
        assert "/fake/project" in result
        assert "events" in result
        assert "commits" in result

    def test_status_with_data(self):
        self.db.insert_event(
            session_id="s1", category="file", event_type="file_read",
            priority=3, data="/a.py", project_dir="/p"
        )
        self.db.insert_commit(
            session_id="s1", commit_date="2026-03-15", hash="x"*40,
            short_hash="xxxxxxx", author="t@t.com", subject="test commit",
            body="", files_changed="a.py", tags="test", project_dir="/p"
        )
        result = show_status(self.db, self.tmp, "/fake/project")
        assert "file_read" in result
        assert "xxxxxxx" in result
        assert "test commit" in result
