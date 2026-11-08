"""Deterministic cheap monitor for deferred Hindsight retain (online/offline)."""
import urllib.request

try:
    with urllib.request.urlopen('http://192.168.0.90:8888/docs', timeout=3) as reply:
        print('online' if reply.status == 200 else 'offline')
except Exception:
    print('offline')
