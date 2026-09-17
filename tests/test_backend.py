"""Backend setup/discovery contracts. All writes stay in temporary homes."""
import json
import os
import subprocess
from pathlib import Path
import runpy
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT/'bin/omadev'
HELPER = ROOT/'plugin/dry.omadev/backend.py'


class SetupTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)
        self.config = self.home/'.config'
        self.lib = self.home/'backend/libexec'
        self.native = self.lib/'input-gate/keyboard-gate.so'
        self.native.parent.mkdir(parents=True)
        self.native.touch()
        self.m = runpy.run_path(str(CLI))
        self.env = patch.dict(os.environ, {'HOME':str(self.home)}, clear=True)
        self.env.start(); self.addCleanup(self.env.stop)
        self.globals = patch.dict(self.m['cmd_setup'].__globals__, CONFIG_DIR=self.config, ROOT=self.lib)
        self.globals.start(); self.addCleanup(self.globals.stop)

    def test_setup_preserves_git_managed_and_legacy_widgets(self):
        for git_managed in (False, True):
            directory = self.config/'omarchy/plugins/dry.omadev'
            directory.mkdir(parents=True, exist_ok=True)
            if git_managed:
                (directory/'.git').mkdir()
                (directory/'.git/HEAD').write_text('ref: refs/heads/main\n')
            (directory/'Omadev.qml').write_text('user edits must survive')
            before = {str(p.relative_to(directory)):p.read_bytes() for p in directory.rglob('*') if p.is_file()}
            with patch('subprocess.run'):
                self.assertEqual(self.m['cmd_setup']([]), 0)
            after = {str(p.relative_to(directory)):p.read_bytes() for p in directory.rglob('*') if p.is_file()}
            self.assertEqual(before, after)
        record = json.loads((self.config/'omadev/backend.json').read_text())
        self.assertEqual(record, {'schemaVersion':1, 'launcher':str(CLI.resolve())})
        self.assertEqual(self.m['backend_capabilities']()['state'], 'ready')

    def test_missing_helper_changes_nothing(self):
        self.native.unlink()
        with patch('subprocess.run') as run:
            with self.assertRaisesRegex(self.m['Fail'], 'build first'):
                self.m['cmd_setup']([])
        run.assert_not_called()
        self.assertFalse(self.config.exists())

    def test_custom_bindings_are_preserved_and_reported_unconfigured(self):
        bindings = self.config/'hypr/bindings.lua'
        bindings.parent.mkdir(parents=True)
        bindings.write_text('-- omadev: custom bindings\n')
        with patch('subprocess.run'):
            with self.assertRaisesRegex(self.m['Fail'], 'custom bindings were preserved'):
                self.m['cmd_setup']([])
        self.assertEqual(bindings.read_text(), '-- omadev: custom bindings\n')
        self.assertEqual(self.m['backend_capabilities']()['state'], 'unconfigured')
        self.assertEqual(self.m['backend_capabilities']()['bindingsStatus'], 'custom')

    def test_capabilities_are_read_only_and_need_no_widget(self):
        before = sorted(str(p) for p in self.home.rglob('*'))
        with patch('subprocess.run') as run, patch('builtins.print') as output:
            self.assertEqual(self.m['cmd_capabilities'](['--json']), 0)
        data = json.loads(output.call_args.args[0])
        self.assertEqual(data['application'], 'omadev')
        self.assertEqual(data['protocol'], 1)
        self.assertEqual(data['state'], 'unconfigured')
        self.assertEqual(before, sorted(str(p) for p in self.home.rglob('*')))
        run.assert_not_called()

    def test_skills_do_not_write_through_shared_nest_home_links(self):
        outside = self.home/'external-skills'
        outside.mkdir()
        target = outside/'omadev'
        target.write_text('preserve')
        nest = self.home/'nest'; (nest/'.agents').mkdir(parents=True)
        (nest/'.agents/skills').symlink_to(outside)
        with patch.dict(os.environ, HOME=str(nest)):
            message = self.m['setup_skills']()
        self.assertIn('skipped (outside home)', message)
        self.assertEqual(target.read_text(), 'preserve')


class DiscoveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)
        self.env = {'HOME':str(self.home), 'PATH':'/nonexistent'}
        self.m = runpy.run_path(str(HELPER))

    def executable(self, path, data):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('#!/usr/bin/python3\nimport json\nprint('+repr(json.dumps(data))+')\n')
        path.chmod(0o755)
        return path

    def response(self, path, **extra):
        return {'application':'omadev','protocol':1,'launcher':str(path),'version':'test',
                'nativeHelperPresent':True,'bindingsConfigured':True, **extra}

    def test_missing_backend(self):
        self.assertEqual(self.m['probe'](self.env)['state'], 'missing')

    def test_states_and_registry_precedence_over_path(self):
        path = self.home/'registered/omadev'
        config = self.home/'.config/omadev'; config.mkdir(parents=True)
        (config/'backend.json').write_text(json.dumps({'schemaVersion':1,'launcher':str(path)}))
        other = self.home/'.local/bin/omadev'
        self.executable(other, self.response(other))
        for extra, expected in [({},'ready'), ({'bindingsConfigured':False},'unconfigured'),
                                ({'nativeHelperPresent':False},'build-required'), ({'protocol':99},'incompatible')]:
            self.executable(path, self.response(path, **extra))
            state = self.m['probe'](self.env)
            self.assertEqual(state['state'], expected)
            self.assertEqual(state['launcher'], str(path))

    def test_transient_probe_errors_are_not_incompatible(self):
        path = self.home/'.local/bin/omadev'
        self.executable(path, self.response(path))
        for error in (subprocess.TimeoutExpired('probe',3), OSError('temporary execution failure')):
            with patch('subprocess.run', side_effect=error):
                self.assertEqual(self.m['probe'](self.env)['state'], 'unavailable')
        path.write_text('#!/bin/sh\necho temporary-error >&2\nexit 1\n')
        self.assertEqual(self.m['probe'](self.env)['state'], 'unavailable')
        path.write_text('#!/bin/sh\necho "unknown command: capabilities" >&2\nexit 1\n')
        self.assertEqual(self.m['probe'](self.env)['state'], 'incompatible')

    def test_custom_binding_status_explains_manual_repair(self):
        path = self.home/'.local/bin/omadev'
        self.executable(path, self.response(path, bindingsConfigured=False, bindingsStatus='custom'))
        state = self.m['probe'](self.env)
        self.assertTrue(state['compatible'])
        self.assertEqual(state['state'], 'unconfigured')
        self.assertIn('manual configuration', state['message'])
        self.assertIn('cannot repair', state['message'])

    def test_nested_discovery_pins_its_launcher(self):
        root = self.home/'checkout/libexec'; root.mkdir(parents=True)
        path = root.parent/'bin/omadev'
        self.executable(path, self.response(path))
        env = {**self.env, 'OMADEV_SLOT':'2','OMADEV_ROOT':str(root)}
        self.assertEqual(self.m['probe'](env)['launcher'], str(path))
        path.unlink()
        self.executable(self.home/'.local/bin/omadev', {})
        self.assertEqual(self.m['probe'](env)['state'], 'missing')

    def test_actions_refuse_without_a_terminal_and_inside_nests(self):
        with patch.dict(os.environ, self.env, clear=True), patch('sys.stdin.isatty', return_value=False), patch('subprocess.run') as run:
            with self.assertRaisesRegex(RuntimeError, 'confirmation'):
                self.m['main'](['install'])
        run.assert_not_called()
        with self.assertRaisesRegex(RuntimeError, 'real host'):
            self.m['install_snapshot'](env={**self.env,'OMADEV_SLOT':'2'})

    def test_cancelled_install_has_no_side_effects(self):
        main = self.m['main']
        with patch.dict(os.environ, self.env, clear=True), patch('sys.stdin.isatty', return_value=True), \
             patch('builtins.input', return_value='n'), patch.dict(main.__globals__, install_snapshot=lambda: self.fail('unexpected install')), \
             patch('subprocess.run') as run:
            self.assertEqual(main(['install']), 0)
        run.assert_not_called()
        self.assertEqual(list(self.home.iterdir()), [])

    def test_missing_skill_is_rejected_before_creating_snapshot(self):
        source = self.home/'source'
        for name in ('bin/omadev','bin/build-input-gate','libexec/input-gate/keyboard-gate.c','install.sh'):
            file = source/name; file.parent.mkdir(parents=True,exist_ok=True); file.touch()
        with self.assertRaisesRegex(RuntimeError, 'sources are unavailable'):
            self.m['install_snapshot'](source, self.env)
        self.assertFalse((self.home/'.local/share/omadev/backends').exists())

    def test_install_is_a_separate_snapshot_without_widget_or_git(self):
        source = self.home/'plugin'; source.mkdir()
        for name in ('bin/omadev','bin/build-input-gate','libexec/input-gate/keyboard-gate.c','install.sh','skill/SKILL.md'):
            file = source/name; file.parent.mkdir(parents=True,exist_ok=True); file.write_text('source')
        (source/'libexec/input-gate/keyboard-gate.so').write_text('stale helper')
        (source/'.git').mkdir()
        before = (source/'bin/omadev').read_text()
        with patch('subprocess.run') as run:
            target = self.m['install_snapshot'](source, self.env)
        self.assertFalse(target.is_relative_to(source))
        self.assertFalse((target/'.git').exists())
        self.assertFalse((target/'libexec/input-gate/keyboard-gate.so').exists())
        self.assertEqual((target/'bin/omadev').read_text(), before)
        (source/'bin/omadev').write_text('plugin updated')
        self.assertEqual((target/'bin/omadev').read_text(), before)
        self.assertEqual(run.call_args.args[0], ['bash',str(target/'install.sh')])
