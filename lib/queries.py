"""Query commands for the commit index."""
import os
import sys
import re

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lib.db import ContextDB, data_dir, resolve_git_root


def query_parity(db) -> str:
    """Show parallel path alerts."""
    lines = ["=== PARALLEL PATH ALERTS ===", ""]

    # Solo edits
    solo = db.query(
        "SELECT short_hash, commit_date, subject, tags FROM commits "
        "WHERE tags LIKE '%solo-a:%' OR tags LIKE '%solo-b:%' "
        "ORDER BY id DESC LIMIT 25"
    )
    if solo:
        lines.append("Solo edits (one file edited without its usual companion):")
        for row in solo:
            short_hash, date, subject, tags = row
            solo_tags = [t for t in (tags or "").split(",") if t.startswith("solo-")]
            lines.append(f"  {short_hash}  {(date or '?')[:10]}  {subject}")
            lines.append(f"      [{', '.join(solo_tags)}]")

    lines.append("")

    # Paired edits
    paired = db.query(
        "SELECT short_hash, commit_date, subject, tags FROM commits "
        "WHERE tags LIKE '%paired:%' "
        "ORDER BY id DESC LIMIT 10"
    )
    if paired:
        lines.append("Proper paired edits (both sides touched together):")
        for row in paired:
            short_hash, date, subject, tags = row
            paired_tags = [t for t in (tags or "").split(",") if t.startswith("paired:")]
            lines.append(f"  {short_hash}  {(date or '?')[:10]}  {subject}")
            lines.append(f"      [{', '.join(paired_tags)}]")

    return "\n".join(lines)


def query_search(db, term: str) -> str:
    """Full-text search over commits."""
    rows = db.query(
        "SELECT short_hash, commit_date, subject, tags FROM commits "
        "WHERE subject LIKE ? OR body LIKE ? OR files_changed LIKE ? "
        "ORDER BY id DESC LIMIT 30",
        (f"%{term}%", f"%{term}%", f"%{term}%")
    )
    lines = [f"=== Commits matching '{term}' ==="]
    for row in rows:
        short_hash, date, subject, tags = row
        tag_str = f" [{tags}]" if tags else ""
        lines.append(f"  {short_hash}  {(date or '?')[:10]}  {subject}{tag_str}")
    if not rows:
        lines.append("  (no matches)")
    return "\n".join(lines)


def query_tag(db, tag: str) -> str:
    """Find commits by tag."""
    rows = db.query(
        "SELECT short_hash, commit_date, subject, tags FROM commits "
        "WHERE ',' || tags || ',' LIKE ? OR tags = ? "
        "ORDER BY id DESC LIMIT 30",
        (f"%,{tag},%", tag)
    )
    lines = [f"=== Commits tagged '{tag}' ==="]
    for row in rows:
        short_hash, date, subject, tags = row
        lines.append(f"  {short_hash}  {(date or '?')[:10]}  {subject}  [{tags}]")
    if not rows:
        lines.append("  (no matches)")
    return "\n".join(lines)


def query_file(db, path: str) -> str:
    """Find commits touching a file."""
    rows = db.query(
        "SELECT short_hash, commit_date, subject, tags FROM commits "
        "WHERE files_changed LIKE ? "
        "ORDER BY id DESC LIMIT 30",
        (f"%{path}%",)
    )
    lines = [f"=== Commits touching '{path}' ==="]
    for row in rows:
        short_hash, date, subject, tags = row
        tag_str = f" [{tags}]" if tags else ""
        lines.append(f"  {short_hash}  {(date or '?')[:10]}  {subject}{tag_str}")
    if not rows:
        lines.append("  (no matches)")
    return "\n".join(lines)


def query_bugs(db) -> str:
    """List bug-fix commits."""
    rows = db.query(
        "SELECT short_hash, commit_date, subject, tags FROM commits "
        "WHERE tags LIKE '%BUG-%' "
        "ORDER BY id DESC LIMIT 30"
    )
    lines = ["=== Bug-fix commits ==="]
    for row in rows:
        short_hash, date, subject, tags = row
        bugs = [t for t in (tags or "").split(",") if t.startswith("BUG-")]
        lines.append(f"  {short_hash}  {(date or '?')[:10]}  {subject}  [{','.join(bugs)}]")
    if not rows:
        lines.append("  (no bug-fix commits found)")
    return "\n".join(lines)


def query_related(db, hash_prefix: str) -> str:
    """Find commits touching the same files."""
    target = db.query(
        "SELECT files_changed, subject FROM commits WHERE hash LIKE ? OR short_hash LIKE ?",
        (f"{hash_prefix}%", f"{hash_prefix}%")
    )
    if not target:
        return f"Commit {hash_prefix} not found in index."

    files = target[0][0] or ""
    lines = [f"=== Commits related to {hash_prefix} ===",
             f"Files: {files}", ""]

    seen = set()
    for f in files.split(","):
        f = f.strip()
        if not f:
            continue
        basename = os.path.basename(f)
        rows = db.query(
            "SELECT short_hash, commit_date, subject, tags FROM commits "
            "WHERE files_changed LIKE ? AND short_hash NOT LIKE ? "
            "ORDER BY id DESC LIMIT 5",
            (f"%{basename}%", f"{hash_prefix}%")
        )
        for row in rows:
            key = row[0]
            if key not in seen:
                seen.add(key)
                tag_str = f" [{row[3]}]" if row[3] else ""
                lines.append(f"  {row[0]}  {(row[1] or '?')[:10]}  {row[2]}{tag_str}")

    if len(lines) == 3:
        lines.append("  (no related commits found)")
    return "\n".join(lines)


def query_recent(db, limit: int = 20) -> str:
    """Show most recent commits."""
    rows = db.query(
        "SELECT short_hash, commit_date, subject, tags FROM commits "
        "ORDER BY id DESC LIMIT ?",
        (limit,)
    )
    lines = [f"=== Last {limit} commits ==="]
    for row in rows:
        short_hash, date, subject, tags = row
        tag_str = f" [{tags}]" if tags else ""
        lines.append(f"  {short_hash}  {(date or '?')[:10]}  {subject}{tag_str}")
    if not rows:
        lines.append("  (no commits indexed yet -- run 'context-hooks bootstrap')")
    return "\n".join(lines)


def query_stats(db) -> str:
    """Tag distribution."""
    rows = db.query("""
        WITH RECURSIVE split(tag, rest) AS (
            SELECT '', tags || ',' FROM commits WHERE tags != '' AND tags IS NOT NULL
            UNION ALL
            SELECT substr(rest, 1, instr(rest, ',') - 1),
                   substr(rest, instr(rest, ',') + 1)
            FROM split WHERE rest != ''
        )
        SELECT tag, COUNT(*) as cnt FROM split WHERE tag != ''
        GROUP BY tag ORDER BY cnt DESC LIMIT 30
    """)
    lines = ["=== Tag distribution ==="]
    for tag, count in rows:
        lines.append(f"  {tag:<30s} {count}")
    return "\n".join(lines)


def main():
    """CLI entry point: context-hooks query <subcommand> [args]"""
    if len(sys.argv) < 2:
        print("Usage: queries.py parity|search|tag|file|bugs|related|recent|stats [args]", file=sys.stderr)
        sys.exit(1)

    subcmd = sys.argv[1]
    git_root = resolve_git_root(os.getcwd())
    project_dir = data_dir(git_root)
    db = ContextDB(project_dir)

    try:
        if subcmd == "parity":
            print(query_parity(db))
        elif subcmd == "search" and len(sys.argv) > 2:
            print(query_search(db, sys.argv[2]))
        elif subcmd == "tag" and len(sys.argv) > 2:
            print(query_tag(db, sys.argv[2]))
        elif subcmd == "file" and len(sys.argv) > 2:
            print(query_file(db, sys.argv[2]))
        elif subcmd == "bugs":
            print(query_bugs(db))
        elif subcmd == "related" and len(sys.argv) > 2:
            print(query_related(db, sys.argv[2]))
        elif subcmd == "recent":
            limit = int(sys.argv[2]) if len(sys.argv) > 2 else 20
            print(query_recent(db, limit))
        elif subcmd == "stats":
            print(query_stats(db))
        else:
            print(f"Unknown query: {subcmd}", file=sys.stderr)
            sys.exit(1)
    finally:
        db.close()


if __name__ == "__main__":
    main()
