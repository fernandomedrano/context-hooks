"""MCP tool registry — defines all tools + agent-bridge compat aliases.

Each handler: receives args dict, opens DB, calls lib/*, returns string result.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lib.db import ContextDB
from lib import knowledge


def _open_local_db(ctx):
    """Open a fresh local DB connection (events, commits, tags)."""
    return ContextDB(ctx["project_dir"])

def _open_cluster_db(ctx):
    """Open a fresh cluster DB connection (memos, knowledge, shared_state)."""
    return ContextDB(ctx["cluster_dir"])


def build_handlers(ctx):
    """Build all tool handlers closed over the shared context. Returns dict of name -> handler."""
    handlers = {}

    # ── Knowledge tools ──────────────────────────────────────────────────

    def context_store_knowledge(args):
        db = _open_cluster_db(ctx)
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
        db = _open_cluster_db(ctx)
        try:
            results = knowledge.search(db, args["query"], limit=args.get("limit", 10))
            return json.dumps(results)
        finally:
            db.close()

    def context_get_knowledge(args):
        db = _open_cluster_db(ctx)
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
        db = _open_cluster_db(ctx)
        try:
            entries = knowledge.list_entries(
                db, category=args.get("category"), status=args.get("status", "active")
            )
            return json.dumps(entries)
        finally:
            db.close()

    def context_promote_knowledge(args):
        db = _open_cluster_db(ctx)
        try:
            knowledge.promote(db, args["id"])
            return f"Promoted entry {args['id']}"
        finally:
            db.close()

    def context_archive_knowledge(args):
        db = _open_cluster_db(ctx)
        try:
            knowledge.archive(db, args["id"])
            return f"Archived entry {args['id']}"
        finally:
            db.close()

    def context_restore_knowledge(args):
        db = _open_cluster_db(ctx)
        try:
            knowledge.restore(db, args["id"])
            return f"Restored entry {args['id']}"
        finally:
            db.close()

    def context_supersede_knowledge(args):
        db = _open_cluster_db(ctx)
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

    # ── Memo tools ───────────────────────────────────────────────────────

    def context_send_memo(args):
        db = _open_cluster_db(ctx)
        try:
            knowledge.send_memo(
                db, args["from_agent"], args["subject"], args["content"],
                to_agent=args.get("to_agent", "*"),
                expires_at=args.get("expires_at"),
            )
            return f"Memo sent: {args['subject']}"
        finally:
            db.close()

    def context_check_memos(args):
        db = _open_cluster_db(ctx)
        try:
            to_agent = args.get("to_agent")
            unread_only = args.get("unread_only", False)
            if to_agent:
                sql = ("SELECT id, from_agent, to_agent, subject, content, created_at, read, expires_at "
                       "FROM memos WHERE (to_agent = ? OR to_agent = '*')")
                params = [to_agent]
                if unread_only:
                    sql += " AND read = 0"
                sql += " ORDER BY id ASC"
                rows = db.query(sql, tuple(params))
                result = [
                    {"id": r[0], "from_agent": r[1], "to_agent": r[2], "subject": r[3],
                     "content": r[4], "created_at": r[5], "read": r[6], "expires_at": r[7]}
                    for r in rows
                ]
            else:
                result = knowledge.list_memos(db, unread_only=unread_only)
            return json.dumps(result)
        finally:
            db.close()

    def context_read_memo(args):
        db = _open_cluster_db(ctx)
        try:
            memo = knowledge.read_memo(db, args["id"])
            return json.dumps(memo)
        finally:
            db.close()

    def context_reply_memo(args):
        db = _open_cluster_db(ctx)
        try:
            memo_id = args["memo_id"]
            rows = db.query(
                "SELECT id, from_agent, to_agent, subject, thread_id FROM memos WHERE id = ?",
                (memo_id,)
            )
            if not rows:
                raise ValueError(f"Memo {memo_id} not found")
            orig = rows[0]
            orig_from = orig[1]
            orig_subject = orig[3]
            thread_id = orig[4]
            if not thread_id:
                thread_id = f"thread-{memo_id}"
                db.execute("UPDATE memos SET thread_id = ? WHERE id = ?", (thread_id, memo_id))
            db.insert_memo(
                from_agent=args["from_agent"],
                to_agent=orig_from,
                subject=f"Re: {orig_subject}",
                content=args["content"],
                thread_id=thread_id,
            )
            return f"Replied to memo {memo_id} in {thread_id}"
        finally:
            db.close()

    def context_broadcast(args):
        db = _open_cluster_db(ctx)
        try:
            db.insert_memo(
                from_agent=args["from_agent"],
                to_agent="*",
                subject=args["subject"],
                content=args["content"],
                priority=args.get("priority", "normal"),
            )
            return f"Broadcast sent: {args['subject']}"
        finally:
            db.close()

    def context_list_threads(args):
        db = _open_cluster_db(ctx)
        try:
            limit = args.get("limit", 20)
            rows = db.query(
                "SELECT thread_id, MIN(subject), GROUP_CONCAT(DISTINCT from_agent), "
                "COUNT(*), MAX(created_at) "
                "FROM memos WHERE thread_id IS NOT NULL "
                "GROUP BY thread_id ORDER BY MAX(created_at) DESC LIMIT ?",
                (limit,)
            )
            result = [
                {"thread_id": r[0], "subject": r[1], "participants": r[2].split(","),
                 "message_count": r[3], "last_activity": r[4]}
                for r in rows
            ]
            return json.dumps(result)
        finally:
            db.close()

    handlers["context_send_memo"] = context_send_memo
    handlers["context_check_memos"] = context_check_memos
    handlers["context_read_memo"] = context_read_memo
    handlers["context_reply_memo"] = context_reply_memo
    handlers["context_broadcast"] = context_broadcast
    handlers["context_list_threads"] = context_list_threads

    # ── Task & state tools ───────────────────────────────────────────────

    def context_handoff_task(args):
        db = _open_cluster_db(ctx)
        try:
            task_content = json.dumps({
                "description": args["description"],
                "relevant_files": args.get("relevant_files", ""),
                "context": args.get("context", ""),
                "blockers": args.get("blockers", ""),
                "priority": args.get("priority", "normal"),
            })
            db.insert_memo(
                from_agent=args["from_agent"],
                to_agent=args["to_agent"],
                subject=f"[TASK] {args['title']}",
                content=task_content,
                priority=args.get("priority", "normal"),
            )
            return f"Task handed off: {args['title']} -> {args['to_agent']}"
        finally:
            db.close()

    def context_set_shared_state(args):
        db = _open_cluster_db(ctx)
        try:
            db.upsert_shared_state(
                key=args["key"], value=args["value"], updated_by=args["updated_by"]
            )
            return f"State set: {args['key']} = {args['value']}"
        finally:
            db.close()

    def context_get_shared_state(args):
        db = _open_cluster_db(ctx)
        try:
            key = args.get("key")
            rows = db.get_shared_state(key)
            if key:
                if not rows:
                    return f"Not found: {key}"
                r = rows[0]
                return json.dumps({"key": r[0], "value": r[1], "updated_by": r[2], "updated_at": r[3]})
            return json.dumps([
                {"key": r[0], "value": r[1], "updated_by": r[2], "updated_at": r[3]}
                for r in rows
            ])
        finally:
            db.close()

    handlers["context_handoff_task"] = context_handoff_task
    handlers["context_set_shared_state"] = context_set_shared_state
    handlers["context_get_shared_state"] = context_get_shared_state

    # ── Query & analysis tools ───────────────────────────────────────────

    from lib import queries

    _TERM_REQUIRED_MODES = {"search", "tag", "file", "related"}

    def context_query_commits(args):
        db = _open_local_db(ctx)
        try:
            mode = args["mode"]
            term = args.get("term")
            limit = args.get("limit", 20)

            if mode in _TERM_REQUIRED_MODES and not term:
                raise ValueError(f"'term' is required for mode '{mode}'")

            if mode == "search":
                return queries.query_search(db, term)
            elif mode == "tag":
                return queries.query_tag(db, term)
            elif mode == "file":
                return queries.query_file(db, term)
            elif mode == "bugs":
                return queries.query_bugs(db)
            elif mode == "related":
                return queries.query_related(db, term)
            elif mode == "recent":
                return queries.query_recent(db, limit)
            elif mode == "stats":
                return queries.query_stats(db)
            else:
                raise ValueError(f"Unknown mode: {mode}")
        finally:
            db.close()

    def context_check_parity(args):
        db = _open_local_db(ctx)
        try:
            return queries.query_parity(db)
        finally:
            db.close()

    def context_run_xref(args):
        db = _open_cluster_db(ctx)
        try:
            from lib.xref import run_xref
            return run_xref(db, ctx["git_root"], ctx["project_dir"])
        finally:
            db.close()

    def context_get_health(args):
        local_db = _open_local_db(ctx)
        cluster_db = _open_cluster_db(ctx)
        try:
            from lib.health import health_summary
            result = health_summary(local_db, cluster_db, ctx["git_root"], ctx["project_dir"], ctx["config"])
            return result or "No health issues detected."
        finally:
            local_db.close()
            cluster_db.close()

    def context_get_profile(args):
        from lib.tags import generate_profile, save_profile
        days = args.get("days", 30)
        profile = generate_profile(ctx["git_root"], days=days)
        save_profile(ctx["project_dir"], profile)
        return json.dumps(profile)

    def context_get_project_context(args):
        cluster_db = _open_cluster_db(ctx)
        local_db = _open_local_db(ctx)
        try:
            result = {}
            if args.get("include_health", True):
                from lib.health import health_summary
                result["health"] = health_summary(local_db, cluster_db, ctx["git_root"], ctx["project_dir"], ctx["config"]) or "OK"
            if args.get("include_memos", True):
                result["memos"] = knowledge.list_memos(cluster_db, unread_only=True)
            if args.get("include_knowledge", True):
                limit = args.get("knowledge_limit", 10)
                entries = knowledge.list_entries(cluster_db)
                result["knowledge"] = entries[:limit]
            return json.dumps(result)
        finally:
            cluster_db.close()
            local_db.close()

    handlers["context_query_commits"] = context_query_commits
    handlers["context_check_parity"] = context_check_parity
    handlers["context_run_xref"] = context_run_xref
    handlers["context_get_health"] = context_get_health
    handlers["context_get_profile"] = context_get_profile
    handlers["context_get_project_context"] = context_get_project_context

    return handlers


# ── Tool schemas ─────────────────────────────────────────────────────────────
# Each entry: (native_name, compat_alias_or_None, description, input_schema)

TOOL_SCHEMAS = [
    # Knowledge tools
    ("context_store_knowledge", "store_knowledge", "Store a knowledge entry with category, title, content, optional reasoning/tags/maturity",
     {"type": "object", "properties": {"category": {"type": "string", "enum": ["architectural-decision", "coding-convention", "failure-class", "reference", "rejected-approach"]}, "title": {"type": "string"}, "content": {"type": "string"}, "reasoning": {"type": "string"}, "maturity": {"type": "string", "enum": ["signal", "pattern", "decision", "convention"], "default": "decision"}, "bug_refs": {"type": "string"}, "file_refs": {"type": "string"}, "tags": {"type": "string"}}, "required": ["category", "title", "content"]}),
    ("context_search_knowledge", "search_knowledge", "FTS5 search over knowledge entries",
     {"type": "object", "properties": {"query": {"type": "string"}, "limit": {"type": "integer", "default": 10}}, "required": ["query"]}),
    ("context_get_knowledge", "get_knowledge", "Get a specific knowledge entry by exact title (active only)",
     {"type": "object", "properties": {"title": {"type": "string"}, "category": {"type": "string"}}, "required": ["title"]}),
    ("context_list_knowledge", "list_knowledge", "List knowledge entries, optionally filtered by category",
     {"type": "object", "properties": {"category": {"type": "string"}, "status": {"type": "string", "enum": ["active", "archived", "superseded", "dismissed"], "default": "active"}}}),
    ("context_promote_knowledge", None, "Advance maturity: signal -> pattern -> decision -> convention",
     {"type": "object", "properties": {"id": {"type": "integer"}}, "required": ["id"]}),
    ("context_archive_knowledge", None, "Archive a knowledge entry (soft delete)",
     {"type": "object", "properties": {"id": {"type": "integer"}}, "required": ["id"]}),
    ("context_restore_knowledge", None, "Restore an archived or dismissed entry to active",
     {"type": "object", "properties": {"id": {"type": "integer"}}, "required": ["id"]}),
    ("context_supersede_knowledge", None, "Replace a knowledge entry with a new one, preserving lineage",
     {"type": "object", "properties": {"old_id": {"type": "integer"}, "category": {"type": "string", "enum": ["architectural-decision", "coding-convention", "failure-class", "reference", "rejected-approach"]}, "title": {"type": "string"}, "content": {"type": "string"}, "reasoning": {"type": "string"}}, "required": ["old_id", "category", "title", "content"]}),
    # Memo tools
    ("context_send_memo", "send_memo", "Send a memo to a specific agent",
     {"type": "object", "properties": {"from_agent": {"type": "string"}, "to_agent": {"type": "string"}, "subject": {"type": "string"}, "content": {"type": "string"}, "expires_at": {"type": "string"}}, "required": ["from_agent", "to_agent", "subject", "content"]}),
    ("context_check_memos", "check_memos", "List memos, optionally filtered to unread or by recipient",
     {"type": "object", "properties": {"unread_only": {"type": "boolean", "default": False}, "to_agent": {"type": "string"}}}),
    ("context_read_memo", "read_memo", "Read a memo and mark it as read",
     {"type": "object", "properties": {"id": {"type": "integer"}}, "required": ["id"]}),
    ("context_reply_memo", "reply_memo", "Reply to a memo (creates/continues thread)",
     {"type": "object", "properties": {"memo_id": {"type": "integer"}, "from_agent": {"type": "string"}, "content": {"type": "string"}}, "required": ["memo_id", "from_agent", "content"]}),
    ("context_broadcast", "broadcast", "Send a broadcast memo to all agents with priority",
     {"type": "object", "properties": {"from_agent": {"type": "string"}, "subject": {"type": "string"}, "content": {"type": "string"}, "priority": {"type": "string", "enum": ["normal", "high", "urgent"], "default": "normal"}}, "required": ["from_agent", "subject", "content"]}),
    ("context_list_threads", "list_threads", "List conversation threads with summary",
     {"type": "object", "properties": {"limit": {"type": "integer", "default": 20}}}),
    # Task & state tools
    ("context_handoff_task", "handoff_task", "Structured task handoff between agents",
     {"type": "object", "properties": {"from_agent": {"type": "string"}, "to_agent": {"type": "string"}, "title": {"type": "string"}, "description": {"type": "string"}, "relevant_files": {"type": "string"}, "context": {"type": "string"}, "blockers": {"type": "string"}, "priority": {"type": "string", "enum": ["low", "normal", "high", "urgent"], "default": "normal"}}, "required": ["from_agent", "to_agent", "title", "description"]}),
    ("context_set_shared_state", "set_shared_state", "Set key-value state visible to all agents",
     {"type": "object", "properties": {"key": {"type": "string"}, "value": {"type": "string"}, "updated_by": {"type": "string"}}, "required": ["key", "value", "updated_by"]}),
    ("context_get_shared_state", "get_shared_state", "Get shared state by key or all state",
     {"type": "object", "properties": {"key": {"type": "string"}}}),
    # Query & analysis tools
    ("context_query_commits", None, "Search indexed commits by term, tag, file, or list recent/bugs/stats",
     {"type": "object", "properties": {"mode": {"type": "string", "enum": ["search", "tag", "file", "bugs", "related", "recent", "stats"]}, "term": {"type": "string"}, "limit": {"type": "integer", "default": 20}}, "required": ["mode"]}),
    ("context_check_parity", None, "Show parallel path alerts (solo edits without companion)",
     {"type": "object", "properties": {}}),
    ("context_run_xref", None, "Cross-reference report across all memory layers",
     {"type": "object", "properties": {}}),
    ("context_get_health", None, "Session health summary",
     {"type": "object", "properties": {}}),
    ("context_get_profile", None, "Regenerate and return auto-discovered file pair patterns",
     {"type": "object", "properties": {"days": {"type": "integer", "default": 30}}}),
    ("context_get_project_context", "get_context_for_project", "Composite: health + unread memos + recent knowledge",
     {"type": "object", "properties": {"include_health": {"type": "boolean", "default": True}, "include_memos": {"type": "boolean", "default": True}, "include_knowledge": {"type": "boolean", "default": True}, "knowledge_limit": {"type": "integer", "default": 10}}}),
]


def register_all_tools(server, ctx, compat=None):
    """Register all tools on an MCPServer. If compat='agent-bridge', also register aliases."""
    handlers = build_handlers(ctx)

    for native_name, alias, description, schema in TOOL_SCHEMAS:
        handler = handlers[native_name]
        server.register_tool(
            name=native_name, description=description,
            input_schema=schema, handler=handler,
        )
        if compat == "agent-bridge" and alias:
            server.register_tool(
                name=alias, description=description,
                input_schema=schema, handler=handler,
            )


# ── CLI entry point ──────────────────────────────────────────────────────────

def main():
    """Start the MCP server. Called by: bin/context-hooks mcp [flags]"""
    import argparse
    from lib.db import data_dir, resolve_git_root, resolve_cluster_db
    from lib.config import load_config
    from lib.mcp import MCPServer

    parser = argparse.ArgumentParser(description="context-hooks MCP server")
    parser.add_argument("--compat", choices=["agent-bridge"], default=None,
                        help="Register compatibility aliases")
    parser.add_argument("--project", default=None,
                        help="Project directory (default: resolve from cwd)")
    args = parser.parse_args()

    if args.project:
        git_root = args.project
    else:
        git_root = resolve_git_root(os.getcwd())

    project_dir = data_dir(git_root)
    cluster_dir = resolve_cluster_db(project_dir)
    config = load_config(project_dir)

    ctx = {
        "project_dir": project_dir,
        "cluster_dir": cluster_dir,
        "git_root": git_root,
        "config": config,
    }

    server = MCPServer("context-hooks", "0.2.0")
    register_all_tools(server, ctx, compat=args.compat)
    server.run()


if __name__ == "__main__":
    main()
