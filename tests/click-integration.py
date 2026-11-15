#!/usr/bin/env python3
"""omadev click/move against a running nest: tests/click-integration.py N

Opens tests/fixtures/click-target.py (a fullscreen GTK 4 window that logs
pointer input) in nest N, clicks and hovers with `omadev`, and checks what the
window received. Needs python-gobject with GTK 4 and xkbcli."""
import json
import os
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OMADEV = str(ROOT / 'bin/omadev')
slot = sys.argv[1] if len(sys.argv) > 1 else sys.exit('usage: click-integration.py N')
log = Path(tempfile.mkdtemp(prefix='omadev-click.')) / 'events.jsonl'
log.touch()
target = subprocess.Popen([OMADEV, 'run', slot, 'python3', str(ROOT / 'tests/fixtures/click-target.py'), str(log)],
                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)


def events(kind):
    return [e for e in map(json.loads, log.read_text().splitlines()) if e['event'] == kind]


def omadev(*args):
    subprocess.run([OMADEV, *args], check=True)


try:
    deadline = time.time() + 15
    while not events('ready'):
        if time.time() > deadline:
            sys.exit('the click target did not open')
        time.sleep(0.2)
    time.sleep(0.5)
    monitor = json.loads(subprocess.run([OMADEV, 'hyprctl', slot, 'monitors', '-j'], capture_output=True,
                                        text=True, check=True).stdout)[0]
    scale = monitor['scale']
    omadev('click', slot, '400', '300')
    omadev('click', slot, '800', '600', '--mods', 'shift')
    omadev('click', slot, '600', '400', '--button', 'right', '--mods', 'ctrl,alt')
    omadev('click', slot, '200', '200', '--double')
    omadev('click', slot, '150', '120', '--logical')
    omadev('move', slot, '500', '500')
    time.sleep(0.5)
    got = [(e['button'], e['n'], e['x'], e['y'], e['mods']) for e in events('click')]
    px = lambda v: round(v / scale)  # noqa: E731 - window coordinates are logical
    want = [(1, 1, px(400), px(300), []), (1, 1, px(800), px(600), ['shift']),
            (3, 1, px(600), px(400), ['alt', 'ctrl']), (1, 1, px(200), px(200), []), (1, 2, px(200), px(200), []),
            (1, 1, 150, 120, [])]
    assert got == want, f'clicks\n got  {got}\n want {want}'
    last = events('motion')[-1]
    assert (last['x'], last['y']) == (px(500), px(500)), f'hover ended at {last}'
    print(f'click-integration: ok (nest {slot}, scale {scale})')
finally:
    os.killpg(target.pid, signal.SIGTERM)
