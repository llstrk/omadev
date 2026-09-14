"""Restore direct bindings, including migration away from the handoff experiment."""
import runpy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

MODULE = Path(__file__).resolve().parents[1] / 'bin/omadev'


class InputSetupTests(unittest.TestCase):
    def test_old_bindings_migrate_and_install_is_idempotent(self):
        for kind in ('LEGACY_BINDINGS_BLOCK', 'HANDOFF_BINDINGS_BLOCK', 'BINDINGS_BLOCK'):
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
                self.assertIn('hl.dsp.submap("reset")', path.read_text())

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
