from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "install_claude_hooks.py"


class ClaudeHookInstallerTests(unittest.TestCase):
    def run_installer(self, home: Path) -> subprocess.CompletedProcess[str]:
        env = {**os.environ, "HOME": str(home)}
        return subprocess.run(
            [sys.executable, str(SCRIPT)], env=env, text=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
        )

    def test_installs_command_handler_objects_idempotently(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            first = self.run_installer(home)
            self.assertEqual(first.returncode, 0, first.stderr)
            second = self.run_installer(home)
            self.assertEqual(second.returncode, 0, second.stderr)
            settings = json.loads((home / ".claude" / "settings.json").read_text())
            handlers = settings["hooks"]["PreToolUse"]
            self.assertEqual(len(handlers), 1)
            self.assertEqual(handlers[0]["hooks"][0]["type"], "command")
            self.assertIn("claude_status_hook.py", handlers[0]["hooks"][0]["command"])

    def test_refuses_to_overwrite_invalid_json(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            settings_path = home / ".claude" / "settings.json"
            settings_path.parent.mkdir(parents=True)
            settings_path.write_text("{invalid", encoding="utf-8")
            result = self.run_installer(home)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("Refusing to overwrite", result.stderr)
            self.assertEqual(settings_path.read_text(encoding="utf-8"), "{invalid")


if __name__ == "__main__":
    unittest.main()
