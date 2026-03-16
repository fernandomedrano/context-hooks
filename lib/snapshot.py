"""Compaction survival: save session state before compaction, restore after."""
import os
import json
import subprocess
from datetime import datetime


def build_snapshot(db, session_id: str, project_dir: str) -> str:
    """Build a priority-tiered XML snapshot from session events. Target: ~4KB."""

    # P1: Active files being edited
    active_files = db.query(
        "SELECT DISTINCT data FROM events WHERE session_id = ? AND event_type IN ('file_edit', 'file_write') ORDER BY id DESC LIMIT 20",
        (session_id,)
    )

    # P1: Task state
    task_state = db.query(
        "SELECT data FROM events WHERE session_id = ? AND category = 'task' ORDER BY id DESC LIMIT 1",
        (session_id,)
    )

    # P1: Errors
    errors = db.query(
        "SELECT data FROM events WHERE session_id = ? AND category = 'error' ORDER BY id DESC LIMIT 5",
        (session_id,)
    )

    # P2: Git operations
    git_ops = db.query(
        "SELECT timestamp || ' ' || data FROM events WHERE session_id = ? AND category = 'git' ORDER BY id DESC LIMIT 10",
        (session_id,)
    )

    # P2: Session commits
    session_commits = db.query(
        "SELECT short_hash || ' ' || subject FROM commits WHERE session_id = ? ORDER BY id DESC LIMIT 10",
        (session_id,)
    )

    # P2: Test runs
    test_runs = db.query(
        "SELECT timestamp || ' ' || data FROM events WHERE session_id = ? AND category = 'test' ORDER BY id DESC LIMIT 5",
        (session_id,)
    )

    # P3: Files read
    read_files = db.query(
        "SELECT DISTINCT data FROM events WHERE session_id = ? AND event_type = 'file_read' "
        "AND data NOT IN (SELECT data FROM events WHERE session_id = ? AND event_type IN ('file_edit', 'file_write')) "
        "ORDER BY id DESC LIMIT 10",
        (session_id, session_id)
    )

    # P3: Subagents
    subagents = db.query(
        "SELECT data FROM events WHERE session_id = ? AND category = 'subagent' ORDER BY id DESC LIMIT 5",
        (session_id,)
    )

    event_count = db.query("SELECT COUNT(*) FROM events WHERE session_id = ?", (session_id,))[0][0]

    # Build XML
    lines = [f'<session_snapshot session_id="{session_id}" project="{project_dir}" generated_at="{datetime.now().isoformat()}" events="{event_count}">']

    if active_files:
        lines.append("  <active_files>")
        for row in active_files:
            lines.append(f"    <file>{row[0]}</file>")
        lines.append("  </active_files>")

    if task_state:
        lines.append("  <task_state>")
        for line in task_state[0][0].split('\n'):
            if line.strip():
                lines.append(f"    {line.strip()}")
        lines.append("  </task_state>")

    if errors:
        lines.append("  <recent_errors>")
        for row in errors:
            lines.append(f"    <error>{row[0][:200]}</error>")
        lines.append("  </recent_errors>")

    if git_ops:
        lines.append("  <git_operations>")
        for row in git_ops:
            lines.append(f"    <op>{row[0]}</op>")
        lines.append("  </git_operations>")

    if session_commits:
        lines.append("  <session_commits>")
        for row in session_commits:
            lines.append(f"    <commit>{row[0]}</commit>")
        lines.append("  </session_commits>")

    if test_runs:
        lines.append("  <test_runs>")
        for row in test_runs:
            lines.append(f"    <run>{row[0][:200]}</run>")
        lines.append("  </test_runs>")

    if read_files:
        lines.append("  <files_read>")
        for row in read_files:
            lines.append(f"    <file>{row[0]}</file>")
        lines.append("  </files_read>")

    if subagents:
        lines.append("  <subagents>")
        for row in subagents:
            lines.append(f"    <agent>{row[0]}</agent>")
        lines.append("  </subagents>")

    # Live git state
    try:
        branch = subprocess.run(['git', '-C', project_dir, 'branch', '--show-current'],
                                capture_output=True, text=True, timeout=5).stdout.strip() or "detached"
        last_commit = subprocess.run(['git', '-C', project_dir, 'log', '-1', '--format=%h %s'],
                                     capture_output=True, text=True, timeout=5).stdout.strip()
        lines.append(f'  <git_state branch="{branch}">')
        if last_commit:
            lines.append(f"    <last_commit>{last_commit}</last_commit>")
        lines.append("  </git_state>")
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass

    lines.append("</session_snapshot>")
    xml = "\n".join(lines)

    # Truncate to 4KB
    if len(xml.encode()) > 4096:
        xml = xml.encode()[:4096].decode(errors='ignore')
        xml += "\n</session_snapshot>"

    return xml


def save_snapshot(project_data_dir: str, xml: str) -> str:
    """Write snapshot to disk with restricted permissions."""
    path = os.path.join(project_data_dir, "snapshot.xml")
    with open(path, 'w') as f:
        f.write(xml)
    os.chmod(path, 0o600)
    return path


def load_snapshot(project_data_dir: str) -> dict | None:
    """Load snapshot and return JSON for additionalContext injection.
    Returns None if no snapshot exists."""
    path = os.path.join(project_data_dir, "snapshot.xml")
    if not os.path.exists(path):
        return None
    with open(path) as f:
        snapshot = f.read()
    prefix = "CONTEXT RECOVERY — The conversation was just compacted. Below is your pre-compaction working state. Use this to continue without asking the user what you were doing:\n\n"
    return {"additionalContext": prefix + snapshot}


def recovery_response(project_data_dir: str) -> str:
    """Return JSON string for SessionStart(compact) hook response."""
    result = load_snapshot(project_data_dir)
    if result is None:
        result = {"additionalContext": "Context was just compacted. No pre-compaction snapshot was available. You may have lost track of which files you were editing and what tasks were in progress. Ask the user what you were working on if needed."}
    return json.dumps(result)
