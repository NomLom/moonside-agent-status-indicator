#!/usr/bin/env python3
"""One isolated scan/connect/write probe. No resets, pairing or cache removal."""
import asyncio
import sys
from bleak import BleakClient, BleakScanner

SERVICE = '6e400001-b5a3-f393-e0a9-e50e24dcca9e'
TX = '6e400002-b5a3-f393-e0a9-e50e24dcca9e'

async def main():
    devices = await BleakScanner.discover(timeout=20, return_adv=True, adapter='hci0')
    lamps = []
    for device, adv in devices.values():
        print(f'SCAN {device.address} name={device.name!r} rssi={adv.rssi}', flush=True)
        if (adv.local_name or device.name or '').upper().startswith('MOONSIDE-'):
            lamps.append(device)
    if len(lamps) != 1:
        print(f'Cannot select lamp: observed {len(lamps)} MOONSIDE devices', flush=True)
        return 2
    async with BleakClient(lamps[0], timeout=20) as client:
        if client.services.get_service(SERVICE) is None:
            raise RuntimeError('Nordic UART service missing; refusing to write')
        print(f'CONNECTED {lamps[0].address}', flush=True)
        for command in ('LEDON', 'BRIGH80', 'COLOR255000000'):
            await client.write_gatt_char(TX, command.encode('ascii'), response=True)
            print(f'ATT DELIVERED {command}; visual verification still required', flush=True)
            await asyncio.sleep(1)
    return 0

if __name__ == '__main__':
    try:
        sys.exit(asyncio.run(main()))
    except Exception as error:
        print(f'PROBE FAILED {type(error).__name__}: {error}', flush=True)
        raise
