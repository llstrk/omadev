"""Migrate known bindings to acknowledged release without replacing custom ones."""
import runpy
import json
import shlex
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

MODULE = Path(__file__).resolve().parents[1] / 'bin/omadev'


class InputSetupTests(unittest.TestCase):
    def test_old_bindings_migrate_and_install_is_idempotent(self):
        for kind in ('LEGACY_BINDINGS_BLOCK', 'HANDOFF_BINDINGS_BLOCK', 'IMMEDIATE_BINDINGS_BLOCK', 'PATH_RELEASE_BINDINGS_BLOCK', 'BINDINGS_BLOCK'):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as tmp:
                module = runpy.run_path(str(MODULE))
                setup = module['setup_bindings']
                setup.__globals__['CONFIG_DIR'] = Path(tmp)
                path = Path(tmp) / 'hypr/bindings.lua'
                path.parent.mkdir()
                prefix, suffix = '-- existing settings\n', '\n-- keep this\n'
                path.write_text(prefix + module[kind] + suffix)
                with patch('subprocess.run'):
                    setup()
                    expected = prefix + module['BINDINGS_BLOCK'] + suffix
                    self.assertEqual(path.read_text(), expected)
                    setup()
                    self.assertEqual(path.read_text(), expected)
                self.assertFalse(path.with_name('omadev-input.lua').exists())
                self.assertNotIn('omadev_input', path.read_text())
                self.assertIn(module['RELEASE_DISPATCH'], path.read_text())
                command = json.loads(module['RELEASE_DISPATCH'][len('hl.dsp.exec_cmd('):-1])
                self.assertEqual(shlex.split(command), [str(MODULE.resolve()), 'release'])
                self.assertIn('SUPER + SHIFT + ALT + D', path.read_text())
                self.assertIn('hl.dispatch(hl.dsp.submap("reset"))', path.read_text())
                self.assertIn('without confirming nest key cleanup', path.read_text())

    def test_moved_launcher_block_migrates_but_custom_blocks_do_not(self):
        with tempfile.TemporaryDirectory() as tmp:
            module = runpy.run_path(str(MODULE))
            setup = module['setup_bindings']
            setup.__globals__['CONFIG_DIR'] = Path(tmp)
            path = Path(tmp)/'hypr/bindings.lua'
            path.parent.mkdir()
            # Deleted old copy and shell quoting are both supported.
            command = 'hl.dsp.exec_cmd(' + json.dumps(shlex.quote("/old checkout/it's here/bin/omadev") + ' release') + ')'
            old = module['BINDINGS_BLOCK'].replace(module['RELEASE_DISPATCH'], command)
            path.write_text(old)
            with patch('subprocess.run'):
                setup()
                self.assertEqual(path.read_text(), module['BINDINGS_BLOCK'])
                custom = old.replace('Release keys from the omadev session', 'My own release action')
                path.write_text(custom)
                self.assertIn('custom', setup())
                self.assertEqual(path.read_text(), custom)
            link = Path(tmp)/'omadev'
            link.symlink_to(MODULE)
            alias = module['BINDINGS_BLOCK'].replace(module['RELEASE_DISPATCH'],
                      'hl.dsp.exec_cmd(' + json.dumps(shlex.quote(str(link)) + ' release') + ')')
            found = list(module['installed_binding_blocks'](alias))
            self.assertEqual(len(found), 1)
            self.assertEqual(found[0][1].resolve(), MODULE.resolve())

    def test_custom_bindings_are_not_overwritten(self):
        with tempfile.TemporaryDirectory() as tmp:
            module = runpy.run_path(str(MODULE))
            setup = module['setup_bindings']
            setup.__globals__['CONFIG_DIR'] = Path(tmp)
            path = Path(tmp) / 'hypr/bindings.lua'
            path.parent.mkdir()
            original = '-- omadev: my own bindings\n'
            path.write_text(original)
            with patch('subprocess.run'):
                self.assertIn('custom', setup())
            self.assertEqual(path.read_text(), original)
