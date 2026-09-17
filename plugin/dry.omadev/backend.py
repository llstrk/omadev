#!/usr/bin/env python3
"""Widget-owned backend discovery. Probe is read-only; actions require a terminal confirmation.

The widget checkout is never a live backend installation. A source install takes
an independent snapshot so plugin update/remove cannot replace a running backend.
"""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import uuid

PROTOCOL = 1
SOURCE = Path(__file__).resolve().parents[2]


def candidates(env):
    if env.get('OMADEV_SLOT'):
        root = env.get('OMADEV_ROOT')
        return [Path(root).parent/'bin/omadev'] if root else []
    config = Path(env.get('XDG_CONFIG_HOME') or Path(env['HOME'])/'.config')
    paths = []
    try:
        record = json.loads((config/'omadev/backend.json').read_text())
        path = record.get('launcher')
        if record.get('schemaVersion') == 1 and isinstance(path, str) and Path(path).is_absolute():
            paths.append(Path(path))
    except (OSError, ValueError, AttributeError):
        pass
    paths.append(Path(env['HOME'])/'.local/bin/omadev')
    on_path = shutil.which('omadev', path=env.get('PATH', ''))
    if on_path:
        paths.append(Path(on_path))
    return list(dict.fromkeys(p.resolve() for p in paths))


def probe(env=None):
    env = dict(os.environ if env is None else env)
    for path in candidates(env):
        if not path.is_file() or not os.access(path, os.X_OK):
            continue
        # An explicitly registered/pinned backend wins. Never silently use a
        # different copy just because its version or binding configuration differs.
        def failure(state, message):
            return {'state':state, 'compatible':False, 'launcher':str(path), 'message':message}
        try:
            result = subprocess.run([str(path), 'capabilities', '--json'], env=env,
                                    capture_output=True, text=True, timeout=3)
            if result.returncode:
                if 'unknown command: capabilities' in result.stderr:
                    return failure('incompatible', 'This backend predates the capabilities protocol. Install a compatible backend.')
                return failure('unavailable', 'Backend probe failed. Recheck readiness; installation is not a recovery action.')
            data = json.loads(result.stdout)
            if data.get('application') != 'omadev' or type(data.get('protocol')) is not int or data['protocol'] != PROTOCOL:
                return failure('incompatible', 'The selected backend does not implement Omadev protocol 1.')
            if Path(data['launcher']).resolve() != path.resolve():
                raise ValueError('backend identity mismatch')
            if not isinstance(data.get('nativeHelperPresent'), bool) or not isinstance(data.get('bindingsConfigured'), bool):
                raise ValueError('incomplete readiness response')
            state = 'build-required' if not data['nativeHelperPresent'] else 'ready' if data['bindingsConfigured'] else 'unconfigured'
            binding_status = str(data.get('bindingsStatus', 'unknown'))
            custom = binding_status == 'custom'
            return {'state': state, 'compatible': True, 'launcher': str(path), 'bindingsStatus': binding_status,
                    'version': str(data.get('version', 'unknown')), 'message': {
                        'ready': 'Backend ready.',
                        'build-required': 'The required keyboard helper is missing. Install a complete backend.',
                        'unconfigured': ('Custom host bindings need manual configuration. Setup preserves them and cannot repair them. '
                                         'Uncaptured sessions can still be started; capture remains disabled.' if custom else
                                         'Host release bindings need setup before capture. You can still start uncaptured sessions.')}[state]}
        except (OSError, ValueError, TypeError, KeyError, AttributeError, subprocess.SubprocessError) as error:
            return failure('unavailable', 'Backend readiness is temporarily unknown. Recheck: ' + str(error))
    return {'state': 'missing', 'compatible': False, 'launcher': '',
            'message': 'Install the Omadev backend to create and capture sessions.'}


def install_snapshot(source=SOURCE, env=None):
    """Called only after explicit user confirmation, never during discovery."""
    env = dict(os.environ if env is None else env)
    if env.get('OMADEV_SLOT'):
        raise RuntimeError('Install the backend from the real host, not inside a nest.')
    for name in ('bin/omadev', 'bin/build-input-gate', 'libexec/input-gate/keyboard-gate.c', 'install.sh', 'skill/SKILL.md'):
        if not (source/name).is_file():
            raise RuntimeError('Backend sources are unavailable; install the Omadev package instead.')
    base = Path(env.get('XDG_DATA_HOME') or Path(env['HOME'])/'.local/share')/'omadev/backends'
    target = base/(time.strftime('%Y%m%d-%H%M%S')+'-'+uuid.uuid4().hex[:8])
    target.mkdir(parents=True)
    for name in ('bin', 'libexec', 'skill'):
        shutil.copytree(source/name, target/name, symlinks=True,
                        ignore=shutil.ignore_patterns('__pycache__', '*.so', '*.tmp'))
    shutil.copy2(source/'install.sh', target/'install.sh')
    print('Installing an independent backend snapshot in ' + str(target), flush=True)
    # Keep even an unsuccessful snapshot for diagnostics or manual setup. No
    # plugin files, git metadata, or existing backend releases are removed.
    subprocess.run(['bash', str(target/'install.sh')], env=env, check=True)
    return target


def main(argv):
    if argv == ['probe']:
        print(json.dumps(probe()))
        return 0
    if argv not in (['install'], ['setup']):
        raise RuntimeError('usage: backend.py probe|install|setup')
    if os.environ.get('OMADEV_SLOT'):
        raise RuntimeError('Backend installation and host setup must be done from the host widget.')
    if not sys.stdin.isatty():
        raise RuntimeError('Installation/setup requires confirmation in an interactive terminal.')
    action = argv[0]
    print('Omadev backend ' + action)
    print('This will configure host key bindings, backend registration and agent skills.')
    if action == 'install':
        print('Builds from this trusted plugin checkout using a compiler and Wayland 1.26+ headers.')
        print('The backend is copied outside the plugin checkout. No package manager or sudo is run.')
    if input('Continue? [y/N] ').strip().lower() not in ('y', 'yes'):
        print('Cancelled. Nothing changed.')
        return 0
    if action == 'install':
        install_snapshot()
    else:
        state = probe()
        if not state['compatible']:
            raise RuntimeError('Install a compatible backend before setup.')
        subprocess.run([state['launcher'], 'setup'], check=True)
    print('Done. The widget will recheck readiness. Close this terminal when finished.')
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main(sys.argv[1:]))
    except (OSError, RuntimeError, subprocess.SubprocessError) as error:
        print('Omadev: ' + str(error), file=sys.stderr)
        raise SystemExit(1)
