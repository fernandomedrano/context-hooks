import os, sys, json, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from lib.db import ContextDB
from lib.pretool import handle_pretool


class TestPreToolUseRead:
    def setup_method(self):
        self.tmp = tempfile.mkdtemp()
        import lib.pretool
        self._orig_data_dir = lib.pretool.data_dir
        self._orig_resolve = lib.pretool.resolve_git_root
        self._orig_resolve_cluster = lib.pretool.resolve_cluster_db
        lib.pretool.data_dir = lambda x: self.tmp
        lib.pretool.resolve_git_root = lambda x: x
        lib.pretool.resolve_cluster_db = lambda x: x

    def teardown_method(self):
        import lib.pretool
        lib.pretool.data_dir = self._orig_data_dir
        lib.pretool.resolve_git_root = self._orig_resolve
        lib.pretool.resolve_cluster_db = self._orig_resolve_cluster

    def test_read_no_intel_returns_none(self):
        result = handle_pretool({
            "tool_name": "Read",
            "tool_input": {"file_path": "/some/file.py"},
            "session_id": "s1",
            "cwd": "/project",
        })
        assert result is None

    def test_read_with_bug_history(self):
        db = ContextDB(self.tmp)
        # Insert commits with BUG tags referencing our file
        for i in range(3):
            db.execute(
                "INSERT INTO commits (hash, short_hash, subject, files_changed, tags, author, session_id, timestamp, project_dir) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, '/project')",
                (f"abc{i}00", f"abc{i}", "fix bug", "models.py", f"BUG-{100+i}", "dev", "s0", "2026-03-18")
            )
        db.close()

        result = handle_pretool({
            "tool_name": "Read",
            "tool_input": {"file_path": "/project/models.py"},
            "session_id": "s1",
            "cwd": "/project",
        })
        assert result is not None
        parsed = json.loads(result)
        assert "hookSpecificOutput" in parsed
        assert parsed["hookSpecificOutput"]["hookEventName"] == "PreToolUse"
        ctx = parsed["hookSpecificOutput"]["additionalContext"]
        assert "bug-fix commits" in ctx
        assert "BUG-" in ctx

    def test_read_with_knowledge_refs(self):
        db = ContextDB(self.tmp)
        db.execute(
            "INSERT INTO knowledge (title, content, category, status, file_refs, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, datetime('now'), datetime('now'))",
            ("Auth token rotation", "Rotate tokens every 24h", "decision", "active", "auth.py")
        )
        db.close()

        result = handle_pretool({
            "tool_name": "Read",
            "tool_input": {"file_path": "/project/auth.py"},
            "session_id": "s2",
            "cwd": "/project",
        })
        assert result is not None
        ctx = json.loads(result)["hookSpecificOutput"]["additionalContext"]
        assert "Auth token rotation" in ctx

    def test_read_with_indexed_output(self):
        db = ContextDB(self.tmp)
        # Insert indexed chunks from a previous Read of the same file
        db.execute(
            "INSERT INTO output_chunks (session_id, source, chunk_index, content, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            ("s3", "Read:bigfile.py", 0, "chunk content here", "2026-03-18T00:00:00")
        )
        db.close()

        result = handle_pretool({
            "tool_name": "Read",
            "tool_input": {"file_path": "/project/bigfile.py"},
            "session_id": "s3",
            "cwd": "/project",
        })
        assert result is not None
        ctx = json.loads(result)["hookSpecificOutput"]["additionalContext"]
        assert "Indexed" in ctx
        assert "search-output" in ctx

    def test_read_dedup_fires_once(self):
        """Same file read twice in same session should only trigger intel once."""
        db = ContextDB(self.tmp)
        for i in range(3):
            db.execute(
                "INSERT INTO commits (hash, short_hash, subject, files_changed, tags, author, session_id, timestamp, project_dir) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, '/project')",
                (f"def{i}00", f"def{i}", "fix", "utils.py", f"BUG-{200+i}", "dev", "s0", "2026-03-18")
            )
        db.close()

        payload = {
            "tool_name": "Read",
            "tool_input": {"file_path": "/project/utils.py"},
            "session_id": "s4",
            "cwd": "/project",
        }
        result1 = handle_pretool(payload)
        assert result1 is not None  # First time: has intel

        result2 = handle_pretool(payload)
        assert result2 is None  # Second time: deduped


class TestPreToolUseEdit:
    def setup_method(self):
        self.tmp = tempfile.mkdtemp()
        import lib.pretool
        self._orig_data_dir = lib.pretool.data_dir
        self._orig_resolve = lib.pretool.resolve_git_root
        self._orig_resolve_cluster = lib.pretool.resolve_cluster_db
        lib.pretool.data_dir = lambda x: self.tmp
        lib.pretool.resolve_git_root = lambda x: x
        lib.pretool.resolve_cluster_db = lambda x: x

    def teardown_method(self):
        import lib.pretool
        lib.pretool.data_dir = self._orig_data_dir
        lib.pretool.resolve_git_root = self._orig_resolve
        lib.pretool.resolve_cluster_db = self._orig_resolve_cluster

    def test_edit_no_intel_returns_none(self):
        result = handle_pretool({
            "tool_name": "Edit",
            "tool_input": {"file_path": "/project/foo.py", "old_string": "a", "new_string": "b"},
            "session_id": "s5",
            "cwd": "/project",
        })
        assert result is None

    def test_write_with_knowledge_refs(self):
        db = ContextDB(self.tmp)
        db.execute(
            "INSERT INTO knowledge (title, content, category, status, file_refs, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, datetime('now'), datetime('now'))",
            ("Config migration", "YAML v2 format", "convention", "active", "config.py")
        )
        db.close()

        result = handle_pretool({
            "tool_name": "Write",
            "tool_input": {"file_path": "/project/config.py", "content": "new content"},
            "session_id": "s6",
            "cwd": "/project",
        })
        assert result is not None
        ctx = json.loads(result)["hookSpecificOutput"]["additionalContext"]
        assert "Config migration" in ctx


class TestPreToolUseBash:
    def setup_method(self):
        self.tmp = tempfile.mkdtemp()
        import lib.pretool
        self._orig_data_dir = lib.pretool.data_dir
        self._orig_resolve = lib.pretool.resolve_git_root
        self._orig_resolve_cluster = lib.pretool.resolve_cluster_db
        lib.pretool.data_dir = lambda x: self.tmp
        lib.pretool.resolve_git_root = lambda x: x
        lib.pretool.resolve_cluster_db = lambda x: x

    def teardown_method(self):
        import lib.pretool
        lib.pretool.data_dir = self._orig_data_dir
        lib.pretool.resolve_git_root = self._orig_resolve
        lib.pretool.resolve_cluster_db = self._orig_resolve_cluster

    def test_non_test_command_returns_none(self):
        result = handle_pretool({
            "tool_name": "Bash",
            "tool_input": {"command": "ls -la"},
            "session_id": "s7",
            "cwd": "/project",
        })
        assert result is None

    def test_test_command_with_failure_knowledge(self):
        db = ContextDB(self.tmp)
        db.execute(
            "INSERT INTO knowledge (title, content, category, status, file_refs, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, datetime('now'), datetime('now'))",
            ("Flaky auth test", "Token expiry causes intermittent failure", "failure-class", "active", "test_auth.py")
        )
        db.close()

        result = handle_pretool({
            "tool_name": "Bash",
            "tool_input": {"command": "pytest tests/"},
            "session_id": "s8",
            "cwd": "/project",
        })
        assert result is not None
        ctx = json.loads(result)["hookSpecificOutput"]["additionalContext"]
        assert "failure-class" in ctx

    def test_test_command_no_knowledge_returns_none(self):
        result = handle_pretool({
            "tool_name": "Bash",
            "tool_input": {"command": "pytest tests/"},
            "session_id": "s9",
            "cwd": "/project",
        })
        assert result is None


class TestPreToolUseHookOutput:
    """Verify the output format matches Claude Code PreToolUse spec."""

    def setup_method(self):
        self.tmp = tempfile.mkdtemp()
        import lib.pretool
        self._orig_data_dir = lib.pretool.data_dir
        self._orig_resolve = lib.pretool.resolve_git_root
        self._orig_resolve_cluster = lib.pretool.resolve_cluster_db
        lib.pretool.data_dir = lambda x: self.tmp
        lib.pretool.resolve_git_root = lambda x: x
        lib.pretool.resolve_cluster_db = lambda x: x

    def teardown_method(self):
        import lib.pretool
        lib.pretool.data_dir = self._orig_data_dir
        lib.pretool.resolve_git_root = self._orig_resolve
        lib.pretool.resolve_cluster_db = self._orig_resolve_cluster

    def test_output_format_matches_spec(self):
        db = ContextDB(self.tmp)
        db.execute(
            "INSERT INTO knowledge (title, content, category, status, file_refs, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, datetime('now'), datetime('now'))",
            ("Test entry", "Some content", "decision", "active", "spec.py")
        )
        db.close()

        result = handle_pretool({
            "tool_name": "Read",
            "tool_input": {"file_path": "/project/spec.py"},
            "session_id": "s10",
            "cwd": "/project",
        })
        parsed = json.loads(result)

        # Must have hookSpecificOutput at top level
        assert "hookSpecificOutput" in parsed
        output = parsed["hookSpecificOutput"]

        # Must have hookEventName
        assert output["hookEventName"] == "PreToolUse"

        # Must have additionalContext (string)
        assert isinstance(output["additionalContext"], str)
        assert len(output["additionalContext"]) > 0

        # Must NOT have permissionDecision (we never block)
        assert "permissionDecision" not in output

    def test_unknown_tool_returns_none(self):
        result = handle_pretool({
            "tool_name": "Glob",
            "tool_input": {"pattern": "**/*.py"},
            "session_id": "s11",
            "cwd": "/project",
        })
        assert result is None
