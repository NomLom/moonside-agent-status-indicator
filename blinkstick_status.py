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
VALID_STATES = {"idle", "thinking", "tool_use", "permission", "success", "failed", "cancelled"}
# Active work/attention always beats a terminal state from another session.
STATE_PRIORITY = {
    "permission": 5,
    "thinking": 4,
    "tool_use": 4,
    "failed": 3,
    "success": 2,
    "cancelled": 2,
    "idle": 1,
}
COLOURS = {
    "idle": (120, 55, 0),          # dim amber
    "thinking": (0, 0, 255),       # blue
    "permission": (214, 0, 255),   # vivid purple
    "success": (0, 100, 30),       # muted green
    "failed": (130, 0, 0),         # dark red
    "cancelled": (0, 0, 0),        # off
    "off": (0, 0, 0),
}
# Working states animate: a soft blue blob glides smoothly around the strip.
# Fractional head position + per-LED interpolation kills the strobe effect:
# each LED ramps up/down instead of snapping on and off, and a faint base
# glow means no LED ever goes fully dark mid-lap.
ANIMATED_STATES = {"thinking", "tool_use"}
CHASE_FRAME_SECONDS = 0.05         # 20 fps paint for smooth motion
CHASE_CYCLE_SECONDS = 1.6          # one full lap of the strip
CHASE_MAX = 165                    # head peak (dimmed from 255 to cut flash)
CHASE_BASE = 10                    # faint always-on glow, all LEDs
CHASE_SPAN = 1.5                   # LED distance over which brightness ramps down


def walk_colours(pos: float, count: int) -> list[tuple[int, int, int]]:
    """Per-LED colours for one frame: smooth blob centred on fractional head pos."""
    count = max(count, 1)
    colours = []
    for i in range(count):
        d = abs(pos - i)
        d = min(d, count - d)      # circular distance from the head
        ramp = max(0.0, 1.0 - d / CHASE_SPAN)
        colours.append((0, 0, int(CHASE_BASE + (CHASE_MAX - CHASE_BASE) * ramp)))
    return colours


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


def run(status_dir: Path, led_index: int, idle_timeout: float, poll_interval: float,
        led_count: int = 8) -> None:
    """Watch state files until interrupted, retrying USB discovery on errors."""
    stick = None
    displayed: list[tuple[int, int, int]] | None = None
    displayed_state: str | None = None
    last_non_idle = time.monotonic()
    permission_phase = False
    next_permission_pulse = 0.0
    walk_pos: float | None = None
    keep_running = True

    def stop(_signal, _frame) -> None:
        nonlocal keep_running
        keep_running = False

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    LOG.info("watching %s; LEDs %d-%d; idle timeout %.0fs",
             status_dir, led_index, led_index + led_count - 1, idle_timeout)

    while keep_running:
        state = effective_state(status_dir)
        now = time.monotonic()
        if state != "idle":
            last_non_idle = now

        if state == "idle" and now - last_non_idle >= idle_timeout:
            target: list[tuple[int, int, int]] = [COLOURS["off"]] * led_count
        elif state in ANIMATED_STATES:
            if not isinstance(displayed, list) or walk_pos is None:
                walk_pos = 0.0
            else:
                walk_pos = (walk_pos + led_count * CHASE_FRAME_SECONDS / CHASE_CYCLE_SECONDS) % led_count
            target = walk_colours(walk_pos, led_count)
        else:
            if state == "permission" and now >= next_permission_pulse:
                permission_phase = not permission_phase
                next_permission_pulse = now + 0.65
            target = [COLOURS["off"]] * led_count
            target[0] = colour_for(state, permission_phase)
            walk_pos = None

        if target != displayed:
            try:
                if stick is None:
                    stick = blinkstick.find_first()
                    if stick is None:
                        raise RuntimeError("no BlinkStick detected")
                    LOG.info("connected %s", stick.get_serial())
                for offset, colour in enumerate(target):
                    if displayed is not None and displayed[offset] == colour:
                        continue  # only push LEDs whose level changed
                    set_led(stick, led_index + offset, colour)
                displayed = target
                if state != displayed_state:
                    LOG.info("state=%s", state)
                    displayed_state = state
            except Exception as exc:
                LOG.warning("BlinkStick write failed: %s", exc)
                stick = None
                displayed = None
        time.sleep(CHASE_FRAME_SECONDS if state in ANIMATED_STATES else poll_interval)


def main() -> None:
    parser = argparse.ArgumentParser(description="BlinkStick status-file renderer")
    parser.add_argument("--status-dir", type=Path, default=Path("/tmp/claude_code_status"))
    parser.add_argument("--led-index", type=int, default=0)
    parser.add_argument("--led-count", type=int, default=8,
                        help="number of LEDs the chase sweeps (default 8)")
    parser.add_argument("--idle-timeout-seconds", type=float, default=3600)
    parser.add_argument("--poll-seconds", type=float, default=0.2)
    parser.add_argument("--once", action="store_true", help="apply the current state once, then exit")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    if args.led_index < 0:
        parser.error("--led-index must be zero or greater")
    if args.led_count < 1:
        parser.error("--led-count must be at least 1")
    if args.once:
        state = effective_state(args.status_dir)
        stick = blinkstick.find_first()
        if stick is None:
            raise SystemExit("No BlinkStick detected")
        if state in ANIMATED_STATES:
            target = walk_colours(0.5, args.led_count)
        else:
            target = [COLOURS["off"]] * args.led_count
            target[0] = colour_for(state)
        for offset, colour in enumerate(target):
            set_led(stick, args.led_index + offset, colour)
        print(f"state={state} led_index={args.led_index} led_count={args.led_count}")
        return
    run(args.status_dir, args.led_index, args.idle_timeout_seconds, args.poll_seconds,
        args.led_count)


if __name__ == "__main__":
    main()
