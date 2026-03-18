"""Smart context surfacing for hook responses.

Three briefing generators:
  1. session_briefing() — rich context at session start (parity pairs, recent
     knowledge, last session errors). Augments the existing health check.
  2. file_briefing() — intelligence about a file being read (bug history,
     knowledge refs, conventions). Fires on PostToolUse Read.
  3. test_briefing() — related failure-class knowledge when tests run.
     Fires on PostToolUse Bash (test commands).

All generators return list[str] of short, actionable lines (or empty list).
Session-level dedup is handled by the same cache as edit_nudge.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ── Session briefing (SessionStart) ──────────────────────────────────────────

def session_briefing(local_db, cluster_db, project_data_dir: str, config: dict) -> list[str]:
    """Generate a rich project context briefing for session start.

    Supplements (not replaces) the health summary. Returns list of context lines.
    """
    lines = []

    # 1. Active parity pairs from profile
    from lib.tags import load_profile
    profile = load_profile(project_data_dir)
    if profile:
        pairs = profile.get("parallel_paths", [])
        high_pairs = [p for p in pairs if p.get("co_pct", 0) >= 60]
        if high_pairs:
            pair_strs = [
                f"{os.path.basename(p['files'][0])} <-> {os.path.basename(p['files'][1])} ({p['co_pct']}%)"
                for p in high_pairs[:5]
            ]
            lines.append(f"* Parity pairs active: {'; '.join(pair_strs)}")

    # 2. Recent knowledge entries (last 7 days)
    recent_knowledge = cluster_db.query(
        "SELECT title, category FROM knowledge "
        "WHERE status = 'active' AND created_at > datetime('now', '-7 days', 'localtime') "
        "ORDER BY id DESC LIMIT 5"
    )
    if recent_knowledge:
        titles = [f'"{t}" ({c})' for t, c in recent_knowledge]
        lines.append(f"* Recent knowledge: {', '.join(titles)}")

    # 3. Last session errors (from events table)
    recent_errors = local_db.query(
        "SELECT data FROM events "
        "WHERE category = 'error' "
        "ORDER BY id DESC LIMIT 3"
    )
    if recent_errors:
        error_summaries = []
        for (data,) in recent_errors:
            # First line of error data (command), truncated
            first_line = data.split('\n')[0][:80] if data else "unknown"
            error_summaries.append(first_line)
        lines.append(f"* Recent errors: {'; '.join(error_summaries)}")

    # 4. Hot files from profile (if any)
    if profile:
        hot = profile.get("hot_files", {})
        if hot:
            hot_names = [os.path.basename(f) for f in list(hot.keys())[:5]]
            lines.append(f"* Hot files (high churn): {', '.join(hot_names)}")

    return lines


# ── File briefing (PostToolUse Read) ─────────────────────────────────────────

def file_briefing(
    file_path: str,
    db,
    profile: dict | None,
    session_cache: dict,
    session_id: str,
) -> list[str]:
    """Generate intelligence about a file being read.

    Uses the same dedup cache as edit_nudge to avoid repeating.
    Returns list of context lines.
    """
    from lib.edit_nudge import _already_fired, _mark_fired

    lines = []
    basename = os.path.basename(file_path)

    # 1. Bug history
    key = f"read-bugs:{file_path}"
    if not _already_fired(session_cache, session_id, key):
        rows = db.query(
            "SELECT tags FROM commits "
            "WHERE files_changed LIKE ? AND tags LIKE '%BUG-%' "
            "ORDER BY id DESC LIMIT 10",
            (f"%{basename}%",)
        )
        if rows and len(rows) >= 2:
            bug_refs = set()
            for (tags,) in rows:
                for tag in (tags or "").split(","):
                    tag = tag.strip()
                    if tag.startswith("BUG-"):
                        bug_refs.add(tag)
            if bug_refs:
                _mark_fired(session_cache, session_id, key)
                refs_str = ", ".join(sorted(bug_refs)[:5])
                lines.append(
                    f"File intel: {basename} has {len(rows)} bug-fix commits ({refs_str})"
                )

    # 2. Knowledge entries referencing this file
    key = f"read-knowledge:{file_path}"
    if not _already_fired(session_cache, session_id, key):
        rows = db.query(
            "SELECT title, category FROM knowledge "
            "WHERE file_refs LIKE ? AND status = 'active' "
            "LIMIT 3",
            (f"%{basename}%",)
        )
        if rows:
            _mark_fired(session_cache, session_id, key)
            entries = [f'"{t}" ({c})' for t, c in rows]
            lines.append(f"File intel: {', '.join(entries)} reference {basename}")

    # 3. Parity companion reminder
    if profile:
        key = f"read-parity:{file_path}"
        if not _already_fired(session_cache, session_id, key):
            for pp in profile.get("parallel_paths", []):
                pp_files = pp.get("files", [])
                if len(pp_files) != 2 or pp.get("co_pct", 0) < 60:
                    continue
                file_a, file_b = pp_files
                companion = None
                if file_path == file_a or file_path.endswith("/" + file_a) or basename == os.path.basename(file_a):
                    companion = file_b
                elif file_path == file_b or file_path.endswith("/" + file_b) or basename == os.path.basename(file_b):
                    companion = file_a

                if companion:
                    _mark_fired(session_cache, session_id, key)
                    lines.append(
                        f"File intel: {basename} has parity companion "
                        f"{os.path.basename(companion)} ({pp['co_pct']}% co-occurrence)"
                    )
                    break

    return lines


# ── Test briefing (PostToolUse Bash test run) ────────────────────────────────

def check_testrun_briefing(
    command: str,
    db,
    session_cache: dict,
    session_id: str,
) -> list[str]:
    """Surface relevant failure-class knowledge when tests are run.

    Checks if any failure-class knowledge entries reference files
    that appear in recent test-related commits.
    """
    from lib.edit_nudge import _already_fired, _mark_fired

    key = f"test-briefing:{session_id}"
    if _already_fired(session_cache, session_id, key):
        return []

    # Find failure-class knowledge entries
    rows = db.query(
        "SELECT title, file_refs FROM knowledge "
        "WHERE category = 'failure-class' AND status = 'active' "
        "ORDER BY id DESC LIMIT 10"
    )
    if not rows:
        return []

    # Only fire once per session
    _mark_fired(session_cache, session_id, key)

    lines = []
    entries = [f'"{t}"' for t, _ in rows[:3]]
    lines.append(
        f"Test context: {len(rows)} failure-class knowledge entries exist. "
        f"Recent: {', '.join(entries)}"
    )

    return lines
