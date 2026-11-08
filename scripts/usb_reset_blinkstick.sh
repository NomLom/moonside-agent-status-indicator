#!/usr/bin/env bash
# Root helper: authorized-toggle the BlinkStick (20a0:41e5) so a wedged device re-enumerates.
# Run via: sudo bash <this file>   (matches the NOPASSWD pattern for /usr/bin/bash on this dir)
set -x
DEVDIR=$(grep -l . /sys/bus/usb/devices/*/idVendor | while read f; do [ "$(cat $f)" = "20a0" ] && dirname $f; done | head -1)
echo "device: $DEVDIR"
echo 0 > "$DEVDIR/authorized"; sleep 1; echo 1 > "$DEVDIR/authorized"; sleep 2
udevadm trigger
cat "$DEVDIR/authorized"
