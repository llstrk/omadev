"""Run the real installer/helper/setup with a private HOME and fake build/Hyprland tools.

No subprocess is mocked. A private PTY supplies confirmation, never host keyboard
input. Only the compiler, pkg-config and hyprctl are stubs; no live desktop is used.
"""
import json
import os
from pathlib import Path
import pty
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class BackendInstallTests(unittest.TestCase):
    def test_plugin_install_confirmation_snapshot_registration_and_setup(self):
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary)/'home'; home.mkdir()
            source = home/'.config/omarchy/plugins/dry.omadev'
            source.mkdir(parents=True)
            for name in ('bin','libexec','skill','plugin'):
                shutil.copytree(ROOT/name, source/name, symlinks=True,
                                ignore=shutil.ignore_patterns('__pycache__','*.so','*.tmp'))
            for name in ('install.sh','manifest.json'):
                shutil.copy2(ROOT/name, source/name)
            (source/'.git').mkdir(); (source/'.git/HEAD').write_text('ref: refs/heads/main\n')
            before = {str(p.relative_to(source)):p.read_bytes() for p in source.rglob('*') if p.is_file()}
            tools = home/'tools'; tools.mkdir()
            def script(name, body):
                path = tools/name; path.write_text('#!/bin/bash\n'+body+'\n'); path.chmod(0o755)
            script('pkg-config','exit 0')
            script('cc','while (( $# )); do if [[ $1 == -o ]]; then shift; printf fixture-helper > "$1"; exit 0; fi; shift; done; exit 1')
            script('hyprctl','printf "%s\\n" "$*" >> "$HOME/hyprctl-calls"; exit 0')
            env = {'HOME':str(home), 'PATH':str(tools)+':/usr/bin:/bin',
                   'XDG_CONFIG_HOME':str(home/'.config'), 'XDG_DATA_HOME':str(home/'.local/share'),
                   'XDG_STATE_HOME':str(home/'.local/state'), 'XDG_CACHE_HOME':str(home/'.cache'),
                   'XDG_RUNTIME_DIR':str(home/'runtime'), 'CC':'cc'}
            # Deliberately no inherited display, bus, agent, or nest variables.
            def terminal(argv, answer):
                master, slave = pty.openpty()
                process = subprocess.Popen(argv, stdin=slave, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                           env=env, cwd=home, text=True)
                os.close(slave)
                try:
                    os.write(master, (answer+'\n').encode())
                    stdout, stderr = process.communicate(timeout=30)
                finally:
                    if process.poll() is None:
                        process.kill(); process.wait()
                    os.close(master)
                self.assertEqual(process.returncode, 0, stdout+stderr)
                return stdout
            # Exercises install.sh's plugin-directory branch and the actual
            # interactive backend.py confirmation, not just install_snapshot().
            cancelled = terminal(['bash',str(source/'install.sh')], 'n')
            self.assertIn('Cancelled', cancelled)
            self.assertFalse((home/'.local/share/omadev/backends').exists())
            output = terminal(['bash',str(source/'install.sh')], 'y')
            self.assertIn('Installing an independent backend snapshot', output)
            backends = home/'.local/share/omadev/backends'
            releases = list(backends.iterdir())
            self.assertEqual(len(releases), 1)
            installed = releases[0]
            launcher = home/'.local/bin/omadev'
            self.assertEqual(launcher.resolve(), installed/'bin/omadev')
            registry = json.loads((home/'.config/omadev/backend.json').read_text())
            self.assertEqual(registry['launcher'], str(installed/'bin/omadev'))
            self.assertNotIn(str(source), registry['launcher'])
            self.assertFalse((installed/'.git').exists())
            self.assertFalse((source/'libexec/input-gate/keyboard-gate.so').exists())
            capabilities = json.loads(subprocess.check_output([str(launcher),'capabilities','--json'],env=env,text=True))
            self.assertEqual(capabilities['state'], 'ready')
            self.assertEqual(capabilities['bindingsStatus'], 'ready')
            # Exercise backend.py's setup action too, including discovery of the
            # registered snapshot and executing its real setup command.
            output = terminal(['/usr/bin/python3',str(source/'plugin/dry.omadev/backend.py'),'setup'], 'y')
            self.assertIn('backend ready', output)
            self.assertEqual(len(list(backends.iterdir())), 1)
            self.assertEqual(before, {str(p.relative_to(source)):p.read_bytes() for p in source.rglob('*') if p.is_file()})
            self.assertTrue((home/'hyprctl-calls').exists())
