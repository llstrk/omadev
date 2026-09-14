"""Run in a visible terminal inside your claimed omadev test nest.

Link plugin/dry.omadev into that nest first. This creates a disposable child
compositor to exercise real Wayland key events without taking host keyboard
focus. It is intentionally not part of the headless unittest suite.
"""
import json, os, subprocess, tempfile, time
from pathlib import Path

if (not os.environ.get('OMADEV_OWNER') or not os.environ.get('OMADEV_SLOT')
        or not os.environ.get('OMADEV_HOST_SIG')
        or os.environ.get('HYPRLAND_INSTANCE_SIGNATURE') == os.environ.get('OMADEV_HOST_SIG')):
    raise SystemExit('Run this only inside an owned omadev test nest, never on the host or in a user session')

work = Path(tempfile.mkdtemp(prefix='omadev-clear-integration-'))
result = work / 'result.json'
parent_env = dict(os.environ)
child = None
child_env = None

def ctl(env, *args):
    p = subprocess.run(['hyprctl', *args], env=env, text=True, capture_output=True, timeout=5)
    if p.returncode:
        raise RuntimeError(p.stdout + p.stderr)
    return p.stdout

def status(env):
    p = subprocess.run(['omarchy-shell', 'dry.omadev', 'status'], env=env, text=True, capture_output=True, timeout=3)
    return json.loads(p.stdout) if p.returncode == 0 and p.stdout.startswith('{') else None

def wait_for(fn, seconds=20):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        value = fn()
        if value:
            return value
        time.sleep(.1)
    raise TimeoutError('Condition did not become ready')

try:
    print('Testing modifier clearing entirely inside this test session.', flush=True)
    print('Test artifacts:', work, flush=True)
    publisher = work / 'publish.py'
    publisher.write_text('import os,json\nfrom pathlib import Path\nPath(' + repr(str(work / 'env.json')) + ').write_text(json.dumps(dict(os.environ)))\n')
    config = work / 'hyprland.lua'
    config.write_text('''hl.monitor({output = "WAYLAND-1", mode = "preferred", position = "0x0", scale = 1})
hl.config({animations = {enabled = false}})
hl.on("hyprland.start", function()
  hl.exec_cmd("python ''' + str(publisher) + '''")
  hl.exec_cmd("quickshell -p /usr/share/omarchy/shell")
end)
''')
    env = {**parent_env, 'AQ_BACKEND': 'wayland', 'OMADEV_HOST_SIG': parent_env['HYPRLAND_INSTANCE_SIGNATURE'], 'OMADEV_SLOT': 'test-child'}
    # A disposable compositor application, not another omadev slot. It only
    # connects to this owned nest's Wayland display and private session bus.
    child = subprocess.Popen(['Hyprland', '--config', str(config)], env=env, stdout=(work / 'child.log').open('w'), stderr=subprocess.STDOUT)
    wait_for(lambda: (work / 'env.json').exists())
    child_env = json.loads((work / 'env.json').read_text())
    wait_for(lambda: status(child_env))
    windows = json.loads(ctl(parent_env, 'clients', '-j'))
    target = next(w for w in windows if w['pid'] == child.pid)
    addr = target['address']
    ctl(parent_env, 'dispatch', 'hl.dsp.focus({window = "address:' + addr + '"})')
    wait_for(lambda: status(child_env).get('focused'))
    print('Child compositor ready; injecting test-only left/right modifier key-downs.', flush=True)
    for code in (133, 134, 37, 105, 64, 108, 50, 62):
        ctl(parent_env, 'dispatch', 'hl.dsp.send_key_state({mods = "", key = "code:' + str(code) + '", state = "down", window = "address:' + addr + '"})')
    before = wait_for(lambda: (s if {'Super','Ctrl','Alt','Shift'}.issubset(s['heldModifiers']) else None) if (s := status(child_env)) else None)
    print('Before capture:', before['heldModifiers'], before['heldModifierCodes'], flush=True)
    # This changes routing in the owned parent nest only, never on the host.
    ctl(parent_env, 'dispatch', 'hl.dsp.submap("nested")')
    after = wait_for(lambda: (s if s['captured'] and s['modifierStateKnown'] and not s['heldModifiers'] and not s['heldModifierCodes'] else None) if (s := status(child_env)) else None, 10)
    print('PASS: capture cleared all left/right modifiers using key-up events.', flush=True)
    # A subsequent, deliberate modifier hold must stay held: no continuous reset.
    ctl(parent_env, 'dispatch', 'hl.dsp.send_key_state({mods = "", key = "code:50", state = "down", window = "address:' + addr + '"})')
    wait_for(lambda: 'Shift' in status(child_env)['heldModifiers'])
    time.sleep(.5)
    assert 'Shift' in status(child_env)['heldModifiers']
    ctl(parent_env, 'dispatch', 'hl.dsp.send_key_state({mods = "", key = "code:50", state = "up", window = "address:' + addr + '"})')
    print('PASS: deliberate modifiers still work after capture.', flush=True)
    result.write_text(json.dumps({'ok': True, 'before': before['heldModifiers'], 'after': after['heldModifiers'], 'logs': str(work)}))
except Exception as e:
    result.write_text(json.dumps({'ok': False, 'error': str(e), 'logs': str(work)}))
    raise
finally:
    ctl(parent_env, 'dispatch', 'hl.dsp.submap("reset")')
    if child_env:
        try: ctl(child_env, 'dispatch', 'hl.dsp.exit()')
        except Exception: pass
    if child:
        try: child.wait(timeout=8)
        except subprocess.TimeoutExpired: child.terminate(); child.wait(timeout=5)
