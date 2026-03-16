"""Health summary for session-start injection. Stub — will be expanded later."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def health_summary(db, git_root: str, project_dir: str, config: dict) -> str | None:
    """Return a short health summary string, or None if everything looks fine."""
    lines = []

    # Count indexed commits
    commit_count = db.query("SELECT COUNT(*) FROM commits")[0][0]
    if commit_count == 0:
        lines.append("No commits indexed yet. Run 'context-hooks bootstrap' to backfill.")

    # Count knowledge entries
    knowledge_count = db.query("SELECT COUNT(*) FROM knowledge WHERE status = 'active'")[0][0]

    # Count recent errors
    error_count = db.query(
        "SELECT COUNT(*) FROM events WHERE category = 'error' "
        "AND timestamp > datetime('now', '-1 day', 'localtime')"
    )[0][0]
    if error_count > 0:
        lines.append(f"{error_count} error(s) in the last 24 hours.")

    if not lines:
        return None

    header = f"context-hooks: {commit_count} commits, {knowledge_count} knowledge entries."
    return header + "\n" + "\n".join(lines)
