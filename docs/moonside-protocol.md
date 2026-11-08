# Moonside O101 BLE control protocol (reproducible reference)

Verified against developer.moonside.design and on-device on 2026-11-08
(MOONSIDE-O101, ESP32-class). Anything marked *device-verified* was observed on
our unit; the rest is documented vendor behaviour reproduced via BLE.

## Service and characteristics

| Role | UUID | Notes |
|---|---|---|
| Nordic UART service | `6e400001-b5a3-f393-e0a9-e50e24dcca9e` | validate before writing |
| Write (TX) | `6e400002-b5a3-f393-e0a9-e50e24dcca9e` | ASCII/UTF-8 commands, `response=True` *device-verified* |
| Read (RX) | `6e400003-b5a3-f393-e0a9-e50e24dcca9e` | returns `version|mac`, e.g. `1.0|AA:BB:...` |

Commands are plain text. Write with `response=True`; the ATT ACK confirms
transport only — the firmware never acknowledges application execution, and
unknown commands silently do nothing. Visual confirmation is the only proof.

## Command grammar

| Command | Form | Notes |
|---|---|---|
| Power on | `LEDON` | restores saved mode/brightness/colors |
| Power off | `LEDOFF` | blacks all LEDs, stores `ledpower false` |
| Brightness | `BRIGHnnn` | recommended 10–100; also re-applies saved mode |
| Solid colour | `COLORrrrgggbbb` | three padded decimal digits per channel, 000–255, *device-verified* (`COLOR255000000` → solid red) |
| Theme | `THEME.<CODE>.<r,g,b,...,>` | trailing comma; params are comma-separated decimal RGB channels |
| Pixel | `PIXEL,<index>,<rrrgggbbb>,` | 172-LED array; stores pixel colour |
| Back LEDs | `BACKLED<n>,0,<rrrgggbbb>,` | n=1 pixels 0–15, n=2 pixels 16–31 |
| Buttons | `BTN1..BTN5` | brightness+, brightness−, power toggle, next mode, previous mode |

Theme codes in the official catalog: `THEME1-5`, `WAVE1`, `BEAT1-3`,
`GRADIENT1-3`, `RAINBOW1-3`, `TWINKLE1`, `FIRE1-2`, `COLORDROP1`, `LAVA1`,
`PULSING1`, `PALETTE1-2`; sound-reactive variants `M<n>` (mic-driven, not
useful headless). Parameter count varies per code (FIRE2 takes four RGB
triplets: background + three effect colours). Anything not in the catalog is
accepted by ATT and silently ignored — never trust an ACK.

## Connection behaviour (what burns hours if undocumented)

- **No bonding required.** `BleakClient(device, pair=False)` controls it; forced
  pairing fails `AuthenticationCanceled`/`AuthenticationFailed` on our unit.
  *device-verified*
- **One central at a time.** While any central holds the link it stops
  advertising; the vendor phone app auto-reconnects on Bluetooth-on. Scan
  misses usually mean the phone has it. *device-verified*
- **Advertised name** `MOONSIDE-O101`; discover by name prefix `MOONSIDE-*`,
  prefer a configured address among candidates. Cached BlueZ entries are not
  live discovery.
- Device persists state to flash; power-on restores the saved mode.

## Python (bleak ≥2.1; device-verified on bleak 3.1.0, Python 3.14)

```python
import asyncio
from bleak import BleakScanner, BleakClient

TX = "6e400002-b5a3-f393-e0a9-e50e24dcca9e"

async def main():
    d = await BleakScanner.find_device_by_name("MOONSIDE-O101", timeout=15)
    async with BleakClient(d, timeout=20, pair=False) as c:
        for cmd in ("LEDON", "BRIGH80", "COLOR255000000"):
            await c.write_gatt_char(TX, cmd.encode(), response=True)

asyncio.run(main())
```

System prerequisites: `bluez` installed and bluetoothd running, otherwise bleak
fails obscurely. Host: lompi2 (Pi 5); onboard Wi-Fi + BLE share one chip —
suspect RF contention before blaming the lamp when scans intermittently miss.

## Day/night data

Primary: `daynight_fetch.py` (Open-Meteo sunrise/sunset, Cambridge UK,
`Europe/London`) → `daynight.json`, refreshed daily by Hermes cron.
Offline fallback: `scripts/daynight_fallback.py` (astral, same geometric
sunrise/sunset; within a minute of the primary — cross-checked 2026-11-08).
`daynight.is_day()` is inclusive of sunrise and sunset; missing/corrupt file
defaults to day.
