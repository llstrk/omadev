"""Run in a visible terminal in an owned input-gate-integration nest, never on the host."""
import json,os,subprocess,time,sys,shutil,runpy
from unittest.mock import patch
from pathlib import Path
root=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(root/'libexec'))
from input_gate import Controller,native_status,request
art=root/'test-results/gate-integration'; art.mkdir(parents=True,exist_ok=True)
(art/'passed').unlink(missing_ok=True)
probe=root/'tests/fixtures/input-gate'
parent=dict(os.environ)
assert parent.get('OMADEV_OWNER')=='input-gate-integration'
assert parent.get('HYPRLAND_INSTANCE_SIGNATURE')!=parent.get('OMADEV_HOST_SIG')
child=None; env=None; app=None; controller=None
child2=None; env2=None; app2=None; controller2=None
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
def ipc(method): return run(env,'quickshell','-p',str(probe),'ipc','call','inputproof',method)
def key(code,state):
    ctl(parent,'dispatch',f'hl.dsp.send_key_state({{mods="",key="code:{code}",state="{state}",window="address:{address}"}})')
def type_key(code):key(code,'down');key(code,'up')
def write_lease(token, seconds):
    state=native_status(art,child.pid)
    assert state['state']!='unavailable',state
    tmp=lease.with_suffix('.tmp')
    tmp.write_text(f'{token} {time.monotonic_ns()+int(seconds*1e9)} {state["focusEpoch"]} {child.pid}\n')
    tmp.replace(lease)
def capture(seconds=1.9,pending=False):
    global generation
    generation+=1
    write_lease(generation,seconds)
    if not pending:
        wait(lambda:(s.get('state')=='captured' and s.get('generation')==generation) if (s:=native_status(art,child.pid)) else False)
def close():lease.unlink(missing_ok=True)
def snapshot(stage):
    p=art/'state.txt'
    ctl(env,'eval',f'local f=io.open("{p}","w"); f:write(tostring(hl.is_key_down("Super_R"))); f:close()')
    value={'stage':stage,'superDown':p.read_text()=='true','events':json.loads(ipc('status'))}
    results.append(value); print(json.dumps(value),flush=True)
    return value
try:
    publisher=art/'publish-native.py'
    publisher.write_text('import os,json\nfrom pathlib import Path\nPath('+repr(str(art/'env.json'))+').write_text(json.dumps(dict(os.environ)))\n')
    (art/'env.json').unlink(missing_ok=True)
    cfg=art/'native-hyprland.lua'
    cfg.write_text('''hl.monitor({output="WAYLAND-1",mode="preferred",position="0x0",scale=1})
hl.config({animations={enabled=false}})
hl.on("hyprland.start",function() hl.exec_cmd("python '''+str(publisher)+'''") end)
''')
    launch={**parent,'AQ_BACKEND':'wayland','OMADEV_KEYBOARD_GATE':str(lease),
            'OMADEV_ORIGINAL_LD_PRELOAD':parent.get('LD_PRELOAD',''),
            'LD_PRELOAD':str(root/'libexec/input-gate/keyboard-gate.so')+(':'+parent['LD_PRELOAD'] if parent.get('LD_PRELOAD') else '')}
    child=subprocess.Popen(['Hyprland','--config',str(cfg)],env=launch,stdout=(art/'child.log').open('w'),stderr=subprocess.STDOUT)
    env=json.loads(wait(lambda:(art/'env.json').read_text()))
    assert 'OMADEV_KEYBOARD_GATE' not in env and env.get('LD_PRELOAD','')==parent.get('LD_PRELOAD','')
    windows=wait(lambda: [w for w in json.loads(ctl(parent,'clients','-j')) if w['pid']==child.pid])
    address=windows[0]['address']
    ctl(parent,'dispatch',f'hl.dsp.focus({{window="address:{address}"}})')
    app=subprocess.Popen(['quickshell','-p',str(probe)],env=env,stdout=(art/'probe.log').open('w'),stderr=subprocess.STDOUT)
    wait(lambda:ipc('status'))
    appwin=wait(lambda:[w for w in json.loads(ctl(env,'clients','-j')) if w['pid']==app.pid])[0]
    ctl(env,'dispatch',f'hl.dsp.focus({{window="address:{appwin["address"]}"}})')
    wait(lambda:native_status(art,child.pid)['state']!='unavailable')
    # The test parent's physical state may predate the fixture. Simulate releasing
    # the entry chord, only into the disposable child, before the scenarios.
    for code in (37,50,62,64,105,108,133,134): key(code,'up')
    print('1. Initially closed: host A blocked, virtual B allowed.',flush=True)
    type_key(38);time.sleep(.2)
    s=snapshot('initial-host-A');assert not s['events'],s
    run(env,'wtype','b');wait(lambda:json.loads(ipc('status')))
    s=snapshot('initial-virtual-B');assert any(e['text']=='b' and not e['modifiers'] for e in s['events']),s
    ipc('clear')
    state=native_status(art,child.pid)
    valid_deadline=time.monotonic_ns()+1_000_000_000
    for bad in ('invalid', f'88 {valid_deadline} {state["focusEpoch"]} {child.pid+1}',
                f'88 {valid_deadline} {state["focusEpoch"]+1} {child.pid}',
                f'88 {time.monotonic_ns()+99_000_000_000} {state["focusEpoch"]} {child.pid}'):
        lease.write_text(bad+'\n');type_key(38);time.sleep(.05)
        assert not json.loads(ipc('status')),bad
    close()
    print('PASS: malformed, wrong-PID/focus-epoch, and far-future leases fail closed',flush=True)
    print('2. Hold Super before capture: remain closed until its release.',flush=True)
    key(134,'down');capture(pending=True);time.sleep(.1);type_key(38);time.sleep(.2)
    s=snapshot('capture-pending-held-Super');assert not s['events'] and not s['superDown'],s
    key(134,'up');wait(lambda:native_status(art,child.pid).get('state')=='captured');type_key(41);wait(lambda:json.loads(ipc('status')))
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
    ipc('clear');write_lease(generation,1.9)
    type_key(38);time.sleep(.2)
    s=snapshot('expired-generation-renewal-stays-blocked');assert not s['events'],s
    capture();type_key(53);wait(lambda:json.loads(ipc('status')))
    s=snapshot('fresh-generation-recaptures-X');assert any(e['text']=='x' and not e['modifiers'] for e in s['events']),s
    print('7. Real controller capture/release, with pre-held shortcut keys.',flush=True)
    close()
    wait(lambda:native_status(art,child.pid).get('generation')==0)
    def host_state():
        active=json.loads(ctl(parent,'activewindow','-j'))
        return str(active.get('pid','')),ctl(parent,'submap').strip()
    controller=Controller(art,lambda:child.pid if child.poll() is None else None,host_state).start()
    ctl(parent,'dispatch','hl.dsp.submap("nested")')
    ipc('clear');key(134,'down');key(64,'down')
    answer=request(art,'capture')
    assert answer['state']=='pending',answer
    type_key(38);time.sleep(.15);assert not json.loads(ipc('status'))
    key(134,'up');key(64,'up')
    wait(lambda:native_status(art,child.pid)['state']=='captured')
    type_key(39);wait(lambda:json.loads(ipc('status')))
    s=snapshot('controller-capture-S-clean');assert any(e['text']=='s' and not e['modifiers'] for e in s['events']),s
    key(50,'down');wait(lambda:any(e['key']==16777248 for e in json.loads(ipc('status'))))
    answer=request(art,'release');assert answer['state']=='uncaptured',answer
    assert any(e['key']==16777248 and e['kind']=='up' for e in json.loads(ipc('status')))
    ctl(parent,'dispatch','hl.dsp.submap("reset")')
    key(50,'up')
    print('8. Controller stall fails closed without silently reopening.',flush=True)
    ctl(parent,'dispatch','hl.dsp.submap("nested")');request(art,'capture')
    wait(lambda:native_status(art,child.pid)['state']=='captured')
    ipc('clear');key(37,'down');wait(lambda:json.loads(ipc('status')))
    def stalled_host():
        time.sleep(1.1)
        return host_state()
    controller.host=stalled_host
    wait(lambda:any(e['key']==16777249 and e['kind']=='up' for e in json.loads(ipc('status'))))
    wait(lambda:controller.generation==0)
    controller.host=host_state
    ipc('clear');type_key(38);time.sleep(.2)
    s=snapshot('controller-stall-stays-closed');assert not s['events'],s
    key(37,'up');request(art,'capture')
    print('9. Config reload retains gate; repeated capture/release stays clean.',flush=True)
    ctl(env,'reload')
    wait(lambda:native_status(art,child.pid)['state']=='captured')
    for iteration in range(8):
        request(art,'release');request(art,'capture')
        wait(lambda:native_status(art,child.pid)['state']=='captured')
        ipc('clear');type_key(38);wait(lambda:json.loads(ipc('status')))
        assert all(not e['modifiers'] for e in json.loads(ipc('status')))
    request(art,'release');ctl(parent,'dispatch','hl.dsp.submap("reset")')
    print('10. Alternate XKB layout and actual modifier combinations.',flush=True)
    ctl(env,'eval','hl.device({name="wl_keyboard", kb_layout="de"})')
    ctl(parent,'dispatch','hl.dsp.submap("nested")');request(art,'capture')
    wait(lambda:native_status(art,child.pid)['state']=='captured')
    ipc('clear');type_key(29);wait(lambda:json.loads(ipc('status')))
    s=snapshot('german-layout-physical-Y');assert any(e['text']=='z' for e in s['events']),s
    for code in (37,50,64,134): key(code,'down')
    wait(lambda:len(json.loads(ipc('status')))>=6)
    request(art,'release')
    for code in (37,50,64,134): key(code,'up')
    ipc('clear');run(env,'wtype','clean');wait(lambda:json.loads(ipc('status')))
    s=snapshot('all-modifiers-release-virtual-clean');assert all(not e['modifiers'] for e in s['events']),s
    print('11. Switching two nests requires explicit capture of each.',flush=True)
    request(art,'release')
    second=art/'second';second.mkdir(exist_ok=True)
    publisher2=second/'publish.py'
    publisher2.write_text('import os,json\nfrom pathlib import Path\nPath('+repr(str(second/'env.json'))+').write_text(json.dumps(dict(os.environ)))\n')
    (second/'env.json').unlink(missing_ok=True)
    config2=second/'hyprland.lua'
    config2.write_text(cfg.read_text().replace(str(publisher),str(publisher2)))
    probe2=second/'probe';shutil.copytree(probe,probe2,dirs_exist_ok=True)
    launch2={**launch,'OMADEV_KEYBOARD_GATE':str(second/'lease')}
    child2=subprocess.Popen(['Hyprland','--config',str(config2)],env=launch2,stdout=(second/'child.log').open('w'),stderr=subprocess.STDOUT)
    env2=json.loads(wait(lambda:(second/'env.json').read_text()))
    address2=wait(lambda:[w for w in json.loads(ctl(parent,'clients','-j')) if w['pid']==child2.pid])[0]['address']
    app2=subprocess.Popen(['quickshell','-p',str(probe2)],env=env2,stdout=(second/'probe.log').open('w'),stderr=subprocess.STDOUT)
    def ipc2(method):return run(env2,'quickshell','-p',str(probe2),'ipc','call','inputproof',method)
    wait(lambda:ipc2('status'))
    window2=wait(lambda:[w for w in json.loads(ctl(env2,'clients','-j')) if w['pid']==app2.pid])[0]
    ctl(env2,'dispatch',f'hl.dsp.focus({{window="address:{window2["address"]}"}})')
    wait(lambda:native_status(second,child2.pid)['state']!='unavailable')
    controller2=Controller(second,lambda:child2.pid if child2.poll() is None else None,host_state).start()
    ctl(parent,'dispatch',f'hl.dsp.focus({{window="address:{address}"}})')
    for code in (37,50,62,64,105,108,133,134):key(code,'up')
    request(art,'capture');wait(lambda:native_status(art,child.pid)['state']=='captured');ipc('clear');key(134,'down')
    wait(lambda:json.loads(ipc('status')))
    ctl(parent,'dispatch',f'hl.dsp.focus({{window="address:{address2}"}})')
    wait(lambda:controller.generation==0)
    assert any(e['key']==16777250 and e['kind']=='up' for e in json.loads(ipc('status')))
    first_address=address;address=address2
    for code in (37,50,62,64,105,108,133,134):key(code,'up')
    type_key(38);time.sleep(.15);assert not json.loads(ipc2('status'))
    request(second,'capture');wait(lambda:native_status(second,child2.pid)['state']=='captured');type_key(38);wait(lambda:json.loads(ipc2('status')))
    assert any(e['text']=='a' and not e['modifiers'] for e in json.loads(ipc2('status')))
    ctl(parent,'dispatch',f'hl.dsp.focus({{window="address:{first_address}"}})')
    address=first_address
    for code in (37,50,62,64,105,108,133,134):key(code,'up')
    ipc('clear');type_key(38);time.sleep(.15)
    assert not json.loads(ipc('status'))
    wait(lambda:controller2.generation==0)
    snapshot('switch-back-does-not-recapture')
    print('12. Closing the gate stops application key repeat.',flush=True)
    ctl(env,'eval','hl.config({input={repeat_rate=25,repeat_delay=200}})')
    request(art,'capture');wait(lambda:native_status(art,child.pid)['state']=='captured')
    ipc('clear');key(38,'down')
    wait(lambda:any(e['repeat'] and e['kind']=='down' for e in json.loads(ipc('status'))))
    request(art,'release')
    wait(lambda:any(e['kind']=='up' and not e['repeat'] for e in json.loads(ipc('status'))))
    ipc('clear');time.sleep(.5)
    assert not json.loads(ipc('status')), 'key repeat continued after gate closed'
    key(38,'up');snapshot('closed-stops-key-repeat')
    print('13. Dead controller release fallback waits for live native cleanup.',flush=True)
    controller.stop();controller=None
    capture();ipc('clear');key(50,'down');wait(lambda:json.loads(ipc('status')))
    launcher=runpy.run_path(str(root/'bin/omadev'))
    release=launcher['release_gate_records']
    record={'OMADEV_INPUT_GATE':'1','OMADEV_INPUT_GATE_DIR':str(art),'OMADEV_PID':str(child.pid)}
    with patch.dict(release.__globals__, SLOTS=(1,), read_env=lambda _:record, slot_health=lambda *_:'alive'):
        release()
    assert native_status(art,child.pid)['forwarded']==0
    wait(lambda:any(e['kind']=='up' and e['key']==16777248 for e in json.loads(ipc('status'))))
    key(50,'up');snapshot('dead-controller-native-cleanup')
    print('14. Installed emergency binding resets routing inside the disposable child.',flush=True)
    # Exercise the actual Lua callback without invoking any real-host capture.
    setup='o={bind=function() end}; local original=hl.bind; hl.bind=function(k,a,opts) if k=="SUPER + SHIFT + ALT + D" then gate_test_escape=a end; return original(k,a,opts) end; '
    ctl(env,'eval',setup+launcher['BINDINGS_BLOCK']+'\nhl.bind=original')
    assert not ctl(env,'configerrors')
    ctl(env,'dispatch','hl.dsp.submap("nested")')
    assert ctl(env,'submap')=='nested'
    ctl(env,'eval','gate_test_escape()')
    assert ctl(env,'submap') in ('default','reset','')
    print('PASS: emergency escape works through the compositor, independently of the launcher',flush=True)
    run(env,'grim',str(art/'probe.png'))
    print('PASS: native gate and controller integration scenarios.',flush=True)
    (art/'passed').write_text('passed\n')
finally:
    if controller2: controller2.stop()
    if controller: controller.stop()
    close()
    ctl(parent,'dispatch','hl.dsp.submap("reset")')
    (art/'results.json').write_text(json.dumps(results,indent=2)+'\n')
    if env:
        try:ctl(env,'dispatch','hl.dsp.exit()')
        except Exception:pass
    if env2:
        try:ctl(env2,'dispatch','hl.dsp.exit()')
        except Exception:pass
    for p in [app2,child2,app,child]:
        if p:
            try:p.wait(timeout=5)
            except subprocess.TimeoutExpired:p.terminate();p.wait(timeout=5)
