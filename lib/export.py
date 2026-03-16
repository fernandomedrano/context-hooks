"""Knowledge durability: dual-write markdown export. CLI-callable."""
import os
import re
import sys


def _slugify(title, entry_id=0):
    """Convert title to filesystem-safe kebab-case slug."""
    slug = title.lower()
    slug = re.sub(r'[^a-z0-9\s_-]', '', slug)
    slug = re.sub(r'[\s_]+', '-', slug)
    slug = re.sub(r'-+', '-', slug)
    slug = slug.strip('-')[:80]
    return slug if slug else f'entry-{entry_id}'


def _render_frontmatter(entry):
    """Render a knowledge entry dict as markdown with YAML frontmatter."""
    lines = ['---']
    for key in ('id', 'category', 'maturity', 'status', 'title'):
        if entry.get(key) is not None:
            lines.append(f'{key}: {entry[key]}')
    for key in ('file_refs', 'commit_refs', 'bug_refs', 'tags'):
        val = entry.get(key)
        if val:
            lines.append(f'{key}: {val}')
    if entry.get('superseded_by'):
        lines.append(f'superseded_by: {entry["superseded_by"]}')
    lines.append(f'created: {entry.get("created_at", "")}')
    lines.append(f'updated: {entry.get("updated_at", "")}')
    lines.append('---')
    lines.append('')
    lines.append(entry.get('content', ''))
    lines.append('')
    return '\n'.join(lines)


def _fetch_entry(db, entry_id):
    """Fetch a knowledge entry as a dict."""
    rows = db.query(
        "SELECT id, category, maturity, title, content, reasoning, "
        "status, superseded_by, bug_refs, file_refs, commit_refs, tags, "
        "created_at, updated_at "
        "FROM knowledge WHERE id = ?",
        (entry_id,)
    )
    if not rows:
        return None
    r = rows[0]
    return {
        'id': r[0], 'category': r[1], 'maturity': r[2], 'title': r[3],
        'content': r[4], 'reasoning': r[5], 'status': r[6],
        'superseded_by': r[7], 'bug_refs': r[8], 'file_refs': r[9],
        'commit_refs': r[10], 'tags': r[11],
        'created_at': r[12], 'updated_at': r[13]
    }


def resolve_export_dir(git_root, project_dir):
    """Return the export directory, or None if export is disabled."""
    from lib.config import load_config
    config = load_config(project_dir)
    if not config.get('knowledge_export'):
        return None

    effective_git_root = git_root
    cluster_path = os.path.join(project_dir, 'cluster.yaml')
    if os.path.exists(cluster_path):
        from lib.config import _parse_simple_yaml
        with open(cluster_path) as f:
            cluster_config = _parse_simple_yaml(f.read())
        master_root = cluster_config.get('master')
        if master_root and master_root.strip():
            effective_git_root = master_root

    custom = config.get('knowledge_export_dir')
    if custom:
        return os.path.join(effective_git_root, custom)
    return os.path.join(effective_git_root, 'data', 'knowledge')


def write_entry(db, entry_id, export_dir, filename=None):
    """Write/update one entry's markdown file."""
    entry = _fetch_entry(db, entry_id)
    if not entry:
        return
    slug = filename or _slugify(entry['title'], entry_id=entry['id'])
    cat_dir = os.path.join(export_dir, entry['category'])
    os.makedirs(cat_dir, exist_ok=True)
    path = os.path.join(cat_dir, f'{slug}.md')
    content = _render_frontmatter(entry)
    with open(path, 'w') as f:
        f.write(content)


def remove_entry(category, slug, export_dir):
    """Delete a knowledge entry's markdown file."""
    path = os.path.join(export_dir, category, f'{slug}.md')
    if os.path.exists(path):
        os.remove(path)


def write_index(db, export_dir):
    """Regenerate index.md from all active entries."""
    rows = db.query(
        "SELECT id, category, title, tags FROM knowledge "
        "WHERE status = 'active' ORDER BY category, title"
    )
    os.makedirs(export_dir, exist_ok=True)

    groups = {}
    for row in rows:
        cat = row[1]
        groups.setdefault(cat, []).append(row)

    lines = [
        '# Knowledge Index',
        '',
        '*Auto-generated. Do not edit — regenerated on each knowledge mutation.*',
        ''
    ]
    for cat in sorted(groups.keys()):
        entries = groups[cat]
        lines.append(f'## {cat} ({len(entries)} active)')
        lines.append('')
        for row in entries:
            entry_id, _, title, tags = row
            slug = _slugify(title, entry_id=entry_id)
            tag_suffix = f' — {tags}' if tags else ''
            lines.append(f'- [{title}]({cat}/{slug}.md){tag_suffix}')
        lines.append('')

    path = os.path.join(export_dir, 'index.md')
    with open(path, 'w') as f:
        f.write('\n'.join(lines))


def export_all(db, export_dir):
    """Bulk re-export all active + archived entries and regenerate index."""
    rows = db.query(
        "SELECT id FROM knowledge WHERE status IN ('active', 'archived') ORDER BY id"
    )
    for row in rows:
        write_entry(db, row[0], export_dir)
    write_index(db, export_dir)


def _safe_export(fn, *args, **kwargs):
    """Call export function, logging but not raising on failure."""
    try:
        fn(*args, **kwargs)
    except Exception as e:
        print(f"WARNING: knowledge export failed: {e}", file=sys.stderr)
