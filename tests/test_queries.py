import os, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from lib.db import ContextDB
from lib.queries import query_search, query_tag, query_bugs, query_recent, query_parity, query_file, query_related

class TestQueries:
    def setup_method(self):
        self.tmp = tempfile.mkdtemp()
        self.db = ContextDB(self.tmp)
        # Seed some commits
        self.db.insert_commit(session_id="s1", commit_date="2026-03-15", hash="a"*40, short_hash="aaaaaaa",
                              author="t@t.com", subject="fix: login bug", body="BUG-138",
                              files_changed="src/auth.py,src/api.py", tags="fix,BUG-138", project_dir="/p")
        self.db.insert_commit(session_id="s1", commit_date="2026-03-14", hash="b"*40, short_hash="bbbbbbb",
                              author="t@t.com", subject="feat: add dashboard", body="",
                              files_changed="src/dashboard.py,src/api.py", tags="feat,solo-a:auth+api", project_dir="/p")
        self.db.insert_commit(session_id="s1", commit_date="2026-03-13", hash="c"*40, short_hash="ccccccc",
                              author="t@t.com", subject="refactor: cleanup", body="",
                              files_changed="src/utils.py", tags="refactor,paired:auth+api", project_dir="/p")

    def teardown_method(self):
        self.db.close()

    def test_search_finds_match(self):
        result = query_search(self.db, "login")
        assert "aaaaaaa" in result
        assert "login" in result

    def test_search_no_match(self):
        result = query_search(self.db, "nonexistent_xyz")
        assert "no matches" in result

    def test_tag_finds_match(self):
        result = query_tag(self.db, "fix")
        assert "aaaaaaa" in result

    def test_bugs(self):
        result = query_bugs(self.db)
        assert "BUG-138" in result

    def test_recent(self):
        result = query_recent(self.db, limit=3)
        assert "aaaaaaa" in result
        assert "bbbbbbb" in result
        assert "ccccccc" in result

    def test_parity(self):
        result = query_parity(self.db)
        assert "solo-a" in result

    def test_file(self):
        result = query_file(self.db, "api.py")
        assert "aaaaaaa" in result
        assert "bbbbbbb" in result

    def test_related(self):
        result = query_related(self.db, "aaaaaaa")
        assert "bbbbbbb" in result  # shares api.py
