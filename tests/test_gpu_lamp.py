"""Hardware-free IO-path tests for the five-state engine.

State/palette/priority logic lives in test_gpu_lamp_states.py; this file covers
the BLE writer, SSH reader, singleton, and delivery/backoff plumbing.
"""
import asyncio
import pathlib
import sys
import unittest
from unittest.mock import AsyncMock, patch
from types import SimpleNamespace
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import gpu_lamp as g


def day_controller():
    return g.Controller(is_day=lambda path=None, now=None: True)


class IOTests(unittest.IsolatedAsyncioTestCase):
    async def test_writer_validates_uart_then_sends_brightness_and_command(self):
        self.assertTrue(hasattr(g, 'LampWriter'), 'persistent BLE writer missing')
        dev = SimpleNamespace(address=g.LAMP, name='MOONSIDE-O101')
        other = SimpleNamespace(address='other', name='MOONSIDE-O101')
        tile = SimpleNamespace(address='tile', name='Tile')
        service = SimpleNamespace(characteristics=[SimpleNamespace(uuid=g.TX)])
        client = SimpleNamespace(is_connected=False,
                                 services=SimpleNamespace(get_service=lambda uuid: service),
                                 write_gatt_char=AsyncMock(), disconnect=AsyncMock())
        async def connect():
            client.is_connected = True
        client.connect = connect
        scan = AsyncMock(return_value={str(i): (d, SimpleNamespace(local_name=d.name))
                                       for i, d in enumerate([tile, other, dev])})
        factory = unittest.mock.Mock(return_value=client)
        writer = g.LampWriter(scan=scan, client_factory=factory)
        await writer.send(('low', 'day'))
        await writer.send(('busy', 'day'))
        self.assertEqual(factory.call_args.args[0], dev)   # configured address preferred
        self.assertEqual(scan.await_count, 1)              # connection reused, no rescan
        commands = [c.args[1].decode() for c in client.write_gatt_char.await_args_list]
        p = g.PALETTES
        self.assertEqual(commands, [
            'LEDON', 'BRIGH80', p[('low', 'day')]['command'],
            'LEDON', 'BRIGH80', p[('busy', 'day')]['command']])
        await writer.close()
        client.disconnect.assert_awaited_once()

    async def test_rejects_missing_uart_before_writes_then_recovers(self):
        dev = SimpleNamespace(address=g.LAMP, name='MOONSIDE')
        client = SimpleNamespace(is_connected=True, connect=AsyncMock(),
            disconnect=AsyncMock(), write_gatt_char=AsyncMock(),
            services=SimpleNamespace(get_service=lambda uuid: None))
        writer = g.LampWriter(scan=AsyncMock(return_value={'d': (dev, SimpleNamespace(local_name='MOONSIDE'))}),
                              client_factory=lambda *a, **kw: client)
        with self.assertRaisesRegex(RuntimeError, 'Nordic UART'):
            await writer.send(('thermal', 'day'))
        client.write_gatt_char.assert_not_awaited()
        self.assertIsNone(writer.client)
        client.services.get_service = lambda uuid: SimpleNamespace(characteristics=[SimpleNamespace(uuid=g.TX)])
        await writer.send(('thermal', 'night'))
        self.assertEqual(client.write_gatt_char.await_args_list[-1].args[1],
                         g.PALETTES[('thermal', 'night')]['command'].encode())
        await writer.close()

    async def test_ssh_reader_records_without_blocking_writer_and_reaps(self):
        self.assertTrue(hasattr(g, 'telemetry'), 'independent SSH reader missing')
        reader = asyncio.StreamReader()
        proc = SimpleNamespace(stdout=reader, returncode=None,
                               terminate=unittest.mock.Mock(), wait=AsyncMock(),
                               kill=unittest.mock.Mock())
        c = day_controller()
        with patch.object(g.asyncio, 'create_subprocess_exec', AsyncMock(return_value=proc)):
            task = asyncio.create_task(g.telemetry(c))
            await asyncio.sleep(0)
            reader.feed_data(b'10,50\n90,50\n0,90\n')
            await asyncio.sleep(0)
            self.assertEqual(c.last_reading, (0, 90))
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
        proc.terminate.assert_called_once()
        proc.wait.assert_awaited_once()

    async def test_singleton_rejects_duplicate(self):
        import tempfile
        with tempfile.TemporaryDirectory(dir='/home/slide/.hermes/cache/scratch') as directory:
            with g.singleton(pathlib.Path(directory) / 'lock'):
                with self.assertRaises(RuntimeError):
                    with g.singleton(pathlib.Path(directory) / 'lock'):
                        pass

    async def test_partial_failure_invalidates_old_delivery(self):
        c = day_controller()
        c.desired = ('low', 'day')
        writer = SimpleNamespace(send=AsyncMock(side_effect=OSError('partial write')))
        await c.deliver(writer, 3)
        self.assertIsNone(c.delivered)
        c.desired = ('thermal', 'day')
        writer.send.side_effect = None
        await c.deliver(writer, 3.5)          # new desired resets backoff instantly
        self.assertEqual(writer.send.await_count, 2)

    async def test_newest_desired_wins_during_slow_failure(self):
        c = day_controller()
        for t in (0, 2):
            c.observe(util=10, temp=50, now=t)
        entered, release = asyncio.Event(), asyncio.Event()
        calls = []
        async def send(key):
            calls.append(key)
            if len(calls) == 1:
                entered.set()
                await release.wait()
                raise OSError('failed')
        writer = SimpleNamespace(send=send)
        task = asyncio.create_task(c.deliver(writer, 3))
        await entered.wait()
        for t in (4, 6):
            c.observe(util=90, temp=90, now=t)
        release.set()
        await task
        await c.deliver(writer, 6)
        self.assertEqual(calls, [('low', 'day'), ('thermal', 'day')])


if __name__ == '__main__':
    unittest.main()
