#!/usr/bin/env bash
set -euo pipefail
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
python3 "$REPO_ROOT/scripts/install_claude_hooks.py"
echo "Claude Code hook states will default to: ${CLAUDE_CODE_STATUS_DIR:-/tmp/claude_code_status}"
