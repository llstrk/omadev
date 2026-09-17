"""Official git-plugin lifecycle inside an owned nest, no real host installation/capture."""
import json
import os
from pathlib import Path
import shutil
import shlex
import subprocess
import time

ROOT = Path(__file__).resolve().parents[1]
ENV = dict(os.environ)
assert ENV.get('OMADEV_OWNER') == 'plugin-install-tests'
assert ENV.get('OMADEV_SLOT') and ENV['HYPRLAND_INSTANCE_SIGNATURE'] != ENV['OMADEV_HOST_SIG']
HOME = Path(ENV['HOME'])
CONFIG = Path(ENV['XDG_CONFIG_HOME'])
assert CONFIG.resolve().is_relative_to(HOME.resolve())
ART = ROOT/'test-results/plugin-lifecycle'
ART.mkdir(parents=True, exist_ok=True)
(ART/'passed').unlink(missing_ok=True)


def run(*args, env=ENV):
    result = subprocess.run(args, env=env, text=True, capture_output=True, timeout=40)
    if result.returncode:
        raise RuntimeError(str(args)+': '+result.stdout+result.stderr)
    return result.stdout.strip()


def git_commit(directory, message):
    run('git','-C',str(directory),'-c','user.name=Fixture','-c','user.email=fixture@example.invalid',
        '-c','commit.gpgsign=false','-c','core.hooksPath=/dev/null','commit','-qm',message)


def restart_model(model):
    # Same display-scoped stop used by the nest-safe restart wrapper. Launch via
    # the owned compositor with explicit test-only overrides, not the host bus.
    shell = str(Path(ENV['OMARCHY_PATH'])/'shell')
    for _ in range(5):
        stopped = subprocess.run(['quickshell','kill','-p',shell], env=ENV, capture_output=True, timeout=5)
        if stopped.returncode: break
    overrides = ['env'] + [key+'='+model[key] for key in ('OMADEV_HOST_SIG','OMADEV_HOST_CONFIG','OMADEV_ROOT')]
    command = shlex.join(overrides+['omarchy-launch-shell'])
    run('hyprctl','dispatch','hl.dsp.exec_cmd('+json.dumps(command)+')')


def capture_panel(name):
    run('omarchy-shell','shell','toggle','dry.omadev','{}')
    deadline = time.monotonic()+10
    while time.monotonic()<deadline:
        layers = json.loads(run('hyprctl','layers','-j'))
        panels = [layer for monitor in layers.values() for level in monitor['levels'].values()
                  for layer in level if layer['namespace']=='omarchy-keyboard-panel'
                  and layer.get('alpha',0)>=.99 and layer['h']>60]
        if panels:
            run('grim',str(ART/name))
            return
        time.sleep(.1)
    raise AssertionError('panel did not become visible')


def wait_state(state):
    deadline = time.monotonic()+20
    while time.monotonic()<deadline:
        try:
            value = json.loads(run('omarchy-shell','dry.omadev','status'))
            if value.get('backend', {}).get('state') == state:
                return value
        except (RuntimeError, ValueError):
            pass
        time.sleep(.2)
    raise AssertionError('widget did not reach '+state)


print('1. Build a local git fixture containing the current plugin/backend source.', flush=True)
source = ART/'source'
if source.exists(): shutil.rmtree(source)
source.mkdir()
files = run('git','-C',str(ROOT),'ls-files','--cached','--others','--exclude-standard','-z').split('\0')
for name in files:
    if not name or not (ROOT/name).is_file(): continue
    target = source/name; target.parent.mkdir(parents=True, exist_ok=True)
    assert not (ROOT/name).is_symlink(), name
    shutil.copy2(ROOT/name,target)
run('git','init','-q',str(source))
run('git','-C',str(source),'add','.')
git_commit(source, 'Plugin fixture')
run('omarchy','plugin','validate',str(source))

print('2. Back up only this nest\'s old widget and use the official add command.', flush=True)
plugin = CONFIG/'omarchy/plugins/dry.omadev'
if plugin.exists() or plugin.is_symlink():
    backup = ART/('old-widget-'+str(time.monotonic_ns()))
    plugin.rename(backup)
run('omarchy','plugin','add',str(source),'--enable','--yes')
assert (plugin/'.git').is_dir()
assert not run('git','-C',str(plugin),'status','--porcelain')
head = run('git','-C',str(plugin),'rev-parse','HEAD')

print('3. Backend setup must leave the plugin git checkout and settings untouched.', flush=True)
settings = (CONFIG/'omarchy/shell.json').read_bytes()
run(str(ROOT/'bin/omadev'),'setup')
assert run('git','-C',str(plugin),'rev-parse','HEAD') == head
assert not run('git','-C',str(plugin),'status','--porcelain')
assert (CONFIG/'omarchy/shell.json').read_bytes() == settings
registration = (CONFIG/'omadev/backend.json').read_bytes()
backend = (ROOT/'bin/omadev').read_bytes()

print('4. Official plugin update changes only the widget checkout.', flush=True)
with (source/'README.md').open('a') as file: file.write('\nFixture update.\n')
run('git','-C',str(source),'add','README.md'); git_commit(source,'Update fixture')
run('omarchy','plugin','update','dry.omadev','--yes')
assert run('git','-C',str(plugin),'rev-parse','HEAD') != head
assert not run('git','-C',str(plugin),'status','--porcelain')
assert (CONFIG/'omadev/backend.json').read_bytes() == registration
assert (ROOT/'bin/omadev').read_bytes() == backend

print('5. Render the host face in this guarded nest: missing backend then ready.', flush=True)
# Keep OMADEV_SLOT and private HOME/runtime/bus to preserve the nest guards.
# Only the widget's host-face selector and modeled parent config are changed.
missing_root = ART/'missing-backend'
shutil.copytree(ROOT/'libexec', missing_root/'libexec', dirs_exist_ok=True,
                ignore=shutil.ignore_patterns('*.so','__pycache__'))
# Keep the command-guard overlay valid even when the modeled launcher is absent.
model = {**ENV, 'OMADEV_HOST_SIG':'', 'OMADEV_HOST_CONFIG':str(CONFIG), 'OMADEV_ROOT':str(missing_root/'libexec')}
restart_model(model)
missing = wait_state('missing')
assert not missing['backend']['compatible']
capture_panel('missing.png')
model['OMADEV_ROOT'] = str(ROOT/'libexec')
restart_model(model)
ready = wait_state('ready')
capture_panel('ready.png')
assert ready['backend']['launcher'] == str(ROOT/'bin/omadev')
print('6. Custom bindings remain manual, while uncaptured session creation is available.',flush=True)
bindings = CONFIG/'hypr/bindings.lua'
assert bindings.resolve().is_relative_to(HOME.resolve())
original_bindings = bindings.read_bytes()
try:
    bindings.write_text('-- omadev: custom fixture bindings\n')
    restart_model(model)
    custom = wait_state('unconfigured')
    assert custom['backend']['bindingsStatus'] == 'custom'
    assert custom['backendCanStart'] and not custom['backendReady'] and not custom['backendActionAvailable']
    capture_panel('custom-bindings.png')
finally:
    bindings.write_bytes(original_bindings)
print('7. Render incompatible, build-required and unavailable responses from a fixture backend.',flush=True)
fixture = ART/'modeled-backend'; (fixture/'bin').mkdir(parents=True,exist_ok=True)
shutil.copytree(ROOT/'libexec', fixture/'libexec', dirs_exist_ok=True,
                ignore=shutil.ignore_patterns('*.so','__pycache__'))
(fake := fixture/'bin/omadev').touch()
model['OMADEV_ROOT'] = str(fixture/'libexec')
modeled = {}
for state, protocol, helper in [('incompatible',99,True), ('build-required',1,False), ('unavailable',1,True)]:
    data = {'application':'omadev','protocol':protocol,'launcher':str(fake), 'version':'fixture',
            'nativeHelperPresent':helper,'bindingsConfigured':True,'bindingsStatus':'ready'}
    code = '#!/usr/bin/python3\nimport sys\n'
    code += 'sys.exit(1)\n' if state=='unavailable' else 'print("[]" if sys.argv[1]=="list" else '+repr(json.dumps(data))+')\n'
    fake.write_text(code);fake.chmod(0o755)
    restart_model(model)
    observed = wait_state(state)
    modeled[state] = observed['backend']
    if state == 'unavailable': assert not observed['backendActionAvailable']
    capture_panel(state+'.png')
assert not run('git','-C',str(plugin),'status','--porcelain')
(ART/'results.json').write_text(json.dumps({'missing':missing['backend'], 'ready':ready['backend'],
                                         'custom':custom['backend'], 'modeled':modeled,
                                         'pluginClean':True, 'backendUnchangedByPluginUpdate':True},indent=2))
(ART/'passed').write_text('passed\n')
print('PASS: official add/update, setup ownership, and backend readiness UI',flush=True)
