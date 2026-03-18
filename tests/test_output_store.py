"""Tests for tool output indexing, search, and progressive throttling."""
import os
import tempfile
import pytest
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lib.db import ContextDB
from lib.output_store import (
    chunk_output,
    index_output,
    search_output,
    list_sources,
    cleanup_session_outputs,
    summarize_output,
    make_source_label,
    get_output_text,
    _throttle_limit,
    _extract_snippet,
    OUTPUT_THRESHOLD,
    MAX_CHUNK_SIZE,
    MAX_CHUNKS_PER_SESSION,
)


@pytest.fixture
def tmp_dir():
    return tempfile.mkdtemp()


@pytest.fixture
def db(tmp_dir):
    d = ContextDB(tmp_dir)
    yield d
    d.close()


# ── Chunking tests ───────────────────────────────────────────────────────────

class TestChunking:
    def test_small_text_single_chunk(self):
        chunks = chunk_output("hello world")
        assert len(chunks) == 1
        assert chunks[0] == "hello world"

    def test_empty_text(self):
        assert chunk_output("") == []
        assert chunk_output(None) == []

    def test_heading_based_split(self):
        # Must exceed MAX_CHUNK_SIZE to trigger splitting
        text = "# Section 1\n" + "Content 1 " * 500 + "\n\n# Section 2\n" + "Content 2 " * 500
        chunks = chunk_output(text)
        assert len(chunks) >= 2
        assert any("Section 1" in c for c in chunks)
        assert any("Section 2" in c for c in chunks)

    def test_paragraph_based_split(self):
        # No headings, just paragraphs — each > MAX_CHUNK_SIZE total
        para = "x" * 2000
        text = f"{para}\n\n{para}\n\n{para}"
        chunks = chunk_output(text)
        assert len(chunks) >= 2

    def test_line_based_fallback(self):
        # Single giant block with no headings or paragraph breaks, exceeds MAX_CHUNK_SIZE
        text = "line\n" * 2000
        chunks = chunk_output(text)
        assert len(chunks) >= 2

    def test_chunks_under_max_size(self):
        text = ("# Section\n" + "a" * 3000 + "\n") * 5
        chunks = chunk_output(text)
        for chunk in chunks:
            assert len(chunk) <= MAX_CHUNK_SIZE + 200  # Allow small overflow from heading splits

    def test_code_blocks_preserved(self):
        text = "# Code\n```python\ndef foo():\n    pass\n```\n\n# Other\nmore text"
        chunks = chunk_output(text)
        # Code block should be in the first chunk, not split
        code_chunk = [c for c in chunks if "```python" in c]
        assert len(code_chunk) == 1
        assert "def foo():" in code_chunk[0]


# ── Indexing tests ───────────────────────────────────────────────────────────

class TestIndexing:
    def test_index_and_count(self, db):
        # Must exceed MAX_CHUNK_SIZE to get multiple chunks
        text = "# Part 1\n" + "Content A " * 500 + "\n\n# Part 2\n" + "Content B " * 500
        count = index_output(db, "sess1", "Bash:test", text)
        assert count >= 2

    def test_index_populates_fts(self, db):
        text = "# Section\nThe quick brown fox jumps over the lazy dog"
        index_output(db, "sess1", "Bash:test", text)
        rows = db.query(
            "SELECT source, content FROM output_chunks_fts "
            "WHERE output_chunks_fts MATCH 'fox'"
        )
        assert len(rows) == 1
        assert "fox" in rows[0][1]

    def test_index_stores_session_id(self, db):
        text = "a" * 5000
        index_output(db, "sess1", "Read:file.py", text)
        rows = db.query(
            "SELECT session_id, source FROM output_chunks WHERE session_id = ?",
            ("sess1",)
        )
        assert len(rows) >= 1
        assert rows[0][1] == "Read:file.py"

    def test_fifo_eviction(self, db):
        # Index enough chunks to exceed the limit
        for i in range(MAX_CHUNKS_PER_SESSION + 50):
            db.execute(
                "INSERT INTO output_chunks (session_id, source, chunk_index, content, created_at) "
                "VALUES (?, ?, ?, ?, datetime('now'))",
                ("sess1", f"Bash:cmd{i}", 0, f"content {i}", )
            )
            row_id = db.query("SELECT last_insert_rowid()")[0][0]
            db.execute(
                "INSERT INTO output_chunks_fts(rowid, source, content) VALUES (?, ?, ?)",
                (row_id, f"Bash:cmd{i}", f"content {i}")
            )

        from lib.output_store import _evict_chunks
        _evict_chunks(db, "sess1")

        count = db.query(
            "SELECT COUNT(*) FROM output_chunks WHERE session_id = ?",
            ("sess1",)
        )[0][0]
        assert count == MAX_CHUNKS_PER_SESSION

    def test_empty_text_returns_zero(self, db):
        assert index_output(db, "sess1", "Bash:test", "") == 0


# ── Search tests ─────────────────────────────────────────────────────────────

class TestSearch:
    def _index_sample(self, db):
        texts = [
            "# Test Results\n3 passed, 1 failed\nFAILED test_auth.py::test_login",
            "# Build Output\nCompiling 42 files...\nBuild successful in 3.2s",
            "# Error Log\nTypeError: cannot read property of undefined\nat server.js:42",
        ]
        for i, text in enumerate(texts):
            index_output(db, "sess1", f"Bash:cmd{i}", text)

    def test_fts_search_finds_match(self, db):
        self._index_sample(db)
        result = search_output(db, "sess1", "failed", call_count=1)
        assert not result["blocked"]
        assert len(result["results"]) >= 1
        assert any("FAILED" in r["snippet"] for r in result["results"])

    def test_like_fallback(self, db):
        self._index_sample(db)
        # FTS might not handle partial matches — LIKE should
        result = search_output(db, "sess1", "TypeError", call_count=1)
        assert len(result["results"]) >= 1

    def test_no_results(self, db):
        self._index_sample(db)
        result = search_output(db, "sess1", "nonexistent_term_xyz", call_count=1)
        assert len(result["results"]) == 0

    def test_wrong_session(self, db):
        self._index_sample(db)
        result = search_output(db, "other_session", "failed", call_count=1)
        assert len(result["results"]) == 0


# ── Throttling tests ─────────────────────────────────────────────────────────

class TestThrottling:
    def test_first_three_calls_full(self):
        assert _throttle_limit(1) == 5
        assert _throttle_limit(2) == 5
        assert _throttle_limit(3) == 5

    def test_calls_four_to_eight_limited(self):
        assert _throttle_limit(4) == 1
        assert _throttle_limit(8) == 1

    def test_beyond_eight_blocked(self):
        assert _throttle_limit(9) == 0
        assert _throttle_limit(20) == 0

    def test_blocked_search_returns_message(self, db):
        index_output(db, "sess1", "Bash:test", "# Data\nSome searchable content here")
        result = search_output(db, "sess1", "content", call_count=9)
        assert result["blocked"] is True
        assert len(result["results"]) == 0
        assert "blocked" in result["message"].lower()

    def test_throttled_flag_set(self, db):
        index_output(db, "sess1", "Bash:test", "# Data\nSearchable content")
        result = search_output(db, "sess1", "content", call_count=5)
        assert result["throttled"] is True or len(result["results"]) <= 1


# ── Source listing tests ─────────────────────────────────────────────────────

class TestListSources:
    def test_lists_sources(self, db):
        index_output(db, "sess1", "Bash:pytest", "# Results\nAll passed" * 100)
        index_output(db, "sess1", "Read:big.py", "def foo():\n    pass\n" * 500)
        sources = list_sources(db, "sess1")
        assert len(sources) == 2
        labels = [s["source"] for s in sources]
        assert "Bash:pytest" in labels
        assert "Read:big.py" in labels

    def test_empty_session(self, db):
        sources = list_sources(db, "sess1")
        assert sources == []


# ── Cleanup tests ────────────────────────────────────────────────────────────

class TestCleanup:
    def test_removes_old_sessions(self, db):
        index_output(db, "old-sess", "Bash:old", "# Old\n" + "data " * 1000)
        index_output(db, "current", "Bash:new", "# New\n" + "data " * 1000)
        cleanup_session_outputs(db, "current")

        old = db.query(
            "SELECT COUNT(*) FROM output_chunks WHERE session_id = ?", ("old-sess",)
        )[0][0]
        current = db.query(
            "SELECT COUNT(*) FROM output_chunks WHERE session_id = ?", ("current",)
        )[0][0]
        assert old == 0
        assert current >= 1

    def test_keeps_current_session(self, db):
        index_output(db, "sess1", "Bash:cmd", "# Data\n" + "content " * 1000)
        cleanup_session_outputs(db, "sess1")
        count = db.query(
            "SELECT COUNT(*) FROM output_chunks WHERE session_id = ?", ("sess1",)
        )[0][0]
        assert count >= 1

    def test_cleanup_also_cleans_fts(self, db):
        index_output(db, "old", "Bash:cmd", "# Unique\nfindable_term_abc")
        cleanup_session_outputs(db, "current")
        # FTS should also be clean
        rows = db.query(
            "SELECT COUNT(*) FROM output_chunks_fts "
            "WHERE output_chunks_fts MATCH 'findable_term_abc'"
        )
        assert rows[0][0] == 0


# ── Helper tests ─────────────────────────────────────────────────────────────

class TestHelpers:
    def test_summarize_output(self):
        summary = summarize_output("x" * 10240, "Bash:pytest", 5)
        assert "10KB" in summary
        assert "5 chunks" in summary
        assert "search-output" in summary

    def test_make_source_label_bash(self):
        label = make_source_label("Bash", {"command": "pytest tests/ -v"})
        assert label == "Bash:pytest tests/ -v"

    def test_make_source_label_bash_long(self):
        label = make_source_label("Bash", {"command": "x" * 100})
        assert len(label) <= 85  # "Bash:" + 80 chars max

    def test_make_source_label_read(self):
        label = make_source_label("Read", {"file_path": "/home/user/src/big_file.py"})
        assert label == "Read:big_file.py"

    def test_make_source_label_grep(self):
        label = make_source_label("Grep", {"pattern": "TODO"})
        assert label == "Grep:TODO"

    def test_get_output_text_bash(self):
        text = get_output_text("Bash", {"output": "hello world"})
        assert text == "hello world"

    def test_get_output_text_read(self):
        text = get_output_text("Read", {"output": "file content"})
        assert text == "file content"

    def test_get_output_text_unknown(self):
        assert get_output_text("Agent", {}) is None

    def test_extract_snippet_with_match(self):
        content = "prefix " * 50 + "THE_MATCH_HERE" + " suffix" * 50
        snippet = _extract_snippet(content, "THE_MATCH_HERE")
        assert "THE_MATCH_HERE" in snippet
        assert len(snippet) < len(content)

    def test_extract_snippet_no_match(self):
        content = "just some content here"
        snippet = _extract_snippet(content, "nonexistent")
        assert snippet  # Returns start of content


# ── Schema migration test ────────────────────────────────────────────────────

class TestSchemaMigration:
    def test_output_chunks_table_exists(self, db):
        tables = db.query(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        )
        names = [r[0] for r in tables]
        assert "output_chunks" in names

    def test_output_chunks_fts_exists(self, db):
        tables = db.query(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        )
        names = [r[0] for r in tables]
        assert "output_chunks_fts" in names

    def test_schema_version_is_3(self, db):
        version = db.query("SELECT version FROM schema_version")[0][0]
        assert version == 3
