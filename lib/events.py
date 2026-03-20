"""Event extraction and logging from PostToolUse hooks."""
import json
import re
import os
import sys

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def is_test_command(command: str) -> bool:
    """Check if a bash command is a test runner invocation."""
    return bool(re.search(r'(pytest|jest|vitest|npm\s+test|pnpm\s+test)', command))


def extract_event(payload: dict) -> dict | None:
    """Extract a structured event from a PostToolUse hook payload.

    Returns a dict with category, event_type, priority, data, is_commit flag.
    Returns None for uninteresting events (ls, echo, etc.)
    """
    tool_name = payload.get("tool_name", "")
    tool_input = payload.get("tool_input", {})
    tool_response = payload.get("tool_response", {})

    if tool_name == "Read":
        file_path = tool_input.get("file_path", "")
        if not file_path:
            return None
        return {"category": "file", "event_type": "file_read", "priority": 3, "data": file_path, "is_commit": False}

    elif tool_name == "Edit":
        file_path = tool_input.get("file_path", "")
        if not file_path:
            return None
        return {"category": "file", "event_type": "file_edit", "priority": 1, "data": file_path, "is_commit": False}

    elif tool_name == "Write":
        file_path = tool_input.get("file_path", "")
        if not file_path:
            return None
        return {"category": "file", "event_type": "file_write", "priority": 1, "data": file_path, "is_commit": False}

    elif tool_name == "Bash":
        command = tool_input.get("command", "")
        output = str(tool_response.get("output", ""))
        is_error = str(tool_response.get("is_error", "false")).lower() == "true"
        exit_code = tool_response.get("exit_code", 0)

        # Git commands
        git_match = re.search(r'(?:^|\s)git\s+(commit|push|pull|merge|rebase|checkout|branch|stash|reset)', command)
        if git_match:
            subcmd = git_match.group(1)
            is_commit = False
            if subcmd == "commit" and re.search(r'\[.+[0-9a-f]{7}\]', output):
                is_commit = True
            return {"category": "git", "event_type": f"git_{subcmd}", "priority": 2, "data": f"git {subcmd}", "is_commit": is_commit}

        # Test commands
        if is_test_command(command):
            return {"category": "test", "event_type": "test_run", "priority": 2, "data": command[:200], "is_commit": False}

        # Docker commands
        if re.search(r'(?:^|\s)docker\s', command):
            return {"category": "infra", "event_type": "docker", "priority": 3, "data": command[:200], "is_commit": False}

        # SSH commands
        if re.search(r'(?:^|\s)ssh\s', command):
            return {"category": "infra", "event_type": "ssh", "priority": 2, "data": command[:200], "is_commit": False}

        # Errors
        if is_error or (isinstance(exit_code, int) and exit_code != 0):
            stderr = str(tool_response.get("stderr", tool_response.get("output", "")))[:300]
            return {"category": "error", "event_type": "error_bash", "priority": 1, "data": f"{command[:200]}\n---\n{stderr}", "is_commit": False}

        # Uninteresting bash command
        return None

    elif tool_name in ("Glob", "Grep"):
        pattern = tool_input.get("pattern", "")
        return {"category": "search", "event_type": "search", "priority": 4, "data": f"{tool_name}: {pattern}", "is_commit": False}

    elif tool_name == "TodoWrite":
        todos = tool_input.get("todos", [])
        summary = "\n".join(f"{t.get('status', '?')}: {t.get('content', '?')}" for t in todos)
        return {"category": "task", "event_type": "task_update", "priority": 1, "data": summary or "task update", "is_commit": False}

    elif tool_name == "Agent":
        desc = tool_input.get("description", "subagent")
        return {"category": "subagent", "event_type": "subagent", "priority": 2, "data": desc, "is_commit": False}

    elif tool_name.startswith("mcp__"):
        return {"category": "mcp", "event_type": "mcp_call", "priority": 3, "data": tool_name, "is_commit": False}

    return None


def handle_event(payload: dict, db, session_id: str, project_dir: str) -> dict | None:
    """Process a PostToolUse event. Returns commit info dict if a git commit was detected."""
    event = extract_event(payload)
    if event is None:
        return None

    db.insert_event(
        session_id=session_id,
        category=event["category"],
        event_type=event["event_type"],
        priority=event["priority"],
        data=event["data"],
        project_dir=project_dir,
    )
    db.evict_events(session_id, max_events=500)

    if event.get("is_commit"):
        return {"is_commit": True, "cwd": payload.get("cwd", project_dir)}

    # Return event type for file ops and test runs so hooks.py can trigger context surfacing
    if event["event_type"] in ("file_edit", "file_write", "file_read", "test_run"):
        return {"event_type": event["event_type"]}

    return None
