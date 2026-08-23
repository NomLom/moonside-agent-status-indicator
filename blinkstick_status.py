#!/usr/bin/env python3
"""Render normalized Claude Code/Hermes state files on one BlinkStick LED.

The Moonside lamp runner and this renderer may consume the same status-file
directory.  This renderer only writes the selected BlinkStick LED index, so
other LEDs can be independently assigned later.
"""
from __future__ import annotations

import argparse
import collections
import collections.abc
import logging
import signal
import time
from pathlib import Path

# BlinkStick 1.2.0 references collections.Callable, removed in Python 3.10.
setattr(collections, "Callable", collections.abc.Callable)
from blinkstick import blinkstick

LOG = logging.getLogger("blinkstick-status")
VALID_STATES = {"idle", "thinking", "tool_use", "permission"}
STATE_PRIORITY = {"permission": 3, "thinking": 2, "tool_use": 2, "idle": 1}
COLOURS = {
    "idle": (120, 55, 0),          # dim amber
    "thinking": (0, 0, 255),       # blue
    "permission": (214, 0, 255),   # vivid purple
    "off": (0, 0, 0),
}


def effective_state(status_dir: Path) -> str:
    """Return the highest-priority valid state across active session files."""
    best = "idle"
    best_priority = 0
    try:
        files = list(status_dir.iterdir())
    except FileNotFoundError:
        return best

    for path in files:
        if not path.is_file() or path.name.startswith("."):
            continue
        try:
            state = path.read_text(encoding="utf-8").strip()
        except OSError:
            continue
        if state in VALID_STATES and STATE_PRIORITY[state] > best_priority:
            best = state
            best_priority = STATE_PRIORITY[state]
    return best


def colour_for(state: str, permission_phase: bool = False) -> tuple[int, int, int]:
    """Map the normalized state to this renderer's reserved LED colour."""
    if state == "tool_use":
        state = "thinking"
    if state == "permission" and permission_phase:
        return (120, 0, 117)  # darker half of the attention pulse
    return COLOURS[state]


def set_led(stick, index: int, colour: tuple[int, int, int]) -> None:
    stick.set_color(index=index, red=colour[0], green=colour[1], blue=colour[2])


def run(status_dir: Path, led_index: int, idle_timeout: float, poll_interval: float) -> None:
    """Watch state files until interrupted, retrying USB discovery on errors."""
    stick = None
    displayed: tuple[int, int, int] | None = None
    last_non_idle = time.monotonic()
    permission_phase = False
    next_permission_pulse = 0.0
    keep_running = True

    def stop(_signal, _frame) -> None:
        nonlocal keep_running
        keep_running = False

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    LOG.info("watching %s; reserved LED index %d; idle timeout %.0fs", status_dir, led_index, idle_timeout)

    while keep_running:
        state = effective_state(status_dir)
        now = time.monotonic()
        if state != "idle":
            last_non_idle = now

        if state == "idle" and now - last_non_idle >= idle_timeout:
            target = COLOURS["off"]
        else:
            if state == "permission" and now >= next_permission_pulse:
                permission_phase = not permission_phase
                next_permission_pulse = now + 0.65
            target = colour_for(state, permission_phase)

        if target != displayed:
            try:
                if stick is None:
                    stick = blinkstick.find_first()
                    if stick is None:
                        raise RuntimeError("no BlinkStick detected")
                    LOG.info("connected %s", stick.get_serial())
                set_led(stick, led_index, target)
                displayed = target
                LOG.info("state=%s rgb=%s", state if target != COLOURS["off"] else "off", target)
            except Exception as exc:
                LOG.warning("BlinkStick write failed: %s", exc)
                stick = None
        time.sleep(poll_interval)


def main() -> None:
    parser = argparse.ArgumentParser(description="BlinkStick status-file renderer")
    parser.add_argument("--status-dir", type=Path, default=Path("/tmp/claude_code_status"))
    parser.add_argument("--led-index", type=int, default=0)
    parser.add_argument("--idle-timeout-seconds", type=float, default=3600)
    parser.add_argument("--poll-seconds", type=float, default=0.2)
    parser.add_argument("--once", action="store_true", help="apply the current state once, then exit")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    if args.led_index < 0:
        parser.error("--led-index must be zero or greater")
    if args.once:
        state = effective_state(args.status_dir)
        stick = blinkstick.find_first()
        if stick is None:
            raise SystemExit("No BlinkStick detected")
        target = colour_for(state)
        set_led(stick, args.led_index, target)
        print(f"state={state} led_index={args.led_index} rgb={target}")
        return
    run(args.status_dir, args.led_index, args.idle_timeout_seconds, args.poll_seconds)


if __name__ == "__main__":
    main()
