"""Gate controller and launcher contracts, no desktop or keyboard capture."""
import json
import os
from pathlib import Path
import runpy
import sys
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'libexec'))
from input_gate import Controller, native_status, request


class GateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.directory = Path(self.tmp.name)
        self.host = ('123', 'nested')
        self.state = {'protocol': 1, 'pid': 123, 'keyboards': 1, 'focusEpoch': 7,
                      'entered': True, 'forwarded': 0, 'generation': 0, 'state': 'uncaptured'}
        self.gate = Controller(self.directory, lambda: '123', lambda: self.host)

    def status_file(self, **values):
        state = {**self.state, 'updatedNs': time.monotonic_ns(), **values}
        (self.directory / 'lease.status').write_text(json.dumps(state))

    def fake_native(self):
        def status():
            return {**self.state, 'generation': self.gate.generation,
                    'state': ('pending' if self.state.get('pending') else 'captured')
                    if self.gate.generation else 'uncaptured'}
        self.gate.status = status

    def test_readiness_requires_fresh_protocol_keyboard_and_pid(self):
        self.assertEqual(native_status(self.directory)['state'], 'unavailable')
        self.status_file()
        self.assertEqual(native_status(self.directory, 123)['state'], 'uncaptured')
        for values in ({'pid': 456}, {'keyboards': 0}, {'protocol': 2},
                       {'updatedNs': time.monotonic_ns() - 1_000_000_000},
                       {'updatedNs': time.monotonic_ns() + 1_000_000_000}):
            self.status_file(**values)
            self.assertEqual(native_status(self.directory, 123)['state'], 'unavailable')
        (self.directory / 'lease.status').write_text('{')
        self.assertEqual(native_status(self.directory)['state'], 'unavailable')

    def test_capture_binds_generation_to_pid_and_focus_epoch(self):
        self.fake_native()
        self.assertEqual(self.gate.capture()['state'], 'captured')
        fields = list(map(int, (self.directory / 'lease').read_text().split()))
        self.assertEqual(fields[0], self.gate.generation)
        self.assertGreater(fields[1], time.monotonic_ns())
        self.assertEqual(fields[2:], [7, 123])
        self.assertEqual((self.directory / 'lease').stat().st_mode & 0o777, 0o600)

    def test_pending_is_acknowledged_but_not_reported_captured(self):
        self.fake_native()
        self.state['pending'] = True
        self.assertEqual(self.gate.capture()['state'], 'pending')

    def test_no_capture_without_focused_nested_submap(self):
        self.fake_native()
        for self.host in [('other', 'nested'), ('123', 'default')]:
            with self.assertRaisesRegex(RuntimeError, 'focused'):
                self.gate.capture()
            self.assertFalse((self.directory / 'lease').exists())

    def test_heartbeat_revokes_on_focus_epoch_pid_or_submap_changes(self):
        self.fake_native()
        for field, value in [('focusEpoch', 8), ('pid', 124)]:
            self.state.update(focusEpoch=7, pid=123)
            self.gate.capture()
            self.state[field] = value
            self.gate.heartbeat()
            self.assertEqual(self.gate.generation, 0)
        self.state.update(focusEpoch=7, pid=123)
        self.gate.capture()
        self.host = ('123', 'default')
        self.gate.heartbeat()
        self.assertFalse((self.directory / 'lease').exists())

    def test_expired_heartbeat_cannot_renew(self):
        self.fake_native()
        self.gate.capture()
        self.gate.deadline = time.monotonic_ns() - 1
        self.gate.heartbeat()
        self.assertEqual(self.gate.generation, 0)

    def test_focus_loss_requests_host_recovery_after_revoking_lease(self):
        self.fake_native()
        self.gate.capture()
        self.host = ('456', 'nested')
        observations = []
        self.gate.restore_host = lambda: observations.append(
            (self.gate.generation, (self.directory / 'lease').exists()))
        self.gate.heartbeat()
        self.assertEqual(observations, [(0, False)])

    def test_idle_recovery_retries_without_busy_polling(self):
        self.gate.restore_host = Mock()
        with patch('input_gate.time.monotonic', return_value=10):
            self.gate.heartbeat()
            self.gate.heartbeat()
        self.gate.restore_host.assert_called_once_with()
        with patch('input_gate.time.monotonic', return_value=10.5):
            self.gate.heartbeat()
        self.assertEqual(self.gate.restore_host.call_count, 2)

    def test_active_capture_does_not_request_host_recovery(self):
        self.fake_native()
        self.gate.capture()
        self.gate.restore_host = Mock()
        self.gate.heartbeat()
        self.gate.restore_host.assert_not_called()

    def test_release_waits_for_native_ack(self):
        self.fake_native()
        self.gate.capture()
        state = self.gate.release()
        self.assertEqual(state['state'], 'uncaptured')
        self.assertFalse((self.directory / 'lease').exists())

    def test_control_socket_roundtrip_and_unknown_requests_fail_closed(self):
        self.fake_native()
        self.gate.start()
        self.addCleanup(self.gate.stop)
        self.assertEqual(request(self.directory, 'capture')['state'], 'captured')
        with self.assertRaisesRegex(RuntimeError, 'unknown'):
            request(self.directory, 'anything')
        self.assertEqual(self.gate.generation, 0)
        self.assertEqual(request(self.directory, 'release')['state'], 'uncaptured')

    def test_stopping_controller_removes_lease(self):
        self.fake_native()
        self.gate.start()
        request(self.directory, 'capture')
        self.gate.stop()
        self.assertFalse((self.directory / 'lease').exists())
        self.assertFalse((self.directory / 'control.sock').exists())


class LauncherGateTests(unittest.TestCase):
    def setUp(self):
        self.module = runpy.run_path(str(ROOT / 'bin/omadev'))

    def test_list_json_exposes_native_gate_state_and_legacy_mode(self):
        fn = self.module['slot_json']
        record = {'OMADEV_PID':'123', 'OMADEV_INPUT_GATE':'1', 'OMADEV_INPUT_GATE_DIR':'/test'}
        with patch.dict(fn.__globals__, read_env=lambda _:record, slot_state=lambda *_:'running',
                        nest_home=lambda _:None, gate_status=lambda directory,pid:{'state':'pending','pid':pid}):
            self.assertEqual(fn(2)['input_gate'], {'state':'pending','pid':123})
            record['OMADEV_INPUT_GATE'] = '0'
            self.assertEqual(fn(2)['input_gate'], {'state':'legacy'})

    def test_gate_is_required_by_default_and_old_flag_is_compatible(self):
        for args in ([], ['--detach'], ['--no-shell'], ['--input-gate']):
            self.assertTrue(self.module['parse_start'](args).input_gate)

    def test_default_start_refuses_missing_native_helper(self):
        fn = self.module['validate_start']
        with tempfile.TemporaryDirectory() as tmp, \
             patch.dict(fn.__globals__, ROOT=Path(tmp)), \
             patch.dict(os.environ, {'WAYLAND_DISPLAY':'test'}, clear=True), \
             patch('shutil.which', return_value='/usr/bin/test'):
            with self.assertRaisesRegex(self.module['Fail'], 'input gate is not built'):
                fn(self.module['parse_start']([]))

    def test_preload_is_not_given_to_dbus_or_watchdog(self):
        launch = self.module['launch_compositor']
        env = {'OMADEV_INPUT_GATE': '1', 'OMADEV_INPUT_GATE_DIR': '/private/gate',
               'LD_PRELOAD': '/original.so'}
        with patch.dict(launch.__globals__, private_bus_config=lambda _slot: '/private/bus'), \
             patch('shutil.which', side_effect=lambda tool: '/usr/bin/' + tool), \
             patch('subprocess.Popen') as start:
            launch(2, env, [])
        args = start.call_args.args[0]
        child_env = start.call_args.kwargs['env']
        self.assertIn('--path', args)
        self.assertIn(str(ROOT/'libexec/input-gate/Hyprland'), args)
        self.assertEqual(child_env['LD_PRELOAD'], '/original.so')
        self.assertNotIn('OMADEV_KEYBOARD_GATE', child_env)
        self.assertEqual(env['LD_PRELOAD'], '/original.so')

    def test_missing_native_handshake_exits_without_publishing(self):
        publish = self.module['cmd_publish']
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            lock = directory/'hypr/test/hyprland.lock'
            lock.parent.mkdir(parents=True)
            lock.write_text('123\n')
            target = directory/'session.env'
            with patch.dict(os.environ, {'HYPRLAND_INSTANCE_SIGNATURE':'test', 'OMADEV_INPUT_GATE':'1',
                                         'OMADEV_INPUT_GATE_DIR':str(directory/'gate'), 'OMADEV_ENV_FILE':str(target)}), \
                 patch.dict(publish.__globals__, RUNTIME_DIR=directory, wait_until=lambda *_: False), \
                 patch('subprocess.run') as run:
                with self.assertRaisesRegex(self.module['Fail'], 'refusing an unguarded'):
                    publish([])
                self.assertIn(['hyprctl','dispatch','hl.dsp.exit()'], [call.args[0] for call in run.call_args_list])
            self.assertFalse(target.exists())

    def test_forwarder_uses_immediate_host_config_and_bus(self):
        fn = self.module['forward_input_command']
        env = {'OMADEV_SLOT':'2', 'OMADEV_INPUT_GATE':'1', 'OMADEV_HOST_SIG':'parent',
               'OMADEV_HOST_RUNTIME':'/parent/run', 'OMADEV_HOST_HOME':'/parent/home',
               'OMADEV_HOST_CONFIG':'/parent/config', 'OMADEV_HOST_BUS':'unix:path=/parent/bus',
               'HOME':'/nest/home', 'XDG_CONFIG_HOME':'/nest/config', 'XDG_RUNTIME_DIR':'/nest/run',
               'DBUS_SESSION_BUS_ADDRESS':'unix:path=/nest/bus'}
        with patch.dict(os.environ, env, clear=True), patch('subprocess.call', return_value=0) as call:
            self.assertEqual(fn('focus', ['2']), 0)
        forwarded = call.call_args.kwargs['env']
        for key, expected in [('HOME','/parent/home'), ('XDG_CONFIG_HOME','/parent/config'),
                              ('XDG_RUNTIME_DIR','/parent/run'), ('HYPRLAND_INSTANCE_SIGNATURE','parent'),
                              ('DBUS_SESSION_BUS_ADDRESS','unix:path=/parent/bus')]:
            self.assertEqual(forwarded[key], expected)
        self.assertNotIn('OMADEV_SLOT', forwarded)
        self.assertNotIn('OMADEV_HOST_CONFIG', forwarded)

    def test_old_gated_nest_cannot_check_cloned_config(self):
        with patch.dict(os.environ, {'OMADEV_SLOT':'2', 'OMADEV_INPUT_GATE':'1',
                                    'OMADEV_HOST_SIG':'parent', 'OMADEV_HOST_RUNTIME':'/parent/run'}, clear=True), \
             patch('subprocess.call') as call:
            with self.assertRaisesRegex(self.module['Fail'], 'restart this gated nest'):
                self.module['forward_input_command']('focus', ['2'])
        call.assert_not_called()

    def test_status_watcher_rechecks_freshness_without_new_processes(self):
        fn = self.module['cmd_input_status']
        states = iter([{'pid':123, 'state':'captured'}, {'state':'unavailable'}])
        class Done(Exception): pass
        samples = []
        def sample(*_args):
            try: return next(states)
            except StopIteration: raise Done
        with patch.dict(os.environ, {'OMADEV_INPUT_GATE':'1','OMADEV_INPUT_GATE_DIR':'/test'}, clear=True), \
             patch.dict(fn.__globals__, gate_status=sample), patch('time.sleep'), \
             patch('builtins.print', side_effect=lambda text, **_: samples.append(json.loads(text))), \
             patch('subprocess.run') as run:
            with self.assertRaises(Done): fn(['--watch'])
        self.assertEqual([s['state'] for s in samples], ['captured','unavailable'])
        run.assert_not_called()

    def test_capture_guard_precedes_any_host_action(self):
        with patch.dict(os.environ, {'OMADEV_ALLOW_CAPTURE': '0'}), patch('subprocess.run') as run:
            with self.assertRaises(self.module['Fail']):
                self.module['cmd_focus'](['2'])
        run.assert_not_called()

    def test_gate_release_ack_precedes_submap_reset(self):
        fn = self.module['cmd_release']
        events = []
        with tempfile.TemporaryDirectory() as tmp, \
             patch.dict(fn.__globals__, RUN_DIR=Path(tmp),
                        forward_input_command=lambda *_: None,
                        release_gate_records=lambda: events.append('ack')), \
             patch('subprocess.run', side_effect=lambda *_a, **_kw: events.append('reset') or type('Result', (), {'returncode': 0})()):
            self.assertEqual(fn([]), 0)
        self.assertEqual(events, ['ack', 'reset'])

    def test_failed_release_never_resets_routing(self):
        fn = self.module['cmd_release']
        notifications = []
        def fail():
            raise self.module['Fail']('no acknowledgement')
        with tempfile.TemporaryDirectory() as tmp, \
             patch.dict(fn.__globals__, RUN_DIR=Path(tmp), forward_input_command=lambda *_: None, release_gate_records=fail), \
             patch.dict(fn.__globals__, notify=lambda message: notifications.append(message)), \
             patch('subprocess.run') as run:
            with self.assertRaises(self.module['Fail']):
                fn([])
        run.assert_not_called()
        self.assertEqual(len(notifications), 1)
        self.assertIn('Super+Shift+Alt+D', notifications[0])
        self.assertIn('cleanup unconfirmed', notifications[0])


class HostRoutingRecoveryTests(unittest.TestCase):
    """All compositor calls are mocked; never select a real host submap."""
    def setUp(self):
        self.module = runpy.run_path(str(ROOT / 'bin/omadev'))
        self.fn = self.module['restore_idle_host_routing']
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.record = {'OMADEV_HOST_SIG': 'test-host', 'OMADEV_PID': '123',
                       'OMADEV_INPUT_GATE': '1', 'OMADEV_INPUT_GATE_DIR': '/test/gate'}
        self.records = {1: self.record}
        self.health = 'alive'
        self.state = {'state': 'uncaptured', 'generation': 0, 'forwarded': 0}
        self.submap = 'nested'
        self.locked = False
        globals_patch = patch.dict(self.fn.__globals__, RUN_DIR=Path(self.tmp.name),
                                  SLOTS=(1, 2), read_env=lambda n: self.records.get(n, {}),
                                  slot_locked=lambda _: self.locked,
                                  slot_health=lambda *_: self.health,
                                  gate_status=lambda *_: self.state)
        globals_patch.start()
        self.addCleanup(globals_patch.stop)
        self.run = Mock(side_effect=lambda *a, **kw: type('Result', (), {
            'stdout': self.submap + '\n', 'returncode': 0})())
        run_patch = patch('subprocess.run', self.run)
        run_patch.start()
        self.addCleanup(run_patch.stop)

    def resets(self):
        return [call.args[0] for call in self.run.call_args_list if 'dispatch' in call.args[0]]

    def test_stale_nested_mode_resets_after_all_gates_acknowledge(self):
        self.fn(self.record)
        self.assertEqual(self.resets(), [
            ['hyprctl', '-i', 'test-host', 'dispatch', 'hl.dsp.submap("reset")']])

    def test_pending_captured_revoked_and_unknown_states_never_reset(self):
        for state in ('pending', 'captured', 'revoked', 'unavailable'):
            with self.subTest(state=state):
                self.state['state'] = state
                self.fn(self.record)
        self.assertEqual(self.resets(), [])

    def test_forwarded_keys_or_old_generation_prevent_reset(self):
        for state in ({'state': 'uncaptured', 'generation': 0, 'forwarded': 1},
                      {'state': 'uncaptured', 'generation': 7, 'forwarded': 0},
                      {'state': 'uncaptured'}):
            self.state = state
            self.fn(self.record)
        self.assertEqual(self.resets(), [])

    def test_delayed_acknowledgement_allows_later_recovery(self):
        self.state['forwarded'] = 1
        self.fn(self.record)
        self.assertEqual(self.resets(), [])
        self.state['forwarded'] = 0
        self.fn(self.record)
        self.assertEqual(len(self.resets()), 1)

    def test_other_session_capture_is_preserved(self):
        self.records[2] = {**self.record, 'OMADEV_PID': '456'}
        with patch.dict(self.fn.__globals__, gate_status=lambda _d, pid:
                        self.state if pid == 123 else {'state': 'captured', 'generation': 7, 'forwarded': 0}):
            self.fn(self.record)
        self.assertEqual(self.resets(), [])

    def test_other_submaps_are_untouched(self):
        for self.submap in ('default', 'resize', ''):
            self.fn(self.record)
        self.assertEqual(self.resets(), [])

    def test_capture_lock_is_nonblocking_and_prevents_host_queries(self):
        with self.module['capture_lock']():
            self.fn(self.record)
        self.run.assert_not_called()
        self.fn(self.record)
        self.assertEqual(len(self.resets()), 1)

    def test_legacy_unreachable_starting_and_other_host_records_fail_closed(self):
        self.record['OMADEV_INPUT_GATE'] = '0'
        self.fn(self.record)
        self.record['OMADEV_INPUT_GATE'] = '1'
        for self.health in ('unreachable', 'free'):
            self.fn(self.record)
        self.health = 'alive'
        self.locked = True
        self.fn(self.record)
        self.locked = False
        self.records[2] = {**self.record, 'OMADEV_HOST_SIG': 'other-host'}
        self.fn(self.record)
        self.assertEqual(self.resets(), [])

    def test_no_remaining_sessions_can_restore_stale_mode(self):
        self.records.clear()
        self.fn(self.record)
        self.assertEqual(len(self.resets()), 1)

    def test_dead_session_does_not_prevent_recovery(self):
        self.health = 'dead'
        self.state = {'state': 'unavailable'}
        self.fn(self.record)
        self.assertEqual(len(self.resets()), 1)
