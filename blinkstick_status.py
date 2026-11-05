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
import hashlib
import logging
import random
import signal
import time
from logging.handlers import RotatingFileHandler
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
CHASE_BASE = 22                    # passive glow floor — always visible, never black
CHASE_SPAN = 1.5                   # LED distance over which brightness ramps down
STATE_SETTLE_SECONDS = 0.3         # ignore state blips shorter than this


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


# Random-pulse "thinking" animation: 2-3 LEDs at a time breathe up, hold for
# a random beat, then fade out and respawn elsewhere.  A short cooldown keeps
# a just-dead LED from reflickering in place (the strobe trap).
PULSE_FRAME_SECONDS = 1 / 30
PULSE_MIN_ACTIVE = 2
PULSE_MAX_ACTIVE = 3
PULSE_THIRD_CHANCE = 0.04          # per-frame odds of a 3rd pulse => ~2.5 avg
PULSE_IN_SECONDS = 0.18
PULSE_OUT_SECONDS = 0.28
PULSE_HOLD_MIN = 0.10
PULSE_HOLD_MAX = 0.50
PULSE_COOLDOWN = 0.25


def _smoothstep(x: float) -> float:
    x = min(max(x, 0.0), 1.0)
    return x * x * (3.0 - 2.0 * x)


class StateStabiliser:
    """Suppress transient states: a new state must persist SETTLE seconds
    before it may repaint the strip.  Session files get rewritten constantly,
    and each accepted blip would otherwise flash the whole strip."""

    def __init__(self, settle_seconds: float, initial: str = "idle") -> None:
        self.settle_seconds = settle_seconds
        self.stable = initial
        self.pending: str | None = None
        self.pending_since = 0.0

    def update(self, raw: str, now: float) -> str:
        if raw == self.stable:
            self.pending = None
        elif raw != self.pending:
            self.pending, self.pending_since = raw, now
        elif now - self.pending_since >= self.settle_seconds:
            self.stable, self.pending = raw, None
        return self.stable


class Pulse:
    """One LED's breathe-up / hold / fade-out schedule."""

    __slots__ = ("led", "t0", "t1", "t2", "t3", "peak")

    def __init__(self, led: int, t0: float, t1: float, t2: float, t3: float,
                 peak: float) -> None:
        self.led, self.t0, self.t1, self.t2, self.t3, self.peak = led, t0, t1, t2, t3, peak


class PulseEngine:
    """Stateful random-pulse painter; frame(now) returns the strip colours.

    min_active/max_active size how many LEDs of this engine are alive at once
    (the fleet default is the old 2-3 feel; per-agent engines use 1-2).
    """

    def __init__(self, count: int, rng: random.Random | None = None,
                 min_active: int = PULSE_MIN_ACTIVE, max_active: int = PULSE_MAX_ACTIVE,
                 third_chance: float = PULSE_THIRD_CHANCE) -> None:
        self.count = max(count, 1)
        self.rng = rng or random.Random()
        self.min_active = min_active
        self.max_active = max_active
        self.third_chance = third_chance
        self.pulses: dict[int, Pulse] = {}
        self.cooldowns: dict[int, float] = {}

    def _level(self, p: Pulse, now: float) -> int:
        if now < p.t1:
            frac = _smoothstep((now - p.t0) / (p.t1 - p.t0))
        elif now < p.t2:
            frac = 1.0
        else:
            frac = 1.0 - _smoothstep((now - p.t2) / (p.t3 - p.t2))
        return int(CHASE_BASE + (p.peak - CHASE_BASE) * frac)

    def _spawn(self, now: float) -> None:
        free = [i for i in range(self.count)
                if i not in self.pulses and now - self.cooldowns.get(i, -1.0) > PULSE_COOLDOWN]
        if not free:
            return
        led = self.rng.choice(free)
        t1 = now + PULSE_IN_SECONDS
        t2 = t1 + self.rng.uniform(PULSE_HOLD_MIN, PULSE_HOLD_MAX)
        t3 = t2 + PULSE_OUT_SECONDS
        self.pulses[led] = Pulse(led, now, t1, t2, t3,
                                 self.rng.uniform(0.75, 1.0) * CHASE_MAX)

    def frame(self, now: float) -> list[tuple[int, int, int]]:
        for led in [l for l, p in self.pulses.items() if now >= p.t3]:
            del self.pulses[led]
            self.cooldowns[led] = now
        for _ in range(max(0, self.min_active - len(self.pulses))):
            self._spawn(now)
        if len(self.pulses) < self.max_active and self.rng.random() < self.third_chance:
            self._spawn(now)
        levels = [CHASE_BASE] * self.count
        for p in self.pulses.values():
            levels[p.led] = max(levels[p.led], self._level(p, now))
        return [(0, 0, level) for level in levels]


def active_sessions(status_dir: Path, max_state_age: float = 1800.0) -> list[tuple[str, str]]:
    """Return (session_name, state) for live sessions.

    Crashed sessions leave their state file behind forever, so an active
    (non-idle) file whose mtime is older than max_state_age is treated as a
    dead writer and ignored.  Stale idle files are harmless: idle is the
    baseline anyway.
    """
    now = time.time()
    sessions: list[tuple[str, str]] = []
    try:
        files = list(status_dir.iterdir())
    except FileNotFoundError:
        return sessions

    for path in files:
        if not path.is_file() or path.name.startswith("."):
            continue
        try:
            state = path.read_text(encoding="utf-8").strip()
        except OSError:
            continue
        if state not in VALID_STATES or state == "idle":
            continue
        try:
            if now - path.stat().st_mtime > max_state_age:
                continue
        except OSError:
            continue
        sessions.append((path.name, state))
    return sessions


def led_for(session_name: str, led_count: int) -> int:
    """Stable LED assignment: the same session always gets the same LED."""
    digest = hashlib.sha256(session_name.encode("utf-8")).digest()
    return digest[0] % max(led_count, 1)


def paint_sessions(sessions: list[tuple[str, str]], led_count: int,
                   engines: dict, now: float,
                   permission_phase: bool) -> list[tuple[int, int, int]]:
    """Per-agent frame.

    Working agents (thinking/tool_use) each run a private 1-2 pulse engine
    whose breaths respawn at random LEDs, so their blue wanders the strip.
    A permission agent is pinned at its stable LED and pulses bright purple —
    it must be findable, not hidden in the wander — and wins any collision
    with blue.  Terminal states sit pinned and steady in soft green/red.
    """
    blue_levels = [CHASE_BASE] * led_count
    for name, state in sessions:
        if state not in ANIMATED_STATES:
            continue
        engine = engines.get(("blue", name))
        if engine is None:
            seed = int.from_bytes(hashlib.sha256(name.encode()).digest()[:8], "big")
            engine = PulseEngine(led_count, rng=random.Random(seed),
                                 min_active=1, max_active=2, third_chance=0.15)
            engines[("blue", name)] = engine
        for i, colour in enumerate(engine.frame(now)):
            blue_levels[i] = max(blue_levels[i], colour[2])
    target: list[tuple[int, int, int]] = [(0, 0, level) for level in blue_levels]
    for name, state in sessions:
        if state == "cancelled":
            continue
        led = led_for(name, led_count)
        if state == "permission":
            target[led] = (120, 0, 117) if permission_phase else COLOURS["permission"]
        elif state not in ANIMATED_STATES:
            target[led] = COLOURS[state]  # soft steady green/red
    return target


def effective_state(status_dir: Path, max_state_age: float = 1800.0) -> str:
    """Return the highest-priority valid state across active session files."""
    best = "idle"
    best_priority = 0
    for _name, state in active_sessions(status_dir, max_state_age):
        if STATE_PRIORITY[state] > best_priority:
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
        led_count: int = 8, animation: str = "multi", max_state_age: float = 1800.0) -> None:
    """Watch state files until interrupted, retrying USB discovery on errors."""
    stick = None
    displayed: list[tuple[int, int, int]] | None = None
    displayed_state: str | None = None
    last_non_idle = time.monotonic()
    permission_phase = False
    next_permission_pulse = 0.0
    walk_pos: float | None = None
    pulse_engine = PulseEngine(led_count)
    stabiliser = StateStabiliser(STATE_SETTLE_SECONDS)
    session_stabilisers: dict[str, StateStabiliser] = {}
    agent_engines: dict = {}
    keep_running = True

    def stable_sessions(now: float) -> list[tuple[str, str]]:
        confirmed = []
        for name, raw in active_sessions(status_dir, max_state_age):
            stab = session_stabilisers.setdefault(name, StateStabiliser(STATE_SETTLE_SECONDS))
            state = stab.update(raw, now)
            if state != "idle" and state == raw:
                confirmed.append((name, state))
        return confirmed

    def stop(_signal, _frame) -> None:
        nonlocal keep_running
        keep_running = False

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    LOG.info("watching %s; LEDs %d-%d; idle timeout %.0fs",
             status_dir, led_index, led_index + led_count - 1, idle_timeout)

    while keep_running:
        now = time.monotonic()
        if now >= next_permission_pulse:
            permission_phase = not permission_phase
            next_permission_pulse = now + 0.65

        sessions: list[tuple[str, str]] | None = None
        if animation == "multi":
            sessions = stable_sessions(now)
            state = (max(sessions, key=lambda ns: STATE_PRIORITY[ns[1]])[1]
                     if sessions else "idle")
        else:
            state = stabiliser.update(effective_state(status_dir, max_state_age), now)
        if state != "idle":
            last_non_idle = now

        if state == "idle" and now - last_non_idle >= idle_timeout:
            target: list[tuple[int, int, int]] = [COLOURS["off"]] * led_count
        elif animation == "multi" and sessions:
            target = paint_sessions(sessions, led_count, agent_engines, now, permission_phase)
        elif state in ANIMATED_STATES:
            if animation == "pulse":
                target = pulse_engine.frame(now)
            else:
                if walk_pos is None:
                    walk_pos = 0.0
                else:
                    walk_pos = (walk_pos + led_count * CHASE_FRAME_SECONDS / CHASE_CYCLE_SECONDS) % led_count
                target = walk_colours(walk_pos, led_count)
        else:
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
                    LOG.info("state=%s live=%s", state,
                             len(sessions) if sessions is not None else "-")
                    displayed_state = state
            except Exception as exc:
                LOG.warning("BlinkStick write failed: %s", exc)
                stick = None
                displayed = None
        if animation == "multi" or state in ANIMATED_STATES:
            time.sleep(PULSE_FRAME_SECONDS if animation != "walk" else CHASE_FRAME_SECONDS)
        else:
            time.sleep(poll_interval)


def main() -> None:
    parser = argparse.ArgumentParser(description="BlinkStick status-file renderer")
    parser.add_argument("--status-dir", type=Path, default=Path("/tmp/claude_code_status"))
    parser.add_argument("--led-index", type=int, default=0)
    parser.add_argument("--led-count", type=int, default=8,
                        help="number of LEDs the animation sweeps (default 8)")
    parser.add_argument("--animation", choices=("multi", "walk", "pulse"), default="multi",
                        help="working-state animation: per-agent LEDs (multi), gliding blob, or random pulses")
    parser.add_argument("--idle-timeout-seconds", type=float, default=3600)
    parser.add_argument("--poll-seconds", type=float, default=0.2)
    parser.add_argument("--state-max-age-seconds", type=float, default=1800.0,
                        help="ignore active-state files not updated within this window")
    parser.add_argument("--once", action="store_true", help="apply the current state once, then exit")
    parser.add_argument("--log-file", type=Path,
                        default=Path.home() / ".local/share/blinkstick/status.log",
                        help="transition diary (state changes only, rotated)")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    if args.log_file:
        args.log_file.parent.mkdir(parents=True, exist_ok=True)
        _handler = RotatingFileHandler(args.log_file, maxBytes=256 * 1024, backupCount=2)
        _handler.setFormatter(logging.Formatter("%(asctime)s %(name)s %(message)s"))
        LOG.addHandler(_handler)
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
        args.led_count, args.animation)


if __name__ == "__main__":
    main()
