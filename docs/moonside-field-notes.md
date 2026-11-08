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
- EC-7 (2026-11-08, reproduced): after a Pi reboot the lamp accepted scans but
  every ATT **write request** (`response=True`) failed — first with connection
  drops/`Unlikely Error 0x8E`, then 0x8E on `COLOR` while `LEDON`/`BRIGH` had
  just landed. Switching to ATT **write commands** (`response=False`, 0.4 s
  pacing) delivered all payloads; solid red was **visually confirmed**. The
  writer now prefers write-commands and falls back to write-requests per
  payload (`LampWriter._write_cmd`). Corollary: while the phone app is
  connected, Pi connections page-timeout — kill phone BT before Pi control.

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

| Priority | State | Trigger | Day palette | Night palette |
| --- | --- | --- | --- | --- |
| 1 | Unreachable | no fresh Spark sample (~10 s) | `THEME.GRADIENT3.220,0,0,20,0,90,` @60 red/deep-blue (user: earmark, promoted) | `THEME.GRADIENT3.220,0,0,15,0,25,` @20 red/deep-violet |
| 2 | High thermal | temp >85°C | `THEME.LAVA1.255,60,0,180,0,0,255,0,0,` @80 (user: "that's our 2") | same command @100 (safety exception, user: "thermal aye 100%") |
| 3 | Above 50% | util >50% | `THEME.BEAT2.0,0,255,0,0,0,` @80 blue/black | `THEME.BEAT2.180,10,0,0,0,0,` @20 |
| 4 | Below 50% | util ≤50% | `THEME.TWINKLE1.0,0,255,0,60,0,` @80 blue+dark green | `THEME.TWINKLE1.180,10,0,0,60,0,` @20 ember+dark green |
| 5 | Extended low | util ≤50% for 30+ min continuous | `THEME.GRADIENT2.8,175,212,8,175,212,255,255,255,` @80 cyan/cyan/white | `THEME.FIRE2.0,0,0,255,0,0,25,0,45,255,0,0,` @20 red/deep-purple embers |

All night palettes sit at `BRIGH20` by user instruction (2026-11-08), except
**thermal night = `BRIGH100`** — the user's explicit safety override so an
overheat is impossible to miss in the dark.

Earlier drafts of this doc mislabelled these (LAVA1 as "calm state 2", BEAT2 as
"nighttime 3 = thermal"). Corrected 2026-11-08 after user restated the state
definitions. The solid-red `COLOR255000000` was only a transport-verification
demo; the user-chosen thermal look is the LAVA1 red/black. Temperature is
Celsius. Thresholds strict: exactly 50% counts as below; transitions need 2 s
stable samples. Day/night switching itself is not implemented yet.

## Theme shortlist (not assigned)

- `THEME.WAVE1.220,0,0,30,0,130,` — same family in WAVE1, tested; superseded by the GRADIENT3 pair.
- `THEME.THEME2.255,80,0,0,120,255,` — **earmarked** for later consideration; no state assigned.
- `THEME.TWINKLE1.200,140,30,0,60,0,` and `THEME.TWINKLE1.255,180,40,0,60,0,` — intermediate variants, superseded by the 180,10,0 selection.
- `THEME.TWINKLE1.255,255,255,0,120,255,`, PULSING1/FIRE2/COLORDROP1/GRADIENT2 variants — exploratory sends, unassigned.

State 1 day and night were **promoted 2026-11-08** (GRADIENT3 red/deep-blue @60,
red/deep-violet @20); the interim `COLOR000032240` is retired. All states now carry
user-selected day and night palettes.

## Controller behaviour (five-state engine, shipped 2026-11-08)

- One event loop, persistent SSH `nvidia-smi -lms 1000` and independent BLE writer.
- States in priority order: unreachable (stale >10 s), thermal (>85 °C), busy
  (>50%), low (≤50%), extended_low (≤50% for 30 continuous min; timer resets on
  busy/thermal/stale). Candidate stable 2 s before commit; the first observation
  commits immediately, so boot shows unreachable until Spark answers.
- Day/night via `daynight.is_day()` from `daynight.json` (Open-Meteo daily cron;
  astral fallback script). A day/night flip is a state change and repaints once.
- Successful unchanged states produce no repeated writes; persistent connection,
  reconnect on demand. BLE failures back off 2,4,8…capped at **600 s** (phone may
  hold the lamp); a new desired state resets the ladder instantly.
- Scan actual MOONSIDE names, prefer configured address among candidates, then
  validate Nordic UART service and TX before any command writes.
- Same-script singleton lock; SIGINT/SIGTERM cancels tasks, terminates and waits for
  SSH (kill only if termination times out), and disconnects BLE.
- No automatic pairing, removal, adapter resets, or hardware-cause speculation.

Hardware-free regression command:
` .venv/bin/python -m unittest discover -s tests -q `
(tests.test_gpu_lamp_states, tests.test_gpu_lamp, tests.test_daynight — plus the
repo's other suites; one pre-existing emoji-font error unrelated to the lamp)

These regressions validate code behavior using injected IO; they do not claim a
successful BLE connection or a visible lamp response. Live BLE diagnosis belongs
to the parent session; this change did not operate the lamp or services.
