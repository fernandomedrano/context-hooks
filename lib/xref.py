"""Cross-reference report across all memory layers (F6).

Ported from memory-xref.sh. Six sections, each gracefully skips if its data
layer doesn't exist.

Reads:
  1. MEMORY.md — Claude auto-memory patterns
  2. Knowledge store — DB knowledge table
  3. Commit index — DB commits table
"""
import hashlib
import os
import re
from collections import Counter
from datetime import datetime
from itertools import combinations


# Tags too generic to flag as "undocumented patterns"
_SKIP_TAGS = frozenset({
    'fix', 'feat', 'docs', 'tests', 'test', 'chore', 'refactor',
    'api', 'apps', 'data', 'ci', 'perf', 'style', 'build', 'revert',
    'infra', 'migration',
})

# Meta files excluded from co-occurrence analysis
_META_RE = re.compile(
    r'(CURRENT|BACKLOG|COMPLETED|TODO|AGENTS|CLAUDE|MEMORY|CHANGELOG|README|commit-trail|migration-log)\.md$'
)

# Concept words to extract as terms from MEMORY.md rule bodies
_CONCEPT_RE = re.compile(
    r'\b(extraction|humanizer|pipeline|responder|carry.?forward|parity|streaming|'
    r'chat_service|fact.?graph|memory.?service|validator|drills?|sessions?)\b',
    re.IGNORECASE,
)


# ── MEMORY.md discovery ──────────────────────────────────────────────────────

def find_memory_file(git_root: str) -> str | None:
    """Find MEMORY.md for this project. Try both path formats (with/without leading dash)."""
    # Claude Code uses: ~/.claude/projects/-Users-fernando-Dev-PROJECT/memory/MEMORY.md
    # The path separators become dashes
    dashed = git_root.replace('/', '-')  # e.g. -Users-fernando-Dev-PROJECT

    candidates = [
        os.path.expanduser(f"~/.claude/projects/{dashed}/memory/MEMORY.md"),
        # Without leading dash
        os.path.expanduser(f"~/.claude/projects/{dashed.lstrip('-')}/memory/MEMORY.md"),
    ]
    for path in candidates:
        if os.path.isfile(path):
            return path
    return None


# ── MEMORY.md parsing ────────────────────────────────────────────────────────

def extract_memory_rules(content: str) -> list[dict]:
    """Extract bold patterns from MEMORY.md.

    Returns list of {name, terms, hashes, body_preview}.
    Matches lines like: - **Name** — description text
    """
    if not content.strip():
        return []

    rules = []
    for match in re.finditer(
        r'- \*\*(.+?)\*\*\s*[—–\-]?\s*(.*?)(?=\n- \*\*|\n---|\n##|\Z)',
        content,
        re.DOTALL,
    ):
        name = match.group(1).strip()
        body = match.group(2).strip()
        full_text = f"{name} {body}"

        terms: set[str] = set()

        # File references (backticked filenames, may contain dots like vite.config.ts)
        for f in re.findall(r'`([a-zA-Z_/][a-zA-Z_/.\-]*\.\w+)`', full_text):
            terms.add(os.path.basename(f).rsplit('.', 1)[0].lower())

        # BUG references
        for b in re.findall(r'BUG-(\d+)', full_text):
            terms.add(f'BUG-{b}')

        # ADR references
        for a in re.findall(r'ADR-(\d+)', full_text):
            terms.add(f'ADR-{a}')

        # Commit hashes (7+ hex chars in backticks)
        hashes = re.findall(r'`([0-9a-f]{7,})`', full_text)

        # Concept words
        for word in _CONCEPT_RE.findall(full_text):
            terms.add(word.lower().replace(' ', '_').replace('-', '_'))

        rules.append({
            'name': name,
            'terms': terms,
            'hashes': hashes,
            'body_preview': body[:200],
        })

    return rules


# ── Data loaders ─────────────────────────────────────────────────────────────

def _load_commits(db) -> list[dict]:
    """Load all commits from DB."""
    rows = db.query(
        "SELECT hash, commit_date, subject, body, files_changed, tags "
        "FROM commits ORDER BY id DESC"
    )
    commits = []
    for h, date, subject, body, files, tags in rows:
        tag_list = [t.strip() for t in (tags or '').split(',') if t.strip()]
        commits.append({
            'hash': h,
            'date': date or '',
            'subject': subject or '',
            'body': body or '',
            'files': [f for f in (files or '').split(',') if f],
            'tags': tag_list,
        })
    return commits


def _load_knowledge(db) -> list[dict]:
    """Load active knowledge entries from DB."""
    rows = db.query(
        "SELECT id, category, title, content, bug_refs, file_refs, commit_refs, "
        "created_at FROM knowledge WHERE status = 'active'"
    )
    entries = []
    for row in rows:
        kid, cat, title, content, bug_refs, file_refs, commit_refs, created = row
        bugs = []
        if bug_refs:
            bugs = [b.strip() for b in bug_refs.split(',') if b.strip()]
        frefs = []
        if file_refs:
            frefs = [f.strip() for f in file_refs.split(',') if f.strip()]
        crefs = []
        if commit_refs:
            crefs = [c.strip() for c in commit_refs.split(',') if c.strip()]
        entries.append({
            'id': kid,
            'category': cat,
            'title': title,
            'content': content,
            'bugs': bugs,
            'file_refs': frefs,
            'commit_refs': crefs,
            'created': (created or '')[:10],
        })
    return entries


def _compute_tag_counts(commits: list[dict]) -> Counter:
    """Count tag occurrences across commits."""
    counts: Counter = Counter()
    for c in commits:
        for t in c['tags']:
            counts[t] += 1
    return counts


# ── Rule validation updates ──────────────────────────────────────────────────

def _update_rule_validations(db, rules: list[dict], commits: list[dict]):
    """Update rule_validations table when xref finds matches."""
    now = datetime.now().isoformat()
    for rule in rules:
        rule_hash = hashlib.sha256(rule['name'].encode()).hexdigest()[:16]
        match_count = _count_rule_matches(rule, commits)

        existing = db.query(
            "SELECT id FROM rule_validations WHERE rule_hash = ?",
            (rule_hash,),
        )
        if existing:
            if match_count > 0:
                db.execute(
                    "UPDATE rule_validations SET last_validated = ?, match_count = ?, "
                    "status = 'active' WHERE rule_hash = ?",
                    (now, match_count, rule_hash),
                )
        else:
            db.execute(
                "INSERT INTO rule_validations (rule_name, rule_hash, last_validated, "
                "match_count, first_seen, status) VALUES (?, ?, ?, ?, ?, 'active')",
                (rule['name'], rule_hash, now if match_count > 0 else None,
                 match_count, now),
            )


def _count_rule_matches(rule: dict, commits: list[dict]) -> int:
    """Count how many commits match a rule's terms."""
    count = 0
    for c in commits:
        commit_text = f"{c['subject']} {c['body']} {','.join(c['files'])} {','.join(c['tags'])}".lower()
        # Hash match
        if any(h in c['hash'] for h in rule.get('hashes', [])):
            count += 1
            continue
        # Term match: need 2+ terms, or 1 if only 1 term exists
        matches = sum(1 for t in rule.get('terms', set()) if t.lower() in commit_text)
        if matches >= 2 or (len(rule.get('terms', set())) == 1 and matches >= 1):
            count += 1
    return count


# ── Section renderers ────────────────────────────────────────────────────────

def _section_1(rules: list[dict], commits: list[dict]) -> str:
    """MEMORY.md rules vs commit evidence."""
    lines = [
        "=" * 60,
        "1. MEMORY.md RULES vs COMMIT EVIDENCE",
        "   Which rules have recent backing? Which are stale?",
        "=" * 60,
        "",
    ]

    if not rules:
        lines.append("  (MEMORY.md not found or has no rules — skipped)")
        lines.append("")
        return "\n".join(lines)

    rules_with = []
    rules_without = []

    for rule in rules:
        match_count = _count_rule_matches(rule, commits)
        if match_count > 0:
            # Find most recent matching commit date
            most_recent = "?"
            for c in commits:
                commit_text = f"{c['subject']} {c['body']} {','.join(c['files'])} {','.join(c['tags'])}".lower()
                matched = any(h in c['hash'] for h in rule.get('hashes', []))
                if not matched:
                    term_hits = sum(1 for t in rule['terms'] if t.lower() in commit_text)
                    matched = term_hits >= 2 or (len(rule['terms']) == 1 and term_hits >= 1)
                if matched:
                    most_recent = c['date'][:10] if c['date'] else '?'
                    break
            rules_with.append((rule['name'], match_count, most_recent))
        else:
            rules_without.append(rule['name'])

    if rules_without:
        lines.append("  Rules with NO matching commits (possibly stale):")
        for name in rules_without:
            lines.append(f"    * {name}")
        lines.append("")

    if rules_with:
        lines.append("  Rules with commit evidence (sorted by recency):")
        for name, count, date in sorted(rules_with, key=lambda x: x[2], reverse=True)[:15]:
            lines.append(f"    {date}  ({count:3d} commits)  {name}")
        if len(rules_with) > 15:
            lines.append(f"    ... and {len(rules_with) - 15} more")
    lines.append("")
    return "\n".join(lines)


def _section_2(rules: list[dict], commits: list[dict], tag_counts: Counter) -> str:
    """Undocumented patterns — tags in 5+ commits without MEMORY.md coverage."""
    lines = [
        "=" * 60,
        "2. UNDOCUMENTED PATTERNS",
        "   Frequent commit tags that have no MEMORY.md rule",
        "=" * 60,
        "",
    ]

    # Collect all terms from memory rules
    all_memory_terms: set[str] = set()
    for rule in rules:
        all_memory_terms.update(t.lower() for t in rule['terms'])
        all_memory_terms.add(rule['name'].lower())

    uncovered = []
    for tag, count in tag_counts.most_common():
        if count < 5:
            break
        if tag.lower() in _SKIP_TAGS:
            continue
        if tag.startswith('solo-') or tag.startswith('paired:'):
            continue
        if tag.startswith('BUG-') or tag.startswith('ADR-') or tag.startswith('#'):
            continue
        tag_lower = tag.lower().replace('-', '_')
        covered = any(tag_lower in t or t in tag_lower for t in all_memory_terms)
        if not covered:
            sample = next((c for c in commits if tag in c['tags']), None)
            uncovered.append((tag, count, sample))

    if uncovered:
        lines.append("  Tags appearing in 5+ commits but NOT covered by MEMORY.md:")
        for tag, count, sample in uncovered:
            lines.append(f"    {count:3d}x  {tag}")
            if sample:
                lines.append(f"         e.g. {sample['hash'][:10]} {sample['subject'][:70]}")
        lines.append("")
        lines.append("  -> Consider: are any of these worth documenting as stable patterns?")
    else:
        lines.append("  All frequent tags are covered by MEMORY.md. No gaps found.")
    lines.append("")
    return "\n".join(lines)


def _section_3(knowledge: list[dict], commits: list[dict]) -> str:
    """Knowledge freshness — entries with newer related commits."""
    lines = [
        "=" * 60,
        "3. KNOWLEDGE FRESHNESS",
        "   Knowledge entries vs recent code changes",
        "=" * 60,
        "",
    ]

    if not knowledge:
        lines.append("  No knowledge entries found.")
        lines.append("")
        return "\n".join(lines)

    for entry in sorted(knowledge, key=lambda e: e['created']):
        related = []
        for c in commits:
            if any(b in c['tags'] for b in entry['bugs']):
                related.append(c)
                continue
            if any(any(ref in f for f in c['files']) for ref in entry['file_refs']):
                related.append(c)
                continue

        status = ""
        if related:
            latest = related[0]['date'][:10] if related[0]['date'] else '?'
            if entry['created'] and latest > entry['created']:
                status = f"STALE — {len(related)} commits since creation (latest: {latest})"
            else:
                status = f"current ({len(related)} related commits)"
        else:
            status = "no related commits found"

        lines.append(f"  [{entry['category']}] {entry['title']}")
        lines.append(f"    Created: {entry['created'] or '?'}  |  {status}")
        if entry['bugs']:
            lines.append(f"    Bugs: {', '.join(entry['bugs'])}")
        lines.append("")

    return "\n".join(lines)


def _section_4(knowledge: list[dict], commits: list[dict]) -> str:
    """Bug-fix knowledge gaps — BUG-NNN in commits without failure-class knowledge."""
    lines = [
        "=" * 60,
        "4. BUG-FIX KNOWLEDGE GAPS",
        "   BUG-NNN commits without matching failure class docs",
        "=" * 60,
        "",
    ]

    # Collect all documented bugs from knowledge
    documented_bugs: set[str] = set()
    for entry in knowledge:
        documented_bugs.update(entry['bugs'])

    # Collect all bugs from commits
    commit_bugs: set[str] = set()
    for c in commits:
        for tag in c['tags']:
            if tag.startswith('BUG-'):
                commit_bugs.add(tag)

    undocumented = commit_bugs - documented_bugs
    if undocumented:
        lines.append(f"  {len(undocumented)} bugs fixed in code but NOT in knowledge store:")
        for bug in sorted(undocumented, key=lambda b: int(re.search(r'\d+', b).group()), reverse=True):
            c = next((c for c in commits if bug in c['tags']), None)
            if c:
                lines.append(f"    {bug}  {c['hash'][:10]}  {c['subject'][:60]}")
        lines.append("")
        lines.append(f"  {len(documented_bugs)} bugs have knowledge entries (covered).")
    else:
        lines.append("  All bug fixes have knowledge entries. No gaps.")
    lines.append("")
    return "\n".join(lines)


def _section_5(commits: list[dict], profile: dict | None) -> str:
    """Emerging parallel paths — co-occurrences not yet in profile."""
    lines = [
        "=" * 60,
        "5. EMERGING PARALLEL PATHS",
        "   File pairs frequently edited together",
        "   that are NOT yet in the profile",
        "=" * 60,
        "",
    ]

    pair_counts: Counter = Counter()
    file_counts: Counter = Counter()
    for c in commits:
        clean = [f for f in c['files'] if f and not _META_RE.search(f)]
        for f in set(clean):
            file_counts[f] += 1
        for a, b in combinations(sorted(set(clean)), 2):
            pair_counts[(a, b)] += 1

    # Load existing profile pairs
    profile_pairs: set[tuple] = set()
    if profile:
        for pp in profile.get('parallel_paths', []) or []:
            files = pp.get('files', [])
            if len(files) == 2:
                profile_pairs.add(tuple(sorted(files)))

    emerging = []
    for (a, b), together in pair_counts.most_common(100):
        if together < 4:
            break
        key = tuple(sorted([a, b]))
        if key in profile_pairs:
            continue
        a_total = file_counts[a]
        b_total = file_counts[b]
        if a_total < 4 or b_total < 4:
            continue
        co_pct = together / min(a_total, b_total) * 100
        if co_pct >= 30:
            emerging.append((a, b, together, round(co_pct)))

    if emerging:
        lines.append("  New file pairs NOT in profile yet:")
        for a, b, together, co_pct in emerging[:10]:
            lines.append(f"    {os.path.basename(a)} <-> {os.path.basename(b)}  "
                         f"({together} together, {co_pct}% co-occurrence)")
            lines.append(f"      {a}")
            lines.append(f"      {b}")
            lines.append("")
        lines.append("  -> Regenerate profile to track these.")
    else:
        lines.append("  No new parallel paths found beyond what's in the profile.")
    lines.append("")
    return "\n".join(lines)


def _section_6(rules: list[dict], knowledge: list[dict]) -> str:
    """Memory layer overlap — MEMORY.md rules sharing refs with knowledge entries."""
    lines = [
        "=" * 60,
        "6. MEMORY LAYER OVERLAP",
        "   Information duplicated across layers",
        "=" * 60,
        "",
    ]

    if not rules or not knowledge:
        lines.append("  (Need both MEMORY.md and knowledge entries to check — skipped)")
        lines.append("")
        return "\n".join(lines)

    overlaps = []
    for rule in rules:
        for entry in knowledge:
            shared_bugs = rule['terms'] & set(entry['bugs'])
            shared_hashes = set(rule.get('hashes', [])) & set(entry.get('commit_refs', []))
            if shared_bugs or shared_hashes:
                overlaps.append((rule['name'], entry['title'], shared_bugs | shared_hashes))

    if overlaps:
        lines.append("  Same information exists in both MEMORY.md and knowledge store:")
        for mem_name, kb_name, shared in overlaps:
            lines.append(f"    MEMORY.md:   {mem_name}")
            lines.append(f"    Knowledge:   {kb_name}")
            lines.append(f"    Shared refs: {', '.join(shared)}")
            lines.append("")
        lines.append("  -> Consider: consolidate to one source of truth?")
    else:
        lines.append("  No direct overlaps found between MEMORY.md and knowledge store.")
    lines.append("")
    return "\n".join(lines)


# ── Main entry point ─────────────────────────────────────────────────────────

def run_xref(db, git_root: str, project_data_dir: str) -> str:
    """Run the full 6-section cross-reference report. Returns formatted text."""
    from lib.tags import load_profile

    # Load data
    commits = _load_commits(db)
    knowledge = _load_knowledge(db)
    tag_counts = _compute_tag_counts(commits)

    # Load MEMORY.md
    memory_file = find_memory_file(git_root)
    rules = []
    if memory_file and os.path.isfile(memory_file):
        with open(memory_file) as f:
            rules = extract_memory_rules(f.read())

    # Load profile
    profile = load_profile(project_data_dir)

    # Update rule validations
    if rules and commits:
        _update_rule_validations(db, rules, commits)

    # Header
    now = datetime.now().strftime('%Y-%m-%d')
    header = [
        "=" * 60,
        "         CROSS-REFERENCE REPORT",
        f"         {now}",
        "=" * 60,
        "",
        f"Project: {git_root}",
        "",
    ]

    # Data sources summary
    sources = ["=== Data Sources ==="]
    if memory_file:
        sources.append(f"  + MEMORY.md ({len(rules)} rules)")
    else:
        sources.append("  - MEMORY.md (not found)")
    sources.append(f"  + Knowledge store ({len(knowledge)} entries)")
    sources.append(f"  + Commit index ({len(commits)} commits, {len(tag_counts)} unique tags)")
    if profile:
        pp_count = len(profile.get('parallel_paths', []) or [])
        sources.append(f"  + Profile ({pp_count} parallel paths)")
    else:
        sources.append("  - Profile (not found)")
    sources.append("")

    # Sections
    s1 = _section_1(rules, commits)
    s2 = _section_2(rules, commits, tag_counts)
    s3 = _section_3(knowledge, commits)
    s4 = _section_4(knowledge, commits)
    s5 = _section_5(commits, profile)
    s6 = _section_6(rules, knowledge)

    # Summary
    rules_with = sum(1 for r in rules if _count_rule_matches(r, commits) > 0)
    rules_without = len(rules) - rules_with
    commit_bugs = {t for c in commits for t in c['tags'] if t.startswith('BUG-')}
    doc_bugs = {b for e in knowledge for b in e['bugs']}
    undoc_bugs = commit_bugs - doc_bugs

    summary = [
        "=" * 60,
        "SUMMARY",
        "=" * 60,
        f"  MEMORY.md:    {len(rules)} rules ({rules_with} with evidence, {rules_without} possibly stale)",
        f"  Knowledge:    {len(knowledge)} entries",
        f"  Commits:      {len(commits)} commits, {len(tag_counts)} unique tags",
        f"  Bug gaps:     {len(undoc_bugs)}",
        "",
    ]

    return "\n".join(header + sources) + s1 + s2 + s3 + s4 + s5 + s6 + "\n".join(summary)


def main():
    """CLI entry point: context-hooks xref"""
    import sys
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from lib.db import ContextDB, data_dir, resolve_git_root

    git_root = resolve_git_root(os.getcwd())
    project_dir = data_dir(git_root)
    db = ContextDB(project_dir)

    try:
        print(run_xref(db, git_root, project_dir))
    finally:
        db.close()


if __name__ == "__main__":
    main()
