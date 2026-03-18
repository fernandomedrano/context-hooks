"""Shared database layer for context-hooks. All writes use parameterized queries."""
import sqlite3
import hashlib
import os

CURRENT_SCHEMA_VERSION = 3

SCHEMA = """
CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL);

CREATE TABLE IF NOT EXISTS events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  session_id TEXT NOT NULL,
  timestamp TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%S','now','localtime')),
  category TEXT NOT NULL,
  event_type TEXT NOT NULL,
  priority INTEGER NOT NULL DEFAULT 3,
  data TEXT NOT NULL,
  project_dir TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS commits (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  session_id TEXT NOT NULL,
  timestamp TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%S','now','localtime')),
  commit_date TEXT,
  hash TEXT NOT NULL UNIQUE,
  short_hash TEXT NOT NULL,
  author TEXT,
  subject TEXT NOT NULL,
  body TEXT,
  files_changed TEXT,
  tags TEXT,
  project_dir TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS knowledge (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  category TEXT NOT NULL,
  maturity TEXT DEFAULT 'signal',
  title TEXT NOT NULL,
  content TEXT NOT NULL,
  reasoning TEXT,
  status TEXT DEFAULT 'active',
  superseded_by INTEGER,
  bug_refs TEXT,
  file_refs TEXT,
  commit_refs TEXT,
  tags TEXT,
  evidence_count INTEGER DEFAULT 0,
  last_validated TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  UNIQUE(title, status)
);

CREATE TABLE IF NOT EXISTS memos (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  from_agent TEXT NOT NULL,
  to_agent TEXT DEFAULT '*',
  subject TEXT NOT NULL,
  content TEXT NOT NULL,
  thread_id TEXT,
  created_at TEXT NOT NULL,
  read INTEGER DEFAULT 0,
  expires_at TEXT,
  priority TEXT DEFAULT 'normal'
);

CREATE TABLE IF NOT EXISTS rule_validations (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  rule_name TEXT NOT NULL,
  rule_hash TEXT NOT NULL UNIQUE,
  last_validated TEXT,
  match_count INTEGER DEFAULT 0,
  first_seen TEXT NOT NULL,
  status TEXT DEFAULT 'active'
);

CREATE TABLE IF NOT EXISTS shared_state (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL,
  updated_by TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS output_chunks (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  session_id TEXT NOT NULL,
  source TEXT NOT NULL,
  chunk_index INTEGER NOT NULL,
  content TEXT NOT NULL,
  created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%S','now','localtime'))
);

CREATE VIRTUAL TABLE IF NOT EXISTS knowledge_fts USING fts5(
  title, content, reasoning,
  content=knowledge, content_rowid=id
);

CREATE VIRTUAL TABLE IF NOT EXISTS output_chunks_fts USING fts5(
  source, content,
  content=output_chunks, content_rowid=id
);

CREATE INDEX IF NOT EXISTS idx_events_session ON events(session_id);
CREATE INDEX IF NOT EXISTS idx_events_category ON events(category);
CREATE INDEX IF NOT EXISTS idx_events_timestamp ON events(timestamp);
CREATE INDEX IF NOT EXISTS idx_commits_hash ON commits(hash);
CREATE INDEX IF NOT EXISTS idx_commits_short ON commits(short_hash);
CREATE INDEX IF NOT EXISTS idx_commits_tags ON commits(tags);
CREATE INDEX IF NOT EXISTS idx_commits_date ON commits(commit_date);
CREATE INDEX IF NOT EXISTS idx_knowledge_category ON knowledge(category);
CREATE INDEX IF NOT EXISTS idx_knowledge_maturity ON knowledge(maturity);
CREATE INDEX IF NOT EXISTS idx_knowledge_status ON knowledge(status);
CREATE INDEX IF NOT EXISTS idx_memos_agent_read ON memos(to_agent, read);
CREATE INDEX IF NOT EXISTS idx_rules_hash ON rule_validations(rule_hash);
CREATE INDEX IF NOT EXISTS idx_rules_status ON rule_validations(status);
CREATE INDEX IF NOT EXISTS idx_output_session ON output_chunks(session_id);
"""


def project_hash(path: str) -> str:
    """Deterministic 12-char hash of a path."""
    return hashlib.sha256(path.encode()).hexdigest()[:12]


def data_dir(git_root: str) -> str:
    """Return the project data directory, creating it if needed."""
    base = os.path.expanduser("~/.context-hooks/projects")
    d = os.path.join(base, project_hash(git_root))
    os.makedirs(d, exist_ok=True)
    # Ensure parent has correct permissions
    root_dir = os.path.expanduser("~/.context-hooks")
    try:
        os.chmod(root_dir, 0o700)
    except OSError:
        pass
    return d


def resolve_git_root(cwd: str) -> str:
    """Resolve git root from a working directory. Returns cwd if not a git repo."""
    import subprocess
    try:
        result = subprocess.run(
            ['git', '-C', cwd, 'rev-parse', '--show-toplevel'],
            capture_output=True, text=True, timeout=5
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass
    return cwd


def resolve_cluster_db(project_dir: str) -> str:
    """Return the cluster master's data dir, or project_dir if standalone.

    Args:
        project_dir: The local project's data directory (not git root).

    Returns:
        The master's data directory if clustered, or project_dir if standalone.
    """
    cluster_path = os.path.join(project_dir, "cluster.yaml")
    if os.path.exists(cluster_path):
        from lib.config import _parse_simple_yaml
        with open(cluster_path) as f:
            config = _parse_simple_yaml(f.read())
        master_root = config.get("master")
        if master_root and master_root.strip():
            master_dir = data_dir(master_root)
            master_db = os.path.join(master_dir, "context.db")
            if not os.path.exists(master_db):
                import sys
                print(f"WARNING: cluster master DB not found at {master_db}. "
                      f"Check master path in {cluster_path}. Falling back to standalone.",
                      file=sys.stderr)
                return project_dir
            return master_dir
    return project_dir


class ContextDB:
    """SQLite database wrapper. All writes use parameterized queries."""

    def __init__(self, project_dir: str):
        """Initialize DB in the given directory."""
        db_path = os.path.join(project_dir, "context.db")
        self.conn = sqlite3.connect(db_path)
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.executescript(SCHEMA)
        self.conn.commit()
        self._migrate()
        self.db_path = db_path

    def _migrate(self):
        """Run schema migrations based on version tracking."""
        rows = self.conn.execute("SELECT version FROM schema_version").fetchall()
        if not rows:
            # Detect if this is a pre-versioned DB (has tables but no version row)
            tables = [r[0] for r in self.conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()]
            if 'memos' in tables:
                # Existing DB without versioning — start at v1
                version = 1
            else:
                # Brand new DB — apply full schema, set to current version
                version = 0
            self.conn.execute("INSERT INTO schema_version (version) VALUES (?)", (version,))
            self.conn.commit()
        else:
            version = rows[0][0]

        if version < 2:
            self._migrate_v1_to_v2()
        if version < 3:
            self._migrate_v2_to_v3()
        if version < CURRENT_SCHEMA_VERSION:
            self.conn.execute("UPDATE schema_version SET version = ?", (CURRENT_SCHEMA_VERSION,))
            self.conn.commit()

    def _migrate_v1_to_v2(self):
        """v1 → v2: add priority column to memos, add shared_state table."""
        # Check if priority column already exists (idempotent)
        cols = [r[1] for r in self.conn.execute("PRAGMA table_info(memos)").fetchall()]
        if 'priority' not in cols:
            self.conn.execute("ALTER TABLE memos ADD COLUMN priority TEXT DEFAULT 'normal'")
        # shared_state is handled by CREATE TABLE IF NOT EXISTS in SCHEMA
        self.conn.commit()

    def _migrate_v2_to_v3(self):
        """v2 → v3: add output_chunks table + FTS5."""
        # output_chunks and output_chunks_fts are handled by CREATE TABLE/VIRTUAL TABLE
        # IF NOT EXISTS in SCHEMA. This method exists for future v3-specific migrations.
        self.conn.commit()

    def query(self, sql: str, params: tuple = ()) -> list:
        """Execute a read query and return all rows."""
        return self.conn.execute(sql, params).fetchall()

    def execute(self, sql: str, params: tuple = ()) -> None:
        """Execute a write query with parameterized values."""
        self.conn.execute(sql, params)
        self.conn.commit()

    def executemany(self, sql: str, param_list: list) -> None:
        """Execute a write query for many rows."""
        self.conn.executemany(sql, param_list)
        self.conn.commit()

    def insert_event(self, *, session_id, category, event_type, priority, data, project_dir):
        self.execute(
            "INSERT INTO events (session_id, category, event_type, priority, data, project_dir) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (session_id, category, event_type, priority, data, project_dir)
        )

    def insert_commit(self, *, session_id, commit_date, hash, short_hash, author,
                       subject, body, files_changed, tags, project_dir):
        self.execute(
            "INSERT OR IGNORE INTO commits "
            "(session_id, commit_date, hash, short_hash, author, subject, body, files_changed, tags, project_dir) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (session_id, commit_date, hash, short_hash, author, subject, body, files_changed, tags, project_dir)
        )

    def insert_knowledge(self, *, category, title, content, reasoning=None, maturity='decision',
                          bug_refs=None, file_refs=None, commit_refs=None, tags=None):
        from datetime import datetime
        now = datetime.now().isoformat()
        self.execute(
            "INSERT INTO knowledge "
            "(category, maturity, title, content, reasoning, bug_refs, file_refs, commit_refs, tags, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (category, maturity, title, content, reasoning, bug_refs, file_refs, commit_refs, tags, now, now)
        )
        # Update FTS index
        row = self.query("SELECT id FROM knowledge WHERE title = ? AND status = 'active'", (title,))
        if row:
            self.execute(
                "INSERT INTO knowledge_fts(rowid, title, content, reasoning) VALUES (?, ?, ?, ?)",
                (row[0][0], title, content, reasoning or '')
            )

    def insert_memo(self, *, from_agent, subject, content, to_agent='*',
                    thread_id=None, expires_at=None, priority='normal'):
        from datetime import datetime
        self.execute(
            "INSERT INTO memos (from_agent, to_agent, subject, content, thread_id, created_at, expires_at, priority) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (from_agent, to_agent, subject, content, thread_id, datetime.now().isoformat(), expires_at, priority)
        )

    def evict_events(self, session_id: str, max_events: int = 500):
        """FIFO eviction: keep only the most recent max_events for a session."""
        self.execute(
            "DELETE FROM events WHERE session_id = ? AND id NOT IN "
            "(SELECT id FROM events WHERE session_id = ? ORDER BY id DESC LIMIT ?)",
            (session_id, session_id, max_events)
        )

    def upsert_shared_state(self, *, key, value, updated_by):
        """Set or update a shared state key."""
        from datetime import datetime
        now = datetime.now().isoformat()
        self.execute(
            "INSERT INTO shared_state (key, value, updated_by, updated_at) "
            "VALUES (?, ?, ?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value=?, updated_by=?, updated_at=?",
            (key, value, updated_by, now, value, updated_by, now)
        )

    def get_shared_state(self, key=None):
        """Get one key or all shared state. Returns list of tuples."""
        if key:
            return self.query(
                "SELECT key, value, updated_by, updated_at FROM shared_state WHERE key = ?",
                (key,)
            )
        return self.query("SELECT key, value, updated_by, updated_at FROM shared_state ORDER BY key")

    def delete_shared_state(self, key):
        """Remove a shared state key."""
        self.execute("DELETE FROM shared_state WHERE key = ?", (key,))

    def close(self):
        self.conn.close()
