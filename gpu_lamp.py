#!/usr/bin/env python3
"""Five-state, day/night GPU mood lamp: spark-lom nvidia-smi stream -> Moonside O101.

State priority (user map, docs/moonside-field-notes.md): 1 unreachable (no fresh
sample ~10 s), 2 thermal (>85 C), 3 busy (util >50%), 4 low (util <=50%), 5
extended low (util <=50% for 30 continuous min). Day/night from daynight.json
(follows fetched sunrise/sunset; defaults to day). One asyncio loop: telemetry
reader independent of a persistent BLE writer; one write per committed change;
never repaints an unchanged delivered state; BLE retry backoff caps at 10 min so
a phone grabbing the lamp causes silence, not radio hammering.

ATT delivery is not visual verification. No pairing, no device removal, no
adapter resets.
"""
import asyncio
import contextlib
import fcntl
import os
from pathlib import Path
import signal
import time

import daynight

GPU_HOST = 'slide@192.168.0.90'
LAMP = '04:B2:47:8C:E1:F6'
SERVICE = '6e400001-b5a3-f393-e0a9-e50e24dcca9e'
TX = '6e400002-b5a3-f393-e0a9-e50e24dcca9e'
HIGH_UTIL = 50
TEMP_RED = 85
DEBOUNCE_S = 2.0
SAMPLE_MS = 1000
STALE_AFTER_S = 10.0
EXTENDED_LOW_S = 30 * 60.0
BACKOFF_CAP_S = 600.0  # 10 min: user may hold the lamp on the phone

STATE_PRIORITY = ['unreachable', 'thermal', 'busy', 'low', 'extended_low']

# (state, period) -> BLE payload. Approved entries carry the exact user-selected
# strings; interim entries are documented solid COLORs awaiting user approval.
PALETTES = {
    ('thermal', 'day'):   {'command': 'THEME.LAVA1.255,60,0,180,0,0,255,0,0,', 'brightness': 80},
    ('thermal', 'night'): {'command': 'THEME.LAVA1.255,60,0,180,0,0,255,0,0,', 'brightness': 100},
    ('busy', 'day'):      {'command': 'THEME.BEAT2.0,0,255,0,0,0,', 'brightness': 80},
    ('busy', 'night'):    {'command': 'THEME.BEAT2.180,10,0,0,0,0,', 'brightness': 20},
    ('low', 'day'):       {'command': 'THEME.TWINKLE1.0,0,255,0,60,0,', 'brightness': 80},
    ('low', 'night'):     {'command': 'THEME.TWINKLE1.180,10,0,0,60,0,', 'brightness': 20},
    ('extended_low', 'day'):   {'command': 'THEME.GRADIENT2.8,175,212,8,175,212,255,255,255,', 'brightness': 80},
    ('extended_low', 'night'): {'command': 'THEME.FIRE2.0,0,0,255,0,0,25,0,45,255,0,0,', 'brightness': 20},
    # Final user selections 2026-11-08: GRADIENT3 red pair, blue day / purple night.
    ('unreachable', 'day'):   {'command': 'THEME.GRADIENT3.220,0,0,20,0,90,', 'brightness': 60},
    ('unreachable', 'night'): {'command': 'THEME.GRADIENT3.220,0,0,15,0,25,', 'brightness': 20},
}


def log(message):
    print(time.strftime('%H:%M:%S') + ' ' + message, flush=True)


class Controller:
    """Pure state machine; all IO hooks are injectable for tests."""

    def __init__(self, is_day=daynight.is_day, stale_after=STALE_AFTER_S,
                 extended_after=EXTENDED_LOW_S, debounce=DEBOUNCE_S):
        self.is_day = is_day
        self.stale_after = stale_after
        self.extended_after = extended_after
        self.debounce = debounce
        self.last_reading = None        # (util, temp) of newest fresh sample
        self.last_reading_at = None
        self.low_since = None
        self.desired = None             # committed (state, period)
        self.delivered = None
        self._candidate = None
        self._candidate_since = 0.0
        self.retry_at = 0.0
        self.failures = 0
        self._attempted = None

    def record(self, util, temp, now):
        self.last_reading = (util, temp)
        self.last_reading_at = now

    def _evaluate(self, now):
        if self.last_reading is None or now - self.last_reading_at > self.stale_after:
            self.low_since = None
            return 'unreachable'
        util, temp = self.last_reading
        if temp > TEMP_RED:
            self.low_since = None
            return 'thermal'
        if util > HIGH_UTIL:
            self.low_since = None
            return 'busy'
        if self.low_since is None:
            self.low_since = now
        return 'extended_low' if now - self.low_since >= self.extended_after else 'low'

    def observe(self, now, util=None, temp=None):
        if util is not None:
            self.record(util, temp, now)
        period = 'night' if not self.is_day(now=None) else 'day'
        candidate = (self._evaluate(now), period)
        if candidate != self._candidate:
            self._candidate, self._candidate_since = candidate, now
            if self.desired is None:      # first ever observation: commit immediately
                self.desired = candidate
        elif candidate != self.desired and now - self._candidate_since >= self.debounce:
            self.desired = candidate

    async def deliver(self, writer, now=None):
        if self.desired is None:
            return
        realtime = now is None
        now = time.monotonic() if realtime else now
        if self.desired != self._attempted:
            self._attempted = self.desired
            self.retry_at, self.failures = 0.0, 0
        if self.desired == self.delivered or now < self.retry_at:
            return
        target = self.desired
        try:
            await writer.send(target)
        except Exception as exc:
            self.delivered = None  # partial sequence leaves physical state unknown
            self.failures += 1
            delay = min(BACKOFF_CAP_S, 2 ** min(self.failures, 10))
            self.retry_at = (time.monotonic() if realtime else now) + delay
            log(f'BLE delivery failed ({type(exc).__name__}: {exc}); retry in {delay}s')
        else:
            self.delivered = target
            self.failures = 0
            log(f'ATT delivered: {target[0]}/{target[1]}; visual effect not verified')


class LampWriter:
    def __init__(self, address=LAMP, scan=None, client_factory=None):
        if scan is None or client_factory is None:
            from bleak import BleakClient, BleakScanner
            scan = scan or BleakScanner.discover
            client_factory = client_factory or BleakClient
        self.address, self.scan, self.client_factory = address, scan, client_factory
        self.client = None

    async def close(self):
        client, self.client = self.client, None
        if client is not None:
            with contextlib.suppress(Exception):
                await asyncio.wait_for(client.disconnect(), 5)

    async def connect(self):
        if self.client is not None and self.client.is_connected:
            return
        await self.close()
        found = await self.scan(timeout=8, return_adv=True)
        devices = [device for device, adv in found.values()
                   if (adv.local_name or device.name or '').upper().startswith('MOONSIDE')]
        devices.sort(key=lambda d: d.address.upper() != self.address.upper())
        if not devices:
            raise RuntimeError('No MOONSIDE name observed in scan')
        for device in devices:
            self.client = self.client_factory(device, timeout=15)
            try:
                await self.client.connect()
                service = self.client.services.get_service(SERVICE)
                if not self.client.is_connected or service is None or not any(
                        char.uuid.lower() == TX for char in service.characteristics):
                    raise RuntimeError('MOONSIDE lacks Nordic UART service/TX')
                return
            except Exception:
                await self.close()
                if device is devices[-1]:
                    raise

    async def _write_cmd(self, client, data):
        # Post-reboot O101 (EC-7, 2026-11-08) rejects ATT write *requests*
        # (0x8E Unlikely Error) but accepts write *commands*. Prefer the
        # command; fall back to a request if the command path errors.
        try:
            await client.write_gatt_char(TX, data, response=False)
        except BaseException:
            await client.write_gatt_char(TX, data, response=True)
        await asyncio.sleep(0.4)

    async def send(self, key):
        palette = PALETTES[key]
        try:
            await self.connect()
            client = self.client
            if client is None:
                raise RuntimeError('writer not connected')
            await self._write_cmd(client, b'LEDON')
            await self._write_cmd(client, f"BRIGH{palette['brightness']}".encode())
            await self._write_cmd(client, palette['command'].encode())
        except BaseException:
            await self.close()
            raise


async def reap(proc):
    if proc.returncode is None:
        with contextlib.suppress(ProcessLookupError):
            proc.terminate()
        try:
            await asyncio.wait_for(proc.wait(), 5)
        except asyncio.TimeoutError:
            with contextlib.suppress(ProcessLookupError):
                proc.kill()
            await proc.wait()
    else:
        await proc.wait()


async def telemetry(controller):
    """Drain SSH independently of BLE; record() only — controller ages stale itself."""
    while True:
        proc = None
        try:
            proc = await asyncio.create_subprocess_exec(
                'ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=10',
                '-o', 'ServerAliveInterval=15', '-o', 'ServerAliveCountMax=2', GPU_HOST,
                'nvidia-smi --query-gpu=utilization.gpu,temperature.gpu '
                f'--format=csv,noheader,nounits -lms {SAMPLE_MS}',
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
            while line := await proc.stdout.readline():
                try:
                    util, temp = [int(v.strip()) for v in line.decode().split(',')[:2]]
                except (ValueError, UnicodeError):
                    continue
                controller.record(util, temp, time.monotonic())
        except (OSError, ValueError) as exc:
            log(f'SSH telemetry failed: {exc}')
        finally:
            if proc is not None:
                await reap(proc)
        log('SSH stream ended; retry in 3s')
        await asyncio.sleep(3)


@contextlib.contextmanager
def singleton(path=None):
    path = path or Path(os.environ.get('XDG_RUNTIME_DIR', Path.home() / '.cache')) / 'gpu-lamp.lock'
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a') as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError('gpu_lamp.py is already running') from exc
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


async def run():
    controller, writer = Controller(), LampWriter()
    reader = asyncio.create_task(telemetry(controller))
    task = asyncio.current_task()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, task.cancel)
    log(f'gpu-lamp up: states={">".join(STATE_PRIORITY)} stale>{STALE_AFTER_S}s '
        f'extended>{int(EXTENDED_LOW_S)}s backoff cap {int(BACKOFF_CAP_S)}s')
    try:
        while True:
            controller.observe(now=time.monotonic())
            await controller.deliver(writer)
            await asyncio.sleep(0.25)
    finally:
        reader.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await reader
        await writer.close()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.remove_signal_handler(sig)


def main():
    with singleton():
        with contextlib.suppress(asyncio.CancelledError, KeyboardInterrupt):
            asyncio.run(run())


if __name__ == '__main__':
    main()
