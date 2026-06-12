from __future__ import annotations

import asyncio
from typing import Iterable

from bleak import BleakClient, BleakScanner

NUS_SERVICE_UUID = "6e400001-b5a3-f393-e0a9-e50e24dcca9e"
NUS_TX_UUID = "6e400002-b5a3-f393-e0a9-e50e24dcca9e"
NAME_PREFIX = "MOONSIDE"

WORKING_CMD = "THEME.BEAT2.255,255,255,0,110,255,"
IDLE_CMD = "COLOR255214170"
INPUT_CMD = "THEME.FIRE2.255,80,190"
SUCCESS_CMD = "COLOR000255000"
FAILED_CMD = "COLOR255000000"
CANCELLED_CMD = "LEDOFF"


def classify_state(states: Iterable[str | None]) -> str:
    active = {s for s in states if s}
    if not active or active == {"idle"}:
        return "idle"
    if "permission" in active:
        return "input"
    if "failed" in active:
        return "failed"
    if "cancelled" in active:
        return "cancelled"
    if "thinking" in active or "tool_use" in active:
        return "working"
    if "success" in active:
        return "success"
    return "idle"


class MoonsideManager:
    def __init__(self, address: str | None = None, scan_timeout: float = 8.0) -> None:
        self.address = address
        self.scan_timeout = scan_timeout
        self.client: BleakClient | None = None

    async def _discover(self):
        if self.address:
            try:
                device = await BleakScanner.find_device_by_address(self.address, timeout=self.scan_timeout, cached=False)
            except TypeError:
                device = await BleakScanner.find_device_by_address(self.address, timeout=self.scan_timeout)
            if device is not None:
                return device
        devices = await BleakScanner.discover(timeout=self.scan_timeout)
        for dev in devices:
            name = (dev.name or "").upper()
            if name.startswith(NAME_PREFIX):
                self.address = dev.address
                return dev
        raise RuntimeError("No MOONSIDE lamp found")

    async def _ensure_connected(self) -> None:
        if self.client and self.client.is_connected:
            return
        device = await self._discover()
        self.client = BleakClient(device, timeout=15.0)
        await self.client.connect()
        if not self.client.is_connected:
            raise RuntimeError("Failed to connect to Moonside lamp")

    async def __aenter__(self) -> "MoonsideManager":
        await self._ensure_connected()
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        if self.client:
            try:
                await self.client.disconnect()
            except Exception:
                pass
            self.client = None

    async def send_command(self, cmd: str) -> None:
        await self._ensure_connected()
        assert self.client is not None
        await self.client.write_gatt_char(NUS_TX_UUID, cmd.encode("utf-8"), response=True)

    async def send_state(self, states: list[str | None]) -> str:
        state = classify_state(states)
        if state == "idle":
            await self.send_command("LEDON")
            await asyncio.sleep(0.3)
            await self.send_command(IDLE_CMD)
        elif state == "input":
            await self.send_command("LEDON")
            await asyncio.sleep(0.3)
            await self.send_command(INPUT_CMD)
        elif state == "working":
            await self.send_command("LEDON")
            await asyncio.sleep(0.3)
            await self.send_command(WORKING_CMD)
        elif state == "success":
            await self.send_command("LEDON")
            await asyncio.sleep(0.3)
            await self.send_command(SUCCESS_CMD)
        elif state == "failed":
            await self.send_command("LEDON")
            await asyncio.sleep(0.3)
            await self.send_command(FAILED_CMD)
        elif state == "cancelled":
            await self.send_command(CANCELLED_CMD)
        return state
