"""Run: python3 -m unittest discover -s tests -p 'test_*.py' -v.
No compositor, root privileges or changes to the host config are needed.
"""
import importlib.machinery
import importlib.util
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import Mock, patch
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
loader = importlib.machinery.SourceFileLoader('omadev', str(ROOT / 'bin/omadev'))
spec = importlib.util.spec_from_loader(loader.name, loader)
omadev = importlib.util.module_from_spec(spec)
loader.exec_module(omadev)


def write(path, text, executable=False):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    if executable:
        path.chmod(0o755)


class CheckoutTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='omadev-test-')
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.checkout = self.base / 'checkout with spaces'
        self.real = self.base / 'real'
        self.home = self.base / 'home'
        self.real.mkdir()
        self.home.mkdir()
        self.overlay = ROOT / 'libexec/overlay'
        self.env = {**os.environ, 'HOME': str(self.home), 'OMADEV_HOME': str(self.home),
                    'OMADEV_SLOT': '9', 'OMADEV_ROOT': str(ROOT / 'libexec'),
                    'OMARCHY_PATH': str(self.checkout), 'OMADEV_OMARCHY_PATH': str(self.checkout),
                    'BASH_ENV': str(ROOT / 'libexec/bash-env'), 'PATH': '/usr/bin'}
        self.env.pop('OMADEV_BASHRC', None)
        write(self.checkout / 'bin/omarchy-probe', '#!/bin/bash\necho checkout-command\n', True)

    def run_bash(self, args, command, env=None):
        return subprocess.run(['/bin/bash', *args, command], env=env or self.env,
                              capture_output=True, text=True, timeout=10)

    def test_bash_defaults_and_path_in_all_shell_modes(self):
        write(self.checkout / 'default/bash/rc', '''
source "$OMARCHY_PATH/default/bash/envs"
function checkout_function { echo checkout-function; }
alias checkout_alias='echo checkout-alias'
''')
        write(self.checkout / 'default/bash/envs', '''
. /usr/share/omarchy/default/bash/env-bootstrap
export FROM_CHECKOUT=yes
''')
        write(self.real / '.bashrc', '''
source /usr/share/omarchy/default/bash/env-bootstrap
source "$OMARCHY_PATH/default/bash/rc"
PATH=/usr/bin
''')
        write(self.real / '.bash_profile', '. "$HOME/.bashrc"\nPATH=/usr/bin\n')
        omadev.write_bashrc(self.home, self.real, self.checkout)
        for mode in ('-ic', '-lc', '-lic'):
            with self.subTest(mode=mode):
                p = self.run_bash([mode], 'printf "%s\\n" "$OMARCHY_PATH" "$FROM_CHECKOUT" "$PATH"; '
                                  'checkout_function; alias checkout_alias; omarchy-probe')
                self.assertEqual(p.returncode, 0, p.stderr)
                lines = p.stdout.splitlines()
                self.assertEqual(lines[0], str(self.checkout))
                self.assertEqual(lines[1], 'yes')
                self.assertEqual(lines[2].split(':')[:2], [str(self.overlay), str(self.checkout / 'bin')])
                self.assertIn('checkout-function', lines)
                self.assertIn('checkout-command', lines)

    def test_noninteractive_bootstrap_and_nested_shells(self):
        p = self.run_bash(['-c'], '. -- /usr/share/omarchy/default/bash/env-bootstrap; '
                          'source -- "$OMARCHY_PATH/default/bash/env-bootstrap"; '
                          'omarchy-probe; bash -c \'printf "%s\\n" "$OMARCHY_PATH"; omarchy-probe\'')
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(p.stdout.splitlines(), ['checkout-command', str(self.checkout), 'checkout-command'])

    def test_path_repair_deduplicates_and_beats_foreign_checkout(self):
        env = {**self.env, 'PATH': f'/foreign/bin:{self.overlay}:/usr/bin:{self.checkout}/bin:{self.overlay}'}
        p = self.run_bash(['-c'], 'source "$BASH_ENV"; source "$BASH_ENV"; echo "$PATH"', env)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(p.stdout.strip().split(':'),
                         [str(self.overlay), str(self.checkout / 'bin'), '/foreign/bin', '/usr/bin'])

    def test_initial_environment_selects_checkout_before_hyprland(self):
        ns = SimpleNamespace(reuse=False, owner='test', scale='1', place='workspace', no_shell=False, idle=False)
        with patch.object(omadev, 'make_home', return_value=self.home), \
                patch.object(omadev, 'make_runtime', return_value=self.base / 'runtime'), \
                patch.object(omadev, 'session_defaults', side_effect=lambda env: env), \
                patch.dict(os.environ, {'WAYLAND_DISPLAY': 'host', 'HYPRLAND_INSTANCE_SIGNATURE': 'host',
                                        'DISPLAY': ':host', 'PATH': '/foreign/bin:/usr/bin'}):
            env = omadev.nest_environment(9, ns, self.checkout, [])
        self.assertEqual(env['PATH'].split(':')[:2], [str(self.overlay), str(self.checkout / 'bin')])
        self.assertEqual(env['BASH_ENV'], str(ROOT / 'libexec/bash-env'))
        self.assertEqual(env['OMADEV_OMARCHY_PATH'], str(self.checkout))
        self.assertNotIn('DISPLAY', env)
        self.assertNotIn('HYPRLAND_INSTANCE_SIGNATURE', env)

    def test_session_defaults_and_user_overrides(self):
        write(self.checkout / 'default/uwsm/default',
              'export EDITOR=checkout-editor CHECKOUT_ONLY=yes\nunset REMOVE_ME\n')
        write(self.home / '.config/uwsm/default', 'export EDITOR=user-editor\nPATH=/usr/bin\nHOME=/wrong\n')
        result = omadev.session_defaults({**self.env, 'REMOVE_ME': 'host'})
        self.assertEqual(result['EDITOR'], 'user-editor')
        self.assertEqual(result['CHECKOUT_ONLY'], 'yes')
        self.assertEqual(result['HOME'], str(self.home))
        self.assertNotIn('REMOVE_ME', result)
        self.assertIn('REMOVE_ME', result['OMADEV_SESSION_UNSET'].split())
        self.assertIn('CHECKOUT_ONLY', result['OMADEV_SESSION_KEYS'].split())
        self.assertEqual(result['PATH'].split(':')[:2], [str(self.overlay), str(self.checkout / 'bin')])

    def test_session_record_preserves_bootstrap_empty_multiline_and_unsets(self):
        runtime = self.base / 'runtime'
        records = runtime / 'omadev'
        target = records / '9/session.env'
        target.parent.mkdir(parents=True)
        write(runtime / 'hypr/nested/hyprland.lock', '12345\n')
        env = {**self.env, 'HYPRLAND_INSTANCE_SIGNATURE': 'nested',
               'OMADEV_ENV_FILE': str(target), 'OMADEV_SESSION_KEYS': 'EMPTY MULTILINE',
               'OMADEV_SESSION_UNSET': 'REMOVED DISPLAY', 'EMPTY': '', 'MULTILINE': 'one\ntwo=three'}
        with patch.dict(os.environ, env, clear=True), patch.object(omadev, 'RUNTIME_DIR', runtime), \
                patch.object(omadev, 'RUN_DIR', records), patch.object(omadev.subprocess, 'run'):
            omadev.cmd_publish([])
            record = omadev.read_env(9)
        self.assertEqual(record['BASH_ENV'], str(ROOT / 'libexec/bash-env'))
        self.assertEqual(record['EMPTY'], '')
        self.assertEqual(record['MULTILINE'], 'one\ntwo=three')
        with patch.dict(os.environ, {'REMOVED': 'host', 'EMPTY': 'host', 'DISPLAY': ':host'}):
            merged = omadev.session_environment(record)
        self.assertNotIn('REMOVED', merged)
        self.assertEqual(merged['EMPTY'], '')
        # The compositor may set a variable that defaults previously unset.
        if 'DISPLAY' in record:
            self.assertEqual(merged['DISPLAY'], record['DISPLAY'])
        else:
            self.assertNotIn('DISPLAY', merged)

    def test_session_defaults_fail_closed(self):
        write(self.checkout / 'default/uwsm/default', 'return 17\n')
        with self.assertRaises(omadev.Fail):
            omadev.session_defaults(self.env)
        process = Mock(pid=12345)
        process.communicate.side_effect = [subprocess.TimeoutExpired('bash', 15), (b'', b'')]
        with patch.object(omadev.subprocess, 'Popen', return_value=process), \
                patch.object(omadev.os, 'killpg') as killpg:
            with self.assertRaises(omadev.Fail):
                omadev.session_defaults(self.env)
            killpg.assert_called_once_with(12345, omadev.signal.SIGKILL)

    def test_private_hyprland_and_reuse_upgrade(self):
        write(self.real / '.config/omarchy/shell.json', '{}')
        write(self.real / '.local/state/omarchy/state', 'host-state')
        write(self.real / 'dotfiles/bindings.lua', 'host-bindings')
        (self.real / '.config/hypr').symlink_to(self.real / 'dotfiles', target_is_directory=True)
        write(self.real / '.config/terminal/settings', 'shared')
        with patch.object(omadev.Path, 'home', return_value=self.real), \
                patch.object(omadev, 'HOME_BASE', self.base / 'homes'):
            home = omadev.make_home(9, False, self.checkout)
            self.assertFalse((home / '.config/hypr').is_symlink())
            self.assertTrue((home / '.config/terminal').is_symlink())
            (home / '.config/hypr/bindings.lua').write_text('nest-only')
            self.assertEqual((self.real / 'dotfiles/bindings.lua').read_text(), 'host-bindings')
            shutil.rmtree(home / '.config/hypr')
            (home / '.config/hypr').symlink_to(self.real / 'dotfiles')
            omadev.make_home(9, True, self.checkout)
            self.assertFalse((home / '.config/hypr').is_symlink())
            self.assertEqual((home / '.config/hypr/bindings.lua').read_text(), 'host-bindings')

    def test_refresh_rejects_shared_and_escaping_paths(self):
        write(self.checkout / 'bin/omarchy-refresh-config', '#!/bin/bash\necho allowed:"$1"\n', True)
        (self.home / '.config/hypr').mkdir(parents=True)
        (self.home / '.config/omarchy').mkdir()
        (self.home / '.config/hypr/escape').symlink_to(self.real)
        for rel, allowed in [('hypr/bindings.lua', True), ('omarchy/shell.json', True),
                             ('terminal/settings', False), ('../anything', False),
                             ('/tmp/anything', False), ('hypr/../omarchy/shell.json', False),
                             ('hypr/escape/file', False)]:
            with self.subTest(rel=rel):
                p = subprocess.run([str(self.overlay / 'omarchy-refresh-config'), rel], env=self.env,
                                   text=True, capture_output=True, timeout=5)
                self.assertEqual(p.returncode == 0, allowed, p.stdout + p.stderr)

    def test_host_commands_are_refused(self):
        for name in ('sudo', 'pkexec', 'systemctl', 'omarchy-restart-terminal', 'omarchy-theme-set-pi'):
            with self.subTest(name=name):
                p = subprocess.run([str(self.overlay / name), '--help'], env=self.env,
                                   text=True, capture_output=True, timeout=5)
                self.assertNotEqual(p.returncode, 0)
                self.assertIn('affects the host', p.stderr)

    def test_theme_skips_host_hooks_and_reloads_only_nest(self):
        local_root = self.base / 'libexec'
        (local_root / 'overlay').mkdir(parents=True)
        log = self.base / 'theme.log'
        write(self.checkout / 'bin/omarchy-theme-set',
              '#!/bin/bash\necho "theme:$OMARCHY_THEME_HEADLESS:$*" >>"$TEST_LOG"\n', True)
        write(local_root / 'overlay/hyprctl', '#!/bin/bash\necho "hyprctl:$*" >>"$TEST_LOG"\n', True)
        write(local_root / 'overlay/omarchy-restart-shell', '#!/bin/bash\necho restart >>"$TEST_LOG"\n', True)
        env = {**self.env, 'OMADEV_ROOT': str(local_root), 'BASH_ENV': '',
               'PATH': f'{local_root}/overlay:/usr/bin', 'HYPRLAND_INSTANCE_SIGNATURE': 'nested',
               'TEST_LOG': str(log)}
        p = subprocess.run([str(self.overlay / 'omarchy-theme-set'), 'test theme'], env=env,
                           capture_output=True, text=True, timeout=5)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(log.read_text().splitlines(), ['theme:1:test theme', 'hyprctl:reload', 'restart'])

    def test_real_router_aliases_help_and_arguments_use_overlay(self):
        router = Path(os.environ.get('OMARCHY_PATH', '/usr/share/omarchy')) / 'bin/omarchy'
        if not router.is_file():
            self.skipTest('installed Omarchy router needed for integration test')
        shutil.copy2(router, self.checkout / 'bin/omarchy')
        write(self.checkout / 'bin/omarchy-restart-shell', '''#!/bin/bash
# omarchy:summary=Restart shell test
# omarchy:aliases=omarchy reload-desktop
echo UNSAFE
''', True)
        root = self.base / 'routing'
        (root / 'overlay').mkdir(parents=True)
        shutil.copy2(self.overlay / 'omarchy', root / 'overlay/omarchy')
        write(root / 'overlay/omarchy-restart-shell', '#!/bin/bash\nprintf "SAFE:%s\\n" "$*"\n', True)
        env = {**self.env, 'BASH_ENV': ''}
        for args in (['restart', 'shell'], ['restart-shell'], ['reload-desktop'],
                     ['restart', 'shell', '--', 'an argument']):
            with self.subTest(args=args):
                p = subprocess.run([str(root / 'overlay/omarchy'), *args], env=env,
                                   capture_output=True, text=True, timeout=5)
                self.assertEqual(p.returncode, 0, p.stderr)
                self.assertEqual(p.stdout.strip(), 'SAFE:' + ('-- an argument' if '--' in args else ''))
        for args in (['restart', 'shell', '--help'], ['restart-shell', '--help'],
                     ['reload-desktop', '--help']):
            p = subprocess.run([str(root / 'overlay/omarchy'), *args], env=env,
                               capture_output=True, text=True, timeout=5)
            self.assertEqual(p.returncode, 0, p.stderr)
            self.assertIn('Usage:', p.stdout)
            self.assertNotIn('SAFE:', p.stdout)
            self.assertNotIn('UNSAFE', p.stdout)


if __name__ == '__main__':
    unittest.main()
