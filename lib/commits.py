"""Commit indexing and backfill — indexes git commits with auto-tagging."""
import subprocess
import sys

from lib.tags import compute_tags, load_profile


def index_commit(db, git_root: str, session_id: str, profile: dict | None = None):
    """Index HEAD commit into the commits table. Runs tag engine. Dedup via INSERT OR IGNORE."""
    # Full hash
    result = subprocess.run(
        ["git", "-C", git_root, "rev-parse", "HEAD"],
        capture_output=True, text=True, timeout=5,
    )
    if result.returncode != 0:
        return None
    full_hash = result.stdout.strip()

    # Short hash
    result = subprocess.run(
        ["git", "-C", git_root, "rev-parse", "--short", "HEAD"],
        capture_output=True, text=True, timeout=5,
    )
    short_hash = result.stdout.strip()

    # Commit metadata: author, date, subject, body
    result = subprocess.run(
        ["git", "-C", git_root, "log", "-1", "--format=%ae%n%aI%n%s%n%b"],
        capture_output=True, text=True, timeout=5,
    )
    parts = result.stdout.split("\n", 3)
    author = parts[0] if len(parts) > 0 else ""
    commit_date = parts[1] if len(parts) > 1 else ""
    subject = parts[2] if len(parts) > 2 else ""
    body = parts[3].strip() if len(parts) > 3 else ""

    # Files changed
    result = subprocess.run(
        ["git", "-C", git_root, "diff-tree", "--no-commit-id", "-r", "--name-only", "HEAD"],
        capture_output=True, text=True, timeout=5,
    )
    files = [f for f in result.stdout.strip().split("\n") if f.strip()]

    # Compute tags
    tags = compute_tags(subject, body, files, profile=profile)

    db.insert_commit(
        session_id=session_id,
        commit_date=commit_date,
        hash=full_hash,
        short_hash=short_hash,
        author=author,
        subject=subject,
        body=body,
        files_changed=",".join(files),
        tags=tags,
        project_dir=git_root,
    )

    return {
        "hash": full_hash,
        "short_hash": short_hash,
        "subject": subject,
        "tags": tags,
    }


def backfill(db, git_root: str, days: int = 30, profile: dict | None = None):
    """Iterate git log for the last N days and index each commit. Shows progress every 100."""
    # Get all commits with metadata
    sep = "---COMMIT-SEP---"
    result = subprocess.run(
        ["git", "-C", git_root, "log", f"--since={days} days ago",
         f"--format={sep}%n%H%n%h%n%ae%n%aI%n%s%n%b", "--name-only"],
        capture_output=True, text=True, timeout=60,
    )

    if result.returncode != 0:
        return 0

    raw = result.stdout
    blocks = raw.split(sep + "\n")
    count = 0

    for block in blocks:
        block = block.strip()
        if not block:
            continue

        lines = block.split("\n")
        if len(lines) < 5:
            continue

        full_hash = lines[0].strip()
        short_hash = lines[1].strip()
        author = lines[2].strip()
        commit_date = lines[3].strip()
        subject = lines[4].strip()

        if not full_hash or len(full_hash) != 40:
            continue

        # Body + files: everything after subject. Files are bare filenames (no spaces at start).
        # The body comes between subject and the first bare filename line.
        # Use diff-tree per commit for reliable file extraction.
        file_result = subprocess.run(
            ["git", "-C", git_root, "diff-tree", "--no-commit-id", "-r", "--name-only", full_hash],
            capture_output=True, text=True, timeout=5,
        )
        files = [f for f in file_result.stdout.strip().split("\n") if f.strip()]

        # Body: lines between subject and first file line
        body_lines = []
        for line in lines[5:]:
            stripped = line.strip()
            if stripped and stripped in files:
                break
            body_lines.append(line)
        body = "\n".join(body_lines).strip()

        tags = compute_tags(subject, body, files, profile=profile)

        db.insert_commit(
            session_id="backfill",
            commit_date=commit_date,
            hash=full_hash,
            short_hash=short_hash,
            author=author,
            subject=subject,
            body=body,
            files_changed=",".join(files),
            tags=tags,
            project_dir=git_root,
        )
        count += 1

        if count % 100 == 0:
            print(f"  indexed {count} commits...", file=sys.stderr)

    return count
