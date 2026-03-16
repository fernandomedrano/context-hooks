# Knowledge Durability Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Dual-write knowledge entries to SQLite (querying) and git-tracked markdown (durability) so institutional memory survives catastrophic data loss.

**Architecture:** New `lib/export.py` module (~150 lines) with write_entry, remove_entry, write_index, export_all functions. Each knowledge mutation in `lib/knowledge.py` calls `_safe_export()` after its SQLite write. Export dir resolved from git_root + cluster config. Opt-in via `knowledge_export: true` in project config.

**Tech Stack:** Python 3.10+ stdlib only (os, re, datetime). SQLite via existing `lib/db.py`. Config via existing `lib/config.py`.

**Spec:** `docs/superpowers/specs/2026-03-16-knowledge-durability-design.md`

---

## File Map

| File | Action | Responsibility |
|------|--------|---------------|
| `lib/export.py` | Create | Markdown export: write_entry, remove_entry, write_index, export_all, _slugify, _safe_export, resolve_export_dir, _render_frontmatter |
| `tests/test_export.py` | Create | Unit tests for export.py |
| `lib/knowledge.py` | Modify (lines 12-122) | Wire _safe_export calls into store, promote, archive, restore, dismiss, supersede |
| `tests/test_knowledge.py` | Modify | Integration tests: mutation → verify markdown file written |
| `lib/mcp_tools.py` | Modify (lines 60-129, 402-419) | Wire export_dir into all mutation handlers + add ref fields to supersede schema |
| `lib/knowledge.py` | Modify (line 99) | Add ref fields to supersede() signature |
| `bin/context-hooks` | Modify (line 44) | Update help text for knowledge export command |
| `README.md` | Modify (lines 108-119, 158-179) | Add knowledge export docs, use cases, config |

---

## Chunk 1: Core Export Module (TDD)

### Task 1: Slug generation + frontmatter rendering

**Files:**
- Create: `lib/export.py`
- Create: `tests/test_export.py`

- [ ] **Step 1: Write failing tests for _slugify**

```python
# tests/test_export.py
import sys, os, tempfile
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3.12 -m pytest tests/test_export.py -v`
Expected: ImportError — `lib.export` doesn't exist yet

- [ ] **Step 3: Implement _slugify and _render_frontmatter**

```python
# lib/export.py
"""Knowledge durability: dual-write markdown export. CLI-callable."""
import os
import re
import sys


def _slugify(title, entry_id=0):
    """Convert title to filesystem-safe kebab-case slug."""
    slug = title.lower()
    slug = re.sub(r'[^a-z0-9\s-]', '', slug)
    slug = re.sub(r'[\s_]+', '-', slug)
    slug = re.sub(r'-+', '-', slug)
    slug = slug.strip('-')[:80]
    return slug if slug else f'entry-{entry_id}'


def _render_frontmatter(entry):
    """Render a knowledge entry dict as markdown with YAML frontmatter."""
    lines = ['---']
    for key in ('id', 'category', 'maturity', 'status', 'title'):
        if entry.get(key) is not None:
            lines.append(f'{key}: {entry[key]}')
    # Optional fields — only include if non-empty
    for key in ('file_refs', 'commit_refs', 'bug_refs', 'tags'):
        val = entry.get(key)
        if val:
            lines.append(f'{key}: {val}')
    if entry.get('superseded_by'):
        lines.append(f'superseded_by: {entry["superseded_by"]}')
    lines.append(f'created: {entry.get("created_at", "")}')
    lines.append(f'updated: {entry.get("updated_at", "")}')
    lines.append('---')
    lines.append('')
    lines.append(entry.get('content', ''))
    lines.append('')
    return '\n'.join(lines)
```

- [ ] **Step 4: Add frontmatter rendering tests**

```python
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
```

- [ ] **Step 5: Run tests to verify all pass**

Run: `python3.12 -m pytest tests/test_export.py -v`
Expected: All pass

- [ ] **Step 6: Commit**

```bash
git add lib/export.py tests/test_export.py
git commit -m "feat: add _slugify and _render_frontmatter for knowledge export"
```

### Task 2: resolve_export_dir

**Files:**
- Modify: `lib/export.py`
- Modify: `tests/test_export.py`

- [ ] **Step 1: Write failing tests for resolve_export_dir**

```python
class TestResolveExportDir:
    def setup_method(self):
        self.tmp = tempfile.mkdtemp()

    def test_disabled_returns_none(self):
        from lib.export import resolve_export_dir
        # No config.yaml → export disabled → None
        result = resolve_export_dir('/fake/git/root', self.tmp)
        assert result is None

    def test_enabled_returns_default_path(self):
        from lib.export import resolve_export_dir
        # Write config enabling export
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3.12 -m pytest tests/test_export.py::TestResolveExportDir -v`
Expected: ImportError — `resolve_export_dir` doesn't exist yet

- [ ] **Step 3: Implement resolve_export_dir**

Add to `lib/export.py`:

```python
def resolve_export_dir(git_root, project_dir):
    """Return the export directory, or None if export is disabled."""
    from lib.config import load_config
    config = load_config(project_dir)
    if not config.get('knowledge_export'):
        return None

    # In a cluster, export goes to the master's repo
    effective_git_root = git_root
    cluster_path = os.path.join(project_dir, 'cluster.yaml')
    if os.path.exists(cluster_path):
        from lib.config import _parse_simple_yaml
        with open(cluster_path) as f:
            cluster_config = _parse_simple_yaml(f.read())
        master_root = cluster_config.get('master')
        if master_root and master_root.strip():
            effective_git_root = master_root

    custom = config.get('knowledge_export_dir')
    if custom:
        return os.path.join(effective_git_root, custom)
    return os.path.join(effective_git_root, 'data', 'knowledge')
```

- [ ] **Step 4: Run tests to verify all pass**

Run: `python3.12 -m pytest tests/test_export.py -v`
Expected: All pass

- [ ] **Step 5: Commit**

```bash
git add lib/export.py tests/test_export.py
git commit -m "feat: add resolve_export_dir with cluster support"
```

### Task 3: write_entry and remove_entry

**Files:**
- Modify: `lib/export.py`
- Modify: `tests/test_export.py`

- [ ] **Step 1: Write failing tests for write_entry**

```python
class TestWriteEntry:
    def setup_method(self):
        self.tmp = tempfile.mkdtemp()
        self.export_dir = os.path.join(self.tmp, 'knowledge')
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
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
```

- [ ] **Step 2: Write failing tests for remove_entry**

```python
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
        # Should not raise
        remove_entry('failure-class', 'nonexistent', os.path.join(tmp, 'knowledge'))
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `python3.12 -m pytest tests/test_export.py::TestWriteEntry tests/test_export.py::TestRemoveEntry -v`
Expected: ImportError — functions don't exist

- [ ] **Step 4: Implement write_entry and remove_entry**

Add to `lib/export.py`:

```python
def _fetch_entry(db, entry_id):
    """Fetch a knowledge entry as a dict."""
    rows = db.query(
        "SELECT id, category, maturity, title, content, reasoning, "
        "status, superseded_by, bug_refs, file_refs, commit_refs, tags, "
        "created_at, updated_at "
        "FROM knowledge WHERE id = ?",
        (entry_id,)
    )
    if not rows:
        return None
    r = rows[0]
    return {
        'id': r[0], 'category': r[1], 'maturity': r[2], 'title': r[3],
        'content': r[4], 'reasoning': r[5], 'status': r[6],
        'superseded_by': r[7], 'bug_refs': r[8], 'file_refs': r[9],
        'commit_refs': r[10], 'tags': r[11],
        'created_at': r[12], 'updated_at': r[13]
    }


def write_entry(db, entry_id, export_dir, filename=None):
    """Write/update one entry's markdown file.

    Args:
        filename: Override the slug-derived filename (e.g., for superseded entries
                  that were renamed to avoid same-title collision). Without .md extension.
    """
    entry = _fetch_entry(db, entry_id)
    if not entry:
        return
    slug = filename or _slugify(entry['title'], entry_id=entry['id'])
    cat_dir = os.path.join(export_dir, entry['category'])
    os.makedirs(cat_dir, exist_ok=True)
    path = os.path.join(cat_dir, f'{slug}.md')
    content = _render_frontmatter(entry)
    with open(path, 'w') as f:
        f.write(content)


def remove_entry(category, slug, export_dir):
    """Delete a knowledge entry's markdown file."""
    path = os.path.join(export_dir, category, f'{slug}.md')
    if os.path.exists(path):
        os.remove(path)
```

- [ ] **Step 5: Run tests to verify all pass**

Run: `python3.12 -m pytest tests/test_export.py -v`
Expected: All pass

- [ ] **Step 6: Commit**

```bash
git add lib/export.py tests/test_export.py
git commit -m "feat: add write_entry and remove_entry for knowledge export"
```

### Task 4: write_index and export_all

**Files:**
- Modify: `lib/export.py`
- Modify: `tests/test_export.py`

- [ ] **Step 1: Write failing tests for write_index**

```python
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
```

- [ ] **Step 2: Write failing tests for export_all**

```python
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
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `python3.12 -m pytest tests/test_export.py::TestWriteIndex tests/test_export.py::TestExportAll -v`
Expected: ImportError

- [ ] **Step 4: Implement write_index and export_all**

Add to `lib/export.py`:

```python
def write_index(db, export_dir):
    """Regenerate index.md from all active entries."""
    rows = db.query(
        "SELECT id, category, title, tags FROM knowledge "
        "WHERE status = 'active' ORDER BY category, title"
    )
    os.makedirs(export_dir, exist_ok=True)

    # Group by category
    groups = {}
    for row in rows:
        cat = row[1]
        groups.setdefault(cat, []).append(row)

    lines = [
        '# Knowledge Index',
        '',
        '*Auto-generated. Do not edit — regenerated on each knowledge mutation.*',
        ''
    ]
    for cat in sorted(groups.keys()):
        entries = groups[cat]
        lines.append(f'## {cat} ({len(entries)} active)')
        lines.append('')
        for row in entries:
            entry_id, _, title, tags = row
            slug = _slugify(title, entry_id=entry_id)
            tag_suffix = f' — {tags}' if tags else ''
            lines.append(f'- [{title}]({cat}/{slug}.md){tag_suffix}')
        lines.append('')

    path = os.path.join(export_dir, 'index.md')
    with open(path, 'w') as f:
        f.write('\n'.join(lines))


def export_all(db, export_dir):
    """Bulk re-export all active + archived entries and regenerate index."""
    rows = db.query(
        "SELECT id FROM knowledge WHERE status IN ('active', 'archived') ORDER BY id"
    )
    for row in rows:
        write_entry(db, row[0], export_dir)
    write_index(db, export_dir)
```

- [ ] **Step 5: Run tests to verify all pass**

Run: `python3.12 -m pytest tests/test_export.py -v`
Expected: All pass

- [ ] **Step 6: Commit**

```bash
git add lib/export.py tests/test_export.py
git commit -m "feat: add write_index and export_all for bulk knowledge export"
```

### Task 5: _safe_export wrapper

**Files:**
- Modify: `lib/export.py`
- Modify: `tests/test_export.py`

- [ ] **Step 1: Write failing test for _safe_export**

```python
class TestSafeExport:
    def test_suppresses_exceptions(self):
        from lib.export import _safe_export
        def failing_fn():
            raise OSError("read-only filesystem")
        # Should not raise
        _safe_export(failing_fn)

    def test_passes_args_through(self):
        from lib.export import _safe_export
        results = []
        def collecting_fn(a, b):
            results.append((a, b))
        _safe_export(collecting_fn, 1, 2)
        assert results == [(1, 2)]
```

- [ ] **Step 2: Run, verify fail, implement**

Add to `lib/export.py`:

```python
def _safe_export(fn, *args, **kwargs):
    """Call export function, logging but not raising on failure."""
    try:
        fn(*args, **kwargs)
    except Exception as e:
        print(f"WARNING: knowledge export failed: {e}", file=sys.stderr)
```

- [ ] **Step 3: Run all export tests**

Run: `python3.12 -m pytest tests/test_export.py -v`
Expected: All pass

- [ ] **Step 4: Commit**

```bash
git add lib/export.py tests/test_export.py
git commit -m "feat: add _safe_export wrapper for non-blocking export failures"
```

---

## Chunk 2: Wire Export Into Knowledge Mutations

### Task 6: Wire store() and promote()

**Files:**
- Modify: `lib/knowledge.py` (lines 12-18, 55-69)
- Modify: `tests/test_knowledge.py`

- [ ] **Step 1: Write integration tests**

Add to `tests/test_knowledge.py`:

```python
class TestKnowledgeExport:
    """Integration tests: knowledge mutations trigger markdown export."""

    def setup_method(self):
        self.tmp = tempfile.mkdtemp()
        self.db_dir = tempfile.mkdtemp()
        self.export_dir = os.path.join(self.tmp, 'knowledge')
        from lib.db import ContextDB
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
              export_dir=self.export_dir)
        row = self.db.query("SELECT id FROM knowledge WHERE title = 'Promote test'")
        promote(self.db, row[0][0], export_dir=self.export_dir)
        path = os.path.join(self.export_dir, 'failure-class', 'promote-test.md')
        with open(path) as f:
            text = f.read()
        assert 'maturity: pattern' in text

    def test_store_no_export_when_dir_none(self):
        from lib.knowledge import store
        # Should not raise or write anything
        store(self.db, 'reference', 'No export', 'Body.', export_dir=None)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3.12 -m pytest tests/test_knowledge.py::TestKnowledgeExport -v`
Expected: TypeError — `store()` doesn't accept `export_dir`

- [ ] **Step 3: Modify store() and promote() in knowledge.py**

Update `lib/knowledge.py` — add `export_dir=None` parameter to both functions:

```python
def store(db, category, title, content, reasoning=None, bug_refs=None, file_refs=None, tags=None, maturity='decision', export_dir=None):
    """Store a new knowledge entry. Maturity defaults to 'decision'."""
    db.insert_knowledge(
        category=category, title=title, content=content,
        reasoning=reasoning, maturity=maturity,
        bug_refs=bug_refs, file_refs=file_refs, tags=tags
    )
    if export_dir:
        from lib.export import write_entry, write_index, _safe_export
        row = db.query("SELECT id FROM knowledge WHERE title = ? AND status = 'active' ORDER BY id DESC LIMIT 1", (title,))
        if row:
            _safe_export(write_entry, db, row[0][0], export_dir)
            _safe_export(write_index, db, export_dir)


def promote(db, entry_id, export_dir=None):
    """Advance maturity by one stage. Returns error if already at convention."""
    rows = db.query("SELECT maturity FROM knowledge WHERE id = ?", (entry_id,))
    if not rows:
        raise ValueError(f"Entry {entry_id} not found")
    current = rows[0][0]
    idx = MATURITY_ORDER.index(current)
    if idx >= len(MATURITY_ORDER) - 1:
        raise ValueError(f"Already at maximum maturity ({current})")
    new_maturity = MATURITY_ORDER[idx + 1]
    from datetime import datetime
    db.execute(
        "UPDATE knowledge SET maturity = ?, updated_at = ? WHERE id = ?",
        (new_maturity, datetime.now().isoformat(), entry_id)
    )
    if export_dir:
        from lib.export import write_entry, write_index, _safe_export
        _safe_export(write_entry, db, entry_id, export_dir)
        _safe_export(write_index, db, export_dir)
```

- [ ] **Step 4: Run tests**

Run: `python3.12 -m pytest tests/test_knowledge.py -v`
Expected: All pass (existing tests still work because `export_dir` defaults to None)

- [ ] **Step 5: Commit**

```bash
git add lib/knowledge.py tests/test_knowledge.py
git commit -m "feat: wire export into store() and promote()"
```

### Task 7: Wire archive(), restore(), dismiss()

**Files:**
- Modify: `lib/knowledge.py` (lines 72-96)
- Modify: `tests/test_knowledge.py`

- [ ] **Step 1: Write integration tests**

```python
    # Add to TestKnowledgeExport class:

    def test_archive_keeps_file_updates_frontmatter(self):
        from lib.knowledge import store, archive
        store(self.db, 'reference', 'Archive test', 'Content.',
              export_dir=self.export_dir)
        row = self.db.query("SELECT id FROM knowledge WHERE title = 'Archive test'")
        archive(self.db, row[0][0], export_dir=self.export_dir)
        path = os.path.join(self.export_dir, 'reference', 'archive-test.md')
        assert os.path.exists(path)  # File retained
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3.12 -m pytest tests/test_knowledge.py::TestKnowledgeExport -v`
Expected: TypeError — archive/restore/dismiss don't accept `export_dir`

- [ ] **Step 3: Modify archive(), restore(), dismiss()**

```python
def archive(db, entry_id, export_dir=None):
    """Set status to archived."""
    from datetime import datetime
    db.execute(
        "UPDATE knowledge SET status = 'archived', updated_at = ? WHERE id = ?",
        (datetime.now().isoformat(), entry_id)
    )
    if export_dir:
        from lib.export import write_entry, write_index, _safe_export
        _safe_export(write_entry, db, entry_id, export_dir)
        _safe_export(write_index, db, export_dir)


def restore(db, entry_id, export_dir=None):
    """Restore from archived or superseded back to active."""
    from datetime import datetime
    db.execute(
        "UPDATE knowledge SET status = 'active', updated_at = ? WHERE id = ?",
        (datetime.now().isoformat(), entry_id)
    )
    if export_dir:
        from lib.export import write_entry, write_index, _safe_export
        _safe_export(write_entry, db, entry_id, export_dir)
        _safe_export(write_index, db, export_dir)


def dismiss(db, entry_id, export_dir=None):
    """Set status to dismissed. Won't resurface in health suggestions."""
    from datetime import datetime
    # Fetch category and title before updating (needed for file path)
    rows = db.query("SELECT category, title FROM knowledge WHERE id = ?", (entry_id,))
    if rows:
        category, title = rows[0]
    db.execute(
        "UPDATE knowledge SET status = 'dismissed', updated_at = ? WHERE id = ?",
        (datetime.now().isoformat(), entry_id)
    )
    if export_dir and rows:
        from lib.export import remove_entry, write_index, _safe_export, _slugify
        _safe_export(remove_entry, category, _slugify(title, entry_id=entry_id), export_dir)
        _safe_export(write_index, db, export_dir)
```

- [ ] **Step 4: Run all tests**

Run: `python3.12 -m pytest tests/test_knowledge.py -v`
Expected: All pass

- [ ] **Step 5: Commit**

```bash
git add lib/knowledge.py tests/test_knowledge.py
git commit -m "feat: wire export into archive(), restore(), dismiss()"
```

### Task 8: Wire supersede() with same-title rename

**Files:**
- Modify: `lib/knowledge.py` (lines 99-122)
- Modify: `tests/test_knowledge.py`

- [ ] **Step 1: Write integration tests**

```python
    # Add to TestKnowledgeExport class:

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
        # Old file should be renamed with superseded status
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3.12 -m pytest tests/test_knowledge.py::TestKnowledgeExport::test_supersede_writes_both_files tests/test_knowledge.py::TestKnowledgeExport::test_supersede_same_title_renames_old -v`
Expected: TypeError

- [ ] **Step 3: Modify supersede()**

Update `lib/knowledge.py`:

```python
def supersede(db, old_id, new_category, new_title, new_content, new_reasoning=None,
              new_bug_refs=None, new_file_refs=None, new_commit_refs=None, new_tags=None,
              export_dir=None):
    """Replace old entry with new one. Links via superseded_by."""
    from datetime import datetime
    now = datetime.now().isoformat()
    # Fetch old entry info before update (for export rename logic)
    old_rows = db.query("SELECT category, title FROM knowledge WHERE id = ?", (old_id,))
    old_category = old_rows[0][0] if old_rows else None
    old_title = old_rows[0][1] if old_rows else None
    # Mark old entry as superseded first (avoids UNIQUE constraint on title+status)
    db.execute(
        "UPDATE knowledge SET status = 'superseded', updated_at = ? WHERE id = ?",
        (now, old_id)
    )
    # Insert the new entry
    db.insert_knowledge(
        category=new_category, title=new_title, content=new_content,
        reasoning=new_reasoning, maturity='decision',
        bug_refs=new_bug_refs, file_refs=new_file_refs,
        commit_refs=new_commit_refs, tags=new_tags
    )
    # Get the new entry's id and link it
    new_row = db.query(
        "SELECT id FROM knowledge WHERE title = ? AND status = 'active' ORDER BY id DESC LIMIT 1",
        (new_title,)
    )
    new_id = new_row[0][0]
    db.execute(
        "UPDATE knowledge SET superseded_by = ? WHERE id = ?",
        (new_id, old_id)
    )
    if export_dir and old_title:
        from lib.export import write_entry, write_index, _safe_export, _slugify
        import os as _os
        old_slug = _slugify(old_title, entry_id=old_id)
        new_slug = _slugify(new_title, entry_id=new_id)
        # Same-title rename: move old file before writing new, then use filename override
        old_filename = None
        if old_slug == new_slug:
            old_path = _os.path.join(export_dir, old_category, f'{old_slug}.md')
            old_filename = f'{old_slug}-superseded-{old_id}'
            renamed = _os.path.join(export_dir, old_category, f'{old_filename}.md')
            if _os.path.exists(old_path):
                _os.rename(old_path, renamed)
        _safe_export(write_entry, db, old_id, export_dir, filename=old_filename)
        _safe_export(write_entry, db, new_id, export_dir)
        _safe_export(write_index, db, export_dir)
```

- [ ] **Step 4: Run all tests**

Run: `python3.12 -m pytest tests/test_knowledge.py -v`
Expected: All pass (existing supersede tests still work — new params default to None)

- [ ] **Step 5: Commit**

```bash
git add lib/knowledge.py tests/test_knowledge.py
git commit -m "feat: wire export into supersede() with same-title rename"
```

---

## Chunk 3: CLI, MCP, and README

### Task 9: Add `knowledge export` CLI command

**Files:**
- Modify: `lib/knowledge.py` (main function, ~line 235)
- Modify: `bin/context-hooks` (help text, line 44)

- [ ] **Step 1: Add `export` case to knowledge.main()**

In `lib/knowledge.py`, add after the `supersede` elif block (~line 311):

```python
        elif cmd == 'export':
            dry_run = '--dry-run' in args
            export_dir = resolve_export_dir(git_root, project_dir)
            if not export_dir:
                # If not configured, use default for one-off export
                export_dir = os.path.join(git_root, 'data', 'knowledge')
            if dry_run:
                rows = db.query(
                    "SELECT id, category, title, status FROM knowledge "
                    "WHERE status IN ('active', 'archived') ORDER BY id"
                )
                print(f"Would export {len(rows)} entries to {export_dir}/")
                for r in rows:
                    slug = _slugify(r[2], entry_id=r[0])
                    print(f"  {r[1]}/{slug}.md ({r[3]})")
            else:
                from lib.export import export_all
                export_all(db, export_dir)
                rows = db.query("SELECT COUNT(*) FROM knowledge WHERE status IN ('active', 'archived')")
                print(f"Exported {rows[0][0]} entries to {export_dir}/")
```

Add these imports at the top of the `export` branch:

```python
        elif cmd == 'export':
            from lib.export import export_all, resolve_export_dir, _slugify
            dry_run = '--dry-run' in args
            ...
```

- [ ] **Step 2: Update bin/context-hooks help text**

Change line 44 from:
```
Knowledge: knowledge store|search|list|promote|archive|restore|dismiss
```
to:
```
Knowledge: knowledge store|search|list|promote|archive|restore|dismiss|export
```

- [ ] **Step 3: Test manually**

Run: `bin/context-hooks knowledge export --dry-run`
Expected: "Would export 0 entries" (context-hooks has no knowledge entries)

- [ ] **Step 4: Commit**

```bash
git add lib/knowledge.py bin/context-hooks
git commit -m "feat: add knowledge export CLI command with --dry-run"
```

### Task 9b: Wire export_dir into CLI main() and MCP handlers

All mutation call sites (CLI `main()` and MCP handlers) must resolve and pass `export_dir` so dual-write actually fires.

**Files:**
- Modify: `lib/knowledge.py` (main function — store, promote, archive, restore, dismiss, supersede cases)
- Modify: `lib/mcp_tools.py` (store, promote, archive, restore, supersede handlers)

- [ ] **Step 1: Add resolve_export_dir call to knowledge.main()**

At the top of `main()` in `lib/knowledge.py`, after `db = ContextDB(cluster_dir)` (~line 243), add:

```python
    from lib.export import resolve_export_dir
    export_dir = resolve_export_dir(git_root, project_dir)
```

Then pass `export_dir=export_dir` to each mutation call in the CLI dispatcher:

- `store(db, args[1], args[2], args[3], ..., export_dir=export_dir)` (~line 269)
- `promote(db, int(args[1]), export_dir=export_dir)` (~line 289)
- `archive(db, int(args[1]), export_dir=export_dir)` (~line 293)
- `restore(db, int(args[1]), export_dir=export_dir)` (~line 298)
- `dismiss(db, int(args[1]), export_dir=export_dir)` (~line 303)
- `supersede(db, int(args[1]), args[2], args[3], args[4], reasoning, export_dir=export_dir)` (~line 318)

When `knowledge_export` is not enabled, `resolve_export_dir` returns `None` and all mutations skip export (existing default behavior).

- [ ] **Step 2: Wire export_dir into MCP handlers**

In `lib/mcp_tools.py`, add at the top of `build_handlers(ctx)`:

```python
    from lib.export import resolve_export_dir
    export_dir = resolve_export_dir(ctx.get('git_root', ''), ctx.get('project_dir', ''))
```

Then pass `export_dir=export_dir` to each knowledge mutation call:

- `context_store_knowledge`: `knowledge.store(db, ..., export_dir=export_dir)`
- `context_promote_knowledge`: `knowledge.promote(db, ..., export_dir=export_dir)`
- `context_archive_knowledge`: `knowledge.archive(db, ..., export_dir=export_dir)`
- `context_restore_knowledge`: `knowledge.restore(db, ..., export_dir=export_dir)`
- `context_supersede_knowledge`: `knowledge.supersede(db, ..., export_dir=export_dir)`

- [ ] **Step 3: Run all tests**

Run: `python3.12 -m pytest tests/ -v`
Expected: All pass (MCP tests use temp dirs with no config → `resolve_export_dir` returns `None` → no export → behavior unchanged)

- [ ] **Step 4: Commit**

```bash
git add lib/knowledge.py lib/mcp_tools.py
git commit -m "feat: wire export_dir into all CLI and MCP mutation call sites"
```

### Task 10: Update MCP supersede schema

**Files:**
- Modify: `lib/mcp_tools.py` (lines 111-120 handler, lines 418-419 schema)
- Modify: `tests/test_mcp_tools.py`

- [ ] **Step 1: Write test for ref fields in supersede handler**

Add to `tests/test_mcp_tools.py`:

```python
    def test_supersede_with_ref_fields(self):
        """context_supersede_knowledge should pass ref fields to supersede()."""
        # Store an entry first
        self.handlers["context_store_knowledge"]({
            "category": "failure-class", "title": "Old entry", "content": "Old."
        })
        rows = self.db.query("SELECT id FROM knowledge WHERE title = 'Old entry'")
        result = self.handlers["context_supersede_knowledge"]({
            "old_id": rows[0][0], "category": "failure-class",
            "title": "New entry", "content": "New.",
            "file_refs": "foo.py", "bug_refs": "BUG-100"
        })
        new_rows = self.db.query("SELECT file_refs, bug_refs FROM knowledge WHERE title = 'New entry'")
        assert new_rows[0][0] == "foo.py"
        assert new_rows[0][1] == "BUG-100"
```

- [ ] **Step 2: Update handler and schema**

In `lib/mcp_tools.py`, update the handler (~line 111):

```python
    def context_supersede_knowledge(args):
        db = _open_cluster_db(ctx)
        try:
            knowledge.supersede(
                db, args["old_id"], args["category"], args["title"],
                args["content"], args.get("reasoning"),
                new_bug_refs=args.get("bug_refs"),
                new_file_refs=args.get("file_refs"),
                new_commit_refs=args.get("commit_refs"),
                new_tags=args.get("tags")
            )
            return f"Superseded entry {args['old_id']} with '{args['title']}'"
        finally:
            db.close()
```

Update the schema entry (~line 418):

```python
    ("context_supersede_knowledge", None, "Replace a knowledge entry with a new one, preserving lineage",
     {"type": "object", "properties": {
         "old_id": {"type": "integer"}, "category": {"type": "string", "enum": ["architectural-decision", "coding-convention", "failure-class", "reference", "rejected-approach"]},
         "title": {"type": "string"}, "content": {"type": "string"}, "reasoning": {"type": "string"},
         "bug_refs": {"type": "string"}, "file_refs": {"type": "string"}, "commit_refs": {"type": "string"}, "tags": {"type": "string"}
     }, "required": ["old_id", "category", "title", "content"]}),
```

- [ ] **Step 3: Run tests**

Run: `python3.12 -m pytest tests/test_mcp_tools.py -v`
Expected: All pass

- [ ] **Step 4: Commit**

```bash
git add lib/mcp_tools.py tests/test_mcp_tools.py
git commit -m "feat: add ref fields to MCP supersede_knowledge schema"
```

### Task 11: Update README with use cases and config

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Update Knowledge Store command table**

After the existing knowledge commands table (line 117), add the export command:

```markdown
| `context-hooks knowledge export` | Re-export all active + archived entries to markdown |
| `context-hooks knowledge export --dry-run` | Show what would be exported |
```

- [ ] **Step 2: Add Knowledge Durability section after Knowledge Store**

Insert after line 119 (Categories list):

```markdown
### Knowledge Durability

Knowledge entries can be dual-written to both SQLite (for fast querying) and git-tracked markdown files (for durability). This ensures institutional memory survives database corruption, accidental deletion, or machine migration.

**Common use cases:**

- **Disaster recovery** — If your SQLite DB is lost, knowledge entries are preserved as markdown files in your repo's git history
- **Team visibility** — Knowledge entries show up in PRs and `git log`, making institutional decisions visible to the whole team
- **Cross-tool access** — Other tools (grep, editors, scripts) can read knowledge without going through context-hooks
- **Audit trail** — `git blame` on knowledge files shows when decisions were made and evolved

**Enable it:**

```bash
# Per-project: add to ~/.context-hooks/projects/<hash>/config.yaml
knowledge_export: true

# Custom export location (default: data/knowledge/ in repo root)
knowledge_export: true
knowledge_export_dir: docs/knowledge
```

Once enabled, every knowledge mutation (store, promote, archive, dismiss, supersede) automatically writes a corresponding markdown file:

```
<repo>/data/knowledge/
├── index.md                              # auto-generated, links all active entries
├── failure-class/
│   ├── context-correction-ignored.md
│   └── venue-tendency-hallucination.md
├── coding-convention/
│   └── always-use-parameterized-queries.md
└── reference/
    └── api-rate-limiting.md
```

To bulk-export existing entries (e.g., after enabling export on a project with existing knowledge):

```bash
context-hooks knowledge export            # writes all entries + index
context-hooks knowledge export --dry-run  # preview without writing
```
```

- [ ] **Step 3: Update Configuration section**

Add to the Per-Project Overrides section (~line 179):

```markdown
knowledge_export: true              # enable dual-write to markdown (default: false)
knowledge_export_dir: docs/knowledge # custom export path (default: data/knowledge)
```

- [ ] **Step 4: Update test count**

Update line 241 from `146 tests` to match actual count after implementation.

- [ ] **Step 5: Commit**

```bash
git add README.md
git commit -m "docs: add knowledge durability use cases and config to README"
```

### Task 12: Run full test suite

- [ ] **Step 1: Run all tests**

Run: `python3.12 -m pytest tests/ -v`
Expected: All pass (should be ~270+ tests)

- [ ] **Step 2: Run CLI smoke test**

Run: `bin/context-hooks help`
Expected: Shows updated help text with `export` in knowledge line

Run: `bin/context-hooks status`
Expected: No crash

- [ ] **Step 3: Final commit if any fixups needed**

```bash
git add -A
git commit -m "fix: address any test/lint issues from knowledge durability"
```
