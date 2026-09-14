// Run with node tests/panel.cjs. Execute the panel's actual JS with mocked side effects.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const qml = fs.readFileSync(path.join(__dirname, '../plugin/dry.omadev/Omadev.qml'), 'utf8');
const focused = [], stopped = [];
const ctx = vm.createContext({
  sessions: [
    {slot: 2, pid: 102, started_at: 10, state: 'running'},
    {slot: 5, pid: 105, started_at: 20, state: 'running'},
    {slot: 8, pid: 108, state: 'starting'},
  ],
  selectedIndex: 0, pendingStop: '', pendingFocus: '', stopRequests: {}, runningCount: 2,
  Color: {foreground: '#ffffff'}, clearModifiersOnSnapshot: false,
  opened: true, selectedScreen: null, focusAfterSelection: {restart() {}, stop() {}}, flashSelection() {},
  initialSelectionPending: false, hostSnapshotReady: true, loaded: true, hostWindows: [],
  hostMonitors: [{id: 1, focused: true, activeWorkspace: {id: 4}, specialWorkspace: {id: 0}}],
  focusSlot: n => focused.push(n), stopSlot: n => stopped.push(n),
});
for (const name of ['applyModifierState', 'modifierLabel', 'nestLabel', 'markStopping', 'availableIndex', 'moveCursor', 'selectInitiallyFocusedWindow', 'loadHighlightColor', 'focusNumber', 'completeNumberFocus', 'windowOnWorkspace', 'sessionIdentity', 'stopSelected', 'applyList']) {
  const start = qml.indexOf('  function ' + name + '(');
  assert.notEqual(start, -1);
  vm.runInContext(qml.slice(start, qml.indexOf('\n  }', start) + 4), ctx);
}
ctx.applyModifierState('Ctrl,Super,Super,ignored');
assert.equal(ctx.modifierLabel(), 'mods: Super+Ctrl');
ctx.slot = '3'; ctx.labelTier = 'icon'; ctx.captured = false;
assert.ok(ctx.nestLabel().includes('mods: Super+Ctrl'));
ctx.applyModifierState('');
assert.equal(ctx.modifierLabel(), 'mods: none');
ctx.modifierStateKnown = false;
assert.equal(ctx.modifierLabel(), 'mods: ?');
ctx.clearModifierProc = {running: false};
ctx.clearModifiersOnSnapshot = true;
ctx.captured = true;
ctx.applyModifierState('Super,Ctrl|133,66,37,40,999');
assert.equal(JSON.stringify(ctx.modifierCodesToRelease), '[133,66,37]');
assert.equal(ctx.clearModifierProc.running, true);
assert.equal(ctx.clearModifiersOnSnapshot, false);
ctx.clearModifierProc.running = false;
ctx.applyModifierState('Super|133');
assert.equal(ctx.clearModifierProc.running, false); // Not continuously clearing while typing.
ctx.clearModifiersOnSnapshot = true;
ctx.captured = false;
ctx.applyModifierState('Super|133');
assert.equal(ctx.clearModifierProc.running, false); // Capture ended while the query was in flight.
const focusedWindow = {pid: 105, focusHistoryID: 0, mapped: true, monitor: 1, workspace: {id: 4}};
ctx.initialSelectionPending = true;
ctx.selectInitiallyFocusedWindow([focusedWindow]);
assert.equal(ctx.selectedIndex, 1);
ctx.selectedIndex = 0;
ctx.selectInitiallyFocusedWindow([focusedWindow]); // Polling must not override navigation.
assert.equal(ctx.selectedIndex, 0);
ctx.initialSelectionPending = true;
ctx.selectInitiallyFocusedWindow([{...focusedWindow, workspace: {id: 3}}]);
assert.equal(ctx.selectedIndex, 0);
ctx.initialSelectionPending = true;
ctx.selectInitiallyFocusedWindow([{...focusedWindow, pid: 999}]);
assert.equal(ctx.selectedIndex, 0);
ctx.loadHighlightColor('magenta = "#ad8ee6"');
assert.equal(ctx.highlightColor, '#ad8ee6');
ctx.loadHighlightColor('color5 = "#bb9af7"');
assert.equal(ctx.highlightColor, '#bb9af7');
ctx.loadHighlightColor('');
assert.equal(ctx.highlightColor, '#ffffff');
ctx.focusNumber('5');
ctx.focusNumber('1'); // Slot, not row index.
ctx.focusNumber('8'); // Not ready.
assert.deepEqual(focused, []);
assert.equal(ctx.selectedIndex, 1);
ctx.completeNumberFocus();
assert.deepEqual(focused, [5]);
ctx.focusNumber('2');
ctx.opened = false;
ctx.completeNumberFocus();
assert.deepEqual(focused, [5]);
ctx.opened = true;
ctx.selectedIndex = 0;
assert.equal(ctx.windowOnWorkspace({monitor: 1, workspace: {id: 4}}), true);
assert.equal(ctx.windowOnWorkspace({monitor: 1, workspace: {id: 3}}), false);
assert.equal(ctx.windowOnWorkspace({monitor: 2, workspace: {id: 4}}), false);
ctx.hostMonitors[0].specialWorkspace = {id: -9};
assert.equal(ctx.windowOnWorkspace({monitor: 1, workspace: {id: 4}}), false);
assert.equal(ctx.windowOnWorkspace({monitor: 1, workspace: {id: -9}}), true);
ctx.stopSelected();
assert.equal(stopped.length, 0);
assert.notEqual(ctx.pendingStop, '');
ctx.applyList(JSON.stringify(ctx.sessions));
assert.notEqual(ctx.pendingStop, '');
ctx.stopSelected();
assert.deepEqual(stopped, [2]);
assert.equal(ctx.pendingStop, '');
ctx.stopSelected();
ctx.applyList(JSON.stringify([{...ctx.sessions[0], pid: 202}]));
assert.equal(ctx.pendingStop, '');
ctx.stopSelected();
assert.equal(stopped.length, 1);
ctx.applyList('[]');
assert.equal(ctx.pendingStop, '');
ctx.stopSelected();
assert.equal(stopped.length, 1);
const stopEvents = [];
const stopContext = vm.createContext({
  opened: true, nested: false, pendingFocus: 'pending', markStopping() {},
  armRetake: () => stopEvents.push('retain-focus'),
  root: {bar: {run: command => stopEvents.push(command)}},
  refreshSoon: {restart: () => stopEvents.push('refresh')},
});
const stopStart = qml.indexOf('  function stopSlot(n) {');
vm.runInContext(qml.slice(stopStart, qml.indexOf('\n  }', stopStart) + 4), stopContext);
stopContext.stopSlot(2);
assert.deepEqual(stopEvents, ['retain-focus', 'omadev stop 2', 'refresh']);
assert.equal(stopContext.pendingFocus, '');
const snapshot = [1, 2, 3].map(slot => ({slot, pid: 100 + slot, started_at: 10, state: 'running'}));
ctx.sessions = snapshot;
ctx.selectedIndex = 1;
ctx.markStopping(2);
assert.equal(ctx.sessions[1].state, 'stopping');
assert.equal(ctx.sessions[ctx.selectedIndex].slot, 3);
ctx.applyList(JSON.stringify(snapshot)); // A stale poll cannot resurrect the row.
assert.equal(ctx.sessions[1].state, 'stopping');
ctx.applyList(JSON.stringify([snapshot[0], {slot: 2, state: 'starting'}, snapshot[2]]));
assert.equal(ctx.sessions[1].state, 'stopping');
assert.equal(ctx.sessions[1].pid, 102);
ctx.moveCursor(-1); // Skip the disabled row.
assert.equal(ctx.sessions[ctx.selectedIndex].slot, 1);
ctx.moveCursor(1);
assert.equal(ctx.sessions[ctx.selectedIndex].slot, 3);
ctx.applyList(JSON.stringify([snapshot[0], snapshot[2]]));
assert.equal(ctx.selectedIndex, 1); // Same session after the earlier row disappears.
assert.equal(ctx.sessions[ctx.selectedIndex].slot, 3);
ctx.markStopping(3); // Last row falls back to the previous usable session.
assert.equal(ctx.sessions[ctx.selectedIndex].slot, 1);
ctx.markStopping(1);
assert.equal(ctx.selectedIndex, -1);
ctx.applyList('[]');
assert.equal(Object.keys(ctx.stopRequests).length, 0);
console.log('PASS: shortcuts, focus retention, stopping labels, automatic advance, and stable selection');
