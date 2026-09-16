import json,os,subprocess,time,traceback
from pathlib import Path
root=Path(__file__).parent
art=root/'evidence'; art.mkdir(exist_ok=True)
parent=dict(os.environ)
assert parent.get('OMADEV_OWNER')=='input-gate-research'
assert parent.get('HYPRLAND_INSTANCE_SIGNATURE')!=parent.get('OMADEV_HOST_SIG')
child=None; env=None; app=None
results=[]
def run(env,*args):
    p=subprocess.run(args,env=env,text=True,capture_output=True,timeout=8)
    if p.returncode: raise RuntimeError(p.stdout+p.stderr)
    return p.stdout.strip()
def ctl(env,*args): return run(env,'hyprctl',*args)
def wait(fn,seconds=15):
    end=time.monotonic()+seconds
    while time.monotonic()<end:
        try:
            r=fn()
            if r:return r
        except (RuntimeError,FileNotFoundError,json.JSONDecodeError): pass
        time.sleep(.1)
    raise TimeoutError('Readiness condition not met')
def ipc(method): return run(env,'quickshell','-p',str(root/'probe'),'ipc','call','inputproof',method)
def key(code,state):
    ctl(parent,'dispatch',f'hl.dsp.send_key_state({{mods="",key="code:{code}",state="{state}",window="address:{address}"}})')
def type_key(code):key(code,'down');key(code,'up')
def snapshot(stage):
    # Read compositor state without host focus, capture, or synthetic changes.
    p=art/'state.txt'
    ctl(env,'eval',f'local f=io.open("{p}","w"); f:write(tostring(hl.is_key_down("Super_R"))); f:close()')
    value={'stage':stage,'superDown':p.read_text()=='true','events':json.loads(ipc('status'))}
    results.append(value); print(json.dumps(value),flush=True)
    return value
try:
    publisher=root/'publish.py'
    publisher.write_text('import os,json\nfrom pathlib import Path\nPath('+repr(str(art/'env.json'))+').write_text(json.dumps(dict(os.environ)))\n')
    (art/'env.json').unlink(missing_ok=True)
    cfg=root/'hyprland.lua'
    cfg.write_text('''hl.monitor({output="WAYLAND-1",mode="preferred",position="0x0",scale=1})
hl.config({animations={enabled=false}})
hl.device({name="wl_keyboard",enabled=false})
hl.on("hyprland.start",function() hl.exec_cmd("python '''+str(publisher)+'''") end)
''')
    launch={**parent,'AQ_BACKEND':'wayland'}
    child=subprocess.Popen(['Hyprland','--config',str(cfg)],env=launch,stdout=(art/'child.log').open('w'),stderr=subprocess.STDOUT)
    env=json.loads(wait(lambda:(art/'env.json').read_text()))
    windows=wait(lambda: [w for w in json.loads(ctl(parent,'clients','-j')) if w['pid']==child.pid])
    address=windows[0]['address']
    ctl(parent,'dispatch',f'hl.dsp.focus({{window="address:{address}"}})')
    app=subprocess.Popen(['quickshell','-p',str(root/'probe')],env=env,stdout=(art/'probe.log').open('w'),stderr=subprocess.STDOUT)
    wait(lambda:ipc('status'))
    appwin=wait(lambda:[w for w in json.loads(ctl(env,'clients','-j')) if w['pid']==app.pid])[0]
    ctl(env,'dispatch',f'hl.dsp.focus({{window="address:{appwin["address"]}"}})')
    (art/'devices.json').write_text(ctl(env,'devices','-j'))
    print('1. wl_keyboard disabled at startup: inject A through parent.',flush=True)
    type_key(38);time.sleep(.3)
    s=snapshot('disabled-host-A');assert not s['events'],s
    print('2. Virtual test keyboard should still type B.',flush=True)
    run(env,'wtype','b');wait(lambda:json.loads(ipc('status')))
    s=snapshot('disabled-virtual-B');assert any(e['text']=='b' for e in s['events']),s
    ipc('clear')
    print('3. Hold Super through parent while disabled; inspect internal state.',flush=True)
    key(134,'down');time.sleep(.3)
    s=snapshot('disabled-host-Super-down');assert not s['events'],s
    print('4. Enable host keyboard while that stale Super is still down.',flush=True)
    ctl(env,'eval','hl.device({name="wl_keyboard",enabled=true})')
    type_key(38);time.sleep(.3)
    s=snapshot('enabled-after-held-Super')
    key(134,'up');time.sleep(.2)
    ipc('clear')
    print('5. Enabled host keyboard types C normally after release.',flush=True)
    type_key(54);wait(lambda:json.loads(ipc('status')))
    s=snapshot('enabled-host-C');assert any(e['text']=='c' for e in s['events']),s
    print('6. Disable again, host D blocked but virtual E works.',flush=True)
    ctl(env,'eval','hl.device({name="wl_keyboard",enabled=false})');ipc('clear')
    type_key(40);time.sleep(.3)
    s=snapshot('disabled-again-host-D');assert not s['events'],s
    run(env,'wtype','e');wait(lambda:json.loads(ipc('status')))
    s=snapshot('disabled-again-virtual-E');assert any(e['text']=='e' for e in s['events']),s
    print('7. Clear held keys behind closed gate, then enable and type F.',flush=True)
    ipc('clear')
    key(134,'down');key(37,'down')
    key(134,'up');key(37,'up')
    ctl(env,'eval','hl.device({name="wl_keyboard",enabled=true})')
    type_key(41);wait(lambda:json.loads(ipc('status')))
    s=snapshot('clear-while-disabled-then-enable-F')
    assert any(e['text']=='f' for e in s['events']) and all(e['modifiers']==0 for e in s['events']),s
    print('8. Release forwarded Shift before closing; virtual input must stay clean.',flush=True)
    ipc('clear');key(50,'down');key(50,'up')
    wait(lambda:len(json.loads(ipc('status')))>=2)
    s=snapshot('release-forwarded-Shift-before-close')
    assert any(e['kind']=='up' and e['key']==16777248 for e in s['events']),s
    ctl(env,'eval','hl.device({name="wl_keyboard",enabled=false})')
    ipc('clear');type_key(42);time.sleep(.3)
    assert not json.loads(ipc('status'))
    run(env,'wtype','h');wait(lambda:json.loads(ipc('status')))
    s=snapshot('closed-after-handoff-virtual-H')
    assert any(e['text']=='h' for e in s['events']) and all(e['modifiers']==0 for e in s['events']),s
    run(env,'grim',str(art/'probe.png'))
    print('PASS: selective delivery gating and clean handoff sequence.',flush=True)
finally:
    (art/'results.json').write_text(json.dumps(results,indent=2)+'\n')
    if env:
        try:ctl(env,'dispatch','hl.dsp.exit()')
        except Exception:pass
    for p in [app,child]:
        if p:
            try:p.wait(timeout=5)
            except subprocess.TimeoutExpired:p.terminate();p.wait(timeout=5)
