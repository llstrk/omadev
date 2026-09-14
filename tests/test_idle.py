"""Private idle defaults, no desktop or IPC needed."""
import runpy
import tempfile
import unittest
from pathlib import Path

module = runpy.run_path(str(Path(__file__).resolve().parents[1] / 'bin/omadev'))
configure_idle = module['configure_idle']
Fail = module['Fail']


class IdleTests(unittest.TestCase):
    def test_default_and_explicit_idle(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / 'nest'
            home.mkdir()
            marker = home / '.local/state/omarchy/indicators/stay-awake'
            configure_idle(home, False)
            self.assertTrue(marker.is_file())
            configure_idle(home, False)
            self.assertTrue(marker.is_file())
            configure_idle(home, True)
            self.assertFalse(marker.exists())
            configure_idle(home, True)
            configure_idle(home, False)
            self.assertTrue(marker.is_file())

    def test_cannot_write_through_host_symlink(self):
        with tempfile.TemporaryDirectory() as tmp:
            home, host = Path(tmp) / 'nest', Path(tmp) / 'host'
            (home / '.local/state').mkdir(parents=True)
            host.mkdir()
            (home / '.local/state/omarchy').symlink_to(host)
            with self.assertRaises(Fail):
                configure_idle(home, False)
            self.assertFalse((host / 'indicators/stay-awake').exists())
