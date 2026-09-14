import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import Quickshell
import Quickshell.Io
import Quickshell.Wayland
import qs.Commons
import qs.Ui

// Companion widget for omadev. One plugin, two faces:
//
//  * On the host (no OMADEV_HOST_SIG in the environment) it is a single icon
//    that opens a panel listing the open sessions. Each row focuses its
//    session and captures keys for it or stops it; the
//    hero button starts a new session in the first free slot.
//
//  * Inside a session it shows whether the host is passing keys here, and a
//    click releases or captures them. Super+Alt+D releases captured keys.
//
// Session state comes from `omadev list --json`, refreshed by the launcher on
// start/exit (IPC `refresh`), while the panel is open, and polled slowly as a
// fallback. Inside a session the capture state comes from the host's event
// socket (submap>> and activewindow>> events).
Panel {
  id: root
  moduleName: "dry.omadev"
  ipcTarget: "dry.omadev"
  manageIpc: false

  readonly property string hostSignature: Quickshell.env("OMADEV_HOST_SIG") || ""
  readonly property string slot: Quickshell.env("OMADEV_SLOT") || ""
  readonly property string runtimeDir: Quickshell.env("XDG_RUNTIME_DIR") || ("/run/user/" + Quickshell.env("UID"))
  readonly property string home: Quickshell.env("HOME") || ""
  readonly property string captureSubmap: String(setting("submap", "nested"))
  readonly property bool nested: hostSignature !== ""

  // The nest face shrinks with the session's width so the bar's centre block
  // (clock, weather) does not run into it when the window is tiled narrow.
  readonly property int screenWidth: Quickshell.screens.length > 0 ? Quickshell.screens[0].width : 0
  readonly property int compactBelow: Number(setting("compactBelow", 1300))
  readonly property int iconOnlyBelow: Number(setting("iconOnlyBelow", 900))
  readonly property string labelTier: !nested || screenWidth === 0 || screenWidth >= compactBelow ? "full"
    : (screenWidth >= iconOnlyBelow ? "compact" : "icon")

  property var heldModifiers: []
  property var heldModifierCodes: []
  property bool clearModifiersOnSnapshot: false
  property var modifierCodesToRelease: []
  property bool modifierStateKnown: false
  property real modifiersUpdatedAt: 0

  function modifierLabel() {
    return "mods: " + (!modifierStateKnown ? "?" : (heldModifiers.length ? heldModifiers.join("+") : "none"))
  }

  function applyModifierState(raw) {
    var allowed = ["Super", "Ctrl", "Alt", "Shift", "AltGr"]
    var parts = String(raw).split("|")
    var received = parts[0].split(",")
    var modifierCodes = [37, 50, 62, 64, 66, 105, 108, 133, 134]
    heldModifiers = allowed.filter(function(name) { return received.indexOf(name) !== -1 })
    heldModifierCodes = (parts[1] || "").split(",").map(Number).filter(function(code) {
      return modifierCodes.indexOf(code) !== -1
    })
    modifierStateKnown = true
    modifiersUpdatedAt = Date.now()
    if (clearModifiersOnSnapshot && parts.length > 1) {
      clearModifiersOnSnapshot = false
      if (captured && heldModifiers.length && heldModifierCodes.length && !clearModifierProc.running) {
        modifierCodesToRelease = heldModifierCodes.slice()
        clearModifierProc.running = true
      }
    }
  }

  function nestLabel() {
    var head = "󰌌 " + (slot !== "" ? slot : "")
    if (labelTier === "compact") head += "  " + (captured ? "CAPTURING" : "capture")
    else if (labelTier === "full") head += "  " + (captured ? "CAPTURING KEYS" : "click to capture input")
    // Keep the diagnostic visible even on small, icon-only nests.
    return head + "  ·  " + modifierLabel()
  }

  // Nest face. "Captured" means the host is in the passthrough submap AND
  // its focused window is this session's compositor window; with several
  // sessions the submap alone does not say which one receives the keys.
  readonly property string ownSignature: Quickshell.env("HYPRLAND_INSTANCE_SIGNATURE") || ""
  property string ownPid: ""
  property string hostActivePid: ""
  property string hostSubmap: ""
  property bool socketUp: false
  readonly property bool passthrough: hostSubmap === captureSubmap
  onPassthroughChanged: {
    if (nested && !passthrough && focused) pulseCaptureFeedback(false)
  }
  readonly property bool focused: ownPid !== "" && hostActivePid === ownPid
  readonly property bool captured: passthrough && focused
  onCapturedChanged: {
    clearModifiersOnSnapshot = nested && captured
    if (nested && captured) {
      // Query fresh guest state first, rather than replaying a stale UI label.
      if (!modifierProbe.running) modifierProbe.running = true
      pulseCaptureFeedback(true)
    }
  }

  // Host face.
  property var sessions: []          // non-free entries of `omadev list --json` (starting, running, stopping)
  property int runningCount: 0
  property bool loaded: false
  property int selectedIndex: -1
  property string pendingStop: ""
  property var stopRequests: ({})
  property string pendingFocus: ""
  onSelectedIndexChanged: { pendingStop = ""; pendingFocus = "" }
  property bool cursorActive: false
  property var hostWindows: []
  property var hostMonitors: []
  property bool initialSelectionPending: false
  property bool hostSnapshotReady: false
  readonly property var selectedWindow: {
    if (!opened || nested || initialSelectionPending || !cursorActive || selectedIndex < 0 || selectedIndex >= sessions.length) return null
    return windowForSession(sessions[selectedIndex])
  }
  readonly property var selectedScreen: screenForWindow(selectedWindow)
  property color highlightColor: Color.foreground

  function loadHighlightColor(raw) {
    var match = String(raw || "").match(/^\s*(?:magenta|color5)\s*=\s*["']?(#[0-9a-fA-F]{6})/m)
    highlightColor = match ? match[1] : Color.foreground
  }

  FileView {
    id: highlightPalette
    path: Color.currentThemePath + "/colors.toml"
    watchChanges: true
    printErrors: false
    onLoaded: root.loadHighlightColor(text())
    onLoadFailed: root.loadHighlightColor("")
    onFileChanged: reload()
  }

  Connections {
    target: Color
    function onAccentChanged() { highlightPalette.reload() }
    function onBackgroundChanged() { highlightPalette.reload() }
  }
  readonly property string selectionGeometry: selectedWindow && selectedScreen
    ? JSON.stringify([selectedWindow.pid, selectedWindow.at, selectedWindow.size, selectedScreen.name]) : ""
  onSelectionGeometryChanged: Qt.callLater(flashSelection)

  function flashSelection() {
    selectionMove.stop()
    selectionFlash.stop()
    flashFill.opacity = 0
    if (selectedScreen === null) {
      selectionFrame.previousScreen = null
      return
    }
    if (selectionFrame.previousScreen !== selectedScreen) {
      selectionFrame.x = selectionFrame.targetX
      selectionFrame.y = selectionFrame.targetY
      selectionFrame.width = selectionFrame.targetWidth
      selectionFrame.height = selectionFrame.targetHeight
      selectionFrame.previousScreen = selectedScreen
      // Opening only places the outline. Activation uses the nest's red
      // capture pulse, not a second host-side flash.
      if (pendingFocus !== "") completeNumberFocus()
    } else {
      selectionMove.restart()
    }
  }

  function windowForSession(session) {
    var pid = String(session.pid)
    for (var i = 0; i < hostWindows.length; i++) {
      var w = hostWindows[i]
      if (String(w.pid) === pid && w.mapped && !w.hidden && w.visible !== false && windowOnWorkspace(w)) return w
    }
    return null
  }
  function windowOnWorkspace(w) {
    if (!w.workspace) return false
    for (var i = 0; i < hostMonitors.length; i++) {
      var m = hostMonitors[i]
      if (m.id !== w.monitor || m.disabled) continue
      var special = m.specialWorkspace && m.specialWorkspace.id !== 0
      var workspace = special ? m.specialWorkspace : m.activeWorkspace
      return !!workspace && (workspace.id === w.workspace.id || (!special && w.pinned === true))
    }
    return false
  }

  function selectInitiallyFocusedWindow(windows) {
    if (!initialSelectionPending || !hostSnapshotReady || !loaded) return
    if (selectedIndex < 0 && sessions.length > 0) selectedIndex = availableIndex(0)
    // Layer-shell focus can make activewindow empty. The most recently focused
    // client remains at history index zero while the panel owns the keyboard.
    for (var i = 0; i < windows.length; i++) {
      var w = windows[i]
      if (w.focusHistoryID !== 0 || !w.mapped || w.hidden || w.visible === false || !windowOnWorkspace(w)) continue
      var onFocusedMonitor = false
      for (var j = 0; j < hostMonitors.length; j++)
        if (hostMonitors[j].id === w.monitor && hostMonitors[j].focused) onFocusedMonitor = true
      if (!onFocusedMonitor) continue
      for (var k = 0; k < sessions.length; k++)
        if (sessions[k].state === "running" && String(sessions[k].pid) === String(w.pid)) {
          selectedIndex = k
          initialSelectionPending = false
          return
        }
    }
    initialSelectionPending = false
  }

  function screenForWindow(w) {
    if (!w) return null
    var x = w.at[0] + w.size[0] / 2, y = w.at[1] + w.size[1] / 2
    for (var i = 0; i < Quickshell.screens.length; i++) {
      var s = Quickshell.screens[i]
      if (x >= s.x && x < s.x + s.width && y >= s.y && y < s.y + s.height) return s
    }
    return null
  }
  // Close when keyboard focus moves elsewhere (workspace switch, another
  // window). The panel window is not active for the first instants after
  // opening, so only a loss after it held focus counts.
  property bool heldFocus: false
  // Any session window mapping on the host makes Hyprland re-evaluate focus
  // and the panel loses it, whether the panel started that session or an
  // agent did from a terminal. While this is set the panel takes focus back
  // instead of closing; it is armed by starting/stopping and by openwindow events for nest
  // windows on the host's own event socket.
  property bool retakeFocus: false
  readonly property string ownHostSignature: Quickshell.env("HYPRLAND_INSTANCE_SIGNATURE") || ""

  function armRetake() {
    retakeFocus = true
    retakeWindow.restart()
    if (opened && !keyCatcher.Window.active) regainFocus()
  }
  property real now: Date.now() / 1000

  function hostctl(args) {
    return "hyprctl -i " + Util.shellQuote(hostSignature) + " " + args
  }

  function releaseKeys() {
    if (!root.bar || !nested) return
    root.bar.run(hostctl("dispatch " + Util.shellQuote('hl.dsp.submap("reset")')))
  }

  function toggleCapture() {
    if (captured) releaseKeys()
    else if (slot !== "" && root.bar) root.bar.run("env OMADEV_ALLOW_CAPTURE=1 omadev focus " + slot)
  }

  function focusSlot(n) {
    for (var i = 0; i < sessions.length; i++)
      if (String(sessions[i].slot) === String(n) && sessions[i].state === "running") {
        if (root.bar) root.bar.run("env OMADEV_ALLOW_CAPTURE=1 omadev focus " + n)
        close()
        return
      }
  }

  function stopSlot(n) {
    pendingFocus = ""
    pendingStop = ""
    markStopping(n)
    // Closing the nest can move compositor focus before the next list refresh.
    // Arm first, just as for starting a session, so d d keeps the panel open.
    if (opened && !nested) armRetake()
    if (root.bar) root.bar.run("omadev stop " + n)
    refreshSoon.restart()
  }

  function markStopping(n) {
    var requests = Object.assign({}, stopRequests)
    for (var i = 0; i < sessions.length; i++)
      if (String(sessions[i].slot) === String(n)) {
        requests[String(n)] = { session: sessions[i], requestedAt: Date.now() }
        break
      }
    stopRequests = requests
    applyList(JSON.stringify(sessions))
  }

  function availableIndex(start) {
    start = Math.max(0, Math.min(start, sessions.length))
    for (var i = start; i < sessions.length; i++)
      if (sessions[i].state === "running") return i
    for (var j = start - 1; j >= 0; j--)
      if (sessions[j].state === "running") return j
    return -1
  }

  function gotoWorkspace() {
    if (root.bar && !nested) root.bar.run("hyprctl dispatch " + Util.shellQuote('hl.dsp.focus({ workspace = "name:omadev" })'))
    close()
  }

  function startSession() {
    pendingFocus = ""
    pendingStop = ""
    // Prefer the user's installed launcher over an older distribution copy.
    if (root.bar && !nested) root.bar.run('PATH="$HOME/.local/bin:$PATH" omadev start --detach')
    if (opened) armRetake()
    refreshSoon.restart()
  }

  function regainFocus() {
    panel.focusPrimed = false      // Exclusive again for an instant, as on open
    panel.beginFocusPrime()
    Qt.callLater(function() { if (root.opened) keyCatcher.forceActiveFocus() })
  }

  function refreshActive() {
    if (nested && !activeProc.running) activeProc.running = true
  }

  function refresh() {
    if (nested) {
      if (!queryProc.running) queryProc.running = true
      if (ownPid === "" && !pidProc.running) pidProc.running = true
      refreshActive()
    }
    if (!listProc.running) listProc.running = true
  }

  function applySubmapName(raw) {
    var name = String(raw || "").trim()
    hostSubmap = (name === "" || name === "default") ? "default" : name
  }

  function applyList(raw) {
    var previous = sessions[selectedIndex]
    var previousIndex = selectedIndex
    var list = [], requests = {}, previousBySlot = {}
    for (var p = 0; p < sessions.length; p++) previousBySlot[String(sessions[p].slot)] = sessions[p]
    try {
      var all = JSON.parse(raw)
      for (var i = 0; i < all.length; i++) {
        var s = all[i], key = String(s.slot), request = stopRequests[key]
        if (s.state === "free") continue
        if (request && Date.now() - request.requestedAt < 30000
            && (s.state === "starting" || sessionIdentity(s) === sessionIdentity(request.session))) {
          requests[key] = request
          s = Object.assign({}, request.session, s, { state: "stopping" })
        } else if (s.state === "starting" && previousBySlot[key] && previousBySlot[key].state === "stopping") {
          // Older launchers briefly lose their record before releasing the slot
          // lock, misreporting the final cleanup phase as a new startup.
          s = Object.assign({}, previousBySlot[key], s, { state: "stopping" })
        }
        list.push(s)
      }
    } catch (e) {
      list = []
    }
    stopRequests = requests
    now = Date.now() / 1000
    loaded = true
    var running = 0
    for (var j = 0; j < list.length; j++)
      if (list[j].state === "running") running++
    if (running > runningCount) retakeFocus = false  // the started session is up (its window has mapped)
    runningCount = running
    if (JSON.stringify(list) !== JSON.stringify(sessions)) sessions = list
    var preserved = -1, previousPosition = -1
    if (previous) {
      for (var k = 0; k < sessions.length; k++) {
        if (sessionIdentity(sessions[k]) !== sessionIdentity(previous)) continue
        previousPosition = k
        if (sessions[k].state === "running") preserved = k
        break
      }
    }
    selectedIndex = preserved >= 0 ? preserved
      : availableIndex(previousPosition >= 0 ? previousPosition + 1 : Math.max(0, previousIndex))
    var selected = sessions[selectedIndex]
    if (!selected || selected.state !== "running" || sessionIdentity(selected) !== pendingStop) pendingStop = ""
    selectInitiallyFocusedWindow(hostWindows)
  }

  function runningSlots() {
    var out = []
    for (var i = 0; i < sessions.length; i++) out.push(String(sessions[i].slot))
    return out
  }

  function shortPath(p) {
    if (!p || p === "/usr/share/omarchy") return "installed Omarchy"
    return home !== "" && p.indexOf(home) === 0 ? "~" + p.substring(home.length) : p
  }

  function uptime(s) {
    var secs = Math.max(0, Math.round(now - Number(s.started_at || now)))
    if (secs < 60) return "just started"
    var m = Math.floor(secs / 60), h = Math.floor(m / 60)
    return "up " + (h > 0 ? h + "h " + (m % 60) + "m" : m + "m")
  }

  function sessionMeta(s) {
    if (s.state === "stopping") return "Stopping…"
    if (s.state === "starting") return "Starting…"
    var parts = [shortPath(s.omarchy_path)]
    if (s.owner) parts.push(s.owner)
    var plugins = s.plugins ? Object.keys(s.plugins) : []
    if (plugins.length) parts.push(plugins.join(", "))
    parts.push(s.state === "stopping" ? "stopping" : (s.state === "starting" ? "starting" : uptime(s)))
    return parts.join(" · ")
  }

  function moveCursor(delta) {
    initialSelectionPending = false
    if (sessions.length === 0 || delta === 0) return
    var step = delta > 0 ? 1 : -1
    for (var next = selectedIndex + step; next >= 0 && next < sessions.length; next += step)
      if (sessions[next].state === "running") {
        selectedIndex = next
        return
      }
  }

  function focusNumber(n) {
    initialSelectionPending = false
    for (var i = 0; i < sessions.length; i++)
      if (String(sessions[i].slot) === n && sessions[i].state === "running") {
        selectedIndex = i
        cursorActive = true
        pendingStop = ""
        pendingFocus = sessionIdentity(sessions[i])
        flashSelection()
        // No visible target to animate when focusing a session on another workspace.
        if (selectedScreen === null) focusAfterSelection.restart()
        else focusAfterSelection.stop()
        return
      }
  }

  function completeNumberFocus() {
    var s = sessions[selectedIndex]
    if (opened && s && s.state === "running" && pendingFocus === sessionIdentity(s)) {
      pendingFocus = ""
      focusSlot(s.slot)
    }
  }

  function activateCursor() {
    if (selectedIndex >= 0 && selectedIndex < sessions.length) focusSlot(sessions[selectedIndex].slot)
  }

  function sessionIdentity(s) {
    return JSON.stringify([s.slot, s.pid, s.started_at])
  }

  function stopSelected() {
    pendingFocus = ""
    if (selectedIndex < 0 || selectedIndex >= sessions.length) return
    var s = sessions[selectedIndex]
    if (s.state !== "running") return
    var identity = sessionIdentity(s)
    if (pendingStop !== identity) {
      pendingStop = identity
      return
    }
    pendingStop = ""
    stopSlot(s.slot)
  }

  implicitWidth: nested ? nestButton.implicitWidth : button.implicitWidth
  implicitHeight: nested ? nestButton.implicitHeight : button.implicitHeight

  Component.onCompleted: refresh()

  // Older installed launchers don't seed the private stay-awake marker.
  // Enforce the same default when their nested shell loads this widget too.
  readonly property bool preventIdle: nested && Quickshell.env("OMADEV_IDLE") !== "1"
  property bool idlePreventionReady: false

  Timer {
    interval: 1000
    triggeredOnStart: true
    repeat: true
    running: root.preventIdle && !root.idlePreventionReady
    onTriggered: if (!disableIdleProc.running) disableIdleProc.running = true
  }

  Process {
    id: disableIdleProc
    command: ["omarchy-shell", "idle", "disable"]
    stdout: StdioCollector {
      onStreamFinished: {
        // The idle service can become ready after the bar. Retry until it
        // explicitly acknowledges disabling, rather than relying on exit code.
        if (String(text).trim() === "disabled") root.idlePreventionReady = true
      }
    }
  }

  onOpenedChanged: {
    pendingStop = ""
    pendingFocus = ""
    focusAfterSelection.stop()
    initialSelectionPending = opened && !nested
    hostSnapshotReady = false
    hostWindows = []
    hostMonitors = []
    if (opened) {
      refresh()
      if (!nested && !windowsProc.running) windowsProc.running = true
      selectedIndex = availableIndex(0)
      cursorActive = true
      heldFocus = false
    }
  }

  IpcHandler {
    target: "dry.omadev"

    function open(): void { root.open() }
    function close(): void { root.close() }
    function show(): void { root.open() }
    function hide(): void { root.close() }
    function toggle(): void { if (root.nested) { if (root.captured) root.releaseKeys() } else root.toggle() }
    function state(): string { return status() }

    function status(): string {
      return JSON.stringify({
        nested: root.nested,
        slot: root.slot,
        hostSignature: root.hostSignature,
        hostSubmap: root.hostSubmap,
        ownPid: root.ownPid,
        hostActivePid: root.hostActivePid,
        passthrough: root.passthrough,
        focused: root.focused,
        captured: root.captured,
        heldModifiers: root.heldModifiers,
        heldModifierCodes: root.heldModifierCodes,
        modifierStateKnown: root.modifierStateKnown,
        socketUp: root.socketUp,
        opened: root.opened,
        screenWidth: root.screenWidth,
        labelTier: root.labelTier,
        selectedIndex: root.selectedIndex,
        cursorActive: root.cursorActive,
        runningSlots: root.runningSlots(),
        sessions: root.sessions
      })
    }

    // No capture/focus over IPC: taking the user's keyboard is for the user's
    // own binding and clicks, never for scripts or agents. Release stays.
    function release(): void { root.releaseKeys() }
    function stop(n: string): void { root.stopSlot(n) }
    function start(): void { root.startSession() }
    function workspace(): void { root.gotoWorkspace() }
    function refresh(): void { root.broadcastRefresh() }
  }

  // Run refresh() on every live instance of this widget (one bar per monitor).
  function broadcastRefresh() {
    var items = bar && typeof bar.moduleWidgets === "function" ? bar.moduleWidgets(moduleName) : [root]
    for (var i = 0; i < items.length; i++)
      if (items[i] && typeof items[i].refresh === "function") items[i].refresh()
  }

  Process {
    id: queryProc
    command: ["hyprctl", "-i", root.hostSignature, "submap"]
    stdout: StdioCollector {
      onStreamFinished: root.applySubmapName(text)
    }
  }

  Process {
    id: pidProc
    command: ["head", "-n1", root.runtimeDir + "/hypr/" + root.ownSignature + "/hyprland.lock"]
    stdout: StdioCollector {
      onStreamFinished: root.ownPid = String(text || "").trim()
    }
  }

  Process {
    id: activeProc
    command: ["hyprctl", "-i", root.hostSignature, "activewindow", "-j"]
    stdout: StdioCollector {
      onStreamFinished: {
        try {
          var info = JSON.parse(text)
          root.hostActivePid = info && info.pid !== undefined ? String(info.pid) : ""
        } catch (e) {
          root.hostActivePid = ""
        }
      }
    }
  }

  Process {
    id: listProc
    command: ["omadev", "list", "--json"]
    stdout: StdioCollector {
      waitForEnd: true
      onStreamFinished: root.applyList(text)
    }
  }

  Socket {
    id: events
    path: root.runtimeDir + "/hypr/" + root.hostSignature + "/.socket2.sock"
    connected: root.nested
    onConnectionStateChanged: {
      root.socketUp = connected
      if (connected) root.refresh()
    }
    parser: SplitParser {
      onRead: function(line) {
        if (line.indexOf("submap>>") === 0) root.applySubmapName(line.substring(8))
        else if (line.indexOf("activewindow>>") === 0 || line.indexOf("activewindowv2>>") === 0) root.refreshActive()
      }
    }
  }

  // Read-only snapshots from the NEST's key state, not the host's keyboard.
  // A custom event transports only modifier names back to this widget; it
  // doesn't dispatch keys, take focus, change submaps or alter key state.
  Socket {
    id: modifierEvents
    path: root.runtimeDir + "/hypr/" + root.ownSignature + "/.socket2.sock"
    connected: root.nested
    onConnectionStateChanged: if (!connected) root.modifierStateKnown = false
    parser: SplitParser {
      onRead: function(line) {
        var prefix = "custom>>omadev-modifiers:"
        if (line.indexOf(prefix) === 0) root.applyModifierState(line.substring(prefix.length))
      }
    }
  }

  Process {
    id: modifierProbe
    command: ["hyprctl", "eval",
      'local held = {}; local groups = {{"Super", "Super_L", "Super_R"}, {"Ctrl", "Control_L", "Control_R"}, {"Alt", "Alt_L", "Alt_R"}, {"Shift", "Shift_L", "Shift_R"}, {"AltGr", "ISO_Level3_Shift"}}; '
      + 'for _, group in ipairs(groups) do for i = 2, #group do if hl.is_key_down(group[i]) then held[#held + 1] = group[1]; break end end end; '
      + 'local codes, seen = {}, {}; local byName = {Super = {133,134}, Ctrl = {37,105,66}, Alt = {64,108}, Shift = {50,62}, AltGr = {108}}; '
      + 'for _, name in ipairs(held) do for _, code in ipairs(byName[name]) do if not seen[code] and hl.is_key_down(code) and (code ~= 66 or not hl.is_key_down("Caps_Lock")) then codes[#codes + 1] = code; seen[code] = true end end end; '
      + 'hl.dispatch(hl.dsp.event("omadev-modifiers:" .. table.concat(held, ",") .. "|" .. table.concat(codes, ",")))']
    onExited: function(exitCode) { if (exitCode !== 0) root.modifierStateKnown = false }
  }

  Timer {
    interval: 150
    triggeredOnStart: true
    repeat: true
    running: root.nested && modifierEvents.connected
    onTriggered: {
      if (Date.now() - root.modifiersUpdatedAt > 1000) root.modifierStateKnown = false
      if (!modifierProbe.running) modifierProbe.running = true
    }
  }

  Timer {
    interval: 3000
    repeat: true
    running: root.nested && !modifierEvents.connected
    onTriggered: modifierEvents.connected = true
  }

  // Clear only known-held modifier keys, once when capture begins. Send key-up
  // through the parent so the nest's keyboard bookkeeping also sees release.
  // Recheck parent focus and submap before touching the intended nest.
  Process {
    id: clearModifierProc
    command: ["hyprctl", "-i", root.hostSignature, "eval",
      'local w = hl.get_active_window(); '
      + 'if hl.get_current_submap() == ' + JSON.stringify(root.captureSubmap)
      + ' and w and w.pid == ' + (Number(root.ownPid) || 0) + ' and w.class == "aquamarine" then '
      + 'for _, code in ipairs({' + root.modifierCodesToRelease.join(",") + '}) do '
      + 'hl.dispatch(hl.dsp.send_key_state({mods = "", key = "code:" .. code, state = "up", window = "address:" .. w.address})) '
      + 'end end']
    onExited: if (!modifierProbe.running) modifierProbe.running = true
  }

  // Host face: the host compositor's own events, while the panel is open.
  Socket {
    id: hostEvents
    path: root.runtimeDir + "/hypr/" + root.ownHostSignature + "/.socket2.sock"
    connected: !root.nested && root.opened && root.ownHostSignature !== ""
    parser: SplitParser {
      onRead: function(line) {
        if (line.indexOf("openwindow>>") === 0 && line.indexOf(",aquamarine,") !== -1) root.armRetake()
      }
    }
  }

  // Nest: reconnect if the host socket dropped.
  Timer {
    interval: 3000
    running: root.nested && !events.connected
    repeat: true
    onTriggered: events.connected = true
  }

  // Host: fast poll while the panel is open, slow fallback otherwise in case
  // a launcher's refresh notification was missed.
  Timer {
    interval: root.opened ? 2000 : 10000
    running: true
    repeat: true
    onTriggered: root.refresh()
  }

  Timer {
    id: retakeWindow
    interval: 8000
    repeat: false
    onTriggered: root.retakeFocus = false
  }

  Timer {
    id: refreshSoon
    interval: 1500
    repeat: false
    onTriggered: root.refresh()
  }

  Timer {
    id: focusAfterSelection
    interval: 140
    onTriggered: root.completeNumberFocus()
  }

  Process {
    id: windowsProc
    command: ["sh", "-c", "printf '{\"monitors\":%s,\"windows\":%s}' \"$(hyprctl monitors -j)\" \"$(hyprctl clients -j)\""]
    stdout: StdioCollector {
      onStreamFinished: {
        if (!root.opened) return
        try {
          var snapshot = JSON.parse(text)
          root.hostMonitors = snapshot.monitors
          root.hostSnapshotReady = true
          root.selectInitiallyFocusedWindow(snapshot.windows)
          root.hostWindows = snapshot.windows
        } catch (e) {
          root.hostWindows = []; root.hostMonitors = []
          root.initialSelectionPending = false
        }
      }
    }
  }

  Timer {
    interval: 150
    running: root.opened && !root.nested
    repeat: true
    onTriggered: if (!windowsProc.running) windowsProc.running = true
  }

  // Keep the layer stationary. Animate its contents so arrival, flash and
  // focus share one timeline instead of racing the compositor's layer motion.
  PanelWindow {
    visible: root.selectedScreen !== null
    screen: root.selectedScreen
    color: "transparent"
    exclusionMode: ExclusionMode.Ignore
    WlrLayershell.namespace: "omadev-selection"
    WlrLayershell.layer: WlrLayer.Overlay
    WlrLayershell.keyboardFocus: WlrKeyboardFocus.None
    anchors.top: true
    anchors.bottom: true
    anchors.left: true
    anchors.right: true
    mask: Region {}

    Rectangle {
      id: selectionFrame
      property var previousScreen: null
      readonly property real targetX: root.selectedScreen ? root.selectedWindow.at[0] - root.selectedScreen.x : 0
      readonly property real targetY: root.selectedScreen ? root.selectedWindow.at[1] - root.selectedScreen.y : 0
      readonly property real targetWidth: root.selectedWindow ? root.selectedWindow.size[0] : 0
      readonly property real targetHeight: root.selectedWindow ? root.selectedWindow.size[1] : 0
      color: "transparent"
      border.width: Style.space(4)
      border.color: root.pendingStop !== "" ? (root.bar ? root.bar.urgent : Color.urgent) : root.highlightColor
      radius: Style.space(8)

      Rectangle {
        id: flashFill
        anchors.fill: parent
        color: root.highlightColor
        opacity: 0
        radius: parent.radius
      }
    }

    ParallelAnimation {
      id: selectionMove
      NumberAnimation { target: selectionFrame; property: "x"; to: selectionFrame.targetX; duration: 180; easing.type: Easing.OutCubic }
      NumberAnimation { target: selectionFrame; property: "y"; to: selectionFrame.targetY; duration: 180; easing.type: Easing.OutCubic }
      NumberAnimation { target: selectionFrame; property: "width"; to: selectionFrame.targetWidth; duration: 180; easing.type: Easing.OutCubic }
      NumberAnimation { target: selectionFrame; property: "height"; to: selectionFrame.targetHeight; duration: 180; easing.type: Easing.OutCubic }
      onFinished: {
        if (root.selectedScreen === null) return
        if (root.pendingFocus !== "") root.completeNumberFocus()
        else selectionFlash.restart()
      }
    }

    SequentialAnimation {
      id: selectionFlash
      NumberAnimation { target: flashFill; property: "opacity"; from: 0; to: 0.45; duration: 65 }
      NumberAnimation { target: flashFill; property: "opacity"; to: 0; duration: 230; easing.type: Easing.OutCubic }
      onFinished: root.completeNumberFocus()
    }
  }

  // Number every visible session while choosing one, without intercepting input.
  Variants {
    model: root.opened && !root.nested ? root.sessions : []
    delegate: PanelWindow {
      id: numberBadge
      required property var modelData
      readonly property var client: root.windowForSession(modelData)
      readonly property var clientScreen: root.screenForWindow(client)
      readonly property bool confirmingStop: root.pendingStop === root.sessionIdentity(modelData)
      readonly property color foreground: confirmingStop ? (root.bar ? root.bar.urgent : Color.urgent) : root.highlightColor

      visible: clientScreen !== null
      screen: clientScreen
      color: "transparent"
      exclusionMode: ExclusionMode.Ignore
      WlrLayershell.namespace: "omadev-session-number"
      WlrLayershell.layer: WlrLayer.Overlay
      WlrLayershell.keyboardFocus: WlrKeyboardFocus.None
      anchors.top: true
      anchors.left: true
      margins.left: clientScreen ? Math.max(0, client.at[0] + client.size[0] / 2 - clientScreen.x - implicitWidth / 2) : 0
      margins.top: clientScreen ? Math.max(0, client.at[1] + client.size[1] / 2 - clientScreen.y - implicitHeight / 2) : 0
      implicitWidth: Style.space(88)
      implicitHeight: Style.space(88)
      mask: Region {}

      Rectangle {
        anchors.fill: parent
        color: Color.popups.background
        radius: Style.space(16)
        border.width: Style.space(2)
        border.color: numberBadge.foreground

        Text {
          anchors.centerIn: parent
          text: String(numberBadge.modelData.slot)
          color: numberBadge.foreground
          font.family: root.bar ? root.bar.fontFamily : Style.font.family
          font.pixelSize: Style.space(52)
          font.bold: true
        }
      }
    }
  }

  // Feedback stays inside the nest and never takes keyboard or pointer input.
  property real capturePulseOpacity: 0
  property bool capturePulseStarting: false
  readonly property color capturePulseColor: capturePulseStarting ? Color.urgent : highlightColor

  function pulseCaptureFeedback(starting) {
    capturePulse.stop()
    capturePulseOpacity = 0
    capturePulseStarting = starting
    capturePulse.start()
  }

  SequentialAnimation {
    id: capturePulse
    NumberAnimation { target: root; property: "capturePulseOpacity"; from: 0; to: 1; duration: 85 }
    PauseAnimation { duration: 90 }
    NumberAnimation { target: root; property: "capturePulseOpacity"; to: 0; duration: 420; easing.type: Easing.OutCubic }
  }

  PanelWindow {
    visible: root.nested && capturePulse.running
    screen: nestButton.QsWindow.window ? nestButton.QsWindow.window.screen : null
    color: "transparent"
    exclusionMode: ExclusionMode.Ignore
    WlrLayershell.namespace: "omadev-capture-feedback"
    WlrLayershell.layer: WlrLayer.Overlay
    WlrLayershell.keyboardFocus: WlrKeyboardFocus.None
    anchors.top: true
    anchors.bottom: true
    anchors.left: true
    anchors.right: true
    mask: Region {}

    Rectangle {
      anchors.fill: parent
      color: root.capturePulseColor
      opacity: root.capturePulseOpacity * 0.55
    }
    Rectangle {
      anchors.fill: parent
      color: "transparent"
      border.color: root.capturePulseColor
      border.width: Style.space(10)
      opacity: root.capturePulseOpacity
    }
  }

  // Nest face.
  WidgetButton {
    id: nestButton
    visible: root.nested
    anchors.fill: parent
    bar: root.bar
    active: root.captured
    activeColor: root.bar ? root.bar.urgent : Color.urgent
    fontSize: Style.font.caption
    horizontalMargin: 8
    text: root.nestLabel()
    tooltipText: (root.slot !== "" ? "Session " + root.slot + ": " : "")
      + (root.captured
        ? "the host is passing every key here. Click to release (Super+Alt+D)."
        : (root.passthrough
          ? "the host is passing keys to another session. Click to capture them here."
          : "keys go to the host. Click to capture them here, or select this session in the host panel."))
      + "\nNest " + root.modifierLabel() + ". Read-only key state reported by the nested compositor, not the host. Starting capture clears reported held modifiers with key-up events only."
    onPressed: function() { root.toggleCapture() }
  }

  // Nest face, while capturing: a passive box under the widget saying how to
  // get the keys back. No input of its own (empty mask), no focus.
  PanelWindow {
    id: captureHint
    readonly property bool atBottom: root.bar && root.bar.position === "bottom"
    readonly property real pad: Style.space(10)
    property real rightInset: 0

    function place() {
      var win = nestButton.QsWindow.window
      if (!win) return
      var p = nestButton.mapToItem(null, 0, 0)
      rightInset = Math.max(Style.gapsOut, win.width - (p.x + nestButton.width))
    }

    visible: root.nested && (root.captured || (root.modifierStateKnown && root.heldModifiers.length > 0))
    onVisibleChanged: if (visible) place()
    screen: nestButton.QsWindow.window ? nestButton.QsWindow.window.screen : null
    color: "transparent"
    exclusionMode: ExclusionMode.Ignore
    WlrLayershell.namespace: "omadev-capture-hint"
    WlrLayershell.layer: WlrLayer.Overlay
    WlrLayershell.keyboardFocus: WlrKeyboardFocus.None
    anchors.top: !atBottom
    anchors.bottom: atBottom
    anchors.right: true
    readonly property real barSize: root.bar && root.bar.barSize ? root.bar.barSize : Style.bar.sizeHorizontal
    margins.top: atBottom ? 0 : barSize + Style.gapsOut
    margins.bottom: atBottom ? barSize + Style.gapsOut : 0
    margins.right: rightInset
    implicitWidth: Math.max(hintText.implicitWidth, modifierRow.implicitWidth) + pad * 2
    implicitHeight: hintColumn.implicitHeight
    mask: Region {}

    Timer {
      interval: 250
      triggeredOnStart: true
      repeat: true
      running: captureHint.visible
      onTriggered: captureHint.place()
    }

    Column {
      id: hintColumn
      width: parent.width
      spacing: Style.space(8)

      Rectangle {
        visible: root.captured
        width: parent.width
        height: hintText.implicitHeight + captureHint.pad * 2
        color: Color.popups.background
        border.color: root.bar ? root.bar.urgent : Color.urgent
        border.width: Math.max(1, Style.space(2))
        radius: Style.space(6)

        Text {
          id: hintText
          anchors.centerIn: parent
          textFormat: Text.StyledText
          text: "Press <b>Super+Alt+D</b> to release input from this session"
          color: Color.popups.text
          font.family: root.bar ? root.bar.fontFamily : Style.font.family
          font.pixelSize: Style.font.body
        }
      }

      Rectangle {
        width: parent.width
        height: modifierContent.implicitHeight + captureHint.pad * 2
        color: Color.popups.background
        border.color: root.highlightColor
        border.width: Math.max(1, Style.space(2))
        radius: Style.space(6)

        Column {
          id: modifierContent
          anchors.centerIn: parent
          spacing: Style.space(8)

          Text {
            text: !root.modifierStateKnown ? "MODIFIER STATE UNAVAILABLE"
              : (root.heldModifiers.length ? "HELD MODIFIERS" : "HELD MODIFIERS · NONE")
            color: Color.popups.text
            font.family: root.bar ? root.bar.fontFamily : Style.font.family
            font.pixelSize: Style.font.caption
            font.bold: true
          }

          Row {
            id: modifierRow
            spacing: Style.space(6)
            Repeater {
              model: ["Super", "Ctrl", "Alt", "Shift", "AltGr"]
              delegate: Rectangle {
                required property string modelData
                readonly property bool held: root.modifierStateKnown && root.heldModifiers.indexOf(modelData) !== -1
                width: modifierText.implicitWidth + Style.space(20)
                height: Style.space(38)
                radius: Style.space(4)
                color: held ? root.highlightColor : Util.alpha(Color.popups.text, 0.08)
                border.color: held ? root.highlightColor : Util.alpha(Color.popups.text, 0.25)
                border.width: 1

                Text {
                  id: modifierText
                  anchors.centerIn: parent
                  text: modelData.toUpperCase()
                  color: parent.held ? Color.background : Util.alpha(Color.popups.text, 0.45)
                  font.family: root.bar ? root.bar.fontFamily : Style.font.family
                  font.pixelSize: Style.font.body
                  font.bold: true
                }
              }
            }
          }
        }
      }
    }
  }

  // Host face.
  BarIconButton {
    id: button
    visible: !root.nested
    anchors.fill: parent
    bar: root.bar
    text: "󰌌"
    opacity: root.sessions.length > 0 ? 1.0 : 0.5
    onPressed: function(b) { root.toggle() }
  }

  KeyboardPanel {
    id: panel
    anchorItem: button
    owner: root
    bar: root.bar
    open: root.opened && !root.nested
    focusTarget: keyCatcher
    contentWidth: panel.fittedContentWidth(Style.space(400))
    contentHeight: panel.fittedContentHeight(panelColumn.implicitHeight, Style.space(640))

    PanelKeyCatcher {
      id: keyCatcher
      anchors.fill: parent
      onMoveRequested: function(dx, dy) {
        root.cursorActive = true
        if (dy !== 0) root.moveCursor(dy)
      }
      onActivateRequested: if (root.cursorActive) root.activateCursor()
      onCloseRequested: root.close()
      onTabRequested: function(direction) { root.switchPanel(direction) }
      onDeleteRequested: root.stopSelected()
      Window.onActiveChanged: {
        if (!root.opened) return
        if (keyCatcher.Window.active) { root.heldFocus = true; return }
        if (root.retakeFocus) root.regainFocus()
        else if (root.heldFocus) root.close()
      }
      onTextKey: function(t) {
        if (/^[1-9]$/.test(t)) root.focusNumber(t)
        else if (t === "n" || t === "N") root.startSession()
        else if (t === "d" || t === "D") root.stopSelected()
        else if (t === "w" || t === "W") root.gotoWorkspace()
      }

      ScrollView {
        id: scrollArea
        anchors.fill: parent
        clip: true
        ScrollBar.horizontal.policy: ScrollBar.AlwaysOff
        ScrollBar.vertical.policy: panelColumn.implicitHeight > height ? ScrollBar.AsNeeded : ScrollBar.AlwaysOff
        Binding {
          target: scrollArea.contentItem
          property: "interactive"
          value: panelColumn.implicitHeight > scrollArea.height
        }

        Column {
          id: panelColumn
          width: scrollArea.availableWidth
          spacing: Style.space(12)

          PanelHero {
            width: parent.width
            foreground: root.bar.foreground
            fontFamily: root.bar.fontFamily
            title: "Omadev"
            meta: !root.loaded ? "LOADING…"
              : (root.sessions.length === 0 ? "NO SESSION RUNNING"
                : root.sessions.length + (root.sessions.length === 1 ? " SESSION" : " SESSIONS") + " RUNNING")
            iconComponent: Component {
              Text {
                textFormat: Text.PlainText
                text: "󰌌"
                color: root.bar.foreground
                font.family: root.bar.fontFamily
                font.pixelSize: Style.font.display
              }
            }
            trailingControl: Component {
              PanelActionButton {
                iconText: "󰐕"
                tooltipText: "Start a new session in the first free slot (n)"
                foreground: root.bar.foreground
                fontFamily: root.bar.fontFamily
                onClicked: root.startSession()
              }
            }
          }

          PanelSeparator { foreground: root.bar.foreground }

          Text {
            visible: root.loaded && root.sessions.length === 0
            width: parent.width
            textFormat: Text.StyledText
            wrapMode: Text.WordWrap
            text: "Press <b>n</b> to create new session"
            color: Qt.darker(root.bar.foreground, 1.4)
            font.family: root.bar.fontFamily
            font.pixelSize: Style.font.body
            leftPadding: Style.space(8)
            rightPadding: Style.space(8)
          }

          Repeater {
            model: root.sessions
            delegate: SessionRow {
              required property var modelData
              required property int index
              session: modelData
              rowIndex: index
              width: panelColumn.width
            }
          }

          Text {
            visible: root.sessions.length > 0
            width: parent.width
            textFormat: Text.StyledText
            wrapMode: Text.WordWrap
            readonly property string keyColor: String(root.bar.foreground)
            function key(k) { return "<b><font color=\"" + keyColor + "\">" + k + "</font></b>" }
            text: key("n") + " new&nbsp;&nbsp;·&nbsp;&nbsp;" + key("d") + " stop&nbsp;&nbsp;·&nbsp;&nbsp;" + key("w") + " workspace&nbsp;&nbsp;·&nbsp;&nbsp;"
              + key("1–9 / ⏎") + " focus&nbsp;&nbsp;·&nbsp;&nbsp;" + key("esc") + " close"
            color: Qt.darker(root.bar.foreground, 1.4)
            font.family: root.bar.fontFamily
            font.pixelSize: Style.font.caption
            leftPadding: Style.space(8)
            rightPadding: Style.space(8)
          }
        }
      }
    }
  }

  component SessionRow: CursorSurface {
    id: row
    property var session: ({})
    property int rowIndex: 0
    readonly property string slotText: String(session.slot)
    readonly property bool stopping: session.state === "stopping" || session.state === "starting"
    readonly property bool confirmingStop: root.pendingStop === root.sessionIdentity(session)
    readonly property color rowColor: confirmingStop ? (root.bar ? root.bar.urgent : Color.urgent) : root.bar.foreground

    hasCursor: root.cursorActive && root.selectedIndex === rowIndex
    foreground: rowColor
    implicitHeight: rowContent.implicitHeight + Style.space(12)

    MouseArea {
      anchors.fill: parent
      hoverEnabled: true
      onEntered: {
        if (row.session.state !== "running") return
        root.initialSelectionPending = false
        root.cursorActive = true
        root.selectedIndex = row.rowIndex
      }
      onClicked: if (!row.stopping) root.focusSlot(row.slotText)
    }

    RowLayout {
      id: rowContent
      anchors.left: parent.left
      anchors.right: parent.right
      anchors.verticalCenter: parent.verticalCenter
      anchors.leftMargin: Style.space(8)
      anchors.rightMargin: Style.space(8)
      spacing: Style.space(10)

      Text {
        textFormat: Text.PlainText
        text: row.slotText
        color: row.rowColor
        opacity: row.stopping ? 0.5 : 1.0
        font.family: root.bar.fontFamily
        font.pixelSize: Style.font.title
        font.bold: true
        Layout.preferredWidth: Style.space(22)
        horizontalAlignment: Text.AlignHCenter
      }

      Column {
        Layout.fillWidth: true
        spacing: Style.space(2)

        Text {
          width: parent.width
          textFormat: Text.PlainText
          text: "Session " + row.slotText + (row.session.display ? "  ·  " + row.session.display : "")
          color: row.rowColor
          opacity: row.stopping ? 0.5 : 1.0
          font.family: root.bar.fontFamily
          font.pixelSize: Style.font.body
          elide: Text.ElideRight
        }

        Text {
          width: parent.width
          textFormat: Text.PlainText
          text: row.confirmingStop ? "Press d again to confirm stop" : root.sessionMeta(row.session)
          color: row.confirmingStop ? row.rowColor : Qt.darker(root.bar.foreground, 1.4)
          font.family: root.bar.fontFamily
          font.pixelSize: Style.font.caption
          elide: Text.ElideRight
        }
      }

      PanelActionButton {
        iconText: "󰌌"
        tooltipText: "Focus session " + row.slotText + " and capture keys for it (Enter)"
        enabled: !row.stopping
        foreground: root.bar.foreground
        fontFamily: root.bar.fontFamily
        onClicked: root.focusSlot(row.slotText)
      }

      PanelActionButton {
        iconText: "󰅖"
        tooltipText: row.session.state === "starting" ? "Starting…" : (row.stopping ? "Stopping…" : "Stop session " + row.slotText + " (d)")
        enabled: !row.stopping
        foreground: root.bar.foreground
        hoverColor: root.bar ? root.bar.urgent : Color.urgent
        fontFamily: root.bar.fontFamily
        onClicked: root.stopSlot(row.slotText)
      }
    }
  }
}
