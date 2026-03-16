"""Cluster management — join, show, leave. CLI-callable."""
import argparse
import os
import subprocess
import sys

from lib.config import _parse_simple_yaml
from lib.db import data_dir, resolve_git_root


def _validate_master(master_root: str) -> None:
    """Raise ValueError if master_root has no context.db (not a context-hooks project root)."""
    master_db = os.path.join(data_dir(master_root), "context.db")
    if not os.path.exists(master_db):
        raise ValueError(f"{master_root} is not a git repository root")


def join_cluster(project_dir: str, master_root: str, name: str) -> None:
    """Join a cluster by writing cluster.yaml to the project's data dir."""
    _validate_master(master_root)
    cluster_path = os.path.join(project_dir, "cluster.yaml")
    with open(cluster_path, "w", encoding="utf-8") as f:
        f.write(f"cluster: {name}\nmaster: {master_root}\n")
    print(
        f"Joined cluster '{name}'. "
        f"Memos and knowledge now route to master at {master_root}."
    )
    print(
        "Existing local memos/knowledge in this project's DB "
        "will be ignored (not deleted)."
    )


def show_cluster(project_dir: str) -> None:
    """Print current cluster config."""
    cluster_path = os.path.join(project_dir, "cluster.yaml")
    if not os.path.exists(cluster_path):
        print("Not in a cluster (standalone mode).")
        return
    with open(cluster_path, encoding="utf-8") as f:
        config = _parse_simple_yaml(f.read())
    name = config.get("cluster", "unnamed")
    master = config.get("master", "unknown")
    is_master = data_dir(master) == project_dir
    role = "master" if is_master else "satellite"
    print(f"Cluster: {name}")
    print(f"Master: {master}")
    print(f"Role: {role}")


def leave_cluster(project_dir: str) -> None:
    """Leave the current cluster."""
    cluster_path = os.path.join(project_dir, "cluster.yaml")
    if not os.path.exists(cluster_path):
        print("Not in a cluster.")
        return
    os.remove(cluster_path)
    print(
        "Left cluster. Memos and knowledge now use local DB. "
        "Data in master DB is unaffected."
    )


def main(args: list) -> None:
    """CLI entry point: context-hooks cluster join|show|leave"""
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
