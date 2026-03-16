"""MCP tool registry — defines all tools + agent-bridge compat aliases.

Each handler: receives args dict, opens DB, calls lib/*, returns string result.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lib.db import ContextDB
from lib import knowledge


def _open_db(ctx):
    """Open a fresh DB connection for this tool call."""
    return ContextDB(ctx["project_dir"])


def build_handlers(ctx):
    """Build all tool handlers closed over the shared context. Returns dict of name -> handler."""
    handlers = {}

    # ── Knowledge tools ──────────────────────────────────────────────────

    def context_store_knowledge(args):
        db = _open_db(ctx)
        try:
            knowledge.store(
                db, args["category"], args["title"], args["content"],
                reasoning=args.get("reasoning"),
                bug_refs=args.get("bug_refs"),
                file_refs=args.get("file_refs"),
                tags=args.get("tags"),
                maturity=args.get("maturity", "decision"),
            )
            return f"Stored: {args['title']}"
        finally:
            db.close()

    def context_search_knowledge(args):
        db = _open_db(ctx)
        try:
            results = knowledge.search(db, args["query"], limit=args.get("limit", 10))
            return json.dumps(results)
        finally:
            db.close()

    def context_get_knowledge(args):
        db = _open_db(ctx)
        try:
            title = args["title"]
            category = args.get("category")
            sql = ("SELECT id, category, maturity, title, content, reasoning, status, "
                   "bug_refs, file_refs, tags, created_at "
                   "FROM knowledge WHERE title = ? AND status = 'active'")
            params = [title]
            if category:
                sql += " AND category = ?"
                params.append(category)
            rows = db.query(sql, tuple(params))
            if not rows:
                return f"Not found: {title}"
            r = rows[0]
            return json.dumps({
                "id": r[0], "category": r[1], "maturity": r[2], "title": r[3],
                "content": r[4], "reasoning": r[5], "status": r[6],
                "bug_refs": r[7], "file_refs": r[8], "tags": r[9], "created_at": r[10],
            })
        finally:
            db.close()

    def context_list_knowledge(args):
        db = _open_db(ctx)
        try:
            entries = knowledge.list_entries(
                db, category=args.get("category"), status=args.get("status", "active")
            )
            return json.dumps(entries)
        finally:
            db.close()

    def context_promote_knowledge(args):
        db = _open_db(ctx)
        try:
            knowledge.promote(db, args["id"])
            return f"Promoted entry {args['id']}"
        finally:
            db.close()

    def context_archive_knowledge(args):
        db = _open_db(ctx)
        try:
            knowledge.archive(db, args["id"])
            return f"Archived entry {args['id']}"
        finally:
            db.close()

    def context_restore_knowledge(args):
        db = _open_db(ctx)
        try:
            knowledge.restore(db, args["id"])
            return f"Restored entry {args['id']}"
        finally:
            db.close()

    def context_supersede_knowledge(args):
        db = _open_db(ctx)
        try:
            knowledge.supersede(
                db, args["old_id"], args["category"], args["title"],
                args["content"], args.get("reasoning")
            )
            return f"Superseded entry {args['old_id']} with '{args['title']}'"
        finally:
            db.close()

    handlers["context_store_knowledge"] = context_store_knowledge
    handlers["context_search_knowledge"] = context_search_knowledge
    handlers["context_get_knowledge"] = context_get_knowledge
    handlers["context_list_knowledge"] = context_list_knowledge
    handlers["context_promote_knowledge"] = context_promote_knowledge
    handlers["context_archive_knowledge"] = context_archive_knowledge
    handlers["context_restore_knowledge"] = context_restore_knowledge
    handlers["context_supersede_knowledge"] = context_supersede_knowledge

    return handlers
