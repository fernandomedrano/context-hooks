#!/usr/bin/env bash
# Claude Code hook shim — passes JSON to context-hooks CLI
# Called as: hook-shim.sh <hook-type>
set -euo pipefail
HOOK_TYPE="${1:-}"
INPUT=$(cat)
SCRIPT_DIR="$(cd "$(dirname "$0")/../.." && pwd)"
"$SCRIPT_DIR/bin/context-hooks" hook "$HOOK_TYPE" "$INPUT" 2>/dev/null || true
