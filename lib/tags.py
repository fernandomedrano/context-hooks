"""Tag engine for commit indexing — auto-tag with parallel path detection.

Three tiers:
  1. Universal tags (always applied, no profile needed)
  2. Structural tags (directory + hot file, from profile.yaml)
  3. Parallel path tags (paired/solo-a/solo-b, from profile.yaml)
"""
import os
import re
import subprocess
from collections import Counter
from itertools import combinations


# ── Universal tags ────────────────────────────────────────────────────────────

_CONVENTIONAL_PREFIXES = {
    "fix", "feat", "refactor", "docs", "test", "chore",
    "perf", "ci", "style", "build", "revert",
}

_BUG_RE = re.compile(r"BUG-(\d+)")
_ADR_RE = re.compile(r"ADR-(\d+)")
_ISSUE_RE = re.compile(r"#(\d+)")
_PREFIX_RE = re.compile(r"^(" + "|".join(_CONVENTIONAL_PREFIXES) + r")(?:\(|:|\!)")

# Meta files excluded from hot-file tagging
_META_RE = re.compile(
    r"(CURRENT|BACKLOG|COMPLETED|TODO|AGENTS|CLAUDE|MEMORY|CHANGELOG|README|commit-trail|migration-log)\.md$"
)

# __init__/index/main/app — use parent dir name instead
_GENERIC_BASENAMES = {"__init__", "index", "main", "app"}


def apply_universal_tags(subject: str, body: str, files: list[str]) -> list[str]:
    """Apply universal tags that need no profile. Returns deduplicated list."""
    tags: list[str] = []
    text = f"{subject} {body}"

    # Conventional commit prefix
    m = _PREFIX_RE.match(subject)
    if m:
        tags.append(m.group(1))

    # BUG-NNN references
    for match in _BUG_RE.finditer(text):
        tags.append(f"BUG-{match.group(1)}")

    # ADR-NNN references
    for match in _ADR_RE.finditer(text):
        tags.append(f"ADR-{match.group(1)}")

    # GitHub issue refs
    for match in _ISSUE_RE.finditer(text):
        tags.append(f"#{match.group(1)}")

    # File-type categories
    files_joined = "\n".join(files)
    if re.search(r"alembic|migrations?/", files_joined):
        tags.append("migration")
    if re.search(r"Dockerfile|docker-compose|compose\.y", files_joined):
        tags.append("infra")
    if re.search(r"tests?/|\.test\.|\.spec\.|_test\.", files_joined):
        tags.append("tests")
    if re.search(r"\.md$|docs/|README", files_joined):
        tags.append("docs")
    if re.search(r"\.github/|\.gitlab|\.circleci|Jenkinsfile", files_joined):
        tags.append("ci")

    return _dedup(tags)


# ── Profile-based tags ────────────────────────────────────────────────────────

def apply_profile_tags(files: list[str], profile: dict) -> list[str]:
    """Apply structural + parallel path tags from a profile dict."""
    tags: list[str] = []

    # Directory tags
    dir_tags = profile.get("directory_tags") or {}
    for directory, tag_name in dir_tags.items():
        if any(f.startswith(directory + "/") or f == directory for f in files):
            tags.append(tag_name)

    # Hot file tags
    hot_files = profile.get("hot_files") or {}
    for filepath, tag_name in hot_files.items():
        if filepath in files:
            tags.append(tag_name)

    # Parallel path detection
    parallel_paths = profile.get("parallel_paths") or []
    for pp in parallel_paths:
        pp_files = pp["files"]
        name = pp["name"]
        has_a = any(f == pp_files[0] for f in files)
        has_b = any(f == pp_files[1] for f in files)
        if has_a and has_b:
            tags.append(f"paired:{name}")
        elif has_a and not has_b:
            tags.append(f"solo-a:{name}")
        elif has_b and not has_a:
            tags.append(f"solo-b:{name}")

    return _dedup(tags)


# ── Combined ──────────────────────────────────────────────────────────────────

def compute_tags(subject: str, body: str, files: list[str], profile: dict | None = None) -> str:
    """Compute all tags for a commit. Returns comma-separated string."""
    tags = apply_universal_tags(subject, body, files)
    if profile:
        tags.extend(apply_profile_tags(files, profile))
    return ",".join(_dedup(tags))


# ── Profile generation ────────────────────────────────────────────────────────

def generate_profile(git_root: str, days: int = 30) -> dict:
    """Analyze git history and return a profile dict."""
    result = subprocess.run(
        ["git", "-C", git_root, "log", f"--since={days} days ago",
         "--name-only", "--format=COMMIT:%H"],
        capture_output=True, text=True, timeout=30,
    )

    # Parse commits
    commits: dict[str, list[str]] = {}
    current = None
    for line in result.stdout.strip().split("\n"):
        if line.startswith("COMMIT:"):
            current = line[7:]
            commits[current] = []
        elif line.strip() and current is not None:
            commits[current].append(line.strip())

    if not commits:
        return {
            "version": 1,
            "generated_from": f"last {days} days (0 commits)",
            "directory_tags": {},
            "hot_files": {},
            "parallel_paths": [],
        }

    # ── Directory tags: top-level dirs in 5%+ of commits ──
    dir_counts: Counter = Counter()
    for files in commits.values():
        seen_dirs: set[str] = set()
        for f in files:
            top = f.split("/")[0]
            seen_dirs.add(top)
        for d in seen_dirs:
            dir_counts[d] += 1

    min_dir = max(3, int(len(commits) * 0.05))
    dir_tags: dict[str, str] = {}
    for d, count in dir_counts.most_common():
        if count >= min_dir and os.path.isdir(os.path.join(git_root, d)):
            tag_name = d.lower().replace("packages/", "").replace("apps/", "").replace("src/", "")
            dir_tags[d] = tag_name

    # ── Hot files: touched in 8+ commits ──
    file_counts: Counter = Counter()
    for files in commits.values():
        for f in set(files):
            file_counts[f] += 1

    hot_threshold = 8
    hot_files: dict[str, str] = {}
    for f, count in file_counts.most_common():
        if count >= hot_threshold and not _META_RE.search(f):
            base = os.path.basename(f).rsplit(".", 1)[0]
            if base in _GENERIC_BASENAMES:
                base = os.path.basename(os.path.dirname(f))
            hot_files[f] = base.lower().replace("_", "-")

    # ── Parallel paths: co-occurrence analysis ──
    pair_counts: Counter = Counter()
    for files in commits.values():
        unique = {f for f in set(files) if not _META_RE.search(f)}
        for a, b in combinations(sorted(unique), 2):
            pair_counts[(a, b)] += 1

    parallel_paths: list[dict] = []
    min_cooccurrence = 5
    for (a, b), together in pair_counts.most_common(200):
        if together < min_cooccurrence:
            break
        a_total = file_counts[a]
        b_total = file_counts[b]
        if a_total < 5 or b_total < 5:
            continue
        a_solo = a_total - together
        b_solo = b_total - together
        co_pct = together / min(a_total, b_total) * 100

        if co_pct >= 30 and (a_solo >= 2 or b_solo >= 2):
            a_base = os.path.basename(a).rsplit(".", 1)[0]
            b_base = os.path.basename(b).rsplit(".", 1)[0]
            if a_base in _GENERIC_BASENAMES:
                a_base = os.path.basename(os.path.dirname(a))
            if b_base in _GENERIC_BASENAMES:
                b_base = os.path.basename(os.path.dirname(b))
            if a_base == b_base:
                a_parent = os.path.basename(os.path.dirname(os.path.dirname(a)))
                b_parent = os.path.basename(os.path.dirname(os.path.dirname(b)))
                pair_name = f"{a_parent}-{a_base}+{b_parent}-{b_base}"
            else:
                pair_name = f"{a_base}+{b_base}"

            parallel_paths.append({
                "files": [a, b],
                "name": pair_name.lower(),
                "together": together,
                "co_pct": round(co_pct),
            })

    parallel_paths = sorted(parallel_paths, key=lambda p: p["together"], reverse=True)[:15]

    return {
        "version": 1,
        "generated_from": f"last {days} days ({len(commits)} commits)",
        "directory_tags": dir_tags,
        "hot_files": hot_files,
        "parallel_paths": parallel_paths,
    }


# ── Profile I/O (simple YAML, no PyYAML dependency) ──────────────────────────

def save_profile(project_data_dir: str, profile: dict) -> str:
    """Write profile.yaml using a simple serializer. Returns path."""
    path = os.path.join(project_data_dir, "profile.yaml")
    lines = [
        "# Auto-generated commit tag profile",
        f"# Re-generate with: generate_profile(git_root, days)",
        "",
    ]
    lines.append(f"version: {profile.get('version', 1)}")
    lines.append(f"generated_from: {profile.get('generated_from', 'unknown')}")

    # directory_tags
    dt = profile.get("directory_tags") or {}
    if dt:
        lines.append("directory_tags:")
        for k, v in dt.items():
            lines.append(f"  {k}: {v}")
    else:
        lines.append("directory_tags:")

    # hot_files
    hf = profile.get("hot_files") or {}
    if hf:
        lines.append("hot_files:")
        for k, v in hf.items():
            lines.append(f"  {k}: {v}")
    else:
        lines.append("hot_files:")

    # parallel_paths
    pp = profile.get("parallel_paths") or []
    if pp:
        lines.append("parallel_paths:")
        for entry in pp:
            lines.append(f"  - files: [{entry['files'][0]}, {entry['files'][1]}]")
            lines.append(f"    name: {entry['name']}")
            lines.append(f"    together: {entry['together']}")
            lines.append(f"    co_pct: {entry['co_pct']}")
    else:
        lines.append("parallel_paths:")

    os.makedirs(project_data_dir, exist_ok=True)
    with open(path, "w") as f:
        f.write("\n".join(lines) + "\n")
    return path


def load_profile(project_data_dir: str) -> dict | None:
    """Read profile.yaml and return a profile dict. Returns None if missing."""
    path = os.path.join(project_data_dir, "profile.yaml")
    if not os.path.exists(path):
        return None

    with open(path) as f:
        text = f.read()

    profile: dict = {
        "version": 1,
        "generated_from": "",
        "directory_tags": {},
        "hot_files": {},
        "parallel_paths": [],
    }

    section = None
    current_pp_entry: dict | None = None

    for line in text.split("\n"):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue

        # Top-level scalar keys
        m = re.match(r"^(version|generated_from):\s*(.*)", stripped)
        if m:
            key, val = m.group(1), m.group(2).strip()
            if key == "version" and val.isdigit():
                profile["version"] = int(val)
            else:
                profile[key] = val
            section = None
            continue

        # Section headers
        if stripped == "directory_tags:":
            section = "directory_tags"
            continue
        if stripped == "hot_files:":
            section = "hot_files"
            continue
        if stripped == "parallel_paths:":
            section = "parallel_paths"
            continue

        # Parse section content
        indent = len(line) - len(line.lstrip())

        if section == "directory_tags" and indent >= 2:
            kv = re.match(r"(\S+):\s*(.*)", stripped)
            if kv:
                profile["directory_tags"][kv.group(1)] = kv.group(2).strip()
            continue

        if section == "hot_files" and indent >= 2:
            kv = re.match(r"(\S+):\s*(.*)", stripped)
            if kv:
                profile["hot_files"][kv.group(1)] = kv.group(2).strip()
            continue

        if section == "parallel_paths" and indent >= 2:
            if stripped.startswith("- files:"):
                # Save previous entry
                if current_pp_entry is not None:
                    profile["parallel_paths"].append(current_pp_entry)
                # Parse files list: [file_a, file_b]
                files_str = stripped.split("files:", 1)[1].strip()
                files_str = files_str.strip("[]")
                files_list = [f.strip() for f in files_str.split(",")]
                current_pp_entry = {"files": files_list, "name": "", "together": 0, "co_pct": 0}
            elif current_pp_entry is not None:
                kv = re.match(r"(\w+):\s*(.*)", stripped)
                if kv:
                    key, val = kv.group(1), kv.group(2).strip()
                    if key in ("together", "co_pct") and val.isdigit():
                        current_pp_entry[key] = int(val)
                    else:
                        current_pp_entry[key] = val
            continue

    # Don't forget the last parallel path entry
    if current_pp_entry is not None:
        profile["parallel_paths"].append(current_pp_entry)

    return profile


# ── Helpers ───────────────────────────────────────────────────────────────────

def _dedup(tags: list[str]) -> list[str]:
    """Deduplicate while preserving order."""
    seen: set[str] = set()
    result: list[str] = []
    for t in tags:
        if t and t not in seen:
            seen.add(t)
            result.append(t)
    return result
