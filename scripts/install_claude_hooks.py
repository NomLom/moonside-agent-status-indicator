#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

HOOK_EVENTS = [
    "SessionStart",
    "UserPromptSubmit",
    "PreToolUse",
    "PostToolUse",
    "Notification",
    "Stop",
    "SubagentStart",
    "SubagentStop",
    "SessionEnd",
]


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Install Moonside status hooks into Claude Code settings."
    )
    parser.parse_args()

    repo_root = Path(__file__).resolve().parents[1]
    settings_path = Path.home() / ".claude" / "settings.json"
    settings_path.parent.mkdir(parents=True, exist_ok=True)

    if settings_path.exists():
        try:
            data = json.loads(settings_path.read_text(encoding="utf-8"))
        except Exception:
            data = {}
    else:
        data = {}

    hooks = data.setdefault("hooks", {})
    hook_cmd = f"python3 {repo_root / 'scripts' / 'claude_status_hook.py'}"

    for event in HOOK_EVENTS:
        items = hooks.setdefault(event, [])
        exists = any(
            isinstance(item, dict)
            and item.get("matcher") == ".*"
            and item.get("hooks") == [hook_cmd]
            for item in items
        )
        if not exists:
            items.append({"matcher": ".*", "hooks": [hook_cmd]})

    settings_path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    print(settings_path)
    print(hook_cmd)


if __name__ == "__main__":
    main()
