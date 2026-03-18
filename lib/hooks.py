"""Central hook router. Called by platform adapter shims."""
import json
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lib.db import ContextDB, data_dir, resolve_git_root, resolve_cluster_db
from lib.config import load_config
from lib.events import handle_event
from lib.snapshot import build_snapshot, save_snapshot, recovery_response
from lib.commits import index_commit
from lib.tags import load_profile
from lib.nudge import check_parity, check_flywheels
from lib.edit_nudge import check_edit_nudges
from lib.output_store import (
    index_output, summarize_output, make_source_label,
    get_output_text, cleanup_session_outputs, OUTPUT_THRESHOLD,
)


def handle_hook(hook_type: str, payload: dict) -> str | None:
    """Route a hook to the appropriate handler. Returns JSON string or None."""
    cwd = payload.get("cwd", os.getcwd())
    git_root = resolve_git_root(cwd)
    session_id = payload.get("session_id", "unknown")
    project_dir = data_dir(git_root)
    db = ContextDB(project_dir)
    config = load_config(project_dir)

    cluster_db = None
    def get_cluster_db():
        nonlocal cluster_db
        if cluster_db is None:
            cluster_dir = resolve_cluster_db(project_dir)
            cluster_db = ContextDB(cluster_dir) if cluster_dir != project_dir else db
        return cluster_db

    try:
        if hook_type == "event":
            result = handle_event(payload, db, session_id, git_root)

            # Check if tool output is large enough to index
            tool_name = payload.get("tool_name", "")
            output_text = get_output_text(tool_name, payload.get("tool_response", {}))
            additional_parts = []

            if output_text and len(output_text) > OUTPUT_THRESHOLD:
                source_label = make_source_label(tool_name, payload.get("tool_input", {}))
                chunk_count = index_output(db, session_id, source_label, output_text)
                if chunk_count > 0:
                    additional_parts.append(
                        summarize_output(output_text, source_label, chunk_count)
                    )

            # If a file was edited/written, check edit-time nudges
            if result and result.get("event_type") in ("file_edit", "file_write"):
                file_path = payload.get("tool_input", {}).get("file_path", "")
                if file_path:
                    profile = load_profile(project_dir)
                    nudges = check_edit_nudges(
                        file_path=file_path,
                        db=db,
                        profile=profile,
                        config=config,
                        project_data_dir=project_dir,
                        session_id=session_id,
                    )
                    additional_parts.extend(nudges)

            # If a commit was detected, index it and check nudges
            if result and result.get("is_commit"):
                profile = load_profile(project_dir)
                commit_info = index_commit(db, git_root, session_id, profile)

                if commit_info:
                    # Check parity nudge
                    if config.get("nudge.parity"):
                        parity_warn = check_parity(
                            commit_info.get("files_changed", ""),
                            profile
                        )
                        if parity_warn:
                            additional_parts.append(parity_warn)

                    # Check flywheel nudge
                    if config.get("nudge.flywheel"):
                        flywheel_warn = check_flywheels(
                            get_cluster_db(), config, commit_info.get("tags", "")
                        )
                        additional_parts.extend(flywheel_warn)

            if additional_parts:
                return json.dumps({"additionalContext": "\n".join(additional_parts)})

            return None

        elif hook_type == "pre-compact":
            xml = build_snapshot(db, session_id, git_root)
            save_snapshot(project_dir, xml)
            return None  # PreCompact is observability-only

        elif hook_type == "session-start":
            source = payload.get("source", "startup")

            if source == "compact":
                return recovery_response(project_dir)
            elif source in ("startup", "resume"):
                # Clean stale session caches
                from lib.edit_nudge import cleanup_session_cache
                cleanup_session_cache(project_dir, session_id)
                cleanup_session_outputs(db, session_id)

                # Health check injection
                from lib.health import health_summary, format_health_text
                report = health_summary(db, get_cluster_db(), git_root, project_dir, config)
                if not report:
                    return None

                result = {}
                # Critical issues (DB missing, hooks broken) → systemMessage
                if report.get("critical"):
                    result["systemMessage"] = (
                        "CONTEXT-HOOKS HEALTH WARNING:\n"
                        + "\n".join(report["critical"])
                    )
                # Soft warnings (unread memos, bug gaps) → additionalContext
                if report.get("warnings"):
                    result["additionalContext"] = (
                        "Context Hooks:\n"
                        + "\n".join(report["warnings"])
                    )
                return json.dumps(result) if result else None
            else:
                return None

        elif hook_type == "session-end":
            return None  # Placeholder for future session-end logic

        else:
            return None

    finally:
        db.close()
        if cluster_db is not None and cluster_db is not db:
            cluster_db.close()


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
