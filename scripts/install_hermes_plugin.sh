#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PLUGIN_DIR="$HOME/.hermes/plugins/moonside-status"
LEGACY_PLUGIN_DIR="$HOME/.hermes/plugins/bk-light-status"

mkdir -p "$HOME/.hermes/plugins"
rm -rf "$PLUGIN_DIR"
ln -s "$REPO_ROOT" "$PLUGIN_DIR"

if [ -L "$LEGACY_PLUGIN_DIR" ] || [ -d "$LEGACY_PLUGIN_DIR" ]; then
  rm -rf "$LEGACY_PLUGIN_DIR"
fi

hermes plugins enable moonside-status

echo "Installed plugin symlink: $PLUGIN_DIR -> $REPO_ROOT"
echo "Status files will be written to: ${AGENT_STATUS_DIR:-${BK_LIGHT_STATUS_DIR:-/tmp/hermes_agent_status}}"
echo "Legacy env vars remain supported: BK_LIGHT_STATUS_DIR, BK_LIGHT_ADDRESS"
