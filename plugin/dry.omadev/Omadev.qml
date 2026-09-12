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
//    session and captures keys for it (the same as Super+Shift+Alt+D) or stops it; the
//    hero button starts a new session in the first free slot.
//
//  * Inside a session it shows whether the host is passing keys here, and a
//    click releases or captures them, the same as Super+Shift+Alt+D.
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

  function nestLabel() {
    var head = "󰌌 " + (slot !== "" ? slot : "")
    if (labelTier === "icon") return head
    if (labelTier === "compact") return head + "  " + (captured ? "CAPTURING" : "capture")
    return head + "  " + (captured ? "CAPTURING KEYS" : "click to capture input")
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
  readonly property bool focused: ownPid !== "" && hostActivePid === ownPid
  readonly property bool captured: passthrough && focused

  // Host face.
  property var sessions: []          // running entries of `omadev list --json`
  property bool loaded: false
  property int selectedIndex: -1
  property bool cursorActive: false
  // Close when keyboard focus moves elsewhere (workspace switch, another
  // window). The panel window is not active for the first instants after
  // opening, so only a loss after it held focus counts.
  property bool heldFocus: false
  // Any session window mapping on the host makes Hyprland re-evaluate focus
  // and the panel loses it, whether the panel started that session or an
  // agent did from a terminal. While this is set the panel takes focus back
  // instead of closing; it is armed by n and by openwindow events for nest
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

  function setSubmap(name) {
    if (!root.bar || !nested) return
    root.bar.run(hostctl("dispatch " + Util.shellQuote("hl.dsp.submap(\"" + name + "\")")))
  }

  function toggleCapture() {
    if (captured) setSubmap("reset")
    else if (slot !== "" && root.bar) root.bar.run("env OMADEV_ALLOW_CAPTURE=1 omadev focus " + slot)
    else setSubmap(captureSubmap)
  }

  function focusSlot(n) {
    if (root.bar) root.bar.run("env OMADEV_ALLOW_CAPTURE=1 omadev focus " + n)
    close()
  }

  function stopSlot(n) {
    if (root.bar) root.bar.run("omadev stop " + n)
    refreshSoon.restart()
  }

  function gotoWorkspace() {
    if (root.bar && !nested) root.bar.run("hyprctl dispatch " + Util.shellQuote('hl.dsp.focus({ workspace = "name:omadev" })'))
    close()
  }

  function startSession() {
    if (root.bar && !nested) root.bar.run("omadev start --detach")
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
    var list = []
    try {
      var all = JSON.parse(raw)
      for (var i = 0; i < all.length; i++)
        if (all[i].state !== "free") list.push(all[i])
    } catch (e) {
      list = []
    }
    now = Date.now() / 1000
    loaded = true
    if (list.length > sessions.length) retakeFocus = false  // the started session is up
    if (JSON.stringify(list) !== JSON.stringify(sessions)) sessions = list
    if (selectedIndex >= sessions.length) selectedIndex = sessions.length - 1
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
    var parts = [shortPath(s.omarchy_path)]
    if (s.owner) parts.push(s.owner)
    var plugins = s.plugins ? Object.keys(s.plugins) : []
    if (plugins.length) parts.push(plugins.join(", "))
    parts.push(s.state === "stopping" ? "stopping" : uptime(s))
    return parts.join(" · ")
  }

  function moveCursor(delta) {
    if (sessions.length === 0) return
    var next = selectedIndex + delta
    if (next < 0) next = 0
    if (next > sessions.length - 1) next = sessions.length - 1
    selectedIndex = next
  }

  function activateCursor() {
    if (selectedIndex >= 0 && selectedIndex < sessions.length) focusSlot(sessions[selectedIndex].slot)
  }

  function stopSelected() {
    if (selectedIndex < 0 || selectedIndex >= sessions.length) return
    var s = sessions[selectedIndex]
    if (s.state !== "stopping") stopSlot(s.slot)
  }

  implicitWidth: nested ? nestButton.implicitWidth : button.implicitWidth
  implicitHeight: nested ? nestButton.implicitHeight : button.implicitHeight

  Component.onCompleted: refresh()

  onOpenedChanged: {
    if (opened) {
      refresh()
      selectedIndex = sessions.length > 0 ? 0 : -1
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
    function toggle(): void { if (root.nested) { if (root.captured) root.setSubmap("reset") } else root.toggle() }
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
    function release(): void { root.setSubmap("reset") }
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
        ? "the host is passing every key here. Click to release (Super+Shift+Alt+D)."
        : (root.passthrough
          ? "the host is passing keys to another session. Click to capture them here."
          : "keys go to the host. Click to capture them here (Super+Shift+Alt+D with this window focused)."))
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

    visible: root.nested && root.captured
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
    implicitWidth: hintText.implicitWidth + pad * 2
    implicitHeight: hintText.implicitHeight + pad * 2
    mask: Region {}

    Rectangle {
      anchors.fill: parent
      color: Color.popups.background
      border.color: root.bar ? root.bar.urgent : Color.urgent
      border.width: Math.max(1, Style.space(2))
      radius: Style.space(6)
    }

    Text {
      id: hintText
      anchors.centerIn: parent
      textFormat: Text.StyledText
      text: "Press <b>Super+Shift+Alt+D</b> to release input from this session"
      color: Color.popups.text
      font.family: root.bar ? root.bar.fontFamily : Style.font.family
      font.pixelSize: Style.font.body
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
        if (t === "n" || t === "N") root.startSession()
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
              + key("⏎") + " focus&nbsp;&nbsp;·&nbsp;&nbsp;" + key("esc") + " close"
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
    readonly property bool stopping: session.state === "stopping"

    hasCursor: root.cursorActive && root.selectedIndex === rowIndex
    foreground: root.bar.foreground
    implicitHeight: rowContent.implicitHeight + Style.space(12)

    MouseArea {
      anchors.fill: parent
      hoverEnabled: true
      onEntered: { root.cursorActive = true; root.selectedIndex = row.rowIndex }
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
        color: root.bar.foreground
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
          text: "Session " + row.slotText + "  ·  " + (row.session.display || "")
          color: root.bar.foreground
          opacity: row.stopping ? 0.5 : 1.0
          font.family: root.bar.fontFamily
          font.pixelSize: Style.font.body
          elide: Text.ElideRight
        }

        Text {
          width: parent.width
          textFormat: Text.PlainText
          text: root.sessionMeta(row.session)
          color: Qt.darker(root.bar.foreground, 1.4)
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
        tooltipText: row.stopping ? "Stopping…" : "Stop session " + row.slotText + " (d)"
        enabled: !row.stopping
        foreground: root.bar.foreground
        hoverColor: root.bar ? root.bar.urgent : Color.urgent
        fontFamily: root.bar.fontFamily
        onClicked: root.stopSlot(row.slotText)
      }
    }
  }
}
