import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lib.events import extract_event, handle_event
from lib.db import ContextDB


class TestExtractEvent:
    def test_file_edit(self):
        ev = extract_event({"tool_name": "Edit", "tool_input": {"file_path": "/src/main.py"}, "tool_response": {}, "session_id": "s1", "cwd": "/p"})
        assert ev["category"] == "file"
        assert ev["event_type"] == "file_edit"
        assert ev["priority"] == 1
        assert ev["is_commit"] is False

    def test_file_read(self):
        ev = extract_event({"tool_name": "Read", "tool_input": {"file_path": "/src/main.py"}, "tool_response": {}, "session_id": "s1", "cwd": "/p"})
        assert ev["category"] == "file"
        assert ev["event_type"] == "file_read"
        assert ev["priority"] == 3

    def test_file_write(self):
        ev = extract_event({"tool_name": "Write", "tool_input": {"file_path": "/new.py"}, "tool_response": {}, "session_id": "s1", "cwd": "/p"})
        assert ev["event_type"] == "file_write"
        assert ev["priority"] == 1

    def test_git_commit_detected(self):
        ev = extract_event({
            "tool_name": "Bash",
            "tool_input": {"command": "git commit -m 'fix: stuff'"},
            "tool_response": {"output": "[main abc1234] fix: stuff"},
            "session_id": "s1", "cwd": "/p"
        })
        assert ev["category"] == "git"
        assert ev["is_commit"] is True

    def test_git_push_not_commit(self):
        ev = extract_event({
            "tool_name": "Bash",
            "tool_input": {"command": "git push origin main"},
            "tool_response": {"output": "Everything up-to-date"},
            "session_id": "s1", "cwd": "/p"
        })
        assert ev["category"] == "git"
        assert ev["is_commit"] is False

    def test_error_detected(self):
        ev = extract_event({
            "tool_name": "Bash",
            "tool_input": {"command": "make build"},
            "tool_response": {"output": "Error: compilation failed", "is_error": "true", "exit_code": 2},
            "session_id": "s1", "cwd": "/p"
        })
        assert ev["category"] == "error"
        assert ev["priority"] == 1

    def test_test_run(self):
        ev = extract_event({
            "tool_name": "Bash",
            "tool_input": {"command": "python3 -m pytest tests/ -v"},
            "tool_response": {"output": "passed", "exit_code": 0},
            "session_id": "s1", "cwd": "/p"
        })
        assert ev["category"] == "test"

    def test_skips_uninteresting(self):
        ev = extract_event({
            "tool_name": "Bash",
            "tool_input": {"command": "ls -la"},
            "tool_response": {"output": "total 0", "exit_code": 0},
            "session_id": "s1", "cwd": "/p"
        })
        assert ev is None

    def test_todowrite(self):
        ev = extract_event({
            "tool_name": "TodoWrite",
            "tool_input": {"todos": [{"content": "Fix bug", "status": "in_progress"}]},
            "tool_response": {}, "session_id": "s1", "cwd": "/p"
        })
        assert ev["category"] == "task"
        assert ev["priority"] == 1

    def test_subagent(self):
        ev = extract_event({
            "tool_name": "Agent",
            "tool_input": {"description": "Research patterns"},
            "tool_response": {}, "session_id": "s1", "cwd": "/p"
        })
        assert ev["category"] == "subagent"

    def test_mcp_tool(self):
        ev = extract_event({
            "tool_name": "mcp__postgres__query",
            "tool_input": {"sql": "SELECT 1"},
            "tool_response": {}, "session_id": "s1", "cwd": "/p"
        })
        assert ev["category"] == "mcp"

    def test_glob_search(self):
        ev = extract_event({
            "tool_name": "Glob",
            "tool_input": {"pattern": "**/*.py"},
            "tool_response": {}, "session_id": "s1", "cwd": "/p"
        })
        assert ev["category"] == "search"

    def test_empty_file_path(self):
        ev = extract_event({
            "tool_name": "Edit",
            "tool_input": {"file_path": ""},
            "tool_response": {}, "session_id": "s1", "cwd": "/p"
        })
        assert ev is None

    def test_unknown_tool(self):
        ev = extract_event({
            "tool_name": "SomeNewTool",
            "tool_input": {},
            "tool_response": {}, "session_id": "s1", "cwd": "/p"
        })
        assert ev is None


class TestHandleEvent:
    def setup_method(self):
        self.tmp = tempfile.mkdtemp()
        self.db = ContextDB(self.tmp)

    def teardown_method(self):
        self.db.close()

    def test_stores_event_in_db(self):
        handle_event(
            {"tool_name": "Edit", "tool_input": {"file_path": "/a.py"}, "tool_response": {}, "session_id": "s1", "cwd": "/p"},
            self.db, "s1", "/p"
        )
        rows = self.db.query("SELECT COUNT(*) FROM events")
        assert rows[0][0] == 1

    def test_returns_commit_signal(self):
        result = handle_event(
            {"tool_name": "Bash", "tool_input": {"command": "git commit -m 'fix'"}, "tool_response": {"output": "[main abc1234] fix"}, "session_id": "s1", "cwd": "/p"},
            self.db, "s1", "/p"
        )
        assert result is not None
        assert result["is_commit"] is True

    def test_returns_event_type_for_read(self):
        result = handle_event(
            {"tool_name": "Read", "tool_input": {"file_path": "/a.py"}, "tool_response": {}, "session_id": "s1", "cwd": "/p"},
            self.db, "s1", "/p"
        )
        assert result == {"event_type": "file_read"}

    def test_skips_uninteresting(self):
        result = handle_event(
            {"tool_name": "Bash", "tool_input": {"command": "echo hi"}, "tool_response": {"output": "hi", "exit_code": 0}, "session_id": "s1", "cwd": "/p"},
            self.db, "s1", "/p"
        )
        assert result is None
        rows = self.db.query("SELECT COUNT(*) FROM events")
        assert rows[0][0] == 0
