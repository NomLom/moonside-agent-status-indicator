#!/usr/bin/env python3
"""Generic status-file hook for hook-driven agents.

Reads a JSON payload from stdin and writes a session state file under the
configured status directory. Useful for manual tests and for Claude-style
hook surfaces that emit a single JSON object per event.
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

STATUS_DIR = Path(os.getenv("AGENT_STATUS_DIR") or os.getenv("BK_LIGHT_STATUS_DIR") or "/tmp/hermes_agent_status")
_SAFE_NAME = re.compile(r"[^A-Za-z0-9._-]+")

EVENT_STATE_MAP: dict[str, str] = {
    "SessionStart": "idle",
    "Stop": "idle",
    "UserPromptSubmit": "thinking",
    "PreToolUse": "tool_use",
    "PostToolUse": "thinking",
    "PostToolUseFailure": "thinking",
    "PermissionRequest": "permission",
    "SubagentStart": "tool_use",
    "SubagentStop": "thinking",
}

NOTIFICATION_STATE_MAP: dict[str, str] = {
    "permission_prompt": "permission",
    "elicitation_dialog": "permission",
    "idle_prompt": "idle",
}

REMOVE_EVENTS = {"SessionEnd"}


def _safe_name(session_id: str) -> str:
    cleaned = _SAFE_NAME.sub("_", session_id).strip("._")
    return cleaned or "session"


def main() -> None:
    try:
        data = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        return

    session_id = data.get("session_id", "")
    if not session_id:
        return

    hook_event = data.get("hook_event_name", "")

    STATUS_DIR.mkdir(parents=True, exist_ok=True)
    state_file = STATUS_DIR / _safe_name(session_id)

    if hook_event in REMOVE_EVENTS:
        state_file.unlink(missing_ok=True)
        return

    if hook_event == "Notification":
        notif_type = data.get("notification_type", "")
        state = NOTIFICATION_STATE_MAP.get(notif_type)
        if state is not None:
            state_file.write_text(state, encoding="utf-8")
        return

    state = EVENT_STATE_MAP.get(hook_event)
    if state is not None:
        state_file.write_text(state, encoding="utf-8")


if __name__ == "__main__":
    main()

