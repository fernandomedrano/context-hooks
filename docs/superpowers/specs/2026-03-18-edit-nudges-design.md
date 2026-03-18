# Proactive Edit Nudges — Design Spec

**Date:** 2026-03-18
**Status:** Approved
**Author:** context-hooks agent + Fernando

## Problem

Context-hooks surfaces pattern intelligence at two points:
1. **On commit** (too late — parity violation already committed)
2. **On explicit query** (too manual — agent doesn't know to ask)

When the agent edits a file with known parity companions, bug history, or knowledge entries, the system stays silent. This is the #1 bug pattern in KADE — parity misses that pass deploy but fail live testing.

## Solution

Extend the PostToolUse hook handler to check file_edit and file_write events against known patterns and return `additionalContext` nudges before the agent moves on.

## Pattern Matchers

| Pattern | Source | Confidence | Default |
|---------|--------|-----------|---------|
| **Parity pairs** | `profile.yaml` parallel_paths | co_pct ≥ 60% | Always-on |
| **Bug history** | `commits` table (BUG-NNN tags + file match) | commit count | Always-on |
| **Knowledge refs** | `knowledge` table (file_refs match, status=active) | explicit entry | Always-on |
| **Hot file** | `profile.yaml` hot_files | touch frequency | Opt-in (`nudge enable edit-hotfile`) |
| **Convention** | `knowledge` table (category=coding-convention, file_refs match) | explicit entry | Opt-in (`nudge enable edit-convention`) |

## Architecture

### New module: `lib/edit_nudge.py`

```python
def check_edit_nudges(
    file_path: str,
    db: ContextDB,
    profile: dict | None,
    config: dict,
    session_cache: dict,
    session_id: str,
) -> list[str]:
    """Check a file edit against known patterns. Returns list of nudge strings."""
```

Each pattern matcher is a separate function:
- `_check_parity(file_path, profile, session_cache, session_id) -> str | None`
- `_check_bug_history(file_path, db, session_cache, session_id) -> str | None`
- `_check_knowledge_refs(file_path, db, session_cache, session_id) -> str | None`
- `_check_hotfile(file_path, profile, config, session_cache, session_id) -> str | None`
- `_check_convention(file_path, db, config, session_cache, session_id) -> str | None`

### Hook integration: `lib/hooks.py`

After `handle_event()` returns a file_edit or file_write event, call `check_edit_nudges()`. Return `{"additionalContext": "..."}` if any nudges fire.

```python
# In handle_hook("event", ...) after handle_event():
if result and result.get("event_type") in ("file_edit", "file_write"):
    nudges = check_edit_nudges(
        file_path=event_data,
        db=db, profile=profile, config=config,
        session_cache=session_cache, session_id=session_id,
    )
    if nudges:
        return json.dumps({"additionalContext": "\n".join(nudges)})
```

### Session dedup

**Mechanism:** In-memory dict loaded from `~/.context-hooks/projects/<hash>/session_nudge_cache.json`. Keyed by session_id → set of `"pattern_type:file_path"` strings.

**Lifecycle:**
- Loaded at hook invocation (lazy — only when an edit event fires)
- Written back after each nudge check
- Cleaned up by `health.py` on session start (remove entries for non-current sessions)

**Effect:** Same nudge for same file never fires twice in one session.

### Output format

Short, actionable, single-line per nudge:

```
Parity: pipeline.py is usually edited with chat_service.py (72% co-occurrence). Verify companion was updated.
```

```
Bug history: 3 bug-fix commits touched this file in the last 30 days (BUG-041, BUG-038). Extra care advised.
```

```
Knowledge: "streaming responses must flush before return" (coding-convention) references this file.
```

## What This Does NOT Do

- Does not block or reject edits — purely informational
- Does not require profile generation — degrades gracefully (no profile = no parity/hotfile nudges, but bug history and knowledge still work)
- Does not add new DB tables — reads existing commits, knowledge, and profile
- Does not fire on Read events — too noisy, reading is exploratory
- Does not change existing commit-time nudge behavior

## Nudge CLI Extension

Two new nudge names added to `nudge.py`'s available list:
- `edit-hotfile` — "Warn when editing a hot file (high churn)"
- `edit-convention` — "Warn when editing a file referenced by a coding convention"

The always-on patterns (parity, bug-history, knowledge-refs) are not toggleable — they only fire on high-confidence matches.

## File Changes

| File | Change |
|------|--------|
| `lib/edit_nudge.py` | **New** — all edit-time pattern matchers + session cache |
| `lib/hooks.py` | Add edit nudge path after event handling |
| `lib/nudge.py` | Add `edit-hotfile` and `edit-convention` to available nudges |
| `lib/health.py` | Clean stale session cache on startup |
| `tests/test_edit_nudge.py` | **New** — unit tests for each pattern matcher |
| `bin/context-hooks` | No change (nudge CLI already dispatches to nudge.py) |

## Performance

Each edit fires at most 5 pattern checks. All are fast:
- Parity: in-memory profile dict scan (no DB)
- Bug history: single indexed SQL query
- Knowledge refs: single indexed SQL query
- Hot file: in-memory profile dict lookup (no DB)
- Convention: single indexed SQL query

Session cache JSON is <1KB typically. No measurable latency impact.
