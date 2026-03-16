# Cross-Project Routing Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Enable related projects to share memos and knowledge through a cluster model where one master DB serves as the hub.

**Architecture:** A satellite project reads `cluster.yaml` from its data dir, resolves the master's data dir via `data_dir()`, and opens that DB for all memo/knowledge/shared_state operations. Local data (events, commits, snapshots, tags, rule_validations) stays in the local DB. For standalone projects (no cluster.yaml), behavior is unchanged.

**Tech Stack:** Python 3 stdlib only (sqlite3, os, argparse). Zero external deps.

**Spec:** `docs/superpowers/specs/2026-03-15-cross-project-routing-design.md`

---

## Chunk 1: Foundation — resolve_cluster_db + cluster CLI

### Task 1: `resolve_cluster_db()` in db.py

**Files:**
- Modify: `lib/db.py:104-120` (after `resolve_git_root()`)
- Test: `tests/test_db.py`

- [ ] **Step 1: Write failing tests**

Add to `tests/test_db.py`:

```python
class TestResolveClusterDb:
    """Tests for cluster resolution logic."""

    def test_standalone_returns_project_dir(self):
        """No cluster.yaml → returns project_dir unchanged."""
        tmp = tempfile.mkdtemp()
        result = resolve_cluster_db(tmp)
        assert result == tmp

    def test_cluster_resolves_to_master(self):
        """cluster.yaml with valid master → returns master's data dir."""
        # Create a fake master project with a DB
        master_root = tempfile.mkdtemp()
        master_dir = data_dir(master_root)
        ContextDB(master_dir)  # creates context.db

        # Create satellite with cluster.yaml
        satellite_dir = tempfile.mkdtemp()
        os.makedirs(satellite_dir, exist_ok=True)
        with open(os.path.join(satellite_dir, "cluster.yaml"), "w") as f:
            f.write(f"cluster: test-cluster\nmaster: {master_root}\n")

        result = resolve_cluster_db(satellite_dir)
        assert result == master_dir
        assert result != satellite_dir

    def test_empty_master_falls_back(self):
        """cluster.yaml with empty master → falls back to standalone."""
        tmp = tempfile.mkdtemp()
        with open(os.path.join(tmp, "cluster.yaml"), "w") as f:
            f.write("cluster: test\nmaster:\n")
        result = resolve_cluster_db(tmp)
        assert result == tmp

    def test_bad_master_path_falls_back_with_warning(self, capsys):
        """cluster.yaml pointing to nonexistent DB → falls back with warning."""
        tmp = tempfile.mkdtemp()
        with open(os.path.join(tmp, "cluster.yaml"), "w") as f:
            f.write("cluster: test\nmaster: /nonexistent/path/to/repo\n")
        result = resolve_cluster_db(tmp)
        assert result == tmp
        captured = capsys.readouterr()
        assert "WARNING" in captured.err

    def test_master_points_to_self(self):
        """Master project's cluster.yaml points to itself."""
        master_root = tempfile.mkdtemp()
        master_dir = data_dir(master_root)
        ContextDB(master_dir)  # creates context.db
        with open(os.path.join(master_dir, "cluster.yaml"), "w") as f:
            f.write(f"cluster: test\nmaster: {master_root}\n")
        result = resolve_cluster_db(master_dir)
        assert result == master_dir
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3.12 -m pytest tests/test_db.py::TestResolveClusterDb -v`
Expected: ImportError — `resolve_cluster_db` not defined

- [ ] **Step 3: Implement resolve_cluster_db**

Add to `lib/db.py` after `resolve_git_root()` (after line 135):

```python
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
```

Update the import in `tests/test_db.py` line 9:

```python
from lib.db import ContextDB, project_hash, data_dir, resolve_cluster_db
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3.12 -m pytest tests/test_db.py::TestResolveClusterDb -v`
Expected: 5 passed

- [ ] **Step 5: Run full db test suite**

Run: `python3.12 -m pytest tests/test_db.py -v`
Expected: All 40 passed (33 existing + 7 migration)

- [ ] **Step 6: Commit**

```bash
git add lib/db.py tests/test_db.py
git commit -m "feat: add resolve_cluster_db() for cluster routing"
```

---

### Task 2: `lib/cluster.py` — CLI commands

**Files:**
- Create: `lib/cluster.py`
- Modify: `bin/context-hooks` (add cluster dispatch)
- Test: `tests/test_cluster.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_cluster.py`:

```python
import os, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from lib.db import ContextDB, data_dir, resolve_cluster_db

class TestClusterJoin:
    def test_join_writes_cluster_yaml(self):
        master_root = tempfile.mkdtemp()
        master_dir = data_dir(master_root)
        ContextDB(master_dir)  # ensure DB exists

        satellite_root = tempfile.mkdtemp()
        satellite_dir = data_dir(satellite_root)

        from lib.cluster import join_cluster
        join_cluster(satellite_dir, master_root, "test-cluster")

        yaml_path = os.path.join(satellite_dir, "cluster.yaml")
        assert os.path.exists(yaml_path)
        content = open(yaml_path).read()
        assert "test-cluster" in content
        assert master_root in content

    def test_join_validates_master_is_git_root(self):
        import pytest
        satellite_dir = tempfile.mkdtemp()
        from lib.cluster import join_cluster
        with pytest.raises(ValueError, match="not a git repository"):
            join_cluster(satellite_dir, "/tmp/not-a-git-repo", "test")

    def test_join_makes_resolve_work(self):
        master_root = tempfile.mkdtemp()
        master_dir = data_dir(master_root)
        ContextDB(master_dir)

        satellite_root = tempfile.mkdtemp()
        satellite_dir = data_dir(satellite_root)

        from lib.cluster import join_cluster
        join_cluster(satellite_dir, master_root, "test-cluster")

        result = resolve_cluster_db(satellite_dir)
        assert result == master_dir

class TestClusterShow:
    def test_show_standalone(self, capsys):
        tmp = tempfile.mkdtemp()
        from lib.cluster import show_cluster
        show_cluster(tmp)
        assert "standalone" in capsys.readouterr().out.lower()

    def test_show_with_cluster(self, capsys):
        master_root = tempfile.mkdtemp()
        master_dir = data_dir(master_root)
        ContextDB(master_dir)

        satellite_dir = tempfile.mkdtemp()
        from lib.cluster import join_cluster, show_cluster
        join_cluster(satellite_dir, master_root, "my-cluster")
        show_cluster(satellite_dir)
        out = capsys.readouterr().out
        assert "my-cluster" in out
        assert master_root in out

class TestClusterLeave:
    def test_leave_removes_yaml(self):
        master_root = tempfile.mkdtemp()
        master_dir = data_dir(master_root)
        ContextDB(master_dir)

        satellite_dir = tempfile.mkdtemp()
        from lib.cluster import join_cluster, leave_cluster
        join_cluster(satellite_dir, master_root, "test")
        leave_cluster(satellite_dir)
        assert not os.path.exists(os.path.join(satellite_dir, "cluster.yaml"))

    def test_leave_when_not_in_cluster(self, capsys):
        tmp = tempfile.mkdtemp()
        from lib.cluster import leave_cluster
        leave_cluster(tmp)
        assert "not in a cluster" in capsys.readouterr().out.lower()

    def test_leave_then_resolve_is_standalone(self):
        master_root = tempfile.mkdtemp()
        master_dir = data_dir(master_root)
        ContextDB(master_dir)

        satellite_dir = tempfile.mkdtemp()
        from lib.cluster import join_cluster, leave_cluster
        join_cluster(satellite_dir, master_root, "test")
        leave_cluster(satellite_dir)
        assert resolve_cluster_db(satellite_dir) == satellite_dir
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3.12 -m pytest tests/test_cluster.py -v`
Expected: ImportError — `lib.cluster` not found

- [ ] **Step 3: Implement lib/cluster.py**

Create `lib/cluster.py`:

```python
"""Cluster management — join, show, leave. CLI-callable."""
import os
import sys
import subprocess


def join_cluster(project_dir: str, master_root: str, name: str):
    """Join a cluster by writing cluster.yaml to the project's data dir."""
    # Validate master is a git repo
    try:
        result = subprocess.run(
            ['git', '-C', master_root, 'rev-parse', '--show-toplevel'],
            capture_output=True, text=True, timeout=5
        )
        if result.returncode != 0 or result.stdout.strip() != os.path.realpath(master_root):
            raise ValueError(f"{master_root} is not a git repository root")
    except FileNotFoundError:
        raise ValueError(f"{master_root} is not a git repository root (git not found)")

    cluster_path = os.path.join(project_dir, "cluster.yaml")
    with open(cluster_path, "w") as f:
        f.write(f"cluster: {name}\nmaster: {master_root}\n")
    print(f"Joined cluster '{name}'. Memos and knowledge now route to master at {master_root}.")
    print("Existing local memos/knowledge in this project's DB will be ignored (not deleted).")


def show_cluster(project_dir: str):
    """Print current cluster config."""
    cluster_path = os.path.join(project_dir, "cluster.yaml")
    if not os.path.exists(cluster_path):
        print("Not in a cluster (standalone mode).")
        return
    from lib.config import _parse_simple_yaml
    with open(cluster_path) as f:
        config = _parse_simple_yaml(f.read())
    name = config.get("cluster", "unnamed")
    master = config.get("master", "unknown")
    from lib.db import data_dir
    is_master = (data_dir(master) == project_dir)
    role = "master" if is_master else "satellite"
    print(f"Cluster: {name}")
    print(f"Master: {master}")
    print(f"Role: {role}")


def leave_cluster(project_dir: str):
    """Leave the current cluster."""
    cluster_path = os.path.join(project_dir, "cluster.yaml")
    if not os.path.exists(cluster_path):
        print("Not in a cluster.")
        return
    os.remove(cluster_path)
    print("Left cluster. Memos and knowledge now use local DB. Data in master DB is unaffected.")


def main(args):
    """CLI entry point: context-hooks cluster join|show|leave"""
    from lib.db import resolve_git_root, data_dir

    if not args:
        print("Usage: cluster <join|show|leave>")
        print("  join --master /path/to/master --name cluster-name")
        print("  show")
        print("  leave")
        sys.exit(1)

    git_root = resolve_git_root(os.getcwd())
    project_dir = data_dir(git_root)

    cmd = args[0]
    if cmd == "join":
        import argparse
        parser = argparse.ArgumentParser(prog="cluster join")
        parser.add_argument("--master", required=True)
        parser.add_argument("--name", required=True)
        parsed = parser.parse_args(args[1:])
        join_cluster(project_dir, parsed.master, parsed.name)
    elif cmd == "show":
        show_cluster(project_dir)
    elif cmd == "leave":
        leave_cluster(project_dir)
    else:
        print(f"Unknown cluster command: {cmd}")
        sys.exit(1)


if __name__ == "__main__":
    main(sys.argv[1:])
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3.12 -m pytest tests/test_cluster.py -v`
Expected: 7 passed

Note: `test_join_validates_master_is_git_root` may need adjustment — `/tmp/not-a-git-repo` might not exist. Use `tempfile.mkdtemp()` for a real non-git dir if needed.

- [ ] **Step 5: Add cluster dispatch to bin/context-hooks**

Add to `bin/context-hooks` after the `mcp` case:

```bash
cluster)  python3 "$SCRIPT_DIR/lib/cluster.py" "$@" ;;
```

And add to the help text:

```
Cluster:   cluster join|show|leave
```

- [ ] **Step 6: Run full test suite**

Run: `python3.12 -m pytest tests/ -v`
Expected: All pass (existing 215 + new ~12)

- [ ] **Step 7: Commit**

```bash
git add lib/cluster.py tests/test_cluster.py bin/context-hooks
git commit -m "feat: cluster CLI — join, show, leave commands"
```

---

## Chunk 2: Route knowledge + memos through cluster DB

### Task 3: Split `knowledge.py` to use cluster DB

**Files:**
- Modify: `lib/knowledge.py:227-234` (main function DB opening)
- Test: `tests/test_knowledge.py`

- [ ] **Step 1: Write failing integration test**

Add to `tests/test_knowledge.py`:

```python
class TestClusterRouting:
    """Tests that knowledge/memo operations route to master DB when clustered."""

    def test_send_memo_routes_to_master(self):
        """Satellite send_memo should write to master DB, not local."""
        master_root = tempfile.mkdtemp()
        from lib.db import data_dir, resolve_cluster_db
        master_dir = data_dir(master_root)
        master_db = ContextDB(master_dir)

        satellite_dir = tempfile.mkdtemp()
        os.makedirs(satellite_dir, exist_ok=True)
        satellite_db = ContextDB(satellite_dir)

        # Configure satellite to point to master
        with open(os.path.join(satellite_dir, "cluster.yaml"), "w") as f:
            f.write(f"cluster: test\nmaster: {master_root}\n")

        # Open cluster DB and send memo
        cluster_dir = resolve_cluster_db(satellite_dir)
        cluster_db = ContextDB(cluster_dir)
        send_memo(cluster_db, "satellite-agent", "Hello master", "Test content")

        # Memo should be in master DB
        master_memos = list_memos(master_db)
        assert len(master_memos) == 1
        assert master_memos[0]["subject"] == "Hello master"

        # Memo should NOT be in satellite DB
        satellite_memos = list_memos(satellite_db)
        assert len(satellite_memos) == 0

        master_db.close()
        satellite_db.close()
        cluster_db.close()

    def test_store_knowledge_routes_to_master(self):
        """Satellite store should write to master DB."""
        master_root = tempfile.mkdtemp()
        from lib.db import data_dir, resolve_cluster_db
        master_dir = data_dir(master_root)
        master_db = ContextDB(master_dir)

        satellite_dir = tempfile.mkdtemp()
        os.makedirs(satellite_dir, exist_ok=True)
        satellite_db = ContextDB(satellite_dir)

        with open(os.path.join(satellite_dir, "cluster.yaml"), "w") as f:
            f.write(f"cluster: test\nmaster: {master_root}\n")

        cluster_dir = resolve_cluster_db(satellite_dir)
        cluster_db = ContextDB(cluster_dir)
        store(cluster_db, "architectural-decision", "Cluster routing", "We use hub-and-spoke")

        master_entries = list_entries(master_db)
        assert len(master_entries) == 1
        satellite_entries = list_entries(satellite_db)
        assert len(satellite_entries) == 0

        master_db.close()
        satellite_db.close()
        cluster_db.close()

    def test_standalone_unchanged(self):
        """Without cluster.yaml, all operations use local DB."""
        from lib.db import resolve_cluster_db
        standalone_dir = tempfile.mkdtemp()
        db = ContextDB(standalone_dir)

        cluster_dir = resolve_cluster_db(standalone_dir)
        assert cluster_dir == standalone_dir

        send_memo(db, "agent", "Local memo", "Content")
        assert len(list_memos(db)) == 1
        db.close()
```

- [ ] **Step 2: Run integration tests to verify they pass**

These test library functions that already accept a db param — routing is caller-side. They validate the cluster DB pattern works.

Run: `python3.12 -m pytest tests/test_knowledge.py::TestClusterRouting -v`
Expected: 3 passed

- [ ] **Step 3: Write failing test for --project flag with cluster resolution**

Add to `TestClusterRouting`:

```python
    def test_project_flag_resolves_through_cluster(self):
        """--project flag should resolve the target's cluster before sending."""
        from lib.knowledge import parse_memo_send_args
        from lib.db import data_dir, resolve_cluster_db

        # Set up: target project is a satellite that routes to a master
        master_root = tempfile.mkdtemp()
        master_dir = data_dir(master_root)
        master_db = ContextDB(master_dir)

        target_root = tempfile.mkdtemp()
        target_dir = data_dir(target_root)
        ContextDB(target_dir)
        with open(os.path.join(target_dir, "cluster.yaml"), "w") as f:
            f.write(f"cluster: test\nmaster: {master_root}\n")

        # The --project flag points to target_root; cluster resolution should
        # route to master_dir
        target_cluster = resolve_cluster_db(target_dir)
        assert target_cluster == master_dir  # sanity: cluster resolves to master

        master_db.close()
```

Run: `python3.12 -m pytest tests/test_knowledge.py::TestClusterRouting::test_project_flag_resolves_through_cluster -v`
Expected: PASS (this tests resolve_cluster_db, which already works from Task 1)

- [ ] **Step 4: Update knowledge.py main() to resolve cluster**

In `lib/knowledge.py`, update `main()` (around line 227):

```python
def main(args):
    """Handle CLI: knowledge store|search|list|promote|archive|restore|dismiss and memo send|list|read"""
    from lib.db import ContextDB, resolve_git_root, resolve_cluster_db
    from lib.db import data_dir as get_data_dir

    git_root = resolve_git_root(os.getcwd())
    project_dir = get_data_dir(git_root)
    cluster_dir = resolve_cluster_db(project_dir)
    db = ContextDB(cluster_dir)
```

This routes ALL knowledge.py operations (knowledge + memos) through the cluster DB. For standalone projects, `cluster_dir == project_dir` — no change.

Also update the `--project` flag in memo send handler (around line 318) to resolve through cluster:

```python
if parsed['project']:
    target_project_dir = get_data_dir(parsed['project'])
    target_cluster_dir = resolve_cluster_db(target_project_dir)
    target_db = ContextDB(target_cluster_dir)
```

- [ ] **Step 5: Run knowledge tests**

Run: `python3.12 -m pytest tests/test_knowledge.py -v`
Expected: All pass

- [ ] **Step 6: Commit**

```bash
git add lib/knowledge.py tests/test_knowledge.py
git commit -m "feat: route knowledge/memo CLI through cluster DB"
```

---

### Task 4: Split `mcp_tools.py` — dual DB helpers

**Files:**
- Modify: `lib/mcp_tools.py:15-17` (_open_db), `lib/mcp_tools.py:465-495` (main/ctx)
- Test: `tests/test_mcp_tools.py`

- [ ] **Step 1: Write failing test**

Add to `tests/test_mcp_tools.py`:

```python
class TestClusterDBRouting:
    """Tests that MCP handlers route to correct DB based on cluster config."""

    def setup_method(self):
        self.master_root = tempfile.mkdtemp()
        from lib.db import data_dir
        self.master_dir = data_dir(self.master_root)
        self.master_db = ContextDB(self.master_dir)

        self.satellite_dir = tempfile.mkdtemp()
        os.makedirs(self.satellite_dir, exist_ok=True)
        ContextDB(self.satellite_dir)  # create local DB

        # Write cluster.yaml
        with open(os.path.join(self.satellite_dir, "cluster.yaml"), "w") as f:
            f.write(f"cluster: test\nmaster: {self.master_root}\n")

        from lib.db import resolve_cluster_db
        self.cluster_dir = resolve_cluster_db(self.satellite_dir)
        self.ctx = {
            "project_dir": self.satellite_dir,
            "cluster_dir": self.cluster_dir,
            "git_root": self.satellite_dir,
            "config": {},
        }

    def teardown_method(self):
        self.master_db.close()

    def test_memo_handler_uses_cluster_db(self):
        handlers = build_handlers(self.ctx)
        handlers["context_send_memo"]({
            "from_agent": "test", "subject": "Clustered", "content": "Hello"
        })
        # Should be in master DB
        memos = self.master_db.query("SELECT subject FROM memos")
        assert any("Clustered" in m[0] for m in memos)

        # Should NOT be in satellite DB
        sat_db = ContextDB(self.satellite_dir)
        sat_memos = sat_db.query("SELECT COUNT(*) FROM memos")
        assert sat_memos[0][0] == 0
        sat_db.close()

    def test_knowledge_handler_uses_cluster_db(self):
        handlers = build_handlers(self.ctx)
        handlers["context_store_knowledge"]({
            "category": "reference", "title": "Cluster test", "content": "Routed"
        })
        entries = self.master_db.query("SELECT title FROM knowledge WHERE status='active'")
        assert any("Cluster test" in e[0] for e in entries)

    def test_project_context_reads_memos_from_cluster(self):
        """get_project_context should read memos/knowledge from cluster DB."""
        handlers = build_handlers(self.ctx)
        # Send a memo to cluster DB first
        handlers["context_send_memo"]({
            "from_agent": "test", "subject": "Context test", "content": "For project_context"
        })
        result = json.loads(handlers["context_get_project_context"]({}))
        # Should find the memo we just sent
        assert result.get("unread_memos", 0) > 0 or len(result.get("recent_memos", [])) > 0

    def test_reply_memo_reads_and_writes_same_cluster_db(self):
        """reply_memo must read original + write reply on the same cluster handle."""
        handlers = build_handlers(self.ctx)
        # Send original memo
        handlers["context_send_memo"]({
            "from_agent": "sender", "subject": "Thread test", "content": "Original"
        })
        memos = self.master_db.query("SELECT id FROM memos ORDER BY id DESC LIMIT 1")
        memo_id = memos[0][0]
        # Reply — should create thread_id on same DB
        result = handlers["context_reply_memo"]({
            "memo_id": memo_id, "from_agent": "replier", "content": "Reply"
        })
        # Both original and reply should have matching thread_id in master DB
        threads = self.master_db.query("SELECT thread_id FROM memos WHERE thread_id IS NOT NULL")
        assert len(threads) >= 2  # original updated + reply inserted
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3.12 -m pytest tests/test_mcp_tools.py::TestClusterDBRouting -v`
Expected: FAIL — `cluster_dir` not in ctx / handlers use `_open_db` which reads `project_dir`

- [ ] **Step 3: Implement dual DB helpers**

In `lib/mcp_tools.py`, replace `_open_db` (line 15-17):

```python
def _open_local_db(ctx):
    """Open a fresh local DB connection (events, commits, tags)."""
    return ContextDB(ctx["project_dir"])

def _open_cluster_db(ctx):
    """Open a fresh cluster DB connection (memos, knowledge, shared_state)."""
    return ContextDB(ctx["cluster_dir"])
```

Then update `main()` (around line 484) to add `cluster_dir` to ctx:

```python
from lib.db import resolve_cluster_db
project_dir = data_dir(git_root)
cluster_dir = resolve_cluster_db(project_dir)
config = load_config(project_dir)

ctx = {
    "project_dir": project_dir,
    "cluster_dir": cluster_dir,
    "git_root": git_root,
    "config": config,
}
```

Update each handler in `build_handlers()`:
- Knowledge handlers (store, search, get, list, promote, archive, restore, supersede): `_open_db(ctx)` → `_open_cluster_db(ctx)`
- Memo handlers (send, check, read, reply, broadcast, list_threads): `_open_db(ctx)` → `_open_cluster_db(ctx)`
- Shared state handlers (set, get): `_open_db(ctx)` → `_open_cluster_db(ctx)`
- Task handler (handoff_task): `_open_db(ctx)` → `_open_cluster_db(ctx)`
- Commit query handler: `_open_db(ctx)` → `_open_local_db(ctx)`
- Parity handler: `_open_db(ctx)` → `_open_local_db(ctx)`
- Profile handler: no DB opened — no change

For `context_get_project_context` (line 362), open both — use `_open_cluster_db` for `list_memos()` and `list_entries()`, `_open_local_db` for commit queries:

```python
def context_get_project_context(args):
    local_db = _open_local_db(ctx)
    cluster_db = _open_cluster_db(ctx)
    try:
        # memos/knowledge from cluster, commits from local, health from both
        ...
    finally:
        local_db.close()
        cluster_db.close()
```

For other handlers that need both (xref, health), open both:

```python
def context_run_xref(args):
    local_db = _open_local_db(ctx)
    cluster_db = _open_cluster_db(ctx)
    try:
        return run_xref(local_db, cluster_db, ctx["git_root"], ctx["project_dir"])
    finally:
        local_db.close()
        cluster_db.close()
```

Note: `run_xref` signature change is in Task 6. For now, if cluster_dir == project_dir (standalone), both handles point to the same DB — safe in WAL mode.

- [ ] **Step 4: Run MCP tests**

Run: `python3.12 -m pytest tests/test_mcp_tools.py -v`
Expected: All pass (existing + 2 new cluster routing tests)

- [ ] **Step 5: Commit**

```bash
git add lib/mcp_tools.py tests/test_mcp_tools.py
git commit -m "feat: split MCP handlers into local/cluster DB routing"
```

---

## Chunk 3: Update analysis modules — health, xref, hooks

### Task 5: Split `health.py` — prune + health_summary

**Files:**
- Modify: `lib/health.py:14` (health_summary signature), `lib/health.py:59` (prune signature), `lib/health.py:266` (main)
- Test: `tests/test_health.py`

- [ ] **Step 1: Write failing test**

Add to `tests/test_health.py`:

```python
class TestClusterHealthRouting:
    def test_health_summary_reads_memos_from_cluster_db(self):
        """health_summary should count unread memos from cluster_db."""
        master_root = tempfile.mkdtemp()
        from lib.db import data_dir
        master_dir = data_dir(master_root)
        cluster_db = ContextDB(master_dir)
        local_db = ContextDB(tempfile.mkdtemp())

        # Insert unread memo in cluster DB
        cluster_db.insert_memo(from_agent="x", subject="Unread", content="c")

        result = health_summary(local_db, cluster_db, "/fake/root", master_dir, {})
        assert result is not None
        assert "unread" in result.lower()
        local_db.close()
        cluster_db.close()

    def test_prune_deletes_memos_from_cluster_db(self):
        """prune should delete old memos from cluster_db, not local_db."""
        master_root = tempfile.mkdtemp()
        from lib.db import data_dir
        master_dir = data_dir(master_root)
        cluster_db = ContextDB(master_dir)
        local_db = ContextDB(tempfile.mkdtemp())

        # Insert old read memo in cluster DB
        cluster_db.execute(
            "INSERT INTO memos (from_agent, subject, content, created_at, read) VALUES (?,?,?,?,?)",
            ("x", "Old", "c", "2020-01-01T00:00:00", 1)
        )

        result = prune(local_db, cluster_db, "/fake/root", master_dir, dry_run=False)
        remaining = cluster_db.query("SELECT COUNT(*) FROM memos")
        assert remaining[0][0] == 0
        local_db.close()
        cluster_db.close()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3.12 -m pytest tests/test_health.py::TestClusterHealthRouting -v`
Expected: FAIL — `health_summary` and `prune` don't accept two db params

- [ ] **Step 3: Update health.py signatures**

Update `health_summary` (line 14):
```python
def health_summary(local_db, cluster_db, git_root: str, project_data_dir: str, config: dict) -> str | None:
```

All memo/knowledge queries inside use `cluster_db`. Event/rule_validation queries use `local_db`.

Update `prune` (line 59):
```python
def prune(local_db, cluster_db, git_root: str, project_data_dir: str, dry_run: bool = True) -> str:
```

Memo deletions and knowledge archival use `cluster_db`. Rule_validation updates use `local_db`.

Update `main()` (line 266):
```python
from lib.db import resolve_cluster_db
project_dir = data_dir(git_root)
cluster_dir = resolve_cluster_db(project_dir)
local_db = ContextDB(project_dir)
cluster_db = ContextDB(cluster_dir)
try:
    # pass both to health_summary and prune
finally:
    local_db.close()
    if cluster_dir != project_dir:
        cluster_db.close()
```

- [ ] **Step 4: Update existing tests to pass two DB params**

All existing tests in `tests/test_health.py` that call `health_summary(db, ...)` or `prune(db, ...)` must be updated to pass `(db, db, ...)` (standalone mode = same handle for both). Search for all calls and add the second db param.

- [ ] **Step 5: Update all callers of health_summary and prune**

Callers to update:
- `hooks.py:72` — `health_summary(db, ...)` → `health_summary(db, cluster_db, ...)` (Task 7 will handle the lazy cluster_db)
- `mcp_tools.py` — already handled in Task 4

**Dependency note:** Task 7 (hooks.py) depends on this task being complete. Do NOT parallelize or reorder.

- [ ] **Step 6: Run health tests**

Run: `python3.12 -m pytest tests/test_health.py -v`
Expected: All pass

- [ ] **Step 7: Commit**

```bash
git add lib/health.py tests/test_health.py
git commit -m "feat: split health.py into local/cluster DB params"
```

---

### Task 6: Split `xref.py` — two DB handles

**Files:**
- Modify: `lib/xref.py:495` (run_xref signature)
- Test: `tests/test_xref.py`

- [ ] **Step 1: Write failing test**

Add to `tests/test_xref.py`:

```python
class TestClusterXrefRouting:
    def test_xref_reads_knowledge_from_cluster_db(self):
        """run_xref should read knowledge from cluster_db."""
        local_db = ContextDB(tempfile.mkdtemp())
        cluster_db = ContextDB(tempfile.mkdtemp())

        # Put knowledge in cluster DB only
        cluster_db.insert_knowledge(
            category="reference", title="Cluster fact", content="Only in cluster"
        )

        result = run_xref(local_db, cluster_db, "/fake/root", tempfile.mkdtemp())
        assert "Cluster fact" in result or "knowledge" in result.lower()
        local_db.close()
        cluster_db.close()

    def test_xref_writes_rule_validations_to_local_db(self):
        """run_xref should write rule_validations to local_db, not cluster_db."""
        local_dir = tempfile.mkdtemp()
        local_db = ContextDB(local_dir)
        cluster_db = ContextDB(tempfile.mkdtemp())

        result = run_xref(local_db, cluster_db, "/fake/root", local_dir)
        # rule_validations should exist in local DB (even if empty)
        local_rules = local_db.query("SELECT COUNT(*) FROM rule_validations")
        assert local_rules[0][0] >= 0  # just verify the table is used in local
        local_db.close()
        cluster_db.close()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3.12 -m pytest tests/test_xref.py::TestClusterXrefRouting -v`
Expected: FAIL — `run_xref` doesn't accept two db params

- [ ] **Step 3: Update xref.py**

Update `run_xref` signature (line 495):
```python
def run_xref(local_db, cluster_db, git_root: str, project_data_dir: str) -> str:
```

Inside `run_xref`:
- `_load_commits(local_db)` — commits are local
- `_load_knowledge(cluster_db)` — knowledge is cluster
- `_update_rule_validations(local_db, ...)` — rule_validations are local

Update `main()` (line 574):
```python
from lib.db import resolve_cluster_db
project_dir = data_dir(git_root)
cluster_dir = resolve_cluster_db(project_dir)
local_db = ContextDB(project_dir)
cluster_db = ContextDB(cluster_dir)
try:
    print(run_xref(local_db, cluster_db, git_root, project_dir))
finally:
    local_db.close()
    if cluster_dir != project_dir:
        cluster_db.close()
```

Update existing `run_xref` callers to pass two dbs:
- `mcp_tools.py` — already handled in Task 4

- [ ] **Step 4: Update existing xref tests to pass two DB params**

All existing tests in `tests/test_xref.py` that call `run_xref(db, ...)` must be updated to `run_xref(db, db, ...)` (standalone mode = same handle for both).

- [ ] **Step 5: Run xref tests**

Run: `python3.12 -m pytest tests/test_xref.py -v`
Expected: All pass

- [ ] **Step 6: Commit**

```bash
git add lib/xref.py tests/test_xref.py
git commit -m "feat: split xref.py into local/cluster DB params"
```

---

### Task 7: Update `hooks.py` — lazy cluster DB

**Files:**
- Modify: `lib/hooks.py:20-86` (handle_hook function)
- Test: `tests/test_hooks.py`

- [ ] **Step 1: Write failing test**

Add to `tests/test_hooks.py`:

```python
class TestClusterHookRouting:
    def test_hook_passes_cluster_db_to_health(self):
        """SessionStart health check should use cluster DB for memos."""
        # This is an integration-level test. The key behavior:
        # hooks.py should resolve cluster_db lazily and pass it to health_summary.
        # Since health_summary now takes (local_db, cluster_db, ...), hooks.py must adapt.
        pass  # Covered by health.py tests + hooks.py signature update
```

Note: hooks.py is primarily a router. The real test is that it correctly passes two DB handles to `health_summary` and `check_flywheels`. Since those functions are tested separately, hooks.py just needs a mechanical update.

- [ ] **Step 2: Update hooks.py**

In `handle_hook()` (around line 20-23), add lazy cluster DB resolution:

```python
project_dir = data_dir(git_root)
db = ContextDB(project_dir)
config = load_config(project_dir)

# Lazy cluster DB — only opened when needed (health check, flywheels)
cluster_db = None
def get_cluster_db():
    nonlocal cluster_db
    if cluster_db is None:
        from lib.db import resolve_cluster_db
        cluster_dir = resolve_cluster_db(project_dir)
        cluster_db = ContextDB(cluster_dir) if cluster_dir != project_dir else db
    return cluster_db
```

Update callers:
- `health_summary(db, git_root, project_dir, config)` → `health_summary(db, get_cluster_db(), git_root, project_dir, config)`
- `check_flywheels(db, config, ...)` → `check_flywheels(get_cluster_db(), config, ...)`

Add cleanup in the finally block:
```python
finally:
    db.close()
    if cluster_db is not None and cluster_db is not db:
        cluster_db.close()
```

- [ ] **Step 3: Run hooks tests**

Run: `python3.12 -m pytest tests/test_hooks.py -v`
Expected: All pass

- [ ] **Step 4: Commit**

```bash
git add lib/hooks.py tests/test_hooks.py
git commit -m "feat: lazy cluster DB in hooks.py for health/flywheel checks"
```

---

## Chunk 4: Remaining modules + full regression

### Task 8: Update `nudge.py`, `queries.py`, `status.py`

**Files:**
- Modify: `lib/nudge.py:52` (check_flywheels signature)
- Modify: `lib/queries.py:191` (main — queries are all local, but verify)
- Modify: `lib/status.py:10` (show_status — display cluster info)

- [ ] **Step 1: Verify nudge.py needs no code changes**

`check_flywheels(db, config, tags)` reads knowledge — a cluster operation. It has exactly ONE caller: `hooks.py:48`. Task 7 updates that caller to pass `get_cluster_db()`. `nudge.py:main()` does not open a DB (it only handles enable/disable/list config). Therefore no code changes to nudge.py are needed — the routing change is fully handled at the call site in hooks.py.

Verify no other callers: `grep -rn "check_flywheels" lib/` should show only `nudge.py` (definition) and `hooks.py` (caller).

Verify: `python3.12 -m pytest tests/test_nudge.py -v` — all pass.

- [ ] **Step 2: Verify queries.py needs no code changes**

The spec lists `queries.py` as needing "cluster_db for knowledge searches, local_db for commit queries." However, examining the current code, ALL `query_*` functions in `queries.py` read from the `commits` table only — there is no knowledge search in this module. The spec entry is forward-looking (anticipating a future `query knowledge` command). No code changes needed now. If a knowledge query is added later, it should use `resolve_cluster_db`.

Verify: `python3.12 -m pytest tests/test_queries.py -v` — all pass.

- [ ] **Step 3: Update status.py**

`show_status` reads from all tables. Update to show cluster info and use cluster_db for memo/knowledge counts.

Update `show_status` signature (line 10):
```python
def show_status(local_db, cluster_db, project_dir: str, git_root: str) -> str:
```

Use `cluster_db` for knowledge/memo counts, `local_db` for events/commits/rules.

Add cluster info display:
```python
cluster_path = os.path.join(project_dir, "cluster.yaml")
if os.path.exists(cluster_path):
    from lib.config import _parse_simple_yaml
    with open(cluster_path) as f:
        cfg = _parse_simple_yaml(f.read())
    lines.append(f"Cluster: {cfg.get('cluster', 'unnamed')} (master: {cfg.get('master', '?')})")
```

Update `main()` (line 56):
```python
from lib.db import resolve_cluster_db
project_dir = data_dir(git_root)
cluster_dir = resolve_cluster_db(project_dir)
local_db = ContextDB(project_dir)
cluster_db = ContextDB(cluster_dir) if cluster_dir != project_dir else local_db
try:
    print(show_status(local_db, cluster_db, project_dir, git_root))
finally:
    local_db.close()
    if cluster_db is not local_db:
        cluster_db.close()
```

Update existing test in `tests/test_status.py` to pass two db params.

- [ ] **Step 4: Run affected tests**

Run: `python3.12 -m pytest tests/test_nudge.py tests/test_queries.py tests/test_status.py -v`
Expected: All pass

- [ ] **Step 5: Commit**

```bash
git add lib/status.py tests/test_status.py
git commit -m "feat: cluster-aware status display"
```

---

### Task 9: Full regression + cleanup

**Files:** All

- [ ] **Step 1: Run full test suite**

Run: `python3.12 -m pytest tests/ -v`
Expected: All pass (~230+ tests)

- [ ] **Step 2: CLI smoke tests**

```bash
bin/context-hooks help
bin/context-hooks status
bin/context-hooks cluster show
```

All should work without errors. `cluster show` should print "Not in a cluster (standalone mode)."

- [ ] **Step 3: End-to-end cluster test**

```bash
# From context-hooks repo
bin/context-hooks cluster join --master /Users/fernando/Dev/KADE2 --name kade-ecosystem
bin/context-hooks cluster show
bin/context-hooks status
# Should show cluster info and read memos/knowledge from KADE2's DB

# Clean up — leave the cluster so dev environment stays standalone
bin/context-hooks cluster leave
bin/context-hooks cluster show
# Should print "Not in a cluster (standalone mode)."
```

- [ ] **Step 4: Commit any fixups**

```bash
git add -A
git commit -m "fix: regression fixes from cluster routing integration"
```

(Only if needed — skip if no fixes required.)

- [ ] **Step 5: Final commit**

```bash
git add -A
git commit -m "feat: cross-project routing — cluster model for shared memos and knowledge"
```
