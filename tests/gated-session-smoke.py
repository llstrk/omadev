"""Foreground terminal test in an actual --input-gate session.

Wait for test-results/session-smoke/awaiting-input, then use omadev run N wtype
with the exact prompt text and Return. Never capture the host keyboard.
"""
import json
import os
from pathlib import Path
import subprocess
import signal
import time

root = Path(__file__).resolve().parents[1]
assert os.environ.get('OMADEV_OWNER') == 'input-gate-integration'
assert os.environ.get('OMADEV_SLOT') and os.environ.get('OMADEV_INPUT_GATE') == '1'
assert os.environ['HYPRLAND_INSTANCE_SIGNATURE'] != os.environ['OMADEV_HOST_SIG']
assert 'keyboard-gate.so' not in os.environ.get('LD_PRELOAD', '')
assert 'OMADEV_KEYBOARD_GATE' not in os.environ
art = root / 'test-results/session-smoke'
art.mkdir(parents=True, exist_ok=True)
(art / 'passed').unlink(missing_ok=True)

def status():
    return json.loads(subprocess.check_output([str(root/'bin/omadev'), 'input-status'], text=True))

before = status()
assert before['state'] == 'uncaptured' and before['generation'] == 0, before
print('Ready for virtual keyboard input: automation survives closed input', flush=True)
(art / 'awaiting-input').write_text('ready\n')
try:
    text = input()
finally:
    (art / 'awaiting-input').unlink(missing_ok=True)
assert text == 'automation survives closed input', repr(text)
print('PASS: virtual keyboard typed into the real terminal with host input closed', flush=True)
subprocess.run(['omarchy', 'restart', 'shell'], check=True)
deadline = time.monotonic() + 15
while time.monotonic() < deadline:
    probe = subprocess.run(['omarchy-shell', 'dry.omadev', 'status'], capture_output=True, text=True)
    try:
        ui = json.loads(probe.stdout)
        if ui.get('inputGate') == 'uncaptured':
            break
    except ValueError:
        pass
    time.sleep(.1)
else:
    raise AssertionError('widget did not recover after shell restart')
def readers():
    found = []
    for proc in Path('/proc').glob('[0-9]*'):
        try:
            args = (proc/'cmdline').read_bytes().split(b'\0')
            if b'input-status' not in args or b'--watch' not in args:
                continue
            environ = dict(item.split(b'=', 1) for item in (proc/'environ').read_bytes().split(b'\0') if b'=' in item)
            if environ.get(b'HYPRLAND_INSTANCE_SIGNATURE') == os.environ['HYPRLAND_INSTANCE_SIGNATURE'].encode():
                found.append(int(proc.name))
        except (OSError, ValueError):
            pass
    return found

def widget_state():
    return json.loads(subprocess.check_output(['omarchy-shell','dry.omadev','status'], text=True))

reader_ids = readers()
assert len(reader_ids) == 1, reader_ids
reader = reader_ids[0]
# Stop only this nest's status reader, never its controller or compositor.
os.kill(reader, signal.SIGSTOP)
try:
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if widget_state()['inputGate'] == 'unavailable': break
        time.sleep(.1)
    else: raise AssertionError('widget retained stale gate state after reader stall')
finally:
    os.kill(reader, signal.SIGCONT)
deadline = time.monotonic() + 5
while time.monotonic() < deadline:
    ui = widget_state()
    if ui['inputGate'] == 'uncaptured': break
    time.sleep(.1)
else: raise AssertionError('widget status did not recover after reader resumed')
assert readers() == reader_ids, 'status polling respawned its interpreter'
print('PASS: one persistent status reader; stalled reader reports unknown and recovers', flush=True)
after = status()
assert after['state'] == 'uncaptured' and after['pid'] == before['pid'], after
assert not ui['captured'] and not ui['capturePending'], ui
(art / 'results.json').write_text(json.dumps({'before': before, 'after': after, 'ui': {
    k:ui[k] for k in ['captured','capturePending','inputGate']}}, indent=2))
(art / 'passed').write_text('passed\n')
print('PASS: gate and truthful widget status survive nest-local shell restart', flush=True)
