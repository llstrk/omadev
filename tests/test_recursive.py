"""Recursive starts are opt-in and keep compositor socket paths short."""
import os
from pathlib import Path
import runpy
import tempfile
import unittest
from unittest.mock import patch

m = runpy.run_path(str(Path(__file__).resolve().parents[1] / 'bin/omadev'))


class RecursiveTests(unittest.TestCase):
    def test_guard_requires_force(self):
        with patch.dict(os.environ, WAYLAND_DISPLAY='wayland-2', OMADEV_SLOT='5'):
            with self.assertRaisesRegex(m['Fail'], 'use --force'):
                m['validate_start'](m['parse_start']([]))

    def test_force_passes_guard_but_still_validates_tools(self):
        with patch.dict(os.environ, WAYLAND_DISPLAY='wayland-2', OMADEV_SLOT='5'), \
                patch('shutil.which', return_value=None):
            with self.assertRaisesRegex(m['Fail'], 'Hyprland is not installed'):
                m['validate_start'](m['parse_start'](['--force']))

    def test_runtime_uses_shared_registry_parent(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'omadev').mkdir()
            parent = root / 'omadev-5'
            parent.mkdir()
            (parent / 'omadev').symlink_to(root / 'omadev')
            with patch.dict(os.environ, OMADEV_SLOT='5'), patch.dict(
                    m['nest_runtime'].__globals__, RUNTIME_DIR=parent,
                    RUN_DIR=parent / 'omadev'):
                self.assertEqual(m['nest_runtime'](6), root / 'omadev-6')


if __name__ == '__main__':
    unittest.main()
