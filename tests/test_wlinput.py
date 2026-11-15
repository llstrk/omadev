"""wlinput's wire format and the click/move argument handling; no compositor needed."""
import importlib.machinery
import importlib.util
from pathlib import Path
import socket
import struct
import sys
import threading
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'libexec'))
import wlinput  # noqa: E402

loader = importlib.machinery.SourceFileLoader('omadev_click', str(ROOT / 'bin/omadev'))
spec = importlib.util.spec_from_loader(loader.name, loader)
omadev = importlib.util.module_from_spec(spec)
loader.exec_module(omadev)


class WireTests(unittest.TestCase):
    def test_message_header_and_string_padding(self):
        data = wlinput.message(5, 2, wlinput._string('abc'))
        obj, word = struct.unpack_from('<II', data)
        self.assertEqual((obj, word & 0xFFFF, word >> 16), (5, 2, len(data)))
        self.assertEqual(len(data) % 4, 0)
        self.assertEqual(data[8:16], struct.pack('<I', 4) + b'abc\0')

    def test_modifier_masks(self):
        self.assertEqual(wlinput.modifier_mask(['shift', 'Ctrl']), 1 | 4)
        self.assertEqual(wlinput.modifier_mask(['super']), 64)
        with self.assertRaises(wlinput.InputError):
            wlinput.modifier_mask(['hyper'])

    def test_bounds_and_buttons_are_checked_before_connecting(self):
        with patch.object(wlinput, 'Client') as client:
            for kwargs in ({'x': 100, 'y': 5}, {'x': -1, 'y': 5}):
                with self.assertRaises(wlinput.InputError):
                    wlinput.pointer_input(width=100, height=100, **kwargs)
            with self.assertRaises(wlinput.InputError):
                wlinput.pointer_input(1, 1, 100, 100, button='fourth')
        client.assert_not_called()

    def test_registry_and_error_events(self):
        server, peer = socket.socketpair()
        self.addCleanup(server.close)
        c = wlinput.Client.__new__(wlinput.Client)
        c.sock, c.buffer, c.globals, c.done, c.registry, c.next_id = peer, b'', {}, set(), 2, 3
        name = wlinput._string('zwlr_virtual_pointer_manager_v1')
        server.sendall(wlinput.message(2, 0, struct.pack('<I', 7) + name + struct.pack('<I', 2))
                       + wlinput.message(9, 0, struct.pack('<I', 0)))
        c.read_events()
        self.assertEqual(c.globals['zwlr_virtual_pointer_manager_v1'], (7, 2))
        self.assertIn(9, c.done)
        server.sendall(wlinput.message(1, 0, struct.pack('<II', 3, 1) + wlinput._string('bad seat')))
        with self.assertRaisesRegex(wlinput.InputError, 'bad seat'):
            c.read_events()
        peer.close()


class ArgumentTests(unittest.TestCase):
    def test_slot_is_only_taken_with_three_numbers(self):
        with patch.object(omadev, 'pick_slot', return_value=4):
            slot, ns = omadev.parse_pointer(['5', '300'], 'click')
        self.assertEqual((slot, ns.x, ns.y), (4, 5, 300))
        slot, ns = omadev.parse_pointer(['5', '300', '400.6', '--mods', 'shift', '--double'], 'click')
        self.assertEqual((slot, ns.x, ns.y, ns.mods, ns.double), (5, 300, 401, 'shift', True))
        for bad in (['1'], ['1', '2', '3', '4'], ['a', 'b'], ['1', '2', '--button', 'fourth']):
            with self.subTest(bad=bad), self.assertRaises(omadev.Fail):
                with patch.object(omadev, 'pick_slot', return_value=4):
                    omadev.parse_pointer(bad, 'click')

    def test_extent_in_pixels_or_layout(self):
        out = '[{"width": 3052, "height": 1682, "scale": 2}]'
        with patch.object(omadev.subprocess, 'run') as run:
            run.return_value.stdout = out
            self.assertEqual(omadev.output_extent({}, False), (3052, 1682))
            self.assertEqual(omadev.output_extent({}, True), (1526, 841))


if __name__ == '__main__':
    unittest.main()
