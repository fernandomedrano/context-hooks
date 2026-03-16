import os, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from lib.db import ContextDB
from lib.knowledge import store, search, list_entries, promote, archive, restore, dismiss, supersede, send_memo, list_memos, read_memo

class TestKnowledgeStore:
    def setup_method(self):
        self.tmp = tempfile.mkdtemp()
        self.db = ContextDB(self.tmp)

    def teardown_method(self):
        self.db.close()

    def test_store_creates_entry(self):
        store(self.db, "architectural-decision", "Use SQLite", "We chose SQLite for zero deps.")
        entries = list_entries(self.db)
        assert len(entries) == 1
        assert entries[0]["title"] == "Use SQLite"
        assert entries[0]["maturity"] == "decision"

    def test_store_with_bug_refs(self):
        store(self.db, "failure-class", "Stack depth assumed", "Content", bug_refs="BUG-037,BUG-038")
        entries = list_entries(self.db, category="failure-class")
        assert entries[0]["bug_refs"] == "BUG-037,BUG-038"

    def test_search_fts(self):
        store(self.db, "coding-convention", "Parameterized queries", "All SQL writes must use ? placeholders to prevent injection.")
        results = search(self.db, "injection")
        assert len(results) >= 1
        assert "Parameterized" in results[0]["title"]

    def test_search_empty(self):
        results = search(self.db, "nonexistent term xyz123")
        assert len(results) == 0

    def test_list_by_category(self):
        store(self.db, "architectural-decision", "A", "content")
        store(self.db, "failure-class", "B", "content")
        decisions = list_entries(self.db, category="architectural-decision")
        assert len(decisions) == 1
        assert decisions[0]["title"] == "A"

    def test_promote_lifecycle(self):
        store(self.db, "coding-convention", "Test rule", "content")
        # Get the id
        entries = list_entries(self.db)
        eid = entries[0]["id"]
        # decision is the starting maturity for explicit stores
        assert entries[0]["maturity"] == "decision"
        # Promote to convention
        promote(self.db, eid)
        entries = list_entries(self.db)
        assert entries[0]["maturity"] == "convention"

    def test_promote_convention_errors(self):
        store(self.db, "coding-convention", "Max rule", "content")
        entries = list_entries(self.db)
        eid = entries[0]["id"]
        promote(self.db, eid)  # decision → convention
        # Convention → ??? should raise
        try:
            promote(self.db, eid)
            assert False, "Should have raised"
        except ValueError as e:
            assert "maximum maturity" in str(e).lower()

    def test_archive_and_restore(self):
        store(self.db, "reference", "Old doc", "content")
        entries = list_entries(self.db)
        eid = entries[0]["id"]
        archive(self.db, eid)
        # Should not appear in active list
        assert len(list_entries(self.db)) == 0
        # Restore
        restore(self.db, eid)
        assert len(list_entries(self.db)) == 1

    def test_dismiss(self):
        store(self.db, "reference", "Noise", "content")
        entries = list_entries(self.db)
        eid = entries[0]["id"]
        dismiss(self.db, eid)
        assert len(list_entries(self.db)) == 0
        # Check it's dismissed, not just archived
        rows = self.db.query("SELECT status FROM knowledge WHERE id = ?", (eid,))
        assert rows[0][0] == "dismissed"

    def test_supersede(self):
        store(self.db, "architectural-decision", "Use React", "V1 choice")
        entries = list_entries(self.db)
        old_id = entries[0]["id"]
        supersede(self.db, old_id, "architectural-decision", "Use React", "V2 choice with SSR", "Better performance")
        entries = list_entries(self.db)
        assert len(entries) == 1  # only the new active one
        assert "V2" in entries[0]["content"]
        # Old entry should be superseded
        old = self.db.query("SELECT status, superseded_by FROM knowledge WHERE id = ?", (old_id,))
        assert old[0][0] == "superseded"

    def test_store_with_custom_maturity(self):
        from lib.knowledge import store
        store(self.db, "reference", "Low-confidence signal", "Might be a pattern", maturity="signal")
        rows = self.db.query("SELECT title, maturity FROM knowledge WHERE title = 'Low-confidence signal'")
        assert len(rows) == 1
        assert rows[0][1] == "signal"

    def test_store_default_maturity_is_decision(self):
        from lib.knowledge import store
        store(self.db, "reference", "High-confidence fact", "This is decided")
        rows = self.db.query("SELECT title, maturity FROM knowledge WHERE title = 'High-confidence fact'")
        assert len(rows) == 1
        assert rows[0][1] == "decision"


class TestMemoSendCLI:
    """Tests for parse_memo_send_args and --project support."""

    def setup_method(self):
        self.tmp = tempfile.mkdtemp()
        self.db = ContextDB(self.tmp)

    def teardown_method(self):
        self.db.close()

    def test_flag_syntax(self):
        """--from, --subject, --content flags should parse correctly."""
        from lib.knowledge import parse_memo_send_args
        parsed = parse_memo_send_args(['--from', 'agent-1', '--subject', 'Hello', '--content', 'Body text'])
        assert parsed['from_agent'] == 'agent-1'
        assert parsed['subject'] == 'Hello'
        assert parsed['content'] == 'Body text'

    def test_flag_syntax_with_to(self):
        """--to flag should set the recipient."""
        from lib.knowledge import parse_memo_send_args
        parsed = parse_memo_send_args(['--from', 'a', '--subject', 's', '--content', 'c', '--to', 'agent-2'])
        assert parsed['to_agent'] == 'agent-2'

    def test_flag_syntax_default_to_broadcast(self):
        """Without --to, recipient should default to '*'."""
        from lib.knowledge import parse_memo_send_args
        parsed = parse_memo_send_args(['--from', 'a', '--subject', 's', '--content', 'c'])
        assert parsed['to_agent'] == '*'

    def test_flag_syntax_with_priority(self):
        """--priority flag should be parsed."""
        from lib.knowledge import parse_memo_send_args
        parsed = parse_memo_send_args(['--from', 'a', '--subject', 's', '--content', 'c', '--priority', 'urgent'])
        assert parsed['priority'] == 'urgent'

    def test_positional_syntax_still_works(self):
        """Old positional syntax (from subject content) should still parse."""
        from lib.knowledge import parse_memo_send_args
        parsed = parse_memo_send_args(['agent-1', 'Hello', 'Body text'])
        assert parsed['from_agent'] == 'agent-1'
        assert parsed['subject'] == 'Hello'
        assert parsed['content'] == 'Body text'

    def test_project_flag_parsed(self):
        """--project should be available in parsed result."""
        from lib.knowledge import parse_memo_send_args
        parsed = parse_memo_send_args(['--from', 'a', '--subject', 's', '--content', 'c', '--project', '/tmp/other'])
        assert parsed['project'] == '/tmp/other'

    def test_project_flag_sends_to_other_db(self):
        """--project should resolve via data_dir and send to that project's DB."""
        from lib.knowledge import parse_memo_send_args, send_memo
        from lib.db import data_dir as get_data_dir
        other_project_root = tempfile.mkdtemp()  # simulates a git root
        parsed = parse_memo_send_args(['--from', 'sender', '--subject', 'Cross-project',
                                        '--content', 'Hello from afar', '--project', other_project_root])
        assert parsed['project'] == other_project_root
        # Simulate what main() does: resolve via data_dir, open DB, send
        other_data = get_data_dir(other_project_root)
        other_db = ContextDB(other_data)
        send_memo(other_db, parsed['from_agent'], parsed['subject'], parsed['content'],
                  to_agent=parsed['to_agent'])
        other_memos = list_memos(other_db)
        assert len(other_memos) == 1
        assert other_memos[0]['subject'] == 'Cross-project'
        # Local DB should be empty
        local_memos = list_memos(self.db)
        assert len(local_memos) == 0
        other_db.close()

    def test_missing_required_flags_errors(self):
        """Missing --from or --subject should error."""
        import pytest
        from lib.knowledge import parse_memo_send_args
        with pytest.raises(SystemExit):
            parse_memo_send_args(['--from', 'agent-1'])  # missing --subject and --content

    def test_content_with_newlines(self):
        """Multi-line content passed as a single --content arg should be preserved."""
        from lib.knowledge import parse_memo_send_args
        content = "Line one\nLine two\nLine three"
        parsed = parse_memo_send_args(['--from', 'a', '--subject', 's', '--content', content])
        assert parsed['content'] == content
        assert '\n' in parsed['content']

    def test_content_from_stdin(self):
        """--content - should read content from stdin."""
        import io
        from lib.knowledge import parse_memo_send_args
        stdin_data = "Multi-line\ncontent from\nstdin"
        parsed = parse_memo_send_args(
            ['--from', 'a', '--subject', 's', '--content', '-'],
            stdin=io.StringIO(stdin_data)
        )
        assert parsed['content'] == stdin_data

    def test_positional_multiline_content(self):
        """Positional syntax with multi-line content should preserve newlines."""
        from lib.knowledge import parse_memo_send_args
        content = "Line one\nLine two"
        parsed = parse_memo_send_args(['agent-1', 'Hello', content])
        assert parsed['content'] == content

    def test_content_stdin_dash_literal(self):
        """--content - with no stdin should use '-' as literal content."""
        import io
        from lib.knowledge import parse_memo_send_args
        parsed = parse_memo_send_args(
            ['--from', 'a', '--subject', 's', '--content', '-'],
            stdin=io.StringIO('')
        )
        # Empty stdin means - was literal
        assert parsed['content'] == '-'


class TestKnowledgeExport:
    """Integration tests: knowledge mutations trigger markdown export."""

    def setup_method(self):
        self.tmp = tempfile.mkdtemp()
        self.export_dir = os.path.join(self.tmp, 'knowledge')
        from lib.db import ContextDB
        self.db_dir = tempfile.mkdtemp()
        self.db = ContextDB(self.db_dir)

    def teardown_method(self):
        self.db.close()

    def test_store_writes_file(self):
        from lib.knowledge import store
        store(self.db, 'failure-class', 'Export test', 'Content here.',
              export_dir=self.export_dir)
        path = os.path.join(self.export_dir, 'failure-class', 'export-test.md')
        assert os.path.exists(path)
        with open(path) as f:
            text = f.read()
        assert 'title: Export test' in text

    def test_store_writes_index(self):
        from lib.knowledge import store
        store(self.db, 'reference', 'Index test', 'Body.',
              export_dir=self.export_dir)
        index = os.path.join(self.export_dir, 'index.md')
        assert os.path.exists(index)
        with open(index) as f:
            text = f.read()
        assert 'Index test' in text

    def test_promote_updates_file(self):
        from lib.knowledge import store, promote
        store(self.db, 'failure-class', 'Promote test', 'Content.',
              maturity='signal', export_dir=self.export_dir)
        row = self.db.query("SELECT id FROM knowledge WHERE title = 'Promote test'")
        promote(self.db, row[0][0], export_dir=self.export_dir)
        path = os.path.join(self.export_dir, 'failure-class', 'promote-test.md')
        with open(path) as f:
            text = f.read()
        assert 'maturity: pattern' in text

    def test_store_no_export_when_dir_none(self):
        from lib.knowledge import store
        store(self.db, 'reference', 'No export', 'Body.', export_dir=None)

    def test_archive_keeps_file_updates_frontmatter(self):
        from lib.knowledge import store, archive
        store(self.db, 'reference', 'Archive test', 'Content.',
              export_dir=self.export_dir)
        row = self.db.query("SELECT id FROM knowledge WHERE title = 'Archive test'")
        archive(self.db, row[0][0], export_dir=self.export_dir)
        path = os.path.join(self.export_dir, 'reference', 'archive-test.md')
        assert os.path.exists(path)
        with open(path) as f:
            text = f.read()
        assert 'status: archived' in text

    def test_archive_removes_from_index(self):
        from lib.knowledge import store, archive
        store(self.db, 'reference', 'Index gone', 'Body.',
              export_dir=self.export_dir)
        row = self.db.query("SELECT id FROM knowledge WHERE title = 'Index gone'")
        archive(self.db, row[0][0], export_dir=self.export_dir)
        with open(os.path.join(self.export_dir, 'index.md')) as f:
            text = f.read()
        assert 'Index gone' not in text

    def test_restore_updates_file_and_index(self):
        from lib.knowledge import store, archive, restore
        store(self.db, 'reference', 'Restore test', 'Body.',
              export_dir=self.export_dir)
        row = self.db.query("SELECT id FROM knowledge WHERE title = 'Restore test'")
        eid = row[0][0]
        archive(self.db, eid, export_dir=self.export_dir)
        restore(self.db, eid, export_dir=self.export_dir)
        path = os.path.join(self.export_dir, 'reference', 'restore-test.md')
        with open(path) as f:
            text = f.read()
        assert 'status: active' in text
        with open(os.path.join(self.export_dir, 'index.md')) as f:
            assert 'Restore test' in f.read()

    def test_dismiss_deletes_file(self):
        from lib.knowledge import store, dismiss
        store(self.db, 'reference', 'Dismiss test', 'Body.',
              export_dir=self.export_dir)
        row = self.db.query("SELECT id FROM knowledge WHERE title = 'Dismiss test'")
        dismiss(self.db, row[0][0], export_dir=self.export_dir)
        path = os.path.join(self.export_dir, 'reference', 'dismiss-test.md')
        assert not os.path.exists(path)

    def test_supersede_writes_both_files(self):
        from lib.knowledge import store, supersede
        store(self.db, 'failure-class', 'Old entry', 'Old content.',
              export_dir=self.export_dir)
        row = self.db.query("SELECT id FROM knowledge WHERE title = 'Old entry'")
        supersede(self.db, row[0][0], 'failure-class', 'New entry', 'New content.',
                  export_dir=self.export_dir)
        old_path = os.path.join(self.export_dir, 'failure-class', 'old-entry.md')
        new_path = os.path.join(self.export_dir, 'failure-class', 'new-entry.md')
        assert os.path.exists(old_path)
        assert os.path.exists(new_path)
        with open(old_path) as f:
            assert 'status: superseded' in f.read()
        with open(new_path) as f:
            assert 'status: active' in f.read()

    def test_supersede_same_title_renames_old(self):
        from lib.knowledge import store, supersede
        store(self.db, 'failure-class', 'Same title', 'Version 1.',
              export_dir=self.export_dir)
        row = self.db.query("SELECT id FROM knowledge WHERE title = 'Same title'")
        old_id = row[0][0]
        supersede(self.db, old_id, 'failure-class', 'Same title', 'Version 2.',
                  export_dir=self.export_dir)
        renamed = os.path.join(self.export_dir, 'failure-class', f'same-title-superseded-{old_id}.md')
        new_path = os.path.join(self.export_dir, 'failure-class', 'same-title.md')
        assert os.path.exists(renamed)
        assert os.path.exists(new_path)
        with open(renamed) as f:
            renamed_text = f.read()
            assert 'status: superseded' in renamed_text
            assert 'Version 1.' in renamed_text
        with open(new_path) as f:
            new_text = f.read()
            assert 'status: active' in new_text
            assert 'Version 2.' in new_text


class TestClusterRouting:
    """Tests that knowledge/memo operations route to master DB when clustered."""

    def test_send_memo_routes_to_master(self):
        """Satellite send_memo should write to master DB, not local."""
        master_root = tempfile.mkdtemp()
        from lib.db import data_dir, resolve_cluster_db
        master_dir = data_dir(master_root)
        master_db = ContextDB(master_dir)

        satellite_dir = tempfile.mkdtemp()
        os.makedirs(satellite_dir, exist_ok=True)
        satellite_db = ContextDB(satellite_dir)

        with open(os.path.join(satellite_dir, "cluster.yaml"), "w") as f:
            f.write(f"cluster: test\nmaster: {master_root}\n")

        cluster_dir = resolve_cluster_db(satellite_dir)
        cluster_db = ContextDB(cluster_dir)
        send_memo(cluster_db, "satellite-agent", "Hello master", "Test content")

        master_memos = list_memos(master_db)
        assert len(master_memos) == 1
        assert master_memos[0]["subject"] == "Hello master"

        satellite_memos = list_memos(satellite_db)
        assert len(satellite_memos) == 0

        master_db.close()
        satellite_db.close()
        cluster_db.close()

    def test_store_knowledge_routes_to_master(self):
        """Satellite store should write to master DB."""
        master_root = tempfile.mkdtemp()
        from lib.db import data_dir, resolve_cluster_db
        master_dir = data_dir(master_root)
        master_db = ContextDB(master_dir)

        satellite_dir = tempfile.mkdtemp()
        os.makedirs(satellite_dir, exist_ok=True)
        satellite_db = ContextDB(satellite_dir)

        with open(os.path.join(satellite_dir, "cluster.yaml"), "w") as f:
            f.write(f"cluster: test\nmaster: {master_root}\n")

        cluster_dir = resolve_cluster_db(satellite_dir)
        cluster_db = ContextDB(cluster_dir)
        store(cluster_db, "architectural-decision", "Cluster routing", "We use hub-and-spoke")

        master_entries = list_entries(master_db)
        assert len(master_entries) == 1
        satellite_entries = list_entries(satellite_db)
        assert len(satellite_entries) == 0

        master_db.close()
        satellite_db.close()
        cluster_db.close()

    def test_standalone_unchanged(self):
        """Without cluster.yaml, all operations use local DB."""
        from lib.db import resolve_cluster_db
        standalone_dir = tempfile.mkdtemp()
        db = ContextDB(standalone_dir)

        cluster_dir = resolve_cluster_db(standalone_dir)
        assert cluster_dir == standalone_dir

        send_memo(db, "agent", "Local memo", "Content")
        assert len(list_memos(db)) == 1
        db.close()

    def test_project_flag_resolves_through_cluster(self):
        """--project flag should resolve the target's cluster before sending."""
        from lib.knowledge import parse_memo_send_args
        from lib.db import data_dir, resolve_cluster_db

        master_root = tempfile.mkdtemp()
        master_dir = data_dir(master_root)
        master_db = ContextDB(master_dir)

        target_root = tempfile.mkdtemp()
        target_dir = data_dir(target_root)
        ContextDB(target_dir)
        with open(os.path.join(target_dir, "cluster.yaml"), "w") as f:
            f.write(f"cluster: test\nmaster: {master_root}\n")

        target_cluster = resolve_cluster_db(target_dir)
        assert target_cluster == master_dir

        master_db.close()


class TestMemos:
    def setup_method(self):
        self.tmp = tempfile.mkdtemp()
        self.db = ContextDB(self.tmp)

    def teardown_method(self):
        self.db.close()

    def test_send_and_list(self):
        send_memo(self.db, "session-1", "Remember this", "Deploy needs cache bust")
        memos = list_memos(self.db)
        assert len(memos) == 1
        assert memos[0]["subject"] == "Remember this"
        assert memos[0]["read"] == 0

    def test_unread_filter(self):
        send_memo(self.db, "s1", "Unread", "content")
        send_memo(self.db, "s2", "Also unread", "content")
        # Mark one as read
        memos = list_memos(self.db)
        read_memo(self.db, memos[0]["id"])
        unread = list_memos(self.db, unread_only=True)
        assert len(unread) == 1
        assert unread[0]["subject"] == "Also unread"

    def test_read_returns_content(self):
        send_memo(self.db, "s1", "Test", "The actual content here")
        memos = list_memos(self.db)
        content = read_memo(self.db, memos[0]["id"])
        assert content["content"] == "The actual content here"
        # Should be marked read now
        updated = self.db.query("SELECT read FROM memos WHERE id = ?", (memos[0]["id"],))
        assert updated[0][0] == 1
