"""--private paths, `links` and the stop hint. Everything happens in temporary homes."""
import contextlib
import importlib.machinery
import importlib.util
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
loader = importlib.machinery.SourceFileLoader('omadev_private', str(ROOT / 'bin/omadev'))
spec = importlib.util.spec_from_loader(loader.name, loader)
omadev = importlib.util.module_from_spec(spec)
loader.exec_module(omadev)


def write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


class PrivatePathTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix='omadev-private-')
        self.addCleanup(tmp.cleanup)
        self.base = Path(tmp.name)
        self.real = self.base / 'real'
        write(self.real / '.config/omarchy/shell.json', '{}')
        write(self.real / '.config/myapp/config.json', '{"real": true}')
        write(self.real / '.config/other/settings', 'shared')
        write(self.real / '.local/bin/tool', '#!/bin/sh\n')
        write(self.real / '.local/share/applications/app.desktop', '[Desktop Entry]\n')
        write(self.real / '.local/share/fonts/font.ttf', 'font')
        write(self.real / '.local/state/myapp/state.json', '{}')
        write(self.real / '.config/mimeapps.list', '[Default Applications]\n')
        for p in (patch.object(omadev.Path, 'home', return_value=self.real),
                  patch.object(omadev, 'HOME_BASE', self.base / 'homes')):
            p.start()
            self.addCleanup(p.stop)
        self.checkout = self.base / 'checkout'

    def home(self, reuse=False, private=()):
        omadev.remember_private(6, list(private))
        return omadev.make_home(6, reuse, self.checkout)

    def test_a_private_directory_is_a_copy_and_writes_stay_in_the_nest(self):
        sb = self.home(private=['.config/myapp'])
        self.assertFalse((sb / '.config/myapp').is_symlink())
        self.assertEqual((sb / '.config/myapp/config.json').read_text(), '{"real": true}')
        (sb / '.config/myapp/config.json').write_text('{"nest": true}')
        self.assertEqual((self.real / '.config/myapp/config.json').read_text(), '{"real": true}')
        self.assertTrue((sb / '.config/other').is_symlink())  # the rest stays shared

    def test_a_path_under_a_linked_directory_splits_only_that_path(self):
        sb = self.home(private=['.local/share/applications', '.local/bin', '.local/state/myapp'])
        self.assertFalse((sb / '.local/share').is_symlink())
        self.assertFalse((sb / '.local/share/applications').is_symlink())
        self.assertTrue((sb / '.local/share/fonts').is_symlink())
        self.assertEqual(os.path.realpath(sb / '.local/share/fonts'), str(self.real / '.local/share/fonts'))
        self.assertFalse((sb / '.local/bin').is_symlink())
        self.assertTrue((sb / '.local/bin/tool').is_file())
        (sb / '.local/bin/new').write_text('nest only')
        self.assertFalse((self.real / '.local/bin/new').exists())

    def test_files_and_missing_paths(self):
        sb = self.home(private=['.config/mimeapps.list', '.config/notyet'])
        self.assertFalse((sb / '.config/mimeapps.list').is_symlink())
        (sb / '.config/mimeapps.list').write_text('nest')
        self.assertEqual((self.real / '.config/mimeapps.list').read_text(), '[Default Applications]\n')
        self.assertTrue((sb / '.config/notyet').is_dir())
        self.assertFalse((self.real / '.config/notyet').exists())

    def test_the_slot_remembers_its_paths_across_a_rebuild(self):
        sb = self.home(private=['.config/myapp'])
        (sb / '.config/myapp/config.json').write_text('nest edit')
        sb = self.home()  # rebuilt from the real home: a fresh copy, still private
        self.assertFalse((sb / '.config/myapp').is_symlink())
        self.assertEqual((sb / '.config/myapp/config.json').read_text(), '{"real": true}')
        self.assertEqual(omadev.private_paths(6), ['.config/myapp'])

    def test_reuse_keeps_the_nest_copy(self):
        sb = self.home(private=['.config/myapp'])
        (sb / '.config/myapp/config.json').write_text('nest edit')
        sb = self.home(reuse=True)
        self.assertEqual((sb / '.config/myapp/config.json').read_text(), 'nest edit')

    def test_applied_to_a_running_home_and_idempotent(self):
        sb = self.home()
        self.assertTrue((sb / '.config/myapp').is_symlink())
        self.assertTrue(omadev.make_private(sb, self.real, '.config/myapp'))
        (sb / '.config/myapp/config.json').write_text('nest edit')
        self.assertFalse(omadev.make_private(sb, self.real, '.config/myapp'))
        self.assertEqual((sb / '.config/myapp/config.json').read_text(), 'nest edit')

    def test_paths_are_relative_to_the_home(self):
        self.assertEqual(omadev.home_relative('~/.config/myapp/'), '.config/myapp')
        self.assertEqual(omadev.home_relative(str(self.real / '.local/bin')), '.local/bin')
        self.assertEqual(omadev.home_relative('./.config/x'), '.config/x')
        for bad in ('../etc', '.config/../../x', '/etc/passwd', '', '.'):
            with self.subTest(bad=bad), self.assertRaises(omadev.Fail):
                omadev.home_relative(bad)

    def test_links_lists_shared_paths_and_answers_for_one(self):
        sb = self.home(private=['.config/myapp'])
        shared = omadev.shared_paths(sb)
        self.assertIn('.config/other', shared)
        self.assertIn('.local/bin', shared)
        self.assertNotIn('.config/myapp', shared)
        self.assertEqual(omadev.path_status(sb, '.config/myapp/config.json')[0], 'private')
        self.assertEqual(omadev.path_status(sb, '.local/bin/tool'),
                         ('shared', str(self.real / '.local/bin/tool')))
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = omadev.cmd_links(['6', '.config/myapp', '~/.local/bin', '--json'])
        self.assertEqual(code, 1)  # one of them is shared
        self.assertEqual(json.loads(out.getvalue())['~/.local/bin']['state'], 'shared')
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(omadev.cmd_links(['6', '.config/myapp']), 0)

    def test_start_options_reach_the_detached_child(self):
        ns = omadev.parse_start(['6', '--private', '.config/myapp', '--private', '~/.local/bin'])
        self.assertEqual(ns.private, ['.config/myapp', '~/.local/bin'])
        ns.private = [omadev.home_relative(p) for p in ns.private]
        omadev.HELD_LOCK = os.open(os.devnull, os.O_RDONLY)
        with patch.object(omadev.subprocess, 'Popen') as popen, \
                patch.object(omadev, 'RUN_DIR', self.base / 'run'), \
                patch.object(omadev, 'wait_until', return_value=False), \
                contextlib.redirect_stderr(io.StringIO()):
            (self.base / 'run/6').mkdir(parents=True)
            (self.base / 'run/6/launcher.log').write_text('')
            omadev.start_detached(6, [], ns)
        argv = popen.call_args.args[0]
        self.assertEqual([argv[i + 1] for i, a in enumerate(argv) if a == '--private'], ['.config/myapp', '.local/bin'])


if __name__ == '__main__':
    unittest.main()
