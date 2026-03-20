"""PreToolUse hook handler. Surfaces context BEFORE tool execution.

Returns hookSpecificOutput with additionalContext — never blocks tools.

Three enrichment paths:
  1. Read: file intel (parity, bug history, knowledge refs) + indexed output hint
  2. Bash (test): failure-class knowledge before tests run
  3. Edit/Write: parity nudge before the edit happens (earlier than PostToolUse)
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lib.db import ContextDB, data_dir, resolve_git_root, resolve_cluster_db
from lib.tags import load_profile
from lib.edit_nudge import load_session_cache, save_session_cache
from lib.context_briefing import file_briefing, check_testrun_briefing
from lib.events import is_test_command


def handle_pretool(payload: dict) -> str | None:
    """Handle a PreToolUse hook. Returns JSON string or None.

    Output format (Claude Code PreToolUse hookSpecificOutput):
    {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "additionalContext": "..."
        }
    }
    """
    tool_name = payload.get("tool_name", "")
    tool_input = payload.get("tool_input", {})
    session_id = payload.get("session_id", "unknown")
    cwd = payload.get("cwd", os.getcwd())

    git_root = resolve_git_root(cwd)
    project_dir = data_dir(git_root)
    db = ContextDB(project_dir)

    cluster_db = None
    def get_cluster_db():
        nonlocal cluster_db
        if cluster_db is None:
            cluster_dir = resolve_cluster_db(project_dir)
            cluster_db = ContextDB(cluster_dir) if cluster_dir != project_dir else db
        return cluster_db

    try:
        context_lines = []

        if tool_name == "Read":
            context_lines = _enrich_read(
                tool_input, db, get_cluster_db, project_dir, session_id
            )

        elif tool_name in ("Edit", "Write"):
            context_lines = _enrich_edit(
                tool_input, get_cluster_db, project_dir, session_id
            )

        elif tool_name == "Bash":
            context_lines = _enrich_bash(
                tool_input, get_cluster_db, project_dir, session_id
            )

        if not context_lines:
            return None

        return json.dumps({
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "additionalContext": "\n".join(context_lines),
            }
        })

    finally:
        db.close()
        if cluster_db is not None and cluster_db is not db:
            cluster_db.close()


def _enrich_read(tool_input, db, get_cluster_db, project_dir, session_id):
    """Surface file intel before a Read."""
    file_path = tool_input.get("file_path", "")
    if not file_path:
        return []

    cache = load_session_cache(project_dir)
    profile = load_profile(project_dir)
    lines = file_briefing(file_path, get_cluster_db(), profile, cache, session_id)

    # Check if this file has indexed output from earlier in the session
    basename = os.path.basename(file_path)
    sources = db.query(
        "SELECT source, COUNT(*) FROM output_chunks "
        "WHERE session_id = ? AND source LIKE ? "
        "GROUP BY source",
        (session_id, f"Read:{basename}%")
    )
    if sources:
        for source, count in sources:
            lines.append(
                f"Indexed: {source} has {count} chunks from earlier. "
                f"Search with: context-hooks search-output <query>"
            )

    if lines:
        save_session_cache(project_dir, cache)

    return lines


def _enrich_edit(tool_input, get_cluster_db, project_dir, session_id):
    """Surface parity and knowledge refs before an Edit/Write."""
    file_path = tool_input.get("file_path", "")
    if not file_path:
        return []

    cache = load_session_cache(project_dir)
    profile = load_profile(project_dir)

    # Reuse file_briefing for parity/bug/knowledge checks
    lines = file_briefing(file_path, get_cluster_db(), profile, cache, session_id)

    if lines:
        save_session_cache(project_dir, cache)

    return lines


def _enrich_bash(tool_input, get_cluster_db, project_dir, session_id):
    """Surface failure-class knowledge before tests run."""
    command = tool_input.get("command", "")
    if not command or not is_test_command(command):
        return []

    cache = load_session_cache(project_dir)
    lines = check_testrun_briefing(command, get_cluster_db(), cache, session_id)

    if lines:
        save_session_cache(project_dir, cache)

    return lines


def main():
    """CLI entry point: context-hooks hook pre-tool-use [json]"""
    if len(sys.argv) > 1:
        payload = json.loads(sys.argv[1])
    else:
        payload = json.loads(sys.stdin.read())

    result = handle_pretool(payload)
    if result:
        print(result)


if __name__ == "__main__":
    main()
