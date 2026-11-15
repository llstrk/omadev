"""Recording marks and pins the nest's host window: tag, grid skip, --size, release on any end."""
import importlib.machinery
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
loader = importlib.machinery.SourceFileLoader('omadev_pin', str(ROOT / 'bin/omadev'))
spec = importlib.util.spec_from_loader(loader.name, loader)
omadev = importlib.util.module_from_spec(spec)
loader.exec_module(omadev)
import recorder  # noqa: E402  (bin/omadev put libexec on sys.path)


class TempHome(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        for p in (patch.object(omadev, 'HOME_BASE', self.tmp / 'homes'), patch.object(omadev, 'RUN_DIR', self.tmp / 'run')):
            p.start()
            self.addCleanup(p.stop)
        omadev.record_base(7).mkdir(parents=True)


class SizeTests(unittest.TestCase):
    def test_parse_size(self):
        self.assertEqual(omadev.parse_size('1920x1080', 1), (1920, 1080))
        self.assertEqual(omadev.parse_size('2560X1440', 2), (2560, 1440))
        for bad, scale in (('1921x1080', 1), ('100x100', 1), ('1920', 1), ('wide', 1), ('8000x4000', 1)):
            with self.subTest(bad=bad), self.assertRaises(omadev.Fail):
                omadev.parse_size(bad, scale)
        with self.assertRaisesRegex(omadev.Fail, 'does not divide evenly at the nest.s scale 1.5'):
            omadev.parse_size('1366x768', 1.5)

    def test_a_pinned_recording_keeps_full_resolution(self):
        self.assertEqual(recorder.frame_scale(2560, 1, target_width=2560), 1)
        self.assertEqual(recorder.frame_scale(2560, 2, target_width=2560), 2)
        self.assertEqual(recorder.frame_scale(2560, 1), 0.75)  # unpinned: about full HD


class MarkTests(TempHome):
    def mark(self, on, workspace='omadev'):
        client = {'address': '0xabc', 'workspace': {'name': workspace}}
        with patch.object(omadev, 'host_client', return_value=client), \
                patch.object(omadev, 'host_dispatch') as dispatch, \
                patch.object(omadev, 'regrid') as regrid, \
                patch.object(omadev, 'host_refresh'), \
                patch.object(omadev.subprocess, 'run') as run:
            omadev.mark_recording(7, on)
        return dispatch, regrid, run

    def test_on_installs_the_rule_then_tags_and_pins(self):
        dispatch, regrid, run = self.mark(True)
        self.assertIn('omadev_recording_rule', run.call_args.args[0][2])
        self.assertEqual(dispatch.call_args.args[0],
                         'hl.dsp.window.tag({ window = "address:0xabc", tag = "+omadev-recording" })')
        self.assertTrue((omadev.record_base(7) / 'pinned').exists())
        regrid.assert_not_called()

    def test_off_untags_unpins_and_regrids(self):
        self.mark(True)
        dispatch, regrid, _ = self.mark(False)
        self.assertIn('tag = "-omadev-recording"', dispatch.call_args.args[0])
        self.assertFalse((omadev.record_base(7) / 'pinned').exists())
        regrid.assert_called_once()
        _, regrid, _ = self.mark(False, workspace='2')  # a tiled nest is not in the grid
        regrid.assert_not_called()

    def test_pinned_needs_a_live_recorder(self):
        (omadev.record_base(7) / 'pinned').write_text('')
        with patch.object(recorder, 'recording', return_value=True):
            self.assertTrue(omadev.recording_pinned(7))
        with patch.object(recorder, 'recording', return_value=False):  # killed recorder: the grid takes it back
            self.assertFalse(omadev.recording_pinned(7))

    def test_list_reports_the_recording(self):
        recorder.paths(omadev.record_base(7))['meta'].write_text(
            json.dumps({'started': 1_700_000_000_000_000_000, 'file': '/tmp/x.mp4', 'size': '1920x1080'}))
        with patch.object(recorder, 'recording', return_value=True):
            self.assertEqual(omadev.recording_json(7), {'since': 1_700_000_000.0, 'file': '/tmp/x.mp4', 'size': '1920x1080'})
        with patch.object(recorder, 'recording', return_value=False):
            self.assertIsNone(omadev.recording_json(7))

    def test_no_scale_change_while_recording(self):
        with patch.object(omadev, 'nest_env', return_value={'OMADEV_SCALE_FILE': 'x'}), \
                patch.object(recorder, 'recording', return_value=True), \
                self.assertRaisesRegex(omadev.Fail, 'is recording'):
            omadev.set_scale(7, '2')


class GridTests(unittest.TestCase):
    def test_the_grid_leaves_a_pinned_window_alone(self):
        clients = [{'pid': 11, 'address': '0x1', 'floating': True, 'workspace': {'name': 'omadev'}},
                   {'pid': 12, 'address': '0x2', 'floating': True, 'workspace': {'name': 'omadev'}}]
        monitors = [{'id': 1, 'x': 0, 'y': 0, 'width': 3000, 'height': 1600, 'scale': 1, 'reserved': [0, 0, 0, 0]}]
        data = {'clients': clients, 'workspaces': [{'name': 'omadev', 'monitorID': 1}], 'monitors': monitors}
        with patch.object(omadev, 'SLOTS', [6, 7]), \
                patch.object(omadev, 'slot_alive', return_value=True), \
                patch.object(omadev, 'read_env', side_effect=lambda n: {'OMADEV_PID': str(n + 5)}), \
                patch.object(omadev, 'hostctl_json', side_effect=lambda what: data[what]), \
                patch.object(omadev, 'recording_pinned', side_effect=lambda n: n == 7), \
                patch.object(omadev, 'host_dispatch') as dispatch:
            omadev.regrid_locked()
        touched = ' '.join(c.args[0] for c in dispatch.call_args_list)
        self.assertIn('address:0x1', touched)
        self.assertNotIn('address:0x2', touched)


class RecorderExitTests(unittest.TestCase):
    def test_the_recorder_runs_on_exit_and_gives_grim_the_nest_env(self):
        with tempfile.TemporaryDirectory() as tmp:
            base, bin_dir = Path(tmp) / 'record', Path(tmp) / 'bin'
            recorder.paths(base)['frames'].mkdir(parents=True)
            bin_dir.mkdir()
            grim = bin_dir / 'grim'
            grim.write_text(f'#!/bin/sh\necho "$WAYLAND_DISPLAY" > {tmp}/grim-env\nexit 1\n')
            grim.chmod(0o755)
            done = Path(tmp) / 'done'
            recorder.paths(base)['env'].write_text(json.dumps({'PATH': f'{bin_dir}:/usr/bin:/bin', 'WAYLAND_DISPLAY': 'wayland-nest'}))
            recorder.paths(base)['meta'].write_text(json.dumps({'on_exit': ['touch', str(done)]}))
            recorder.capture(base, 1.0, 1, 10)
            self.assertTrue(done.exists())
            self.assertEqual((Path(tmp) / 'grim-env').read_text().strip(), 'wayland-nest')


if __name__ == '__main__':
    unittest.main()
