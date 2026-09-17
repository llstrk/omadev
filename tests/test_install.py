"""Required helper failures stop installation, with fake tools and a private home."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]

class InstallTests(unittest.TestCase):
    def test_failed_required_build_stops_installation(self):
        self.check_fallback(True)

    def test_missing_toolchain_discards_stale_helper(self):
        self.check_fallback(False)

    def check_fallback(self, toolchain):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            source = base/'source'
            tools = base/'tools'
            home = base/'home'
            for p in (source/'bin', tools, home): p.mkdir(parents=True)
            shutil.copy2(ROOT/'install.sh', source/'install.sh')
            stale = source/'libexec/input-gate/keyboard-gate.so'
            stale.parent.mkdir(parents=True)
            stale.write_text('obsolete helper')
            def script(path, body):
                path.write_text('#!/bin/bash\n'+body+'\n')
                path.chmod(0o755)
            script(source/'bin/build-input-gate', 'exit 7')
            script(source/'bin/omadev', 'test "$1" = setup && touch "$HOME/setup-ran"')
            for tool in ('cc', 'pkg-config'): script(tools/tool, 'exit 0')
            result = subprocess.run(['bash', str(source/'install.sh')], capture_output=True, text=True,
                                    env={**{k:v for k,v in os.environ.items() if k != 'OMADEV_SLOT'}, 'HOME':str(home), 'CC':'cc' if toolchain else '/missing/test-compiler',
                                         'PATH':str(tools)+':/usr/bin'}, timeout=10)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('required input-gate build failed' if toolchain else 'installation requires', result.stderr)
            self.assertFalse((home/'setup-ran').exists())
            self.assertFalse(stale.exists())
            self.assertFalse((home/'.local/bin/omadev').exists())
