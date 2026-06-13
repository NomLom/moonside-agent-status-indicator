from __future__ import annotations

import argparse
import asyncio
import sys
import time
from dataclasses import replace
from datetime import datetime
from pathlib import Path

from PIL import Image

from .config import AgentStatusConfig, load_config, to_app_config
from .config import is_image_path
from .display import (
    EMPTY_BG,
    SlotManager,
    compose_frame,
    prerender_quadrants,
    render_emoji_sprite,
    render_fullscreen_png,
)
from .emoji_font import resolve_emoji_font
from .watcher import SessionWatcher
from .moonside import MoonsideManager, classify_state

VALID_STATES = {"idle", "thinking", "tool_use", "permission", "success", "failed", "cancelled"}
POLL_INTERVAL = 0.3
REFRESH_INTERVAL = 10.0


def read_session_states(status_dir: Path, stale_threshold: int) -> dict[str, str]:
    if not status_dir.is_dir():
        return {}

    now = time.time()
    states: dict[str, str] = {}

    for state_file in status_dir.iterdir():
        if state_file.name.startswith(".") or not state_file.is_file():
            continue
        try:
            mtime = state_file.stat().st_mtime
        except OSError:
            continue
        if now - mtime >= stale_threshold:
            state_file.unlink(missing_ok=True)
            print(f"  Removed stale session file: {state_file.name}")
            continue
        try:
            state = state_file.read_text(encoding="utf-8").strip()
        except OSError:
            continue
        if state not in VALID_STATES:
            state = "idle"
        states[state_file.name] = state

    return states


def build_fullscreen_idle(
    emoji: str,
    canvas_size: tuple[int, int],
    font_path: Path,
) -> Image.Image:
    sprite = render_emoji_sprite(emoji, min(canvas_size), font_path)
    bg = Image.new("RGB", canvas_size, (0, 0, 0))
    resized = sprite.resize(canvas_size, Image.LANCZOS)
    bg.paste(resized, mask=resized.split()[3])
    return bg


PROJECT_ROOT = Path(__file__).resolve().parents[1]
ASSETS_DIR = PROJECT_ROOT / "assets"

_ACK_STAGE_TWO_ALT2 = bytes.fromhex("08 00 05 80 0B 04 07 02")


def _patch_ack_stage_two_alt2(display_session: object) -> None:
    from types import ModuleType

    if not isinstance(display_session, ModuleType):
        return
    if hasattr(display_session, "ACK_STAGE_TWO_ALT2"):
        return

    display_session.ACK_STAGE_TWO_ALT2 = _ACK_STAGE_TWO_ALT2  # type: ignore[attr-defined]
    original_handler = display_session.AckWatcher.handler

    bytes_to_hex = display_session.bytes_to_hex  # type: ignore[attr-defined]

    def patched_handler(self: object, _sender: int, data: bytearray) -> None:
        payload = bytes(data)
        if payload == _ACK_STAGE_TWO_ALT2:
            if self.verbose:  # type: ignore[attr-defined]
                print("NOTIF", bytes_to_hex(payload))
            self.stage_two.set()  # type: ignore[attr-defined]
            return
        original_handler(self, _sender, data)

    display_session.AckWatcher.handler = patched_handler  # type: ignore[attr-defined]


async def run(config: AgentStatusConfig, verbose: bool = False) -> None:
    transport = "bk"
    try:
        from bleak import BleakClient, BleakScanner
        device = None
        if config.device.address:
            try:
                device = await BleakScanner.find_device_by_address(config.device.address, timeout=config.device.scan_timeout, cached=False)
            except TypeError:
                device = await BleakScanner.find_device_by_address(config.device.address, timeout=config.device.scan_timeout)
        if device is not None:
            client = BleakClient(device, timeout=10.0)
            await client.connect()
            if client.is_connected:
                for svc in client.services:
                    if str(svc.uuid).lower() == "6e400001-b5a3-f393-e0a9-e50e24dcca9e":
                        transport = "moonside"
                        break
            await client.disconnect()
    except Exception:
        pass

    submodule = Path(__file__).resolve().parents[1] / "Bk-Light-AppBypass"
    if str(submodule) not in sys.path:
        sys.path.insert(0, str(submodule))

    from bk_light.panel_manager import PanelManager
    from bk_light import display_session

    _patch_ack_stage_two_alt2(display_session)

    app_config = to_app_config(config)
    canvas_size = (config.panel.tile_width, config.panel.tile_height)
    quad_w = canvas_size[0] // 2
    quad_h = canvas_size[1] // 2
    quad_size = (quad_w, quad_h)
    status_dir = Path(config.status.status_dir)

    status_dir.mkdir(parents=True, exist_ok=True)

    font_path = resolve_emoji_font(config.status.emoji_font)
    print(f"Using emoji font: {font_path}")

    print("Pre-rendering sprites...")
    quadrants = prerender_quadrants(config.status.statuses, quad_size, font_path, ASSETS_DIR)

    idle_value = config.status.statuses.get("idle", "😴")
    if is_image_path(idle_value):
        png_path = Path(idle_value) if Path(idle_value).is_absolute() else ASSETS_DIR / idle_value
        fullscreen_idle = render_fullscreen_png(png_path, canvas_size)
    else:
        fullscreen_idle = build_fullscreen_idle(idle_value, canvas_size, font_path)

    empty_quad = Image.new("RGB", quad_size, EMPTY_BG)
    print(f"  {len(quadrants)} state tiles ready.")

    slots = SlotManager()
    watcher = SessionWatcher()
    last_snapshot: list[str | None] = []
    last_send_time = 0.0
    reconnect_delay = config.device.reconnect_delay

    while True:
        try:
            if transport == "moonside":
                async with MoonsideManager(config.device.address, config.device.scan_timeout) as manager:
                    print(f"Connected to Moonside. Watching {status_dir}/ for session state files.")
                    print("Press Ctrl+C to stop.\n")
                    while True:
                        states = read_session_states(status_dir, config.status.stale_threshold)
                        active_ids = set(states.keys())
                        slots.update(active_ids)
                        watcher.sync(active_ids)
                        current = slots.snapshot(states)
                        now = time.monotonic()
                        changed = current != last_snapshot
                        if changed:
                            active_count = sum(1 for s in current if s)
                            parts = [s or "---" for s in current]
                            lamp_state = classify_state(current)
                            print(f"  [{active_count}/{SlotManager.MAX_SLOTS}] {', '.join(parts)} -> lamp={lamp_state}")
                            await manager.send_state(current)
                            last_snapshot = current
                            last_send_time = now
                        await asyncio.sleep(POLL_INTERVAL)
            else:
                async with PanelManager(app_config) as manager:
                    print(f"Connected. Watching {status_dir}/ for session state files.")
                    print("Press Ctrl+C to stop.\n")

                    while True:
                        states = read_session_states(status_dir, config.status.stale_threshold)
                        active_ids = set(states.keys())
                        slots.update(active_ids)
                        watcher.sync(active_ids)
                        current = slots.snapshot(states)

                        now = time.monotonic()
                        changed = current != last_snapshot
                        stale = now - last_send_time >= REFRESH_INTERVAL

                        if changed or stale:
                            active_count = sum(1 for s in current if s)
                            parts = [s or "---" for s in current]
                            print(f"  [{active_count}/{SlotManager.MAX_SLOTS}] {', '.join(parts)}")
                            frame = compose_frame(
                                current, quadrants, empty_quad,
                                fullscreen_idle, canvas_size,
                            )
                            await manager.send_image(frame, delay=0.2)
                            last_snapshot = current
                            last_send_time = now

                        await asyncio.sleep(POLL_INTERVAL)
        except KeyboardInterrupt:
            watcher.stop_all()
            print("\nShutting down...")
            return
        except Exception as err:
            last_snapshot = []
            ts = datetime.now().strftime("%H:%M:%S")
            print(f"  [{ts}] Connection lost: {err}")
            print(f"  [{ts}] Reconnecting in {reconnect_delay}s...")
            await asyncio.sleep(reconnect_delay)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Hermes Agent status indicator for a Moonside lamp or BK-Light panel",
    )
    parser.add_argument(
        "--config", type=Path, default=None,
        help="path to config file (default: config.local.yaml if exists, else config.yaml)",
    )
    parser.add_argument(
        "--address",
        help="BLE device address (overrides config)",
    )
    parser.add_argument(
        "--verbose", "-v", action="store_true",
        help="enable verbose output",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    config = load_config(args.config)

    if args.address:
        config = replace(config, device=replace(config.device, address=args.address))

    if not config.device.address and not config.device.name_prefix:
        print("Error: No BLE discovery target configured.")
        print("Set device.address, device.name_prefix, or use --address flag.")
        sys.exit(1)

    asyncio.run(run(config, verbose=args.verbose))

