#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import tempfile
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
        except (OSError, json.JSONDecodeError) as exc:
            raise SystemExit(
                f"Refusing to overwrite unreadable Claude settings at {settings_path}: {exc}"
            ) from exc
        if not isinstance(data, dict):
            raise SystemExit(f"Refusing to overwrite non-object Claude settings at {settings_path}")
    else:
        data = {}

    hooks = data.setdefault("hooks", {})
    if not isinstance(hooks, dict):
        raise SystemExit(f"Refusing to overwrite non-object hooks section in {settings_path}")
    hook_cmd = f"python3 {repo_root / 'scripts' / 'claude_status_hook.py'}"
    handler = {"type": "command", "command": hook_cmd}

    for event in HOOK_EVENTS:
        items = hooks.setdefault(event, [])
        if not isinstance(items, list):
            raise SystemExit(f"Refusing to overwrite non-list hook entry {event!r} in {settings_path}")
        exists = any(
            isinstance(item, dict)
            and item.get("matcher") == ".*"
            and item.get("hooks") == [handler]
            for item in items
        )
        if not exists:
            items.append({"matcher": ".*", "hooks": [handler]})

    serialized = json.dumps(data, indent=2) + "\n"
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=settings_path.parent,
        prefix=f".{settings_path.name}.", suffix=".tmp", delete=False,
    ) as tmp:
        tmp.write(serialized)
        tmp_path = Path(tmp.name)
    os.chmod(tmp_path, 0o600)
    tmp_path.replace(settings_path)
    print(settings_path)
    print(hook_cmd)


if __name__ == "__main__":
    main()
