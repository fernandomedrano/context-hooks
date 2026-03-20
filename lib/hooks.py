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
from lib.context_briefing import session_briefing


MEMO_POLL_INTERVAL_CALLS = 10  # poll every N tool calls
MEMO_POLL_INTERVAL_SECS = 60   # or every N seconds, whichever comes first


def _poll_memos(cluster_db, project_dir: str, session_id: str) -> list[str]:
    """Check for unread memos periodically. Returns nudge lines or []."""
    from lib.edit_nudge import load_session_cache, save_session_cache
    import time

    cache = load_session_cache(project_dir)
    session_cache = cache.setdefault(session_id, [])

    # Track poll state in cache under a special key
    poll_key = "_memo_poll"
    poll_state = None
    for item in session_cache:
        if isinstance(item, dict) and item.get("type") == poll_key:
            poll_state = item
            break
    if poll_state is None:
        poll_state = {"type": poll_key, "call_count": 0, "last_poll": time.time(), "last_poll_count": 0}
        session_cache.append(poll_state)

    poll_state["call_count"] = poll_state.get("call_count", 0) + 1
    now = time.time()
    calls_since = poll_state["call_count"] - poll_state.get("last_poll_count", 0)
    secs_since = now - poll_state.get("last_poll", 0)

    if calls_since < MEMO_POLL_INTERVAL_CALLS and secs_since < MEMO_POLL_INTERVAL_SECS:
        save_session_cache(project_dir, cache)
        return []

    # Time to poll
    poll_state["last_poll"] = now
    poll_state["last_poll_count"] = poll_state["call_count"]
    save_session_cache(project_dir, cache)

    # Query unread memos
    rows = cluster_db.query(
        "SELECT id, from_agent, subject, content FROM memos WHERE read = 0 ORDER BY id ASC"
    )
    if not rows:
        return []

    lines = [f"📬 {len(rows)} unread memo(s):"]
    for row in rows[:5]:  # cap at 5 to avoid flooding
        lines.append(f"  • [{row[0]}] from {row[1]}: {row[2]}")
        if len(row[3]) > 200:
            lines.append(f"    {row[3][:200]}...")
        else:
            lines.append(f"    {row[3]}")
    if len(rows) > 5:
        lines.append(f"  ... and {len(rows) - 5} more. Run: context-hooks memo list --unread")
    return lines


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

            # NOTE: file_read and test_run intel moved to PreToolUse handler
            # (lib/pretool.py) — fires BEFORE the tool, so agent has context
            # while processing, not after. PostToolUse retains output indexing
            # and edit nudges (which need the tool response).

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

            # Periodic memo polling
            memo_lines = _poll_memos(get_cluster_db(), project_dir, session_id)
            additional_parts.extend(memo_lines)

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

                result = {}
                context_lines = []

                if report:
                    # Critical issues (DB missing, hooks broken) → systemMessage
                    if report.get("critical"):
                        result["systemMessage"] = (
                            "CONTEXT-HOOKS HEALTH WARNING:\n"
                            + "\n".join(report["critical"])
                        )
                    # Soft warnings (unread memos, bug gaps)
                    if report.get("warnings"):
                        context_lines.extend(report["warnings"])

                # Project context briefing
                briefing = session_briefing(db, get_cluster_db(), project_dir, config)
                context_lines.extend(briefing)

                if context_lines:
                    result["additionalContext"] = (
                        "Context Hooks:\n" + "\n".join(context_lines)
                    )
                return json.dumps(result) if result else None
            else:
                return None

        elif hook_type == "pre-tool-use":
            from lib.pretool import handle_pretool
            return handle_pretool(payload)

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
