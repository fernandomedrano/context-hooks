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
    "PreToolUse": [{
        "matcher": "Read|Edit|Write|Bash",
        "hooks": [{"type": "command", "command": f"bash {shim_path} pre-tool-use", "timeout": 5}]
    }],
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

# Known v0.1 shell script patterns to remove
v1_patterns = [
    "event-logger.sh", "commit-journal.sh", "session-start.sh",
    "session-end.sh", "pre-compact.sh", "compact-recovery.sh",
    "backfill-commits.sh", "tag-engine.sh", "memory-xref.sh",
    "query-commits.sh",
]

def is_v1_hook(cmd):
    """Check if a hook command references a v0.1 shell script."""
    return any(p in cmd for p in v1_patterns)

def is_our_hook(cmd):
    """Check if a hook command is from context-hooks (any version)."""
    return "context-hooks" in cmd or "hook-shim" in cmd or is_v1_hook(cmd)

# For each event type: remove old v0.1/v0.2 hooks, then add ours
for event, entries in new_hooks.items():
    existing = hooks.get(event, [])
    # Filter out our old hooks (v0.1 scripts and previous shim installs)
    cleaned = []
    removed = 0
    for entry in existing:
        entry_hooks = entry.get("hooks", [])
        if any(is_our_hook(h.get("command", "")) for h in entry_hooks):
            removed += 1
        else:
            cleaned.append(entry)
    if removed > 0:
        print(f"    Removed {removed} old context-hooks hook(s) from {event}")
    cleaned.extend(entries)
    hooks[event] = cleaned

# Also clean v0.1 hooks from event types we don't define (e.g. Stop)
for event in list(hooks.keys()):
    if event in new_hooks:
        continue  # Already handled above
    existing = hooks[event]
    cleaned = [
        entry for entry in existing
        if not any(is_our_hook(h.get("command", "")) for h in entry.get("hooks", []))
    ]
    if len(cleaned) < len(existing):
        print(f"    Removed {len(existing) - len(cleaned)} old context-hooks hook(s) from {event}")
    if cleaned:
        hooks[event] = cleaned
    else:
        del hooks[event]

settings["hooks"] = hooks

with open(settings_path, 'w') as f:
    json.dump(settings, f, indent=2)
    f.write('\n')

print("  ✓ Hooks merged into", settings_path)
print("    (existing hooks preserved)")
PYEOF

    # Inject CLAUDE.md snippet if in a git repo
    GIT_CHECK=$(git rev-parse --show-toplevel 2>/dev/null || echo "")
    SNIPPET="$SCRIPT_DIR/adapters/claude-code/claudemd-snippet.md"
    if [[ -n "$GIT_CHECK" && -f "$SNIPPET" ]]; then
      CLAUDE_MD="$GIT_CHECK/CLAUDE.md"
      if [[ -f "$CLAUDE_MD" ]]; then
        if ! grep -q "Context Hooks — Agent Intelligence" "$CLAUDE_MD" 2>/dev/null; then
          echo "" >> "$CLAUDE_MD"
          cat "$SNIPPET" >> "$CLAUDE_MD"
          echo "  ✓ Context-hooks section appended to CLAUDE.md"
        else
          echo "  ✓ CLAUDE.md already has context-hooks section"
        fi
      else
        cat "$SNIPPET" > "$CLAUDE_MD"
        echo "  ✓ CLAUDE.md created with context-hooks section"
      fi
    fi

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
