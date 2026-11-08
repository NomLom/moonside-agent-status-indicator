#!/usr/bin/env python3
"""GPU mood lamp: spark-lom nvidia-smi stream -> Moonside O101.

1 Hz samples over one persistent SSH exec; 2 s debounce before mode flips.
  util > HIGH_UTIL (50)   -> working beat theme
  util <= HIGH_UTIL       -> calm gradient theme
  temp > TEMP_RED (85 C)  -> solid RED overrides all
BLE is single-connection and flaky: one connect per mode change, with retries.
"""
import subprocess, threading, time, asyncio, collections, sys
from bleak import BleakClient

GPU_HOST = "slide@192.168.0.90"
LAMP = "04:B2:47:8C:E1:F6"
TX = "6e400002-b5a3-f393-e0a9-e50e24dcca9e"
HIGH_UTIL = 50
TEMP_RED = 85
DEBOUNCE_S = 2.0
SAMPLE_MS = 1000

CMD = {
    "busy":  "THEME.BEAT2.0,0,140,53,53,170,",
    "calm":  "THEME.GRADIENT3.120,0,117,0,0,0,214,0,255",
    "hot":   "COLOR255000000",
}

def log(msg):
    print(time.strftime("%H:%M:%S") + " " + msg, flush=True)

def ssh_stream(stop):
    """Yield (util, temp) tuples from one long-lived nvidia-smi -lms loop."""
    while not stop.is_set():
        proc = subprocess.Popen(
            ["ssh", "-o", "BatchMode=yes", "-o", "ServerAliveInterval=15", GPU_HOST,
             f"nvidia-smi --query-gpu=utilization.gpu,temperature.gpu "
             f"--format=csv,noheader,nounits -lms {SAMPLE_MS}"],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, bufsize=1)
        try:
            while not stop.is_set():
                line = (proc.stdout or sys.stdin).readline()
                if not line:
                    break
                try:
                    u, t = [int(x) for x in line.strip().split(",")[:2]]
                    yield u, t
                except ValueError:
                    continue
        finally:
            proc.kill()
        log("ssh stream died; reconnecting in 3s")
        time.sleep(3)

def write_lamp(cmd_str, tries=3):
    async def one():
        async with BleakClient(LAMP, timeout=25) as c:
            await c.write_gatt_char(TX, cmd_str.encode(), response=True)
    for a in range(tries):
        try:
            asyncio.run(one()); return True
        except Exception as e:
            log(f"BLE try {a+1} failed: {type(e).__name__}")
            time.sleep(2)
    return False

def classify(util, temp):
    if temp > TEMP_RED:
        return "hot"
    return "busy" if util > HIGH_UTIL else "calm"

def main():
    stop = threading.Event()
    mode = None            # committed mode
    cand, cand_since = None, 0.0
    last_feed = 0.0
    log(f"gpu-lamp up: util>{HIGH_UTIL}=busy temp>{TEMP_RED}C=hot debounce={DEBOUNCE_S}s")
    if not write_lamp(CMD["calm"]):
        log("initial lamp write failed — is the lamp in range / phone BT off?")
    mode = "calm"
    for util, temp in ssh_stream(stop):
        now = time.time()
        last_feed = now
        want = classify(util, temp)
        if want != cand:
            cand, cand_since = want, now
        elif want != mode and now - cand_since >= DEBOUNCE_S:
            log(f"{mode} -> {want} (util={util}% temp={temp}C)")
            if write_lamp(CMD[want]):
                mode = want
            else:
                log("write failed; keeping mode, will retry on next flip")
                cand = mode; cand_since = now

if __name__ == "__main__":
    main()
