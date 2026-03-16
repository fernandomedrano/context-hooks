"""Central hook router. Called by platform adapter shims."""
import json
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lib.db import ContextDB, data_dir, resolve_git_root
from lib.config import load_config
from lib.events import handle_event
from lib.snapshot import build_snapshot, save_snapshot, recovery_response
from lib.commits import index_commit
from lib.tags import load_profile
from lib.nudge import check_parity, check_flywheels


def handle_hook(hook_type: str, payload: dict) -> str | None:
    """Route a hook to the appropriate handler. Returns JSON string or None."""
    cwd = payload.get("cwd", os.getcwd())
    git_root = resolve_git_root(cwd)
    session_id = payload.get("session_id", "unknown")
    project_dir = data_dir(git_root)
    db = ContextDB(project_dir)
    config = load_config(project_dir)

    try:
        if hook_type == "event":
            result = handle_event(payload, db, session_id, git_root)

            # If a commit was detected, index it and check nudges
            if result and result.get("is_commit"):
                profile = load_profile(project_dir)
                commit_info = index_commit(db, git_root, session_id, profile)

                warnings = []
                if commit_info:
                    # Check parity nudge
                    if config.get("nudge.parity"):
                        parity_warn = check_parity(
                            commit_info.get("files_changed", ""),
                            profile
                        )
                        if parity_warn:
                            warnings.append(parity_warn)

                    # Check flywheel nudge
                    if config.get("nudge.flywheel"):
                        flywheel_warn = check_flywheels(
                            db, config, commit_info.get("tags", "")
                        )
                        if flywheel_warn:
                            warnings.extend(flywheel_warn)

                if warnings:
                    return json.dumps({"additionalContext": "\n".join(warnings)})

            return None

        elif hook_type == "pre-compact":
            xml = build_snapshot(db, session_id, git_root)
            save_snapshot(project_dir, xml)
            return None  # PreCompact is observability-only

        elif hook_type == "session-start":
            source = payload.get("source", "startup")

            if source == "compact":
                return recovery_response(project_dir)
            elif source == "startup":
                # Health check injection
                from lib.health import health_summary
                summary = health_summary(db, git_root, project_dir, config)
                if summary:
                    return json.dumps({"additionalContext": summary})
                return None
            else:
                return None

        elif hook_type == "session-end":
            return None  # Placeholder for future session-end logic

        else:
            return None

    finally:
        db.close()


def main():
    """CLI entry point: context-hooks hook <type> [json]"""
    if len(sys.argv) < 2:
        print("Usage: hooks.py <hook-type> [json]", file=sys.stderr)
        sys.exit(1)

    hook_type = sys.argv[1]

    # Read JSON from argv or stdin
    if len(sys.argv) > 2:
        payload = json.loads(sys.argv[2])
    else:
        payload = json.loads(sys.stdin.read())

    result = handle_hook(hook_type, payload)
    if result:
        print(result)


if __name__ == "__main__":
    main()
