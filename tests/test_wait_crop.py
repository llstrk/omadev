"""omadev wait and screenshot crops, with hyprctl and grim replaced."""
import contextlib
import importlib.machinery
import importlib.util
import io
import json
from pathlib import Path
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
loader = importlib.machinery.SourceFileLoader('omadev_wait', str(ROOT / 'bin/omadev'))
spec = importlib.util.spec_from_loader(loader.name, loader)
omadev = importlib.util.module_from_spec(spec)
loader.exec_module(omadev)

LAYERS = {'WAYLAND-1': {'levels': {'0': [{'namespace': 'omarchy-background', 'x': 0, 'y': 0, 'w': 760, 'h': 841}],
                                    '2': [{'namespace': 'omarchy-dock', 'x': 0, 'y': 634, 'w': 760, 'h': 207},
                                          {'namespace': 'gone', 'x': 0, 'y': 0, 'w': 0, 'h': 0}]}}}
CLIENTS = [{'class': 'foot', 'title': 'Build output', 'at': [12, 38], 'size': [700, 400], 'mapped': True},
           {'class': 'chromium', 'title': 'GitHub', 'at': [0, 0], 'size': [10, 10], 'mapped': False}]
MONITORS = [{'width': 1520, 'height': 1682, 'scale': 2}]


def fake_hyprctl(argv, **_):
    data = {'layers': LAYERS, 'clients': CLIENTS, 'monitors': MONITORS}[argv[1]]
    return type('P', (), {'stdout': json.dumps(data), 'returncode': 0})()


class WaitCropTests(unittest.TestCase):
    def setUp(self):
        for p in (patch.object(omadev, 'nest_env', return_value={}),
                  patch.object(omadev, 'split_slot', side_effect=lambda a: (6, a))):
            p.start()
            self.addCleanup(p.stop)

    def test_boxes(self):
        with patch.object(omadev.subprocess, 'run', side_effect=fake_hyprctl):
            self.assertEqual(omadev.layer_boxes({}, 'omarchy-dock'), [(0, 634, 760, 207)])
            self.assertEqual(omadev.layer_boxes({}, 'gone'), [])  # unmapped (zero size)
            self.assertEqual(omadev.window_boxes({}, 'BUILD'), [(12, 38, 700, 400)])  # title, any case
            self.assertEqual(omadev.window_boxes({}, 'chromium'), [])  # not mapped

    def test_crop_box_pads_and_clamps(self):
        self.assertEqual(omadev.crop_box((12, 38, 700, 400), 20, (760, 841)), '0,18 732x440')
        self.assertEqual(omadev.crop_box((700, 800, 100, 100), 0, (760, 841)), '700,800 60x41')
        with self.assertRaises(omadev.Fail):
            omadev.crop_box((900, 900, 10, 10), 0, (760, 841))

    def shot(self, *args):
        calls = []

        def run(argv, **kw):
            if argv[0] == 'grim':
                calls.append(argv)
                return None
            return fake_hyprctl(argv, **kw)
        with patch.object(omadev.subprocess, 'run', side_effect=run), contextlib.redirect_stdout(io.StringIO()):
            omadev.cmd_screenshot(['/tmp/x.png', *args])
        return calls[0]

    def test_screenshot_geometry_is_in_layout_units(self):
        self.assertEqual(self.shot('--layer', 'omarchy-dock'), ['grim', '-g', '0,634 760x207', '/tmp/x.png'])
        # --region and --pad are screenshot pixels: at scale 2 they halve
        self.assertEqual(self.shot('--region', '100,100,400,300'), ['grim', '-g', '50,50 200x150', '/tmp/x.png'])
        self.assertEqual(self.shot('--window', 'foot', '--pad', '40'), ['grim', '-g', '0,18 732x440', '/tmp/x.png'])
        self.assertEqual(self.shot(), ['grim', '/tmp/x.png'])
        with self.assertRaises(omadev.Fail):
            self.shot('--layer', 'missing')
        with self.assertRaises(omadev.Fail):
            self.shot('--region', '1,2,3')

    def test_wait_returns_when_present_and_times_out(self):
        seen = iter([[], [], [(0, 0, 1, 1)]])
        with patch.object(omadev, 'layer_boxes', side_effect=lambda *_: next(seen)), \
                patch.object(omadev.time, 'sleep'):
            self.assertEqual(omadev.cmd_wait(['layer', 'omaprofile']), 0)
        with patch.object(omadev, 'window_boxes', return_value=[(0, 0, 1, 1)]), \
                contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(omadev.cmd_wait(['window', 'foot', '--gone', '--timeout', '0.2']), 1)
        with self.assertRaises(omadev.Fail):
            omadev.cmd_wait(['pane', 'x'])


if __name__ == '__main__':
    unittest.main()
