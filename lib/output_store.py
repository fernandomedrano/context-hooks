"""Tool output indexing with FTS5 search and progressive throttling.

Large tool outputs (>4KB) are chunked, indexed in FTS5, and searchable
on demand. Ephemeral per session — cleaned up on session start.

Inspired by context-mode (github.com/mksglu/context-mode).
"""
import os
import re
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# ── Constants ────────────────────────────────────────────────────────────────

OUTPUT_THRESHOLD = 4096  # Only index outputs larger than 4KB
MAX_CHUNK_SIZE = 4096    # Max bytes per chunk
MAX_CHUNKS_PER_SESSION = 200  # FIFO eviction threshold

# Progressive throttle: (max_call, max_results)
_THROTTLE_TIERS = [
    (3, 5),    # Calls 1-3: up to 5 results
    (8, 1),    # Calls 4-8: 1 result per call
    # Beyond 8: blocked
]


# ── Chunking ─────────────────────────────────────────────────────────────────

def _chunk_by_headings(text: str) -> list[str]:
    """Split markdown/code by heading boundaries. Keeps code blocks intact."""
    # Split at markdown headings (##, ###, etc.)
    parts = re.split(r'(?=^#{1,6}\s)', text, flags=re.MULTILINE)
    chunks = []
    for part in parts:
        part = part.strip()
        if not part:
            continue
        if len(part) <= MAX_CHUNK_SIZE:
            chunks.append(part)
        else:
            # Sub-chunk large sections by paragraphs
            chunks.extend(_chunk_by_paragraphs(part))
    return chunks


def _chunk_by_paragraphs(text: str) -> list[str]:
    """Split text at paragraph boundaries (double newline)."""
    paragraphs = re.split(r'\n\n+', text)
    chunks = []
    current = ""

    for para in paragraphs:
        if len(current) + len(para) + 2 > MAX_CHUNK_SIZE and current:
            chunks.append(current.strip())
            current = para
        else:
            current = current + "\n\n" + para if current else para

    if current.strip():
        chunks.append(current.strip())

    # Handle case where a single paragraph exceeds max chunk size
    final = []
    for chunk in chunks:
        if len(chunk) <= MAX_CHUNK_SIZE:
            final.append(chunk)
        else:
            final.extend(_chunk_by_lines(chunk))
    return final


def _chunk_by_lines(text: str, lines_per_chunk: int = 100) -> list[str]:
    """Split by fixed line groups. Last resort chunking."""
    lines = text.split('\n')
    chunks = []
    for i in range(0, len(lines), lines_per_chunk):
        chunk = '\n'.join(lines[i:i + lines_per_chunk])
        if chunk.strip():
            chunks.append(chunk)
    return chunks


def chunk_output(text: str) -> list[str]:
    """Smart chunking: headings -> paragraphs -> lines."""
    if not text or len(text) <= MAX_CHUNK_SIZE:
        return [text] if text else []

    # Try heading-based splitting first (markdown, code output)
    if re.search(r'^#{1,6}\s', text, re.MULTILINE):
        return _chunk_by_headings(text)

    # Fall back to paragraph splitting
    chunks = _chunk_by_paragraphs(text)
    if chunks:
        return chunks

    # Last resort: line-based
    return _chunk_by_lines(text)


# ── Indexing ─────────────────────────────────────────────────────────────────

def index_output(db, session_id: str, source: str, text: str) -> int:
    """Chunk and index a large tool output. Returns number of chunks created."""
    chunks = chunk_output(text)
    if not chunks:
        return 0

    now = datetime.now().isoformat()
    for i, chunk in enumerate(chunks):
        db.execute(
            "INSERT INTO output_chunks (session_id, source, chunk_index, content, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (session_id, source, i, chunk, now)
        )
        # Update FTS index
        row_id = db.query("SELECT last_insert_rowid()")[0][0]
        db.execute(
            "INSERT INTO output_chunks_fts(rowid, source, content) VALUES (?, ?, ?)",
            (row_id, source, chunk)
        )

    # FIFO eviction
    _evict_chunks(db, session_id)

    return len(chunks)


def _evict_chunks(db, session_id: str):
    """Remove oldest chunks if over the limit."""
    count = db.query(
        "SELECT COUNT(*) FROM output_chunks WHERE session_id = ?",
        (session_id,)
    )[0][0]

    if count <= MAX_CHUNKS_PER_SESSION:
        return

    excess = count - MAX_CHUNKS_PER_SESSION
    old_ids = db.query(
        "SELECT id FROM output_chunks WHERE session_id = ? ORDER BY id ASC LIMIT ?",
        (session_id, excess)
    )
    for (chunk_id,) in old_ids:
        db.execute(
            "DELETE FROM output_chunks_fts WHERE rowid = ?", (chunk_id,)
        )
    db.execute(
        "DELETE FROM output_chunks WHERE session_id = ? AND id IN "
        "(SELECT id FROM output_chunks WHERE session_id = ? ORDER BY id ASC LIMIT ?)",
        (session_id, session_id, excess)
    )


# ── Search ───────────────────────────────────────────────────────────────────

def search_output(db, session_id: str, query: str, call_count: int) -> dict:
    """Search indexed outputs with progressive throttling.

    Returns dict with:
      - results: list of {source, chunk_index, snippet}
      - throttled: bool (true if results were limited)
      - blocked: bool (true if search was blocked entirely)
      - message: str (user-facing message)
    """
    # Check throttle
    max_results = _throttle_limit(call_count)
    if max_results == 0:
        return {
            "results": [],
            "throttled": False,
            "blocked": True,
            "message": (
                "Search blocked: too many queries this session. "
                "Refine your approach or list sources with: "
                "context-hooks search-output --sources"
            ),
        }

    # Try FTS5 MATCH first
    try:
        rows = db.query(
            "SELECT o.source, o.chunk_index, o.content "
            "FROM output_chunks_fts f "
            "JOIN output_chunks o ON o.id = f.rowid "
            "WHERE output_chunks_fts MATCH ? AND o.session_id = ? "
            "ORDER BY bm25(output_chunks_fts) "
            "LIMIT ?",
            (query, session_id, max_results)
        )
    except Exception:
        rows = []

    # Fallback to LIKE if FTS5 fails or returns nothing
    if not rows:
        rows = db.query(
            "SELECT source, chunk_index, content FROM output_chunks "
            "WHERE session_id = ? AND content LIKE ? "
            "ORDER BY id DESC LIMIT ?",
            (session_id, f"%{query}%", max_results)
        )

    results = []
    for source, chunk_idx, content in rows:
        # Extract a snippet around the match
        snippet = _extract_snippet(content, query)
        results.append({
            "source": source,
            "chunk_index": chunk_idx,
            "snippet": snippet,
        })

    throttled = call_count > 3 and len(results) > 0
    return {
        "results": results,
        "throttled": throttled,
        "blocked": False,
        "message": f"{len(results)} result(s) found." + (
            " (throttled — fewer results per query)" if throttled else ""
        ),
    }


def list_sources(db, session_id: str) -> list[dict]:
    """List all indexed sources for this session. Not throttled."""
    rows = db.query(
        "SELECT source, COUNT(*) as chunks, SUM(LENGTH(content)) as total_bytes "
        "FROM output_chunks WHERE session_id = ? "
        "GROUP BY source ORDER BY MAX(id) DESC",
        (session_id,)
    )
    return [
        {"source": src, "chunks": cnt, "size_bytes": size}
        for src, cnt, size in rows
    ]


def _throttle_limit(call_count: int) -> int:
    """Return max results for this call number. 0 = blocked."""
    for max_call, max_results in _THROTTLE_TIERS:
        if call_count <= max_call:
            return max_results
    return 0  # Blocked


def _extract_snippet(content: str, query: str, context_chars: int = 200) -> str:
    """Extract a snippet around the first match of query in content."""
    lower_content = content.lower()
    lower_query = query.lower()

    # Try to find exact match
    idx = lower_content.find(lower_query)
    if idx == -1:
        # Try first word of query
        first_word = lower_query.split()[0] if lower_query.split() else ""
        idx = lower_content.find(first_word) if first_word else -1

    if idx == -1:
        # No match found, return start of content
        return content[:context_chars * 2] + ("..." if len(content) > context_chars * 2 else "")

    start = max(0, idx - context_chars)
    end = min(len(content), idx + len(query) + context_chars)
    snippet = content[start:end]

    if start > 0:
        snippet = "..." + snippet
    if end < len(content):
        snippet = snippet + "..."

    return snippet


# ── Cleanup ──────────────────────────────────────────────────────────────────

def cleanup_session_outputs(db, current_session_id: str):
    """Remove output chunks from previous sessions. Called on session start."""
    # Get all session IDs with chunks
    sessions = db.query(
        "SELECT DISTINCT session_id FROM output_chunks WHERE session_id != ?",
        (current_session_id,)
    )
    if not sessions:
        return

    for (old_session,) in sessions:
        # Clean FTS entries first
        old_ids = db.query(
            "SELECT id FROM output_chunks WHERE session_id = ?",
            (old_session,)
        )
        for (chunk_id,) in old_ids:
            db.execute(
                "DELETE FROM output_chunks_fts WHERE rowid = ?", (chunk_id,)
            )
        db.execute(
            "DELETE FROM output_chunks WHERE session_id = ?",
            (old_session,)
        )


# ── Summary generation ───────────────────────────────────────────────────────

def summarize_output(text: str, source: str, chunk_count: int) -> str:
    """Generate a short summary for additionalContext."""
    size_kb = len(text) / 1024
    return (
        f"Output indexed: {source} ({size_kb:.0f}KB -> {chunk_count} chunks). "
        f"Search with: context-hooks search-output <query>"
    )


# ── Source label helpers ─────────────────────────────────────────────────────

def make_source_label(tool_name: str, tool_input: dict) -> str:
    """Create a human-readable source label from tool call info."""
    if tool_name == "Bash":
        cmd = tool_input.get("command", "")
        # Truncate long commands
        if len(cmd) > 80:
            cmd = cmd[:77] + "..."
        return f"Bash:{cmd}"
    elif tool_name == "Read":
        path = tool_input.get("file_path", "")
        basename = os.path.basename(path) if path else "unknown"
        return f"Read:{basename}"
    elif tool_name == "Grep":
        pattern = tool_input.get("pattern", "")
        return f"Grep:{pattern[:60]}"
    elif tool_name == "Glob":
        pattern = tool_input.get("pattern", "")
        return f"Glob:{pattern[:60]}"
    else:
        return f"{tool_name}"


def get_output_text(tool_name: str, tool_response: dict) -> str | None:
    """Extract the indexable text from a tool response. Returns None if not indexable."""
    if tool_name == "Bash":
        return str(tool_response.get("output", ""))
    elif tool_name == "Read":
        return str(tool_response.get("output", ""))
    elif tool_name in ("Grep", "Glob"):
        return str(tool_response.get("output", ""))
    return None


# ── CLI entry point ──────────────────────────────────────────────────────────

def main():
    """CLI: context-hooks search-output <query> [--sources]"""
    import argparse
    from lib.db import ContextDB, data_dir, resolve_git_root

    parser = argparse.ArgumentParser(description="Search indexed tool outputs")
    parser.add_argument("query", nargs="?", default=None)
    parser.add_argument("--sources", action="store_true",
                        help="List indexed sources instead of searching")
    parser.add_argument("--session", default="unknown",
                        help="Session ID (auto-detected in hook mode)")
    args = parser.parse_args()

    git_root = resolve_git_root(os.getcwd())
    project_dir = data_dir(git_root)
    db = ContextDB(project_dir)

    try:
        if args.sources:
            sources = list_sources(db, args.session)
            if not sources:
                print("No indexed outputs for this session.")
                return
            print("Indexed sources:")
            for s in sources:
                print(f"  {s['source']} ({s['chunks']} chunks, {s['size_bytes']} bytes)")
            return

        if not args.query:
            print("Usage: search-output <query> [--sources]", file=sys.stderr)
            sys.exit(1)

        # For CLI, we don't track call count — no throttling
        result = search_output(db, args.session, args.query, call_count=1)
        if not result["results"]:
            print("No matches found.")
            return

        for r in result["results"]:
            print(f"\n--- {r['source']} (chunk {r['chunk_index']}) ---")
            print(r["snippet"])

    finally:
        db.close()


if __name__ == "__main__":
    main()
