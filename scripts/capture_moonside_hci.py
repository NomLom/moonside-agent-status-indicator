#!/usr/bin/env python3
"""Root-only, bounded HCI capture and onboard-Wi-Fi A/B probe.

Run explicitly with sudo. Does not change bonds, restart BlueZ, or disable USB Wi-Fi.
Restores the original onboard Wi-Fi connection in finally.
"""
import datetime
import os
from pathlib import Path
import pwd
import subprocess
import sys

REPO = Path(__file__).resolve().parents[1]
USER = 'slide'

def run(*args, **kwargs):
    return subprocess.run(args, check=False, **kwargs)

def probe(label):
    print(f'\n=== {label} ===', flush=True)
    result = run('/usr/sbin/runuser', '-u', USER, '--', str(REPO / '.venv/bin/python'), '-u', str(REPO / 'scripts/diagnose_moonside.py'), timeout=55)
    print(f'PROBE EXIT {result.returncode}', flush=True)
    return result.returncode

def main():
    if os.geteuid() != 0:
        raise SystemExit('Run this diagnostic with sudo; it needs HCI monitor access.')
    account = pwd.getpwnam(USER)
    routes = subprocess.check_output(['ip', 'route', 'show', 'default'], text=True)
    if not routes.splitlines() or 'dev wlx6815790f11f8' not in routes.splitlines()[0]:
        raise SystemExit('USB Wi-Fi is not the preferred default route; refusing Wi-Fi A/B test.')
    active = subprocess.check_output(['nmcli', '-g', 'GENERAL.CON-UUID', 'device', 'show', 'wlan0'], text=True).strip()
    directory = Path('/home/slide/.hermes/cache/scratch') / ('moonside-hci-' + datetime.datetime.now().strftime('%Y%m%d-%H%M%S'))
    directory.mkdir(mode=0o700)
    os.chown(directory, account.pw_uid, account.pw_gid)
    log = directory / 'hci.log'
    changed = False
    with log.open('w') as output:
        monitor = subprocess.Popen(['btmon', '-T'], stdout=output, stderr=subprocess.STDOUT)
        try:
            first = probe('onboard Wi-Fi unchanged')
            if first == 0:
                print('Baseline write delivered; skipping Wi-Fi change.', flush=True)
                return
            if active and active != '--':
                changed = True
                subprocess.run(['nmcli', 'device', 'disconnect', 'wlan0'], check=True, timeout=20)
                probe('onboard Wi-Fi disconnected; USB Wi-Fi retained')
        finally:
            if changed:
                run('nmcli', 'connection', 'up', 'uuid', active, 'ifname', 'wlan0', timeout=30)
            monitor.terminate()
            try:
                monitor.wait(timeout=5)
            except subprocess.TimeoutExpired:
                monitor.kill()
                monitor.wait()
            os.chown(log, account.pw_uid, account.pw_gid)
            print(f'HCI CAPTURE: {log}', flush=True)

if __name__ == '__main__':
    main()
