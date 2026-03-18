"""Proactive edit-time nudges. Fires on file_edit/file_write events.

Checks edited files against known patterns (parity pairs, bug history,
knowledge refs) and returns actionable nudge strings. Session-level dedup
ensures the same nudge never fires twice per session.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ── Session dedup cache ──────────────────────────────────────────────────────

def _cache_path(project_data_dir: str) -> str:
    return os.path.join(project_data_dir, "session_nudge_cache.json")


def load_session_cache(project_data_dir: str) -> dict:
    """Load session nudge cache. Returns {session_id: [fired_keys]}."""
    path = _cache_path(project_data_dir)
    if not os.path.exists(path):
        return {}
    try:
        with open(path) as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}


def save_session_cache(project_data_dir: str, cache: dict):
    """Persist session nudge cache."""
    path = _cache_path(project_data_dir)
    try:
        with open(path, "w") as f:
            json.dump(cache, f)
    except OSError:
        pass


def cleanup_session_cache(project_data_dir: str, current_session_id: str):
    """Remove stale sessions from cache. Called by health.py on startup."""
    cache = load_session_cache(project_data_dir)
    if not cache:
        return
    # Keep only current session
    if current_session_id in cache:
        cache = {current_session_id: cache[current_session_id]}
    else:
        cache = {}
    save_session_cache(project_data_dir, cache)


def _already_fired(cache: dict, session_id: str, key: str) -> bool:
    """Check if a nudge key was already fired this session."""
    return key in cache.get(session_id, [])


def _mark_fired(cache: dict, session_id: str, key: str):
    """Record that a nudge key was fired."""
    if session_id not in cache:
        cache[session_id] = []
    cache[session_id].append(key)


# ── Pattern matchers ─────────────────────────────────────────────────────────

def _normalize_path(file_path: str, git_root: str = None) -> str:
    """Normalize an absolute file path to a repo-relative path."""
    if git_root and file_path.startswith(git_root):
        rel = file_path[len(git_root):].lstrip("/")
        return rel
    return os.path.basename(file_path)


def _check_parity(file_path: str, profile: dict, cache: dict, session_id: str) -> str | None:
    """Check if edited file has a parity companion that should also be updated."""
    parallel_paths = profile.get("parallel_paths", [])
    if not parallel_paths:
        return None

    for pp in parallel_paths:
        pp_files = pp.get("files", [])
        if len(pp_files) != 2:
            continue

        co_pct = pp.get("co_pct", 0)
        if co_pct < 60:
            continue

        file_a, file_b = pp_files
        basename = os.path.basename(file_path)

        matched_file = None
        companion_file = None

        if file_path == file_a or file_path.endswith("/" + file_a) or basename == os.path.basename(file_a):
            matched_file = file_a
            companion_file = file_b
        elif file_path == file_b or file_path.endswith("/" + file_b) or basename == os.path.basename(file_b):
            matched_file = file_b
            companion_file = file_a

        if matched_file and companion_file:
            key = f"parity:{matched_file}:{companion_file}"
            if _already_fired(cache, session_id, key):
                return None
            _mark_fired(cache, session_id, key)
            return (
                f"Parity: {os.path.basename(matched_file)} is usually edited with "
                f"{os.path.basename(companion_file)} ({co_pct}% co-occurrence). "
                f"Verify companion was updated."
            )

    return None


def _check_bug_history(file_path: str, db, cache: dict, session_id: str) -> str | None:
    """Check if edited file has recent bug-fix history."""
    key = f"bug-history:{file_path}"
    if _already_fired(cache, session_id, key):
        return None

    basename = os.path.basename(file_path)
    rows = db.query(
        "SELECT tags FROM commits "
        "WHERE files_changed LIKE ? AND tags LIKE '%BUG-%' "
        "ORDER BY id DESC LIMIT 20",
        (f"%{basename}%",)
    )
    if not rows:
        return None

    # Extract unique BUG refs
    bug_refs = set()
    for (tags,) in rows:
        for tag in (tags or "").split(","):
            tag = tag.strip()
            if tag.startswith("BUG-"):
                bug_refs.add(tag)

    if not bug_refs:
        return None

    count = len(rows)
    if count < 2:
        return None

    _mark_fired(cache, session_id, key)
    refs_str = ", ".join(sorted(bug_refs)[:5])
    return (
        f"Bug history: {count} bug-fix commits touched {basename} "
        f"({refs_str}). Extra care advised."
    )


def _check_knowledge_refs(file_path: str, db, cache: dict, session_id: str) -> str | None:
    """Check if any active knowledge entries reference this file."""
    key = f"knowledge:{file_path}"
    if _already_fired(cache, session_id, key):
        return None

    basename = os.path.basename(file_path)
    rows = db.query(
        "SELECT title, category FROM knowledge "
        "WHERE file_refs LIKE ? AND status = 'active' "
        "LIMIT 3",
        (f"%{basename}%",)
    )
    if not rows:
        return None

    _mark_fired(cache, session_id, key)
    entries = [f'"{title}" ({cat})' for title, cat in rows]
    return f"Knowledge: {', '.join(entries)} — references this file."


def _check_hotfile(file_path: str, profile: dict, config: dict,
                   cache: dict, session_id: str) -> str | None:
    """Check if file is a known hot file (high churn). Opt-in."""
    if not config.get("nudge.edit-hotfile"):
        return None

    hot_files = profile.get("hot_files", {})
    if not hot_files:
        return None

    key = f"hotfile:{file_path}"
    if _already_fired(cache, session_id, key):
        return None

    basename = os.path.basename(file_path)
    for filepath, tag_name in hot_files.items():
        if file_path == filepath or file_path.endswith("/" + filepath) or basename == os.path.basename(filepath):
            _mark_fired(cache, session_id, key)
            return f"Hot file: {basename} is a high-churn file (tagged: {tag_name}). Changes here have wide impact."

    return None


def _check_convention(file_path: str, db, config: dict,
                      cache: dict, session_id: str) -> str | None:
    """Check if a coding convention references this file. Opt-in."""
    if not config.get("nudge.edit-convention"):
        return None

    key = f"convention:{file_path}"
    if _already_fired(cache, session_id, key):
        return None

    basename = os.path.basename(file_path)
    rows = db.query(
        "SELECT title, content FROM knowledge "
        "WHERE file_refs LIKE ? AND category = 'coding-convention' AND status = 'active' "
        "LIMIT 2",
        (f"%{basename}%",)
    )
    if not rows:
        return None

    _mark_fired(cache, session_id, key)
    titles = [f'"{title}"' for title, _ in rows]
    return f"Convention: {', '.join(titles)} applies to this file."


# ── Main entry point ─────────────────────────────────────────────────────────

def check_edit_nudges(
    file_path: str,
    db,
    profile: dict | None,
    config: dict,
    project_data_dir: str,
    session_id: str,
) -> list[str]:
    """Check a file edit against known patterns. Returns list of nudge strings.

    Always-on: parity, bug-history, knowledge-refs
    Opt-in: edit-hotfile, edit-convention
    """
    cache = load_session_cache(project_data_dir)
    nudges = []

    # Always-on: parity (needs profile)
    if profile:
        result = _check_parity(file_path, profile, cache, session_id)
        if result:
            nudges.append(result)

    # Always-on: bug history
    result = _check_bug_history(file_path, db, cache, session_id)
    if result:
        nudges.append(result)

    # Always-on: knowledge refs
    result = _check_knowledge_refs(file_path, db, cache, session_id)
    if result:
        nudges.append(result)

    # Opt-in: hot file (needs profile)
    if profile:
        result = _check_hotfile(file_path, profile, config, cache, session_id)
        if result:
            nudges.append(result)

    # Opt-in: convention
    result = _check_convention(file_path, db, config, cache, session_id)
    if result:
        nudges.append(result)

    # Persist cache if any nudges fired
    if nudges:
        save_session_cache(project_data_dir, cache)

    return nudges
