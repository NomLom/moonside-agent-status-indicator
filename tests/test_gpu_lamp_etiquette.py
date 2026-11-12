"""BLE etiquette tests (2026-11-12 port from moonside_daemon.py design).

The O101 is a one-central peripheral: a client that sits on the link
forever starves the phone app, and reconnect storms orphan links until the
lamp stack wedges (btmon: connect Success -> Disconnect 0x08, power-cycle
only). Proven etiquette: hold the link while working, release it when idle,
reuse the discovered device instead of re-scanning, never yank the link.
"""
import pathlib
import sys
import unittest
from unittest.mock import AsyncMock, Mock
from types import SimpleNamespace
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import gpu_lamp as g


def make_world():
    """Fake scan+client world: one lamp, validated UART, recordable writes."""
    dev = SimpleNamespace(address=g.LAMP, name='MOONSIDE-O101')
    service = SimpleNamespace(characteristics=[SimpleNamespace(uuid=g.TX)])

    def new_client(device, **kw):
        client = SimpleNamespace(
            is_connected=False,
            services=SimpleNamespace(get_service=lambda uuid: service),
            write_gatt_char=AsyncMock(), disconnect=AsyncMock())

        async def connect():
            client.is_connected = True
        client.connect = connect
        return client

    scan = AsyncMock(return_value={'0': (dev, SimpleNamespace(local_name=dev.name))})
    factory = Mock(side_effect=new_client)
    return dev, scan, factory


class EtiquetteTests(unittest.IsolatedAsyncioTestCase):
    async def test_idle_release_after_quiet_period(self):
        self.assertTrue(hasattr(g.LampWriter, 'maybe_release'),
                        'idle slot release missing')
        dev, scan, factory = make_world()
        writer = g.LampWriter(scan=scan, client_factory=factory)
        await writer.send(('low', 'day'))
        self.assertTrue(writer.client.is_connected)
        await writer.maybe_release(now=writer.last_activity + g.IDLE_RELEASE_S - 1)
        self.assertTrue(writer.client.is_connected, 'released too early')
        await writer.maybe_release(now=writer.last_activity + g.IDLE_RELEASE_S + 1)
        self.assertIsNone(writer.client, 'link not released after idle window')
        # graceful: release goes through disconnect(), never yanked
        await writer.close()

    async def test_release_does_not_send_ledoff_or_repaint(self):
        """Releasing the slot must leave the committed lamp state untouched:
        no LEDOFF writes, no controller repaint (user doctrine: set once)."""
        dev, scan, factory = make_world()
        writer = g.LampWriter(scan=scan, client_factory=factory)
        controller = g.Controller(is_day=lambda path=None, now=None: True)
        controller.observe(util=90, temp=50, now=0)     # busy/day committed
        await controller.deliver(writer, now=1)
        writes = []
        for client_call in factory.call_args_list:
            pass
        c = writer.client
        writes = [w.args[1] for w in c.write_gatt_char.await_args_list]
        self.assertNotIn(b'LEDOFF', writes)
        await writer.maybe_release(now=writer.last_activity + g.IDLE_RELEASE_S + 1)
        # controller still believes the state is delivered -> no new sends
        await controller.deliver(writer, now=4000)
        self.assertEqual(c.write_gatt_char.await_count, len(writes),
                         'repaint storm after idle release')

    async def test_reconnect_after_release_reuses_cached_device_no_rescan(self):
        dev, scan, factory = make_world()
        writer = g.LampWriter(scan=scan, client_factory=factory)
        await writer.send(('low', 'day'))
        await writer.maybe_release(now=writer.last_activity + g.IDLE_RELEASE_S + 1)
        await writer.send(('busy', 'day'))
        self.assertEqual(scan.await_count, 1, 'reconnected via rescan, not cache')
        self.assertIs(factory.call_args_list[-1].args[0], dev)
        self.assertEqual(writer.client.is_connected, True)
        await writer.close()

    async def test_stale_cached_device_falls_back_to_fresh_scan(self):
        dev, scan, factory = make_world()
        writer = g.LampWriter(scan=scan, client_factory=factory)
        await writer.send(('low', 'day'))
        await writer.maybe_release(now=writer.last_activity + g.IDLE_RELEASE_S + 1)
        # make cached-device connects fail, fresh-scan connects succeed
        real_factory = factory.side_effect
        attempts = {'cached': 0}

        def flaky(device, **kw):
            client = real_factory(device, **kw)
            original_connect = client.connect

            async def connect():
                attempts['cached'] += 1
                if attempts['cached'] <= 1:
                    raise OSError('cached link dead')
                await original_connect()
            client.connect = connect
            return client
        writer.client_factory = flaky
        await writer.send(('thermal', 'day'))
        self.assertTrue(writer.client.is_connected)
        self.assertGreaterEqual(scan.await_count, 2,
                                'no rescan when cached device is stale')
        await writer.close()

    async def test_failure_path_still_closes_link_politely(self):
        """Send failure must disconnect (untie) rather than abandon the link."""
        dev = SimpleNamespace(address=g.LAMP, name='MOONSIDE-O101')
        service = SimpleNamespace(characteristics=[SimpleNamespace(uuid=g.TX)])
        client = SimpleNamespace(is_connected=False, connect=AsyncMock(),
                                 disconnect=AsyncMock(),
                                 write_gatt_char=AsyncMock(side_effect=OSError('air busy')),
                                 services=SimpleNamespace(get_service=lambda uuid: service))

        async def connect():
            client.is_connected = True
        client.connect = connect
        scan = AsyncMock(return_value={'0': (dev, SimpleNamespace(local_name=dev.name))})
        writer = g.LampWriter(scan=scan, client_factory=lambda *a, **kw: client)
        with self.assertRaises(OSError):
            await writer.send(('busy', 'day'))
        client.disconnect.assert_awaited()
        self.assertIsNone(writer.client)


if __name__ == '__main__':
    unittest.main()
