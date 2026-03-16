#!/usr/bin/env bash
# uninstall.sh — Remove context-hooks
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

echo "context-hooks uninstaller"
echo ""

# Remove symlink
if [[ -L "/usr/local/bin/context-hooks" ]]; then
  rm -f "/usr/local/bin/context-hooks"
  echo "  ✓ Removed PATH symlink"
fi

# Remove Claude Code hooks
if [[ -f "$HOME/.claude/settings.json" ]]; then
  python3 << 'PYEOF'
import json, os

path = os.path.expanduser("~/.claude/settings.json")
if not os.path.exists(path):
    exit(0)

with open(path) as f:
    settings = json.load(f)

hooks = settings.get("hooks", {})
changed = False

for event in list(hooks.keys()):
    entries = hooks[event]
    filtered = [
        entry for entry in entries
        if not any("hook-shim" in h.get("command", "") or "context-hooks" in h.get("command", "")
                   for h in entry.get("hooks", []))
    ]
    if len(filtered) != len(entries):
        changed = True
        if filtered:
            hooks[event] = filtered
        else:
            del hooks[event]

if changed:
    settings["hooks"] = hooks
    with open(path, 'w') as f:
        json.dump(settings, f, indent=2)
        f.write('\n')
    print("  ✓ Removed hooks from ~/.claude/settings.json")

PYEOF
fi

# Remove slash commands
if [[ -f "$HOME/.claude/commands/memory-xref.md" ]]; then
  rm -f "$HOME/.claude/commands/memory-xref.md"
  echo "  ✓ Removed slash commands"
fi

echo ""
echo "  Data at ~/.context-hooks/ was NOT deleted."
echo "  To remove all data: rm -rf ~/.context-hooks/"
echo ""
echo "Done."
