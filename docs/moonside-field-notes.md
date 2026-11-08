# Moonside O101 — field notes: connection edge cases

Living document. Every observed failure mode, with evidence and what (if anything) cleared it.
When the set is complete we code around them in `gpu_lamp.py` / `agent_status/moonside.py`.

Device: MOONSIDE-O101, `04:B2:47:8C:E1:F6`, TX char `6e400002-b5a3-f393-e0a9-e50e24dcca9e`.
Controller: lompi2 Pi (BlueZ + bleak 1.x). Wired power-in-line button remote; no phone app involved.

## EC-1 — Connected peripheral goes silent to other centrals
Once any central holds the link, the lamp stops advertising. `find_device_by_address`
returns None; `bluetoothctl` shows nothing. Symptom: "lamp is ON but invisible".
Clears only when the holding central disconnects.
**Code around:** distinguish "not advertising" (held/off) from "advertising, connect refused".

## EC-2 — Stale BlueZ cache after failed connects
After repeated failed attempts, connect returns `[org.bluez.Error.Failed] Operation already
in progress` even with no client processes alive. `bluetoothctl remove <mac>` + adapter
power cycle clears it.
**Code around:** on this error, auto remove-device + adapter toggle, then retry once.

## EC-3 — `for line in proc.stdout` stalls the nvidia-smi stream
Python file-iterator read-ahead buffers block-wise: streaming `nvidia-smi -lms` yields
nothing until a block fills. Use `readline()` in a loop. (Fixed in 50ba138.)

## EC-4 — Advertise-then-refuse flapping (RESOLVED: weak power supply)
Lamp appears in scans intermittently but every `connect` fails: `TimeoutError` /
`BleakError` / `ConnectionAttemptFailed: Page Timeout`. `pair=True` reached SMP then
`AuthenticationCanceled`. Pi radio exonerated by canary: Tile tracker connected fine
on the same adapter within the same minute.
RESOLUTION: user swapped to a stronger power supply — connects then succeed (first try
after swap landed a write at t=35 s; service ran steady after). Interpretation: the BLE
radio's connection-event boost sagged the weak rail and the MCU dropped every CONNECT_IND.
Advisory symptom: RSSI -69 at the failure distance; bring closer when debugging.
**Code around:** on repeated connect refusals, after EC-2/EC-6 recovery, emit an explicit
"radio may be power-starved — check supply/distance" hint instead of blind retries.

## EC-5 — Write ACKs are not proof of execution
Firmware ACKs `THEME.<name>` writes over the air even when the visual doesn't change.
Human eyes remain the ground truth for theme names.

## EC-6 — Adapter wedge after connect storm
Many consecutive failed connects can leave the adapter degraded. Adapter power off/on
(`bluetoothctl power off|on`) is cheap and safe; do it before escalating.
