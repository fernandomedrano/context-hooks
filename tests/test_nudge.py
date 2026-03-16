import os, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from lib.nudge import check_parity, check_flywheels
from lib.db import ContextDB

class TestCheckParity:
    def test_solo_a_detected(self):
        profile = {"parallel_paths": [
            {"files": ["src/chat.py", "src/chat_service.py"], "name": "chat+chat_service", "co_pct": 63}
        ]}
        result = check_parity("src/chat.py,src/other.py", profile)
        assert result is not None
        assert "chat.py" in result
        assert "chat_service.py" in result
        assert "63%" in result

    def test_solo_b_detected(self):
        profile = {"parallel_paths": [
            {"files": ["src/chat.py", "src/chat_service.py"], "name": "chat+chat_service", "co_pct": 63}
        ]}
        result = check_parity("src/chat_service.py", profile)
        assert result is not None
        assert "chat_service.py" in result

    def test_paired_no_warning(self):
        profile = {"parallel_paths": [
            {"files": ["src/a.py", "src/b.py"], "name": "a+b", "co_pct": 50}
        ]}
        result = check_parity("src/a.py,src/b.py", profile)
        assert result is None

    def test_no_profile(self):
        assert check_parity("a.py", None) is None

    def test_no_files(self):
        profile = {"parallel_paths": [{"files": ["a.py", "b.py"], "name": "a+b", "co_pct": 50}]}
        assert check_parity("", profile) is None

    def test_no_parallel_paths(self):
        assert check_parity("a.py", {"parallel_paths": []}) is None


class TestCheckFlywheels:
    def setup_method(self):
        self.tmp = tempfile.mkdtemp()
        self.db = ContextDB(self.tmp)

    def teardown_method(self):
        self.db.close()

    def test_missing_failure_class(self):
        config = {"flywheels": [{
            "trigger_pattern": r"BUG-\d+",
            "match_field": "bug_refs",
            "required_category": "failure-class",
            "message": "Commit references {ref} but no failure class exists."
        }]}
        warnings = check_flywheels(self.db, config, "fix,BUG-138")
        assert len(warnings) == 1
        assert "BUG-138" in warnings[0]

    def test_covered_no_warning(self):
        # Add a knowledge entry covering BUG-138
        self.db.insert_knowledge(
            category="failure-class", title="Test class",
            content="content", bug_refs="BUG-138"
        )
        config = {"flywheels": [{
            "trigger_pattern": r"BUG-\d+",
            "match_field": "bug_refs",
            "required_category": "failure-class",
            "message": "{ref} missing"
        }]}
        warnings = check_flywheels(self.db, config, "fix,BUG-138")
        assert len(warnings) == 0

    def test_no_flywheel_config(self):
        assert check_flywheels(self.db, {}, "fix,BUG-1") == []

    def test_no_tags(self):
        config = {"flywheels": [{"trigger_pattern": r"BUG-\d+", "match_field": "bug_refs",
                                  "required_category": "failure-class", "message": "{ref}"}]}
        assert check_flywheels(self.db, config, "") == []
