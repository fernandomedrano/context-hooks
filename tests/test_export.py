"""Tests for lib/export.py — knowledge durability markdown export."""
import sys
import os
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))


class TestSlugify:
    def test_basic_title(self):
        from lib.export import _slugify
        assert _slugify("Context correction ignored") == "context-correction-ignored"

    def test_special_characters(self):
        from lib.export import _slugify
        assert _slugify("Bug: v0.1 DB missing priority") == "bug-v01-db-missing-priority"

    def test_underscores_to_hyphens(self):
        from lib.export import _slugify
        assert _slugify("some_snake_case_title") == "some-snake-case-title"

    def test_multiple_hyphens_collapsed(self):
        from lib.export import _slugify
        assert _slugify("foo---bar   baz") == "foo-bar-baz"

    def test_long_title_capped_at_80(self):
        from lib.export import _slugify
        title = "a" * 100
        assert len(_slugify(title)) == 80

    def test_empty_after_strip_falls_back_to_id(self):
        from lib.export import _slugify
        assert _slugify("日本語タイトル", entry_id=42) == "entry-42"

    def test_leading_trailing_hyphens_stripped(self):
        from lib.export import _slugify
        assert _slugify("--hello--") == "hello"


class TestRenderFrontmatter:
    def test_basic_entry(self):
        from lib.export import _render_frontmatter
        entry = {
            'id': 7, 'category': 'failure-class', 'maturity': 'decision',
            'status': 'active', 'title': 'Test entry', 'content': 'Some content.',
            'file_refs': 'foo.py, bar.py', 'commit_refs': None, 'bug_refs': 'BUG-001',
            'tags': 'routing', 'superseded_by': None,
            'created_at': '2026-03-08T02:16:27', 'updated_at': '2026-03-10T14:30:00'
        }
        result = _render_frontmatter(entry)
        assert result.startswith('---\n')
        assert 'id: 7' in result
        assert 'category: failure-class' in result
        assert 'file_refs: foo.py, bar.py' in result
        assert 'commit_refs' not in result  # None → omitted
        assert result.endswith('Some content.\n')

    def test_superseded_entry(self):
        from lib.export import _render_frontmatter
        entry = {
            'id': 3, 'category': 'failure-class', 'maturity': 'decision',
            'status': 'superseded', 'title': 'Old entry', 'content': 'Old.',
            'file_refs': None, 'commit_refs': None, 'bug_refs': None,
            'tags': None, 'superseded_by': 8,
            'created_at': '2026-03-01T00:00:00', 'updated_at': '2026-03-02T00:00:00'
        }
        result = _render_frontmatter(entry)
        assert 'superseded_by: 8' in result
        assert 'status: superseded' in result


class TestResolveExportDir:
    def setup_method(self):
        self.tmp = tempfile.mkdtemp()

    def test_disabled_returns_none(self):
        from lib.export import resolve_export_dir
        result = resolve_export_dir('/fake/git/root', self.tmp)
        assert result is None

    def test_enabled_returns_default_path(self):
        from lib.export import resolve_export_dir
        config_path = os.path.join(self.tmp, 'config.yaml')
        with open(config_path, 'w') as f:
            f.write('knowledge_export: true\n')
        result = resolve_export_dir('/fake/git/root', self.tmp)
        assert result == '/fake/git/root/data/knowledge'

    def test_custom_dir(self):
        from lib.export import resolve_export_dir
        config_path = os.path.join(self.tmp, 'config.yaml')
        with open(config_path, 'w') as f:
            f.write('knowledge_export: true\nknowledge_export_dir: docs/knowledge\n')
        result = resolve_export_dir('/fake/git/root', self.tmp)
        assert result == '/fake/git/root/docs/knowledge'

    def test_cluster_resolves_to_master(self):
        from lib.export import resolve_export_dir
        config_path = os.path.join(self.tmp, 'config.yaml')
        with open(config_path, 'w') as f:
            f.write('knowledge_export: true\n')
        cluster_path = os.path.join(self.tmp, 'cluster.yaml')
        with open(cluster_path, 'w') as f:
            f.write('master: /master/repo\nname: satellite\n')
        result = resolve_export_dir('/satellite/repo', self.tmp)
        assert result == '/master/repo/data/knowledge'


class TestWriteEntry:
    def setup_method(self):
        self.tmp = tempfile.mkdtemp()
        self.export_dir = os.path.join(self.tmp, 'knowledge')
        from lib.db import ContextDB
        self.db_dir = tempfile.mkdtemp()
        self.db = ContextDB(self.db_dir)

    def teardown_method(self):
        self.db.close()

    def test_creates_file_with_frontmatter(self):
        from lib.export import write_entry
        self.db.insert_knowledge(
            category='failure-class', title='Test bug',
            content='Found a bug.', maturity='decision'
        )
        row = self.db.query("SELECT id FROM knowledge WHERE title = 'Test bug'")
        entry_id = row[0][0]
        write_entry(self.db, entry_id, self.export_dir)
        path = os.path.join(self.export_dir, 'failure-class', 'test-bug.md')
        assert os.path.exists(path)
        with open(path) as f:
            content = f.read()
        assert 'title: Test bug' in content
        assert 'Found a bug.' in content

    def test_creates_category_subdirectory(self):
        from lib.export import write_entry
        self.db.insert_knowledge(
            category='coding-convention', title='Use parameterized queries',
            content='Never interpolate.', maturity='convention'
        )
        row = self.db.query("SELECT id FROM knowledge WHERE title = 'Use parameterized queries'")
        write_entry(self.db, row[0][0], self.export_dir)
        assert os.path.isdir(os.path.join(self.export_dir, 'coding-convention'))

    def test_idempotent(self):
        from lib.export import write_entry
        self.db.insert_knowledge(
            category='reference', title='Idempotent test',
            content='Same content.', maturity='decision'
        )
        row = self.db.query("SELECT id FROM knowledge WHERE title = 'Idempotent test'")
        entry_id = row[0][0]
        write_entry(self.db, entry_id, self.export_dir)
        path = os.path.join(self.export_dir, 'reference', 'idempotent-test.md')
        with open(path) as f:
            first = f.read()
        write_entry(self.db, entry_id, self.export_dir)
        with open(path) as f:
            second = f.read()
        assert first == second

    def test_filename_override(self):
        from lib.export import write_entry
        self.db.insert_knowledge(
            category='failure-class', title='Override test',
            content='Content.', maturity='decision'
        )
        row = self.db.query("SELECT id FROM knowledge WHERE title = 'Override test'")
        write_entry(self.db, row[0][0], self.export_dir, filename='custom-name')
        path = os.path.join(self.export_dir, 'failure-class', 'custom-name.md')
        assert os.path.exists(path)
        # Should NOT exist at the default slug path
        default_path = os.path.join(self.export_dir, 'failure-class', 'override-test.md')
        assert not os.path.exists(default_path)


class TestRemoveEntry:
    def test_deletes_file(self):
        from lib.export import remove_entry
        tmp = tempfile.mkdtemp()
        export_dir = os.path.join(tmp, 'knowledge')
        cat_dir = os.path.join(export_dir, 'failure-class')
        os.makedirs(cat_dir)
        path = os.path.join(cat_dir, 'old-bug.md')
        with open(path, 'w') as f:
            f.write('content')
        remove_entry('failure-class', 'old-bug', export_dir)
        assert not os.path.exists(path)

    def test_missing_file_no_error(self):
        from lib.export import remove_entry
        tmp = tempfile.mkdtemp()
        remove_entry('failure-class', 'nonexistent', os.path.join(tmp, 'knowledge'))


class TestWriteIndex:
    def setup_method(self):
        self.tmp = tempfile.mkdtemp()
        self.export_dir = os.path.join(self.tmp, 'knowledge')
        from lib.db import ContextDB
        self.db_dir = tempfile.mkdtemp()
        self.db = ContextDB(self.db_dir)

    def teardown_method(self):
        self.db.close()

    def test_index_lists_active_entries(self):
        from lib.export import write_index
        self.db.insert_knowledge(category='failure-class', title='Bug A', content='A.', maturity='decision')
        self.db.insert_knowledge(category='failure-class', title='Bug B', content='B.', maturity='decision')
        write_index(self.db, self.export_dir)
        path = os.path.join(self.export_dir, 'index.md')
        assert os.path.exists(path)
        with open(path) as f:
            content = f.read()
        assert 'Bug A' in content
        assert 'Bug B' in content
        assert '## failure-class' in content

    def test_index_excludes_archived(self):
        from lib.export import write_index
        from lib.knowledge import archive
        self.db.insert_knowledge(category='reference', title='Active one', content='Yes.', maturity='decision')
        self.db.insert_knowledge(category='reference', title='Archived one', content='No.', maturity='decision')
        row = self.db.query("SELECT id FROM knowledge WHERE title = 'Archived one'")
        archive(self.db, row[0][0])
        write_index(self.db, self.export_dir)
        with open(os.path.join(self.export_dir, 'index.md')) as f:
            content = f.read()
        assert 'Active one' in content
        assert 'Archived one' not in content

    def test_index_grouped_by_category(self):
        from lib.export import write_index
        self.db.insert_knowledge(category='failure-class', title='FC entry', content='X.', maturity='decision')
        self.db.insert_knowledge(category='coding-convention', title='CC entry', content='Y.', maturity='decision')
        write_index(self.db, self.export_dir)
        with open(os.path.join(self.export_dir, 'index.md')) as f:
            content = f.read()
        assert '## coding-convention' in content
        assert '## failure-class' in content


class TestExportAll:
    def setup_method(self):
        self.tmp = tempfile.mkdtemp()
        self.export_dir = os.path.join(self.tmp, 'knowledge')
        from lib.db import ContextDB
        self.db_dir = tempfile.mkdtemp()
        self.db = ContextDB(self.db_dir)

    def teardown_method(self):
        self.db.close()

    def test_exports_all_active_entries(self):
        from lib.export import export_all
        self.db.insert_knowledge(category='failure-class', title='Entry 1', content='One.', maturity='decision')
        self.db.insert_knowledge(category='reference', title='Entry 2', content='Two.', maturity='decision')
        export_all(self.db, self.export_dir)
        assert os.path.exists(os.path.join(self.export_dir, 'failure-class', 'entry-1.md'))
        assert os.path.exists(os.path.join(self.export_dir, 'reference', 'entry-2.md'))
        assert os.path.exists(os.path.join(self.export_dir, 'index.md'))

    def test_exports_archived_entries_too(self):
        from lib.export import export_all
        from lib.knowledge import archive
        self.db.insert_knowledge(category='reference', title='Archived entry', content='Old.', maturity='decision')
        row = self.db.query("SELECT id FROM knowledge WHERE title = 'Archived entry'")
        archive(self.db, row[0][0])
        export_all(self.db, self.export_dir)
        assert os.path.exists(os.path.join(self.export_dir, 'reference', 'archived-entry.md'))


class TestSafeExport:
    def test_suppresses_exceptions(self):
        from lib.export import _safe_export
        def failing_fn():
            raise OSError("read-only filesystem")
        _safe_export(failing_fn)

    def test_passes_args_through(self):
        from lib.export import _safe_export
        results = []
        def collecting_fn(a, b):
            results.append((a, b))
        _safe_export(collecting_fn, 1, 2)
        assert results == [(1, 2)]
