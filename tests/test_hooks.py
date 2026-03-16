import os, sys, json, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from lib.db import ContextDB
from lib.hooks import handle_hook

class TestHookRouter:
    def setup_method(self):
        self.tmp = tempfile.mkdtemp()
        # Patch data_dir to use temp
        import lib.hooks
        self._orig_data_dir = lib.hooks.data_dir
        self._orig_resolve = lib.hooks.resolve_git_root
        lib.hooks.data_dir = lambda x: self.tmp
        lib.hooks.resolve_git_root = lambda x: x

    def teardown_method(self):
        import lib.hooks
        lib.hooks.data_dir = self._orig_data_dir
        lib.hooks.resolve_git_root = self._orig_resolve

    def test_event_returns_none_for_normal(self):
        result = handle_hook("event", {
            "tool_name": "Read", "tool_input": {"file_path": "/a.py"},
            "tool_response": {}, "session_id": "s1", "cwd": "/p"
        })
        assert result is None

    def test_pre_compact_returns_none(self):
        result = handle_hook("pre-compact", {
            "session_id": "s1", "cwd": "/p", "trigger": "auto"
        })
        assert result is None
        # But snapshot should exist
        assert os.path.exists(os.path.join(self.tmp, "snapshot.xml"))

    def test_session_start_compact_returns_recovery(self):
        # First create a snapshot
        handle_hook("pre-compact", {"session_id": "s1", "cwd": "/p", "trigger": "auto"})
        # Then recover
        result = handle_hook("session-start", {"session_id": "s1", "cwd": "/p", "source": "compact"})
        assert result is not None
        parsed = json.loads(result)
        assert "additionalContext" in parsed
        assert "CONTEXT RECOVERY" in parsed["additionalContext"]

    def test_session_end_returns_none(self):
        result = handle_hook("session-end", {"session_id": "s1", "cwd": "/p"})
        assert result is None

    def test_unknown_hook_returns_none(self):
        result = handle_hook("nonexistent", {"session_id": "s1", "cwd": "/p"})
        assert result is None
