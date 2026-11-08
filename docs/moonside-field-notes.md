# Moonside O101 — evidence and operating notes

Device: MOONSIDE-O101, `04:B2:47:8C:E1:F6`.
Nordic UART service `6e400001-b5a3-f393-e0a9-e50e24dcca9e`, TX
`6e400002-b5a3-f393-e0a9-e50e24dcca9e`. Current installed Bleak: 3.1.0.

## Observations (not a root-cause diagnosis)

- User reports the phone controls the lamp immediately. Phone Bluetooth is otherwise
  off during Pi tests. This contradicts claiming the lamp is permanently wedged.
- The Pi discovers Tile Smart Tags, but observes MOONSIDE only rarely. Discovering
  another peripheral proves some scanning works; it does not exonerate the adapter
  or establish why this particular lamp is rarely observed.
- Earlier attempts reported timeouts, connection failures, `Operation already in
  progress`, and `AuthenticationCanceled` with pairing requested. These are error
  observations, not proof of a stale bond or its cause.
- Earlier notes reported intermittent writes accepted without visible changes,
  and the lamp holding its last purple color. ATT delivery alone cannot establish
  execution of the command or the displayed mode.
- Earlier notes associated a power-supply change with a later successful write.
  That timing is not a controlled test and does not establish a weak PSU, voltage
  sag, dropped CONNECT_IND, or resolution of the underlying connection problem.
- Earlier RSSI of -69 was reported. One RSSI value does not identify a fault.

## Corrections to prior claims

The previous stale-bond/cache, adapter-wedge, PSU, firmware-wedge, manual-controller
lock, and RSSI diagnoses were unsupported hypotheses, not established causes.
A connected central can affect availability, but no exclusive-link explanation has
been demonstrated for the present observations. Neither physical-button reset
sequences nor claims of saved-mode persistence were established by these tests.
Do not infer that removing a device, pairing, resetting the adapter, changing power,
or holding buttons is necessary or safe recovery for this incident.

The old claim that Python text-file iteration necessarily buffers until a block
fills and causes this SSH stall was not established. Both iteration and readline
can stream lines. The demonstrated architectural risk was consuming telemetry in
the same control path as blocking BLE retries: the producer can continue while the
consumer is stopped, leaving old samples to process later. The new independent
async SSH reader continuously updates the controller; the writer reads only the
current debounced desired mode, with no FIFO of historical mode requests.

## Known GPU states and wire commands

Automatic GPU modes use only documented, user-visual-tested commands listed
below. The user visually confirmed `LEDON`, `BRIGH80`, then `COLOR255000000`
produced solid red on 2026-11-08 — used once to prove the transport path; it is
not an assigned state palette. Unverified theme strings are not sent.

| Priority | State | Trigger | Approved palette |
| --- | --- | --- | --- |
| 1 | Unreachable | no fresh Spark sample (~10 s) | `COLOR000032240` **interim** (user selection pending) | same @BRIGH30 **interim** |
| 2 | High thermal | temp >85°C | **LAVA1 red/black** `THEME.LAVA1.255,60,0,180,0,0,255,0,0,` (user: "that's our 2") | same command @BRIGH50 (day re-use, interim) |
| 3 | Above 50% | util >50% | day: `THEME.BEAT2.0,0,255,0,0,0,` blue/black; night: `THEME.BEAT2.180,10,0,0,0,0,` @`BRIGH50` |
| 4 | Below 50% | util ≤50% | day: `THEME.TWINKLE1.0,0,255,0,60,0,` blue+dark green; night: `THEME.TWINKLE1.180,10,0,0,60,0,` ember+dark green |
| 5 | Extended low | util ≤50% for 30+ min continuous | day: `THEME.GRADIENT2.8,175,212,8,175,212,255,255,255,` cyan/cyan/white; night: `THEME.FIRE2.0,0,0,255,0,0,25,0,45,255,0,0,` @`BRIGH50` red/deep-purple embers |

Earlier drafts of this doc mislabelled these (LAVA1 as "calm state 2", BEAT2 as
"nighttime 3 = thermal"). Corrected 2026-11-08 after user restated the state
definitions. The solid-red `COLOR255000000` was only a transport-verification
demo; the user-chosen thermal look is the LAVA1 red/black. Temperature is
Celsius. Thresholds strict: exactly 50% counts as below; transitions need 2 s
stable samples. Day/night switching itself is not implemented yet.

## Theme shortlist (not assigned)

- `THEME.BEAT2.180,10,0,0,0,0,` at `BRIGH50` — **user-approved "nighttime 3"** after iterative visual tuning. This is a night-palette label, not permission to replace the high-thermal solid-red safety override. No time-of-day switching implemented yet.
- `THEME.BEAT2.0,0,255,0,0,0,` — blue + black BEAT2, **earmarked for "daytime 3"** in the user's words. This is a candidate only; do **not** replace state 3's solid-red high-thermal override without explicit clarification.
- `THEME.THEME2.255,80,0,0,120,255,` — delivered over BLE and **earmarked by user** for later consideration; no state assigned yet. Do not add it to the automatic state map until the user chooses its state.
- `THEME.TWINKLE1.180,10,0,0,60,0,` — night ember + dark green TWINKLE1 — **user-approved "nighttime 4"**: the palette for state 4 (Spark unreachable), night variant. State 4 logic itself is still unimplemented.
- `THEME.TWINKLE1.200,140,30,0,60,0,` and `THEME.TWINKLE1.255,180,40,0,60,0,` — intermediate variants, superseded by the 180,10,0 selection.

## Controller behavior

- One event loop, persistent SSH `nvidia-smi -lms 1000` and independent BLE writer.
- Strict util >50 busy, temp >85 hot (priority); candidate stable for monotonic 2s.
- Startup has no desired state until a live GPU sample has held for the 2-second
  debounce; with Spark offline, no BLE command is sent. Delivery remains unknown
  until the complete write succeeds.
- Successful unchanged modes produce no repeated writes. The connection is retained
  without periodic GATT writes; subsequent mode changes reconnect if disconnected.
- Failed unchanged modes retry with 2/4/8/16/30s capped backoff after each attempt.
  A newly desired mode does not wait for the old mode's backoff.
- Scan actual MOONSIDE names, prefer configured address among candidates, then
  validate Nordic UART service and TX before any command writes.
- Same-script singleton lock; SIGINT/SIGTERM cancels tasks, terminates and waits for
  SSH (kill only if termination times out), and disconnects BLE.
- No automatic pairing, removal, adapter resets, or hardware-cause speculation.

Hardware-free regression command:
` .venv/bin/python -m unittest discover -s tests -p test_gpu_lamp.py -v `

These regressions validate code behavior using injected IO; they do not claim a
successful BLE connection or a visible lamp response. Live BLE diagnosis belongs
to the parent session; this change did not operate the lamp or services.
