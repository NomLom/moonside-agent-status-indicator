#!/usr/bin/env bash
set -euo pipefail
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
exec "$REPO_ROOT/.venv/bin/python" "$REPO_ROOT/scripts/install_blinkstick_service.py" "$@"
