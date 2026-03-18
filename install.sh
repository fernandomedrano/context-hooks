#!/usr/bin/env bash
# install.sh — Install context-hooks for the detected platform
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ADAPTER="${1:-}"

# ── Detect platform ──────────────────────────────────────────────────────
if [[ "$ADAPTER" == "--adapter="* ]]; then
  ADAPTER="${ADAPTER#--adapter=}"
elif [[ -z "$ADAPTER" ]]; then
  if [[ -f "$HOME/.claude/settings.json" ]]; then
    ADAPTER="claude-code"
  elif [[ -f "$HOME/.gemini/settings.json" ]]; then
    ADAPTER="gemini"
  elif [[ -d "$HOME/.cursor" ]]; then
    ADAPTER="cursor"
  else
    ADAPTER="generic"
  fi
fi

echo "context-hooks installer"
echo ""
echo "Detected platform: $ADAPTER"
echo ""

# ── Create data directory ────────────────────────────────────────────────
DATA_DIR="$HOME/.context-hooks"
mkdir -p "$DATA_DIR/projects"
chmod 700 "$DATA_DIR"
echo "  ✓ Data directory: $DATA_DIR (chmod 700)"

# ── Create symlink on PATH ───────────────────────────────────────────────
SYMLINK="/usr/local/bin/context-hooks"
if [[ -L "$SYMLINK" ]] || [[ -f "$SYMLINK" ]]; then
  rm -f "$SYMLINK"
fi
ln -s "$SCRIPT_DIR/bin/context-hooks" "$SYMLINK" 2>/dev/null || {
  echo "  ⚠ Could not create symlink at $SYMLINK (try with sudo)"
  echo "  Add to PATH manually: export PATH=\"$SCRIPT_DIR/bin:\$PATH\""
}
if [[ -L "$SYMLINK" ]]; then
  echo "  ✓ context-hooks added to PATH ($SYMLINK)"
fi

# ── Install adapter ─────────────────────────────────────────────────────
case "$ADAPTER" in
  claude-code)
    SETTINGS="$HOME/.claude/settings.json"
    SHIM="$SCRIPT_DIR/adapters/claude-code/hook-shim.sh"

    # Merge hooks into settings.json (idempotent)
    python3 << PYEOF
import json, os

settings_path = "$SETTINGS"
shim_path = "$SHIM"

# Read existing settings
settings = {}
if os.path.exists(settings_path):
    with open(settings_path) as f:
        settings = json.load(f)

hooks = settings.setdefault("hooks", {})

# Define our hooks
new_hooks = {
    "PostToolUse": [{
        "matcher": "",
        "hooks": [{"type": "command", "command": f"bash {shim_path} event", "timeout": 5}]
    }],
    "PreCompact": [{
        "matcher": "",
        "hooks": [{"type": "command", "command": f"bash {shim_path} pre-compact", "timeout": 10}]
    }],
    "SessionStart": [{
        "matcher": "compact",
        "hooks": [{"type": "command", "command": f"bash {shim_path} session-start", "timeout": 10}]
    }, {
        "matcher": "startup",
        "hooks": [{"type": "command", "command": f"bash {shim_path} session-start", "timeout": 10}]
    }, {
        "matcher": "resume",
        "hooks": [{"type": "command", "command": f"bash {shim_path} session-start", "timeout": 10}]
    }],
}

# Merge: add our hooks without removing existing ones
for event, entries in new_hooks.items():
    existing = hooks.get(event, [])
    # Check if context-hooks is already installed
    already_installed = any(
        "context-hooks" in h.get("command", "") or "hook-shim" in h.get("command", "")
        for entry in existing for h in entry.get("hooks", [])
    )
    if not already_installed:
        existing.extend(entries)
    hooks[event] = existing

settings["hooks"] = hooks

with open(settings_path, 'w') as f:
    json.dump(settings, f, indent=2)
    f.write('\n')

print("  ✓ Hooks merged into", settings_path)
print("    (existing hooks preserved)")
PYEOF

    # Install slash command
    CMD_DIR="$HOME/.claude/commands"
    mkdir -p "$CMD_DIR"
    if [[ -d "$SCRIPT_DIR/adapters/claude-code/commands" ]]; then
      for cmd in "$SCRIPT_DIR/adapters/claude-code/commands"/*.md; do
        [[ -f "$cmd" ]] && cp "$cmd" "$CMD_DIR/"
      done
      echo "  ✓ Slash commands installed"
    fi
    ;;

  gemini)
    echo "  Gemini CLI adapter: add to ~/.gemini/settings.json manually"
    echo "  See adapters/gemini-cli/README.md for instructions"
    ;;

  cursor)
    echo "  Cursor adapter: add to .cursor/hooks.json manually"
    echo "  See adapters/cursor/README.md for instructions"
    ;;

  generic)
    echo "  Generic install: hooks available via 'context-hooks hook <type>'"
    echo "  Wire into your platform manually"
    ;;
esac

echo ""

# ── Offer bootstrap ─────────────────────────────────────────────────────
GIT_ROOT=$(git rev-parse --show-toplevel 2>/dev/null || echo "")
if [[ -n "$GIT_ROOT" ]]; then
  COMMIT_COUNT=$(git -C "$GIT_ROOT" log --oneline 2>/dev/null | wc -l | tr -d ' ')
  if [[ "$COMMIT_COUNT" -gt 0 ]]; then
    echo "This project has $COMMIT_COUNT commits."
    echo "Bootstrap from git history? (~60 seconds)"
    echo "Enables pattern detection immediately."
    echo ""
    read -p "  [Y] Bootstrap now  [n] Skip: " -n 1 -r REPLY
    echo ""
    if [[ "$REPLY" =~ ^[Nn]$ ]]; then
      echo "  Skipped. Patterns will emerge over time."
    else
      "$SCRIPT_DIR/bin/context-hooks" bootstrap --days=30
    fi
  fi
fi

echo ""
echo "Ready. Context intelligence is now active."
echo "Run 'context-hooks status' to see what's tracked."
