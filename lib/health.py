"""Health check + auto-hygiene (F7, F10).

Two modes:
  - health_summary() — lightweight one-liner for session-start injection
  - prune() — detailed hygiene report with optional cleanup
"""
import hashlib
import re
from datetime import datetime, timedelta


# ── Health summary ───────────────────────────────────────────────────────────

def health_summary(local_db, cluster_db, git_root: str, project_data_dir: str, config: dict) -> str | None:
    """Return a short health summary string, or None if everything looks fine.

    Checks:
      - Bug knowledge gaps (BUG-NNN commits without failure-class knowledge)
      - Stale rules (rule_validations not validated in 60+ days)
      - Emerging parallel paths (solo-a/solo-b tags above threshold)
      - Unread memos count

    Returns None if nudge.health-summary is False or nothing to report.

    local_db  — DB with events, commits, rule_validations
    cluster_db — DB with memos, knowledge
    """
    if config.get('nudge.health-summary') is False:
        return None

    lines = []

    # Bug knowledge gaps (commits in local_db, knowledge in cluster_db)
    bug_gap_count = _count_bug_gaps(local_db, cluster_db, git_root)
    if bug_gap_count > 0:
        lines.append(f"* {bug_gap_count} BUG commit(s) missing failure class docs")

    # Stale rules (rule_validations in local_db)
    stale_rules = _find_stale_rules(local_db, days=60)
    if stale_rules:
        for name in stale_rules[:3]:
            lines.append(f'* 1 rule not validated in 60d: "{name}"')

    # Emerging parallel paths (commits in local_db)
    emerging_count = _count_emerging_pairs(local_db, git_root)
    if emerging_count > 0:
        lines.append(f"* {emerging_count} emerging file pair(s) not in profile")

    # Unread memos (memos in cluster_db)
    unread = cluster_db.query("SELECT COUNT(*) FROM memos WHERE read = 0")[0][0]
    if unread > 0:
        lines.append(f"* {unread} unread memo(s)")

    if not lines:
        return None

    return "Context Hooks:\n" + "\n".join(lines)


# ── Prune / auto-hygiene ─────────────────────────────────────────────────────

def prune(local_db, cluster_db, git_root: str, project_data_dir: str, dry_run: bool = True) -> str:
    """Run hygiene checks and optionally clean up stale entries.

    Handles:
      - Knowledge entries with no related commits in 90+ days -> archive
      - rule_validations not validated in 60+ days -> mark stale
      - Read memos older than 90 days -> delete
      - Auto-signal creation: tags crossing 10-commit threshold with no knowledge entry

    local_db  — DB with events, commits, rule_validations
    cluster_db — DB with memos, knowledge

    Returns a report string.
    """
    lines = ["=== PRUNE REPORT ===", ""]
    now = datetime.now()
    cutoff_90 = (now - timedelta(days=90)).isoformat()
    cutoff_60 = (now - timedelta(days=60)).isoformat()
    mode = "DRY RUN" if dry_run else "APPLIED"
    lines.append(f"Mode: {mode}")
    lines.append("")

    # 1. Old read memos (90+ days) — memos live in cluster_db
    old_memos = cluster_db.query(
        "SELECT id, subject, created_at FROM memos "
        "WHERE read = 1 AND created_at < ?",
        (cutoff_90,),
    )
    if old_memos:
        lines.append(f"Old read memos ({len(old_memos)}):")
        for mid, subj, created in old_memos:
            lines.append(f"  [{mid}] {subj} (created: {created[:10]})")
        if not dry_run:
            cluster_db.execute(
                "DELETE FROM memos WHERE read = 1 AND created_at < ?",
                (cutoff_90,),
            )
            lines.append(f"  -> Deleted {len(old_memos)} old read memo(s)")
        else:
            lines.append(f"  -> Would delete {len(old_memos)} memo(s)")
    else:
        lines.append("Old read memos: none")
    lines.append("")

    # 2. Stale rule validations (60+ days without validation) — rule_validations in local_db
    stale_rules_rows = local_db.query(
        "SELECT id, rule_name, last_validated FROM rule_validations "
        "WHERE status = 'active' AND (last_validated IS NULL OR last_validated < ?)",
        (cutoff_60,),
    )
    if stale_rules_rows:
        lines.append(f"Stale rule validations ({len(stale_rules_rows)}):")
        for rid, name, last_val in stale_rules_rows:
            lines.append(f"  [{rid}] {name} (last: {(last_val or 'never')[:10]})")
        if not dry_run:
            local_db.execute(
                "UPDATE rule_validations SET status = 'stale' "
                "WHERE status = 'active' AND (last_validated IS NULL OR last_validated < ?)",
                (cutoff_60,),
            )
            lines.append(f"  -> Marked {len(stale_rules_rows)} rule(s) as stale")
        else:
            lines.append(f"  -> Would mark {len(stale_rules_rows)} rule(s) as stale")
    else:
        lines.append("Stale rule validations: none")
    lines.append("")

    # 3. Knowledge entries with no related commits in 90+ days — knowledge in cluster_db
    knowledge_rows = cluster_db.query(
        "SELECT id, title, last_validated, created_at FROM knowledge "
        "WHERE status = 'active' AND "
        "(last_validated IS NULL OR last_validated < ?) AND created_at < ?",
        (cutoff_90, cutoff_90),
    )
    if knowledge_rows:
        lines.append(f"Stale knowledge entries ({len(knowledge_rows)}):")
        for kid, title, last_val, created in knowledge_rows:
            lines.append(f"  [{kid}] {title} (created: {created[:10]})")
        if not dry_run:
            now_str = now.isoformat()
            for kid, _, _, _ in knowledge_rows:
                cluster_db.execute(
                    "UPDATE knowledge SET status = 'archived', updated_at = ? WHERE id = ?",
                    (now_str, kid),
                )
            lines.append(f"  -> Archived {len(knowledge_rows)} knowledge entry/entries")
        else:
            lines.append(f"  -> Would archive {len(knowledge_rows)} entry/entries")
    else:
        lines.append("Stale knowledge entries: none")
    lines.append("")

    # 4. Auto-signal: tags crossing 10-commit threshold without knowledge
    #    tag_counts reads commits (local_db), knowledge check uses cluster_db
    tag_counts = _get_tag_counts(local_db, git_root)
    auto_signals = []
    for tag, count in tag_counts.items():
        if count < 10:
            continue
        if tag.lower() in _GENERIC_TAGS:
            continue
        if tag.startswith('solo-') or tag.startswith('paired:'):
            continue
        if tag.startswith('BUG-') or tag.startswith('ADR-') or tag.startswith('#'):
            continue
        # Check if knowledge entry exists (in cluster_db)
        existing = cluster_db.query(
            "SELECT COUNT(*) FROM knowledge WHERE status = 'active' AND tags LIKE ?",
            (f'%{tag}%',),
        )[0][0]
        if existing == 0:
            auto_signals.append((tag, count))

    if auto_signals:
        lines.append(f"Auto-signal candidates ({len(auto_signals)}):")
        for tag, count in auto_signals:
            lines.append(f"  {tag} ({count} commits, no knowledge entry)")
        if not dry_run:
            now_str = now.isoformat()
            for tag, count in auto_signals:
                cluster_db.execute(
                    "INSERT OR IGNORE INTO knowledge "
                    "(category, maturity, title, content, tags, evidence_count, "
                    "created_at, updated_at, status) "
                    "VALUES (?, 'signal', ?, ?, ?, ?, ?, ?, 'active')",
                    ('reference', f'Auto-signal: {tag}',
                     f'Tag {tag} appeared in {count} commits.',
                     tag, count, now_str, now_str),
                )
            lines.append(f"  -> Created {len(auto_signals)} signal(s)")
        else:
            lines.append(f"  -> Would create {len(auto_signals)} signal(s)")
    else:
        lines.append("Auto-signal candidates: none")
    lines.append("")

    return "\n".join(lines)


# ── Helpers ──────────────────────────────────────────────────────────────────

_GENERIC_TAGS = frozenset({
    'fix', 'feat', 'docs', 'tests', 'test', 'chore', 'refactor',
    'api', 'apps', 'data', 'ci', 'perf', 'style', 'build', 'revert',
    'infra', 'migration',
})


def _count_bug_gaps(local_db, cluster_db, git_root: str) -> int:
    """Count BUG-NNN tags in commits that have no matching failure-class knowledge.

    local_db  — has commits table
    cluster_db — has knowledge table
    """
    commit_bugs: set[str] = set()
    rows = local_db.query("SELECT tags FROM commits")
    for (tags,) in rows:
        for tag in (tags or '').split(','):
            tag = tag.strip()
            if tag.startswith('BUG-'):
                commit_bugs.add(tag)

    if not commit_bugs:
        return 0

    doc_bugs: set[str] = set()
    for bug in commit_bugs:
        existing = cluster_db.query(
            "SELECT COUNT(*) FROM knowledge WHERE status = 'active' "
            "AND bug_refs LIKE ?",
            (f'%{bug}%',),
        )
        if existing[0][0] > 0:
            doc_bugs.add(bug)

    return len(commit_bugs - doc_bugs)


def _find_stale_rules(local_db, days: int = 60) -> list[str]:
    """Find rule names not validated in N+ days. Uses local_db (rule_validations)."""
    cutoff = (datetime.now() - timedelta(days=days)).isoformat()
    rows = local_db.query(
        "SELECT rule_name FROM rule_validations "
        "WHERE status = 'active' AND (last_validated IS NULL OR last_validated < ?)",
        (cutoff,),
    )
    return [r[0] for r in rows]


def _count_emerging_pairs(local_db, git_root: str) -> int:
    """Simplified count of solo-a/solo-b tags above threshold. Uses local_db (commits)."""
    rows = local_db.query("SELECT tags FROM commits")
    solo_counts: dict[str, int] = {}
    for (tags,) in rows:
        for tag in (tags or '').split(','):
            tag = tag.strip()
            if tag.startswith('solo-a:') or tag.startswith('solo-b:'):
                pair_name = tag.split(':', 1)[1]
                solo_counts[pair_name] = solo_counts.get(pair_name, 0) + 1

    return sum(1 for count in solo_counts.values() if count >= 3)


def _get_tag_counts(local_db, git_root: str) -> dict[str, int]:
    """Get tag occurrence counts from commits. Uses local_db (commits)."""
    rows = local_db.query("SELECT tags FROM commits")
    counts: dict[str, int] = {}
    for (tags,) in rows:
        for tag in (tags or '').split(','):
            tag = tag.strip()
            if tag:
                counts[tag] = counts.get(tag, 0) + 1
    return counts


def main():
    """CLI entry point: context-hooks health | context-hooks prune [--dry-run]"""
    import sys
    import os
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from lib.db import ContextDB, data_dir, resolve_git_root, resolve_cluster_db
    from lib.config import load_config

    git_root = resolve_git_root(os.getcwd())
    project_dir = data_dir(git_root)
    cluster_dir = resolve_cluster_db(project_dir)
    local_db = ContextDB(project_dir)
    cluster_db = ContextDB(cluster_dir) if cluster_dir != project_dir else local_db
    config = load_config(project_dir)

    try:
        # Check if called as "prune"
        if len(sys.argv) > 1 and sys.argv[1] == "prune":
            dry_run = "--dry-run" in sys.argv
            print(prune(local_db, cluster_db, git_root, project_dir, dry_run=dry_run))
        else:
            summary = health_summary(
                local_db, cluster_db, git_root, project_dir, config
            )
            if summary:
                print(summary)
            else:
                print("Context Hooks: all clear, no issues found.")
    finally:
        local_db.close()
        if cluster_db is not local_db:
            cluster_db.close()


if __name__ == "__main__":
    main()
