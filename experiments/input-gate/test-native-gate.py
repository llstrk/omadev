import json,os,subprocess,time
from pathlib import Path
root=Path(__file__).parent
art=root/'native-evidence'; art.mkdir(exist_ok=True)
parent=dict(os.environ)
assert parent.get('OMADEV_OWNER')=='input-gate-research'
assert parent.get('HYPRLAND_INSTANCE_SIGNATURE')!=parent.get('OMADEV_HOST_SIG')
child=None; env=None; app=None
results=[]; generation=0
lease=art/'lease'; lease.unlink(missing_ok=True)
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
def capture(seconds=10):
    global generation
    generation+=1
    tmp=lease.with_suffix('.tmp');tmp.write_text(f'{generation} {time.monotonic_ns()+int(seconds*1e9)}\n');tmp.replace(lease)
def close():lease.unlink(missing_ok=True)
def snapshot(stage):
    p=art/'state.txt'
    ctl(env,'eval',f'local f=io.open("{p}","w"); f:write(tostring(hl.is_key_down("Super_R"))); f:close()')
    value={'stage':stage,'superDown':p.read_text()=='true','events':json.loads(ipc('status'))}
    results.append(value); print(json.dumps(value),flush=True)
    return value
try:
    publisher=root/'publish-native.py'
    publisher.write_text('import os,json\nfrom pathlib import Path\nPath('+repr(str(art/'env.json'))+').write_text(json.dumps(dict(os.environ)))\n')
    (art/'env.json').unlink(missing_ok=True)
    cfg=root/'native-hyprland.lua'
    cfg.write_text('''hl.monitor({output="WAYLAND-1",mode="preferred",position="0x0",scale=1})
hl.config({animations={enabled=false}})
hl.on("hyprland.start",function() hl.exec_cmd("python '''+str(publisher)+'''") end)
''')
    launch={**parent,'AQ_BACKEND':'wayland','OMADEV_KEYBOARD_GATE':str(lease),
            'OMADEV_ORIGINAL_LD_PRELOAD':parent.get('LD_PRELOAD',''),
            'LD_PRELOAD':str(root/'keyboard-gate.so')+(':'+parent['LD_PRELOAD'] if parent.get('LD_PRELOAD') else '')}
    child=subprocess.Popen(['Hyprland','--config',str(cfg)],env=launch,stdout=(art/'child.log').open('w'),stderr=subprocess.STDOUT)
    env=json.loads(wait(lambda:(art/'env.json').read_text()))
    assert 'OMADEV_KEYBOARD_GATE' not in env and env.get('LD_PRELOAD','')==parent.get('LD_PRELOAD','')
    windows=wait(lambda: [w for w in json.loads(ctl(parent,'clients','-j')) if w['pid']==child.pid])
    address=windows[0]['address']
    ctl(parent,'dispatch',f'hl.dsp.focus({{window="address:{address}"}})')
    app=subprocess.Popen(['quickshell','-p',str(root/'probe')],env=env,stdout=(art/'probe.log').open('w'),stderr=subprocess.STDOUT)
    wait(lambda:ipc('status'))
    appwin=wait(lambda:[w for w in json.loads(ctl(env,'clients','-j')) if w['pid']==app.pid])[0]
    ctl(env,'dispatch',f'hl.dsp.focus({{window="address:{appwin["address"]}"}})')
    print('1. Initially closed: host A blocked, virtual B allowed.',flush=True)
    type_key(38);time.sleep(.2)
    s=snapshot('initial-host-A');assert not s['events'],s
    run(env,'wtype','b');wait(lambda:json.loads(ipc('status')))
    s=snapshot('initial-virtual-B');assert any(e['text']=='b' and not e['modifiers'] for e in s['events']),s
    ipc('clear')
    print('2. Hold Super before capture: remain closed until its release.',flush=True)
    key(134,'down');capture();time.sleep(.1);type_key(38);time.sleep(.2)
    s=snapshot('capture-pending-held-Super');assert not s['events'] and not s['superDown'],s
    key(134,'up');time.sleep(.1);type_key(41);wait(lambda:json.loads(ipc('status')))
    s=snapshot('captured-F-no-stale-modifiers');assert any(e['text']=='f' for e in s['events']) and all(not e['modifiers'] for e in s['events']),s
    print('3. Closing while Shift held sends its release without another host event.',flush=True)
    ipc('clear');key(50,'down');wait(lambda:json.loads(ipc('status')));close()
    wait(lambda:any(e['kind']=='up' and e['key']==16777248 for e in json.loads(ipc('status'))))
    s=snapshot('closed-releases-Shift')
    ipc('clear');run(env,'wtype','g');wait(lambda:json.loads(ipc('status')))
    s=snapshot('closed-virtual-G-clean');assert any(e['text']=='g' for e in s['events']) and all(not e['modifiers'] for e in s['events']),s
    key(50,'up')
    print('4. Captured deliberate Super works; focus leave releases it and revokes capture.',flush=True)
    capture();ipc('clear');key(134,'down');wait(lambda:json.loads(ipc('status')))
    s=snapshot('captured-deliberate-Super');assert any(e['key']==16777250 for e in s['events']),s
    others=[w for w in json.loads(ctl(parent,'clients','-j')) if w['pid']!=child.pid]
    assert others,'parent terminal missing'
    ctl(parent,'dispatch',f'hl.dsp.focus({{window="address:{others[0]["address"]}"}})')
    wait(lambda:any(e['kind']=='up' and e['key']==16777250 for e in json.loads(ipc('status'))))
    snapshot('focus-leave-releases-Super')
    ctl(parent,'dispatch',f'hl.dsp.focus({{window="address:{address}"}})')
    # send_key_state bypasses the parent's physical-device bookkeeping. Release
    # after enter, so the child's snapshot cannot reintroduce the fixture hold.
    key(134,'up');ipc('clear')
    type_key(38);time.sleep(.2)
    s=snapshot('focus-return-not-recaptured');assert not s['events'],s
    capture();type_key(54);wait(lambda:json.loads(ipc('status')))
    s=snapshot('explicit-recapture-C');assert any(e['text']=='c' and not e['modifiers'] for e in s['events']),s
    print('5. Expired controller lease releases held Ctrl and closes input.',flush=True)
    ipc('clear');capture(.8);key(37,'down');wait(lambda:json.loads(ipc('status')))
    wait(lambda:any(e['kind']=='up' and e['key']==16777249 for e in json.loads(ipc('status'))))
    snapshot('expired-lease-releases-Control')
    ipc('clear');type_key(40);time.sleep(.2)
    s=snapshot('expired-host-D-blocked');assert not s['events'],s
    key(37,'up');run(env,'wtype','e');wait(lambda:json.loads(ipc('status')))
    s=snapshot('expired-virtual-E-clean');assert any(e['text']=='e' and not e['modifiers'] for e in s['events']),s
    print('6. Resuming an expired heartbeat cannot silently recapture.',flush=True)
    ipc('clear');lease.write_text(f'{generation} {time.monotonic_ns()+int(10e9)}\n')
    type_key(38);time.sleep(.2)
    s=snapshot('expired-generation-renewal-stays-blocked');assert not s['events'],s
    capture();type_key(53);wait(lambda:json.loads(ipc('status')))
    s=snapshot('fresh-generation-recaptures-X');assert any(e['text']=='x' and not e['modifiers'] for e in s['events']),s
    run(env,'grim',str(art/'probe.png'))
    print('PASS: source gate, clean handoff, focus revocation, expiry, virtual input.',flush=True)
    (art/'passed').write_text('passed\n')
finally:
    close()
    (art/'results.json').write_text(json.dumps(results,indent=2)+'\n')
    if env:
        try:ctl(env,'dispatch','hl.dsp.exit()')
        except Exception:pass
    for p in [app,child]:
        if p:
            try:p.wait(timeout=5)
            except subprocess.TimeoutExpired:p.terminate();p.wait(timeout=5)
