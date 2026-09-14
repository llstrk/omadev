"""Safe cleanup of disconnected per-nest mounts, without real mount operations."""
import runpy
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

module = runpy.run_path(str(Path(__file__).resolve().parents[1] / 'bin/omadev'))
remove_runtime = module['remove_runtime']
Fail = module['Fail']


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.run = self.base / 'omadev-2'
        self.run.mkdir()
        (self.run / 'marker').touch()
        self.env = patch.dict(remove_runtime.__globals__, RUNTIME_DIR=self.base)
        self.env.start()
        self.addCleanup(self.env.stop)

    def mount(self, target, kind='fuse.gvfsd-fuse'):
        return f'42 1 0:1 / {target} rw - {kind} gvfsd-fuse rw\n'

    def test_disconnected_gvfs_is_detached_before_removal(self):
        with patch.object(Path, 'read_text', return_value=self.mount(self.run / 'gvfs')), \
                patch('subprocess.run', return_value=subprocess.CompletedProcess([], 0)) as detach:
            remove_runtime(2)
        detach.assert_called_once_with(['fusermount3', '-u', '-z', str(self.run / 'gvfs')],
                                       capture_output=True, text=True, timeout=10)
        self.assertFalse(self.run.exists())

    def test_failed_unmount_preserves_runtime(self):
        with patch.object(Path, 'read_text', return_value=self.mount(self.run / 'gvfs')), \
                patch('subprocess.run', return_value=subprocess.CompletedProcess([], 1, stderr='busy')):
            with self.assertRaises(Fail):
                remove_runtime(2)
        self.assertTrue((self.run / 'marker').exists())

    def test_unexpected_mount_is_not_traversed(self):
        with patch.object(Path, 'read_text', return_value=self.mount(self.run / 'other', 'tmpfs')), \
                patch('subprocess.run') as detach:
            with self.assertRaises(Fail):
                remove_runtime(2)
        detach.assert_not_called()
        self.assertTrue((self.run / 'marker').exists())

    def test_other_sessions_and_host_links_are_untouched(self):
        host = self.base / 'host'
        host.mkdir()
        (host / 'marker').touch()
        (self.run / 'shared').symlink_to(host)
        with patch.object(Path, 'read_text', return_value=self.mount(self.base / 'omadev-1/gvfs')), \
                patch('subprocess.run') as detach:
            remove_runtime(2)
        detach.assert_not_called()
        self.assertTrue((host / 'marker').exists())


if __name__ == '__main__':
    unittest.main()
