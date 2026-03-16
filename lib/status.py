"""Status command: show what's tracked, DB sizes, health summary."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lib.db import ContextDB, data_dir, resolve_git_root


def show_status(db, project_dir: str, git_root: str) -> str:
    """Show current status of the context-hooks system."""
    lines = ["=== context-hooks status ===", ""]

    # Project info
    lines.append(f"Project: {git_root}")
    db_path = os.path.join(project_dir, "context.db")
    if os.path.exists(db_path):
        size_kb = os.path.getsize(db_path) / 1024
        lines.append(f"Database: {db_path} ({size_kb:.0f} KB)")

    # Row counts
    tables = ["events", "commits", "knowledge", "memos", "rule_validations"]
    lines.append("")
    lines.append("Table counts:")
    for table in tables:
        count = db.query(f"SELECT COUNT(*) FROM {table}")[0][0]
        lines.append(f"  {table:<20s} {count}")

    # Profile
    profile_path = os.path.join(project_dir, "profile.yaml")
    if os.path.exists(profile_path):
        lines.append(f"\nProfile: {profile_path}")
    else:
        lines.append("\nProfile: not generated (run 'context-hooks profile')")

    # Snapshot
    snapshot_path = os.path.join(project_dir, "snapshot.xml")
    if os.path.exists(snapshot_path):
        lines.append(f"Snapshot: {snapshot_path}")
    else:
        lines.append("Snapshot: none (created on compaction)")

    # Last event
    last = db.query("SELECT timestamp, event_type, data FROM events ORDER BY id DESC LIMIT 1")
    if last:
        lines.append(f"\nLast event: {last[0][1]} at {last[0][0]}")

    # Last commit
    last_commit = db.query("SELECT short_hash, subject FROM commits ORDER BY id DESC LIMIT 1")
    if last_commit:
        lines.append(f"Last commit: {last_commit[0][0]} {last_commit[0][1]}")

    return "\n".join(lines)


def main():
    git_root = resolve_git_root(os.getcwd())
    project_dir = data_dir(git_root)
    db = ContextDB(project_dir)
    try:
        print(show_status(db, project_dir, git_root))
    finally:
        db.close()


if __name__ == "__main__":
    main()
