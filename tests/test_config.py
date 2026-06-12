from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from agent_status.config import load_config


class ConfigFallbackTests(unittest.TestCase):
    def test_placeholder_address_falls_back_to_submodule_config(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / 'config.yaml'
            p.write_text('device:\n  address: "YOUR-BLE-DEVICE-ADDRESS"\n', encoding='utf-8')
            cfg = load_config(p)
            self.assertEqual(cfg.device.address, 'F0:27:3C:1A:8B:C3')


if __name__ == '__main__':
    unittest.main()
