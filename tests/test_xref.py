import os, sys, tempfile, re
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from lib.db import ContextDB
from lib.xref import run_xref, extract_memory_rules, find_memory_file


class TestExtractMemoryRules:
    def test_extracts_bold_patterns(self):
        content = """
## Key Patterns

- **Data Flow Completeness (CRITICAL)** — New fields: trace the complete flow.
- **Vite proxy** — Use `127.0.0.1:8002` not `localhost:8002` in `vite.config.ts`.
- **BUG-131 root cause** — `ExtractedContext.to_dict()` stores board as dict. Commit `89fb870`.
"""
        rules = extract_memory_rules(content)
        assert len(rules) == 3
        assert rules[0]["name"] == "Data Flow Completeness (CRITICAL)"
        assert "vite.config" in rules[1].get("terms", set())

    def test_empty_content(self):
        assert extract_memory_rules("") == []

    def test_extracts_bug_refs(self):
        content = "- **Board carry-forward format (BUG-131 root cause, `89fb870`)** — description"
        rules = extract_memory_rules(content)
        assert len(rules) == 1
        assert "BUG-131" in rules[0]["terms"]
        assert "89fb870" in rules[0]["hashes"]

    def test_extracts_adr_refs(self):
        content = "- **ADR-026 format constraint pipeline** — Detection in `_detect_question_constraint()`."
        rules = extract_memory_rules(content)
        assert len(rules) == 1
        assert "ADR-26" in rules[0]["terms"] or "ADR-026" in rules[0]["terms"]

    def test_extracts_concept_words(self):
        content = "- **Streaming/sync parity** — The two paths are chat.py (streaming SSE) and chat_service.py."
        rules = extract_memory_rules(content)
        assert len(rules) == 1
        assert "streaming" in rules[0]["terms"] or "parity" in rules[0]["terms"]


class TestFindMemoryFile:
    def test_returns_none_for_nonexistent(self):
        result = find_memory_file("/nonexistent/path/to/project")
        assert result is None


class TestXrefReport:
    def setup_method(self):
        self.tmp = tempfile.mkdtemp()
        self.db = ContextDB(self.tmp)

    def teardown_method(self):
        self.db.close()

    def test_runs_without_memory_file(self):
        """Should gracefully skip section 1 when no MEMORY.md exists."""
        result = run_xref(self.db, "/nonexistent/project", self.tmp)
        assert "CROSS-REFERENCE" in result

    def test_runs_with_empty_db(self):
        """Report should work with completely empty database."""
        result = run_xref(self.db, "/p", self.tmp)
        assert "CROSS-REFERENCE" in result
        assert "SUMMARY" in result

    def test_bug_knowledge_gaps(self):
        """Commits with BUG tags but no failure-class knowledge = gap."""
        self.db.insert_commit(
            session_id="s1", commit_date="2026-03-15", hash="a"*40, short_hash="aaaaaaa",
            author="t@t.com", subject="fix: BUG-138", body="",
            files_changed="chat.py", tags="fix,BUG-138", project_dir="/p"
        )
        result = run_xref(self.db, "/p", self.tmp)
        assert "BUG-138" in result
        assert "gap" in result.lower() or "not in" in result.lower() or "missing" in result.lower() or "without" in result.lower()

    def test_no_gaps_when_covered(self):
        """Bug with matching knowledge entry = no gap."""
        self.db.insert_commit(
            session_id="s1", commit_date="2026-03-15", hash="d"*40, short_hash="ddddddd",
            author="t@t.com", subject="fix: BUG-200", body="",
            files_changed="x.py", tags="fix,BUG-200", project_dir="/p"
        )
        self.db.insert_knowledge(
            category="failure-class", title="BUG-200 class",
            content="description", bug_refs="BUG-200"
        )
        result = run_xref(self.db, "/p", self.tmp)
        # BUG-200 should NOT appear in gaps section
        lines = result.split("\n")
        in_gap_section = False
        for line in lines:
            if "BUG-FIX" in line and "GAP" in line:
                in_gap_section = True
            elif line.startswith("==="):
                in_gap_section = False
            if in_gap_section and "BUG-200" in line:
                assert False, "BUG-200 should not appear in gaps section"

    def test_knowledge_freshness_stale(self):
        """Knowledge entry older than related commits = stale."""
        self.db.insert_knowledge(
            category="failure-class", title="Old entry",
            content="description", bug_refs="BUG-100"
        )
        # Backdate the knowledge entry
        self.db.execute(
            "UPDATE knowledge SET created_at = '2025-01-01T00:00:00'"
        )
        self.db.insert_commit(
            session_id="s1", commit_date="2026-03-15", hash="b"*40, short_hash="bbbbbbb",
            author="t@t.com", subject="fix: BUG-100", body="",
            files_changed="x.py", tags="fix,BUG-100", project_dir="/p"
        )
        result = run_xref(self.db, "/p", self.tmp)
        assert "STALE" in result

    def test_all_six_sections_present(self):
        """Report should always have all 6 section headers."""
        result = run_xref(self.db, "/p", self.tmp)
        assert "1. MEMORY.md RULES" in result
        assert "2. UNDOCUMENTED PATTERNS" in result
        assert "3. KNOWLEDGE FRESHNESS" in result
        assert "4. BUG-FIX KNOWLEDGE GAPS" in result
        assert "5. EMERGING PARALLEL PATHS" in result
        assert "6. MEMORY LAYER OVERLAP" in result

    def test_rule_validations_updated(self):
        """When xref runs with rules and matching commits, rule_validations should be updated."""
        self.db.insert_commit(
            session_id="s1", commit_date="2026-03-15", hash="c"*40, short_hash="ccccccc",
            author="t@t.com", subject="fix: update pipeline for extraction",
            body="", files_changed="pipeline.py", tags="fix,extraction",
            project_dir="/p"
        )
        # Manually inject a rule and run xref internals
        from lib.xref import _update_rule_validations
        rules = [{"name": "Pipeline extraction", "terms": {"extraction", "pipeline"}, "hashes": []}]
        commits = [{"hash": "c"*40, "date": "2026-03-15", "subject": "fix: update pipeline for extraction",
                     "body": "", "files": ["pipeline.py"], "tags": ["fix", "extraction"]}]
        _update_rule_validations(self.db, rules, commits)

        rows = self.db.query("SELECT rule_name, match_count FROM rule_validations")
        assert len(rows) == 1
        assert rows[0][0] == "Pipeline extraction"
        assert rows[0][1] >= 1
