"""Knowledge store with maturity lifecycle + memos. CLI-callable."""
import os
import sys

MATURITY_ORDER = ['signal', 'pattern', 'decision', 'convention']
VALID_CATEGORIES = [
    'architectural-decision', 'coding-convention', 'failure-class',
    'reference', 'rejected-approach'
]


def store(db, category, title, content, reasoning=None, bug_refs=None, file_refs=None, tags=None, maturity='decision'):
    """Store a new knowledge entry. Maturity defaults to 'decision'."""
    db.insert_knowledge(
        category=category, title=title, content=content,
        reasoning=reasoning, maturity=maturity,
        bug_refs=bug_refs, file_refs=file_refs, tags=tags
    )


def search(db, query_text, limit=10):
    """FTS5 search over title + content + reasoning. Returns ranked results."""
    rows = db.query(
        "SELECT k.id, k.category, k.maturity, k.title, k.content, k.reasoning, "
        "k.status, k.bug_refs, k.file_refs, k.tags, k.created_at "
        "FROM knowledge_fts f "
        "JOIN knowledge k ON k.id = f.rowid "
        "WHERE knowledge_fts MATCH ? AND k.status = 'active' "
        "ORDER BY bm25(knowledge_fts) "
        "LIMIT ?",
        (query_text, limit)
    )
    return [_row_to_dict(r) for r in rows]


def list_entries(db, category=None, status='active'):
    """List knowledge entries, optionally filtered by category."""
    if category:
        rows = db.query(
            "SELECT id, category, maturity, title, content, reasoning, "
            "status, bug_refs, file_refs, tags, created_at "
            "FROM knowledge WHERE status = ? AND category = ? ORDER BY id DESC",
            (status, category)
        )
    else:
        rows = db.query(
            "SELECT id, category, maturity, title, content, reasoning, "
            "status, bug_refs, file_refs, tags, created_at "
            "FROM knowledge WHERE status = ? ORDER BY id DESC",
            (status,)
        )
    return [_row_to_dict(r) for r in rows]


def promote(db, entry_id):
    """Advance maturity by one stage. Returns error if already at convention."""
    rows = db.query("SELECT maturity FROM knowledge WHERE id = ?", (entry_id,))
    if not rows:
        raise ValueError(f"Entry {entry_id} not found")
    current = rows[0][0]
    idx = MATURITY_ORDER.index(current)
    if idx >= len(MATURITY_ORDER) - 1:
        raise ValueError(f"Already at maximum maturity ({current})")
    new_maturity = MATURITY_ORDER[idx + 1]
    from datetime import datetime
    db.execute(
        "UPDATE knowledge SET maturity = ?, updated_at = ? WHERE id = ?",
        (new_maturity, datetime.now().isoformat(), entry_id)
    )


def archive(db, entry_id):
    """Set status to archived."""
    from datetime import datetime
    db.execute(
        "UPDATE knowledge SET status = 'archived', updated_at = ? WHERE id = ?",
        (datetime.now().isoformat(), entry_id)
    )


def restore(db, entry_id):
    """Restore from archived or superseded back to active."""
    from datetime import datetime
    db.execute(
        "UPDATE knowledge SET status = 'active', updated_at = ? WHERE id = ?",
        (datetime.now().isoformat(), entry_id)
    )


def dismiss(db, entry_id):
    """Set status to dismissed. Won't resurface in health suggestions."""
    from datetime import datetime
    db.execute(
        "UPDATE knowledge SET status = 'dismissed', updated_at = ? WHERE id = ?",
        (datetime.now().isoformat(), entry_id)
    )


def supersede(db, old_id, new_category, new_title, new_content, new_reasoning=None):
    """Replace old entry with new one. Links via superseded_by."""
    from datetime import datetime
    now = datetime.now().isoformat()
    # Mark old entry as superseded first (avoids UNIQUE constraint on title+status)
    db.execute(
        "UPDATE knowledge SET status = 'superseded', updated_at = ? WHERE id = ?",
        (now, old_id)
    )
    # Insert the new entry
    db.insert_knowledge(
        category=new_category, title=new_title, content=new_content,
        reasoning=new_reasoning, maturity='decision'
    )
    # Get the new entry's id and link it
    new_row = db.query(
        "SELECT id FROM knowledge WHERE title = ? AND status = 'active' ORDER BY id DESC LIMIT 1",
        (new_title,)
    )
    new_id = new_row[0][0]
    db.execute(
        "UPDATE knowledge SET superseded_by = ? WHERE id = ?",
        (new_id, old_id)
    )


# --- Memo functions ---

def send_memo(db, from_agent, subject, content, to_agent='*', expires_at=None):
    """Send a cross-session memo."""
    db.insert_memo(
        from_agent=from_agent, subject=subject, content=content,
        to_agent=to_agent, expires_at=expires_at
    )


def list_memos(db, unread_only=False):
    """List memos, optionally filtered to unread."""
    if unread_only:
        rows = db.query(
            "SELECT id, from_agent, to_agent, subject, content, created_at, read, expires_at "
            "FROM memos WHERE read = 0 ORDER BY id ASC"
        )
    else:
        rows = db.query(
            "SELECT id, from_agent, to_agent, subject, content, created_at, read, expires_at "
            "FROM memos ORDER BY id ASC"
        )
    return [_memo_to_dict(r) for r in rows]


def read_memo(db, memo_id):
    """Mark a memo as read and return its content."""
    db.execute("UPDATE memos SET read = 1 WHERE id = ?", (memo_id,))
    rows = db.query(
        "SELECT id, from_agent, to_agent, subject, content, created_at, read, expires_at "
        "FROM memos WHERE id = ?",
        (memo_id,)
    )
    if not rows:
        raise ValueError(f"Memo {memo_id} not found")
    return _memo_to_dict(rows[0])


# --- Helpers ---

def _row_to_dict(row):
    return {
        "id": row[0], "category": row[1], "maturity": row[2],
        "title": row[3], "content": row[4], "reasoning": row[5],
        "status": row[6], "bug_refs": row[7], "file_refs": row[8],
        "tags": row[9], "created_at": row[10]
    }


def _memo_to_dict(row):
    return {
        "id": row[0], "from_agent": row[1], "to_agent": row[2],
        "subject": row[3], "content": row[4], "created_at": row[5],
        "read": row[6], "expires_at": row[7]
    }


# --- CLI entry point ---

def main(args):
    """Handle CLI: knowledge store|search|list|promote|archive|restore|dismiss and memo send|list|read"""
    from lib.db import ContextDB, resolve_git_root

    git_root = resolve_git_root(os.getcwd())
    from lib.db import data_dir as get_data_dir
    db = ContextDB(get_data_dir(git_root))

    try:
        if len(args) < 1:
            print("Usage: knowledge <store|search|list|promote|archive|restore|dismiss|supersede|memo> [args]")
            sys.exit(1)

        cmd = args[0]

        if cmd == 'store':
            if len(args) < 4:
                print("Usage: knowledge store <category> <title> <content> [--reasoning R] [--bug-refs B] [--tags T]")
                sys.exit(1)
            kwargs = {}
            i = 4
            while i < len(args):
                if args[i] == '--reasoning' and i + 1 < len(args):
                    kwargs['reasoning'] = args[i + 1]; i += 2
                elif args[i] == '--bug-refs' and i + 1 < len(args):
                    kwargs['bug_refs'] = args[i + 1]; i += 2
                elif args[i] == '--file-refs' and i + 1 < len(args):
                    kwargs['file_refs'] = args[i + 1]; i += 2
                elif args[i] == '--tags' and i + 1 < len(args):
                    kwargs['tags'] = args[i + 1]; i += 2
                else:
                    i += 1
            store(db, args[1], args[2], args[3], **kwargs)
            print(f"Stored: {args[2]}")

        elif cmd == 'search':
            if len(args) < 2:
                print("Usage: knowledge search <query>")
                sys.exit(1)
            results = search(db, args[1])
            for r in results:
                print(f"[{r['id']}] {r['title']} ({r['maturity']}) — {r['category']}")

        elif cmd == 'list':
            category = args[1] if len(args) > 1 else None
            entries = list_entries(db, category=category)
            for e in entries:
                print(f"[{e['id']}] {e['title']} ({e['maturity']}/{e['status']}) — {e['category']}")

        elif cmd == 'promote':
            if len(args) < 2:
                print("Usage: knowledge promote <id>"); sys.exit(1)
            promote(db, int(args[1]))
            print(f"Promoted entry {args[1]}")

        elif cmd == 'archive':
            if len(args) < 2:
                print("Usage: knowledge archive <id>"); sys.exit(1)
            archive(db, int(args[1]))
            print(f"Archived entry {args[1]}")

        elif cmd == 'restore':
            if len(args) < 2:
                print("Usage: knowledge restore <id>"); sys.exit(1)
            restore(db, int(args[1]))
            print(f"Restored entry {args[1]}")

        elif cmd == 'dismiss':
            if len(args) < 2:
                print("Usage: knowledge dismiss <id>"); sys.exit(1)
            dismiss(db, int(args[1]))
            print(f"Dismissed entry {args[1]}")

        elif cmd == 'supersede':
            if len(args) < 5:
                print("Usage: knowledge supersede <old_id> <category> <title> <content> [--reasoning R]")
                sys.exit(1)
            reasoning = None
            if '--reasoning' in args:
                idx = args.index('--reasoning')
                reasoning = args[idx + 1] if idx + 1 < len(args) else None
            supersede(db, int(args[1]), args[2], args[3], args[4], reasoning)
            print(f"Superseded entry {args[1]}")

        elif cmd == 'memo':
            if len(args) < 2:
                print("Usage: knowledge memo <send|list|read> [args]"); sys.exit(1)
            subcmd = args[1]
            if subcmd == 'send':
                if len(args) < 5:
                    print("Usage: knowledge memo send <from> <subject> <content>"); sys.exit(1)
                send_memo(db, args[2], args[3], args[4])
                print(f"Memo sent: {args[3]}")
            elif subcmd == 'list':
                unread = '--unread' in args
                memos = list_memos(db, unread_only=unread)
                for m in memos:
                    status = " [unread]" if not m['read'] else ""
                    print(f"[{m['id']}] {m['subject']} (from: {m['from_agent']}){status}")
            elif subcmd == 'read':
                if len(args) < 3:
                    print("Usage: knowledge memo read <id>"); sys.exit(1)
                memo = read_memo(db, int(args[2]))
                print(f"From: {memo['from_agent']}")
                print(f"Subject: {memo['subject']}")
                print(f"Content: {memo['content']}")
            else:
                print(f"Unknown memo subcommand: {subcmd}"); sys.exit(1)
        else:
            print(f"Unknown command: {cmd}"); sys.exit(1)
    finally:
        db.close()


if __name__ == '__main__':
    main(sys.argv[1:])
