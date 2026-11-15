"""Live scale changes: validation, the rules sent to the nest, and ensure."""
import importlib.machinery
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
loader = importlib.machinery.SourceFileLoader('omadev_scale', str(ROOT / 'bin/omadev'))
spec = importlib.util.spec_from_loader(loader.name, loader)
omadev = importlib.util.module_from_spec(spec)
loader.exec_module(omadev)


class FakeNest:
    """hyprctl for one output whose scale follows the last eval'd rule, if Hyprland would take it."""
    def __init__(self, width=1520, height=1682, scale=1):
        self.width, self.height, self.scale, self.evals = width, height, scale, []

    def run(self, argv, **_):
        if argv[:2] == ['hyprctl', 'eval']:
            self.evals.append(argv[2])
            s = float(argv[2].split('scale = ')[1].split(' ')[0])
            if self.width / s == int(self.width / s) and self.height / s == int(self.height / s):
                self.scale = s
            else:
                self.scale = 1  # Hyprland falls back for a scale that does not fit
            out = 'ok'
        else:
            out = json.dumps([{'name': 'WAYLAND-1', 'width': self.width, 'height': self.height, 'scale': self.scale}])
        return type('P', (), {'stdout': out, 'returncode': 0})()


class ScaleTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.run_dir = Path(tmp.name)
        (self.run_dir / '7').mkdir()
        self.record = self.run_dir / '7/session.env'
        self.record.write_text("WAYLAND_DISPLAY=wayland-3\nOMADEV_SCALE=1\nOMADEV_SCALE_FILE=x\n")
        self.nest = FakeNest()
        env = {'OMADEV_SCALE': '1', 'OMADEV_SCALE_FILE': 'x'}
        for p in (patch.object(omadev, 'RUN_DIR', self.run_dir), patch.object(omadev, 'nest_env', return_value=env),
                  patch.object(omadev.subprocess, 'run', side_effect=self.nest.run),
                  patch.object(omadev, 'wait_until', side_effect=lambda pred, timeout: pred())):
            p.start()
            self.addCleanup(p.stop)

    def test_check_scale(self):
        self.assertEqual(omadev.check_scale('2.0'), '2')
        self.assertEqual(omadev.check_scale('1.25'), '1.25')
        for bad in ('0', '9', 'big'):
            with self.subTest(bad=bad), self.assertRaises(omadev.Fail):
                omadev.check_scale(bad)

    def test_fitting_scales(self):
        self.assertEqual(omadev.fitting_scales(1520, 1682), ['1', '2'])
        self.assertEqual(omadev.fitting_scales(3000, 1500), ['1', '1.25', '1.5', '2', '2.5', '3'])

    def test_explicit_mode_then_preferred_and_the_record_updated(self):
        omadev.set_scale(7, '2')
        self.assertEqual(self.nest.scale, 2)
        self.assertIn('mode = "1520x1682"', self.nest.evals[0])
        self.assertIn('mode = "preferred"', self.nest.evals[-1])  # last, so resizes keep the scale
        self.assertEqual((self.run_dir / '7/scale').read_text(), '2\n')
        self.assertIn('OMADEV_SCALE=2', self.record.read_text().splitlines())
        self.assertIn('WAYLAND_DISPLAY=wayland-3', self.record.read_text().splitlines())

    def test_a_rejected_scale_restores_the_previous_one(self):
        with self.assertRaisesRegex(omadev.Fail, 'kept 1. Scales that fit now: 1, 2'):
            omadev.set_scale(7, '1.5')
        self.assertEqual(self.nest.scale, 1)
        self.assertEqual((self.run_dir / '7/scale').read_text(), '1\n')
        self.assertIn('OMADEV_SCALE=1', self.record.read_text().splitlines())

    def test_a_nest_from_an_older_omadev_is_refused(self):
        with patch.object(omadev, 'nest_env', return_value={'OMADEV_SCALE': '1'}), \
                self.assertRaisesRegex(omadev.Fail, 'older omadev'):
            omadev.set_scale(7, '2')

    def test_ensure_changes_the_scale_only_when_asked(self):
        env = {'OMARCHY_PATH': '/usr/share/omarchy', 'OMADEV_INPUT_GATE': '1', 'OMADEV_OWNER': 'me',
               'OMADEV_HOME': '/nowhere', 'OMADEV_SCALE': '1'}
        with patch.object(omadev, 'slot_alive', return_value=True), \
                patch.object(omadev, 'read_env', return_value=env), \
                patch.object(omadev, 'shell_ping', return_value=True), \
                patch.object(omadev, 'slot_json', return_value={}), \
                patch.object(omadev.os.path, 'realpath', side_effect=lambda p: p), \
                patch.object(omadev, 'set_scale') as set_scale, \
                patch('builtins.print'):
            omadev.cmd_ensure(['7', '--owner', 'me'])
            set_scale.assert_not_called()
            omadev.cmd_ensure(['7', '--owner', 'me', '--scale', '1'])
            set_scale.assert_not_called()  # already 1
            omadev.cmd_ensure(['7', '--owner', 'me', '--scale', '2'])
            set_scale.assert_called_once_with(7, '2')



LUA = shutil.which('lua') or shutil.which('luajit')


class NestConfigScaleTests(unittest.TestCase):
    """The nest's hyprland.lua and the keeper read the same scale file."""

    def scale_function(self):
        text = (ROOT / 'libexec/hyprland.lua').read_text()
        start = text.index('function omadev_scale()')
        return text[start:text.index('\nend\n', start) + 5]

    @unittest.skipUnless(LUA, 'needs lua')
    def test_omadev_scale_reads_the_file_with_the_start_scale_as_fallback(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'scale'

            def run(file_text, start='1'):
                if file_text is None:
                    path.unlink(missing_ok=True)
                else:
                    path.write_text(file_text)
                env = {'OMADEV_SCALE': start, 'OMADEV_SCALE_FILE': str(path), 'PATH': '/usr/bin:/bin'}
                return subprocess.run([LUA, '-e', self.scale_function() + '\nprint(omadev_scale())'],
                                      env=env, capture_output=True, text=True, check=True).stdout.strip()
            self.assertEqual(run('2\n'), '2')
            self.assertEqual(run(' 1.25 \n'), '1.25')
            self.assertEqual(run(None, start='1.5'), '1.5')
            self.assertEqual(run('garbage\n', start='2'), '2')

    def test_config_and_keeper_both_use_it(self):
        lua = (ROOT / 'libexec/hyprland.lua').read_text()
        self.assertIn('scale = omadev_scale() })', lua)
        keeper = (ROOT / 'libexec/keeper/shell.qml').read_text()
        self.assertIn('omadev_scale and omadev_scale()', keeper)


if __name__ == '__main__':
    unittest.main()
