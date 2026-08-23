#!/usr/bin/env python3
"""Install the BlinkStick status renderer as a persistent user service."""
from __future__ import annotations

import argparse
import shlex
import subprocess
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--status-dir", default="/tmp/claude_code_status")
    parser.add_argument("--led-index", type=int, default=0)
    parser.add_argument("--idle-timeout-seconds", type=int, default=3600)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    if args.led_index < 0 or args.idle_timeout_seconds < 0:
        parser.error("LED index and idle timeout must be non-negative")

    root = Path(__file__).resolve().parents[1]
    template = root / "systemd" / "blinkstick-status.service.in"
    python = root / ".venv" / "bin" / "python"
    if not python.is_file():
        raise SystemExit(f"Missing project venv: {python}; run the install steps first.")

    replacements = {
        "@PYTHON@": shlex.quote(str(python)),
        "@REPO_ROOT@": shlex.quote(str(root)),
        "@STATUS_DIR@": shlex.quote(args.status_dir),
        "@LED_INDEX@": str(args.led_index),
        "@IDLE_TIMEOUT_SECONDS@": str(args.idle_timeout_seconds),
    }
    unit = template.read_text(encoding="utf-8")
    for old, new in replacements.items():
        unit = unit.replace(old, new)

    unit_path = Path.home() / ".config" / "systemd" / "user" / "blinkstick-status.service"
    if args.dry_run:
        print(unit)
        return
    unit_path.parent.mkdir(parents=True, exist_ok=True)
    unit_path.write_text(unit, encoding="utf-8")
    subprocess.run(["systemctl", "--user", "daemon-reload"], check=True)
    subprocess.run(["systemctl", "--user", "enable", "--now", "blinkstick-status.service"], check=True)
    print(f"Installed and started {unit_path}")


if __name__ == "__main__":
    main()
