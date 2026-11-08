#!/usr/bin/env python3
"""Install the gpu-lamp user service from systemd/gpu-lamp.service.in."""
import argparse, pathlib, subprocess, sys

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
TEMPLATE = REPO_ROOT / "systemd" / "gpu-lamp.service.in"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--python", default=str(REPO_ROOT / ".venv" / "bin" / "python"))
    ap.add_argument("--no-start", action="store_true")
    args = ap.parse_args()

    unit_dir = pathlib.Path.home() / ".config" / "systemd" / "user"
    unit_dir.mkdir(parents=True, exist_ok=True)
    text = TEMPLATE.read_text().replace("@PYTHON@", args.python).replace("@REPO_ROOT@", str(REPO_ROOT))
    unit = unit_dir / "gpu-lamp.service"
    unit.write_text(text)
    subprocess.run(["systemctl", "--user", "daemon-reload"], check=True)
    subprocess.run(["systemctl", "--user", "enable", "gpu-lamp.service"], check=True)
    if not args.no_start:
        subprocess.run(["systemctl", "--user", "restart", "gpu-lamp.service"], check=True)
    print(f"Installed and started {unit}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
