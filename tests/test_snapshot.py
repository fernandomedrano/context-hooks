import os
import sys
import json
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lib.snapshot import build_snapshot, save_snapshot, load_snapshot, recovery_response
from lib.db import ContextDB


class TestBuildSnapshot:
    def setup_method(self):
        self.tmp = tempfile.mkdtemp()
        self.db = ContextDB(self.tmp)

    def teardown_method(self):
        self.db.close()

    def test_empty_session(self):
        xml = build_snapshot(self.db, "empty-session", "/project")
        assert "<session_snapshot" in xml
        assert 'events="0"' in xml

    def test_includes_active_files(self):
        self.db.insert_event(session_id="s1", category="file", event_type="file_edit", priority=1, data="/src/main.py", project_dir="/p")
        xml = build_snapshot(self.db, "s1", "/p")
        assert "<active_files>" in xml
        assert "/src/main.py" in xml

    def test_includes_errors(self):
        self.db.insert_event(session_id="s1", category="error", event_type="error_bash", priority=1, data="pytest failed", project_dir="/p")
        xml = build_snapshot(self.db, "s1", "/p")
        assert "<recent_errors>" in xml

    def test_includes_task_state(self):
        self.db.insert_event(session_id="s1", category="task", event_type="task_update", priority=1, data="in_progress: Fix bug", project_dir="/p")
        xml = build_snapshot(self.db, "s1", "/p")
        assert "<task_state>" in xml
        assert "Fix bug" in xml

    def test_under_4kb(self):
        for i in range(100):
            self.db.insert_event(session_id="s1", category="file", event_type="file_edit", priority=1, data=f"/very/long/path/to/file_{i}.py", project_dir="/p")
        xml = build_snapshot(self.db, "s1", "/p")
        assert len(xml.encode()) <= 4096 + 50  # small buffer for closing tag


class TestSaveLoadSnapshot:
    def test_save_and_load(self):
        tmp = tempfile.mkdtemp()
        save_snapshot(tmp, "<snapshot>test</snapshot>")
        result = load_snapshot(tmp)
        assert result is not None
        assert "CONTEXT RECOVERY" in result["additionalContext"]
        assert "<snapshot>test</snapshot>" in result["additionalContext"]

    def test_load_missing(self):
        tmp = tempfile.mkdtemp()
        result = load_snapshot(tmp)
        assert result is None

    def test_save_permissions(self):
        tmp = tempfile.mkdtemp()
        path = save_snapshot(tmp, "<snapshot/>")
        stat = os.stat(path)
        assert oct(stat.st_mode)[-3:] == "600"

    def test_recovery_response_with_snapshot(self):
        tmp = tempfile.mkdtemp()
        save_snapshot(tmp, "<snapshot>data</snapshot>")
        resp = recovery_response(tmp)
        parsed = json.loads(resp)
        assert "additionalContext" in parsed
        assert "CONTEXT RECOVERY" in parsed["additionalContext"]

    def test_recovery_response_without_snapshot(self):
        tmp = tempfile.mkdtemp()
        resp = recovery_response(tmp)
        parsed = json.loads(resp)
        assert "compacted" in parsed["additionalContext"]
