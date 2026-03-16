import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lib.tags import (
    apply_universal_tags,
    apply_profile_tags,
    compute_tags,
    generate_profile,
    save_profile,
    load_profile,
)


class TestUniversalTags:
    def test_conventional_fix(self):
        tags = apply_universal_tags("fix: broken login", "", [])
        assert "fix" in tags

    def test_conventional_feat(self):
        tags = apply_universal_tags("feat(auth): add OAuth", "", [])
        assert "feat" in tags

    def test_conventional_refactor(self):
        tags = apply_universal_tags("refactor!: rewrite parser", "", [])
        assert "refactor" in tags

    def test_no_prefix_on_plain_subject(self):
        tags = apply_universal_tags("update readme with details", "", [])
        assert not any(t in tags for t in ["fix", "feat", "refactor", "docs", "test", "chore"])

    def test_bug_reference(self):
        tags = apply_universal_tags("fix: BUG-042 crash on login", "", [])
        assert "BUG-042" in tags

    def test_bug_in_body(self):
        tags = apply_universal_tags("fix: crash", "Closes BUG-123 and BUG-456", [])
        assert "BUG-123" in tags
        assert "BUG-456" in tags

    def test_adr_reference(self):
        tags = apply_universal_tags("feat: implement ADR-029 pipeline", "", [])
        assert "ADR-029" in tags

    def test_github_issue_ref(self):
        tags = apply_universal_tags("fix: resolve #42", "", [])
        assert "#42" in tags

    def test_file_type_migration(self):
        tags = apply_universal_tags("feat: add table", "", ["alembic/versions/001_add_users.py"])
        assert "migration" in tags

    def test_file_type_infra(self):
        tags = apply_universal_tags("chore: update", "", ["Dockerfile", "src/main.py"])
        assert "infra" in tags

    def test_file_type_tests(self):
        tags = apply_universal_tags("test: add coverage", "", ["tests/test_main.py"])
        assert "tests" in tags

    def test_file_type_docs(self):
        tags = apply_universal_tags("docs: update", "", ["README.md", "docs/guide.md"])
        assert "docs" in tags

    def test_file_type_ci(self):
        tags = apply_universal_tags("ci: add workflow", "", [".github/workflows/ci.yml"])
        assert "ci" in tags

    def test_no_duplicates(self):
        tags = apply_universal_tags("fix: BUG-042", "also BUG-042", [])
        assert tags.count("BUG-042") == 1


class TestProfileTags:
    def test_directory_tag(self):
        profile = {"directory_tags": {"api": "api", "apps": "apps"}}
        tags = apply_profile_tags(["api/src/main.py"], profile)
        assert "api" in tags
        assert "apps" not in tags

    def test_hot_file_tag(self):
        profile = {"hot_files": {"api/src/main.py": "main"}}
        tags = apply_profile_tags(["api/src/main.py", "other.py"], profile)
        assert "main" in tags

    def test_hot_file_no_match(self):
        profile = {"hot_files": {"api/src/main.py": "main"}}
        tags = apply_profile_tags(["other.py"], profile)
        assert "main" not in tags

    def test_parallel_paired(self):
        profile = {"parallel_paths": [
            {"files": ["lib/db.py", "tests/test_db.py"], "name": "db+test-db"}
        ]}
        tags = apply_profile_tags(["lib/db.py", "tests/test_db.py"], profile)
        assert "paired:db+test-db" in tags

    def test_parallel_solo_a(self):
        profile = {"parallel_paths": [
            {"files": ["lib/db.py", "tests/test_db.py"], "name": "db+test-db"}
        ]}
        tags = apply_profile_tags(["lib/db.py"], profile)
        assert "solo-a:db+test-db" in tags

    def test_parallel_solo_b(self):
        profile = {"parallel_paths": [
            {"files": ["lib/db.py", "tests/test_db.py"], "name": "db+test-db"}
        ]}
        tags = apply_profile_tags(["tests/test_db.py"], profile)
        assert "solo-b:db+test-db" in tags

    def test_parallel_neither(self):
        profile = {"parallel_paths": [
            {"files": ["lib/db.py", "tests/test_db.py"], "name": "db+test-db"}
        ]}
        tags = apply_profile_tags(["README.md"], profile)
        assert len([t for t in tags if "db+test-db" in t]) == 0

    def test_empty_profile(self):
        tags = apply_profile_tags(["anything.py"], {})
        assert tags == []


class TestComputeTags:
    def test_combines_universal_and_profile(self):
        profile = {"directory_tags": {"lib": "lib"}}
        result = compute_tags("fix: bug", "", ["lib/main.py"], profile=profile)
        assert "fix" in result
        assert "lib" in result

    def test_no_profile(self):
        result = compute_tags("feat: new feature", "", ["src/app.py"])
        assert "feat" in result

    def test_returns_comma_separated(self):
        result = compute_tags("fix: BUG-001", "", ["tests/test_x.py"])
        parts = result.split(",")
        assert "fix" in parts
        assert "BUG-001" in parts
        assert "tests" in parts


class TestGenerateProfile:
    def test_returns_expected_structure(self):
        """Use the context-hooks repo itself — it has at least 3 commits."""
        git_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        profile = generate_profile(git_root, days=365)
        assert "version" in profile
        assert profile["version"] == 1
        assert "generated_from" in profile
        assert "directory_tags" in profile
        assert "hot_files" in profile
        assert "parallel_paths" in profile
        assert isinstance(profile["directory_tags"], dict)
        assert isinstance(profile["hot_files"], dict)
        assert isinstance(profile["parallel_paths"], list)
        # Should mention commits
        assert "commits" in profile["generated_from"]

    def test_empty_repo(self):
        """A temp dir with no git repo should return 0-commit profile."""
        tmp = tempfile.mkdtemp()
        # Initialize empty repo
        import subprocess
        subprocess.run(["git", "init", tmp], capture_output=True)
        profile = generate_profile(tmp, days=30)
        assert profile["version"] == 1
        assert "0 commits" in profile["generated_from"]


class TestProfileIO:
    def test_save_and_load_roundtrip(self):
        tmp = tempfile.mkdtemp()
        profile = {
            "version": 1,
            "generated_from": "last 30 days (100 commits)",
            "directory_tags": {"api": "api", "lib": "lib"},
            "hot_files": {"api/src/main.py": "main"},
            "parallel_paths": [
                {"files": ["a.py", "b.py"], "name": "a+b", "together": 12, "co_pct": 63}
            ],
        }
        save_profile(tmp, profile)
        loaded = load_profile(tmp)
        assert loaded is not None
        assert loaded["version"] == 1
        assert loaded["generated_from"] == "last 30 days (100 commits)"
        assert loaded["directory_tags"] == {"api": "api", "lib": "lib"}
        assert loaded["hot_files"] == {"api/src/main.py": "main"}
        assert len(loaded["parallel_paths"]) == 1
        pp = loaded["parallel_paths"][0]
        assert pp["files"] == ["a.py", "b.py"]
        assert pp["name"] == "a+b"
        assert pp["together"] == 12
        assert pp["co_pct"] == 63

    def test_load_missing(self):
        tmp = tempfile.mkdtemp()
        assert load_profile(tmp) is None

    def test_save_empty_sections(self):
        tmp = tempfile.mkdtemp()
        profile = {
            "version": 1,
            "generated_from": "last 30 days (0 commits)",
            "directory_tags": {},
            "hot_files": {},
            "parallel_paths": [],
        }
        save_profile(tmp, profile)
        loaded = load_profile(tmp)
        assert loaded is not None
        assert loaded["directory_tags"] == {}
        assert loaded["hot_files"] == {}
        assert loaded["parallel_paths"] == []
