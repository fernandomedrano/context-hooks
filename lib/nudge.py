"""Active nudges: parity warnings and flywheel enforcement. Opt-in via config."""
import re
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lib.config import load_config, save_config_key


def check_parity(files_changed: str, profile: dict | None) -> str | None:
    """Check committed files against profile parallel paths.
    Returns a warning string if solo-a or solo-b detected, else None."""
    if not profile or not files_changed:
        return None

    parallel_paths = profile.get("parallel_paths", [])
    if not parallel_paths:
        return None

    files = [f.strip() for f in files_changed.split(",") if f.strip()]
    warnings = []

    for pp in parallel_paths:
        pp_files = pp.get("files", [])
        if len(pp_files) != 2:
            continue

        name = pp.get("name", "unknown")
        co_pct = pp.get("co_pct", 0)
        file_a, file_b = pp_files

        has_a = any(f == file_a or f.endswith("/" + os.path.basename(file_a)) for f in files)
        has_b = any(f == file_b or f.endswith("/" + os.path.basename(file_b)) for f in files)

        if has_a and not has_b:
            warnings.append(
                f"Parity: You're editing {os.path.basename(file_a)} without "
                f"{os.path.basename(file_b)}. These are usually edited together "
                f"({co_pct}% co-occurrence). Is this intentional?"
            )
        elif has_b and not has_a:
            warnings.append(
                f"Parity: You're editing {os.path.basename(file_b)} without "
                f"{os.path.basename(file_a)}. These are usually edited together "
                f"({co_pct}% co-occurrence). Is this intentional?"
            )

    return "\n".join(warnings) if warnings else None


def check_flywheels(db, config: dict, tags: str) -> list[str]:
    """Check flywheel rules against commit tags. Returns list of warnings."""
    flywheels = config.get("flywheels", [])
    if not flywheels or not tags:
        return []

    # Parse flywheels from config — they may be stored as simple strings
    # or as structured dicts depending on config format
    if isinstance(flywheels, list) and all(isinstance(f, str) for f in flywheels):
        # Simple format: ["bug-to-failure-class:BUG-\\d+:bug_refs:failure-class"]
        parsed = []
        for f in flywheels:
            parts = f.split(":")
            if len(parts) >= 4:
                parsed.append({
                    "name": parts[0],
                    "trigger_pattern": parts[1],
                    "match_field": parts[2],
                    "required_category": parts[3],
                    "message": f"Commit references {{ref}} but no {parts[3]} knowledge entry exists."
                })
        flywheels = parsed

    warnings = []
    tag_list = [t.strip() for t in tags.split(",") if t.strip()]

    for fw in flywheels:
        if not isinstance(fw, dict):
            continue
        pattern = fw.get("trigger_pattern", "")
        match_field = fw.get("match_field", "bug_refs")
        required_category = fw.get("required_category", "")
        message_template = fw.get("message", "Commit references {ref} but no matching knowledge entry exists.")

        if not pattern:
            continue

        for tag in tag_list:
            if re.match(pattern, tag):
                # Check if a knowledge entry covers this reference
                if match_field == "bug_refs":
                    count = db.query(
                        "SELECT COUNT(*) FROM knowledge WHERE bug_refs LIKE ? AND category = ? AND status = 'active'",
                        (f"%{tag}%", required_category)
                    )[0][0]
                elif match_field == "tags":
                    count = db.query(
                        "SELECT COUNT(*) FROM knowledge WHERE tags LIKE ? AND category = ? AND status = 'active'",
                        (f"%{tag}%", required_category)
                    )[0][0]
                else:
                    count = 0

                if count == 0:
                    warnings.append(message_template.format(ref=tag))

    return warnings


def nudge_enable(name: str, project_data_dir: str = None):
    """Enable a nudge by writing to config."""
    save_config_key(f"nudge.{name}", True, project_data_dir)
    print(f"Enabled nudge: {name}")


def nudge_disable(name: str, project_data_dir: str = None):
    """Disable a nudge."""
    save_config_key(f"nudge.{name}", False, project_data_dir)
    print(f"Disabled nudge: {name}")


def nudge_list(config: dict):
    """List available nudges and their current state."""
    available = {
        "parity": "Warn when committing to only one side of a parallel path",
        "flywheel": "Warn when a bug-fix commit has no matching knowledge entry",
        "health-summary": "Show health summary at session start",
    }
    print("Available nudges:")
    for name, desc in available.items():
        key = f"nudge.{name}"
        state = "ON" if config.get(key) else "OFF"
        print(f"  [{state}] {name} -- {desc}")


def main():
    """CLI entry point: context-hooks nudge enable|disable|list"""
    from lib.db import data_dir, resolve_git_root

    if len(sys.argv) < 2:
        print("Usage: nudge.py enable|disable|list [name]", file=sys.stderr)
        sys.exit(1)

    action = sys.argv[1]
    git_root = resolve_git_root(os.getcwd())
    project_dir = data_dir(git_root)
    config = load_config(project_dir)

    if action == "list":
        nudge_list(config)
    elif action == "enable" and len(sys.argv) > 2:
        nudge_enable(sys.argv[2], project_dir)
    elif action == "disable" and len(sys.argv) > 2:
        nudge_disable(sys.argv[2], project_dir)
    else:
        print("Usage: nudge.py enable|disable|list [name]", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
