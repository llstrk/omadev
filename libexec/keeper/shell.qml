// Per-nest helper, launched by the nested Hyprland on start.
//
// 1. Holds a Wayland idle inhibitor so the nested shell never starts its
//    screensaver/lock cycle while the window sits unfocused (disabling idle
//    through the shell would write the shared stay-awake file and switch the
//    host's idle off too). OMADEV_IDLE=1 turns this off.
// 2. Makes the nest follow its host window. The Wayland backend resizes the
//    output when the host window is resized, but Hyprland neither re-arranges
//    layers and windows nor tells its clients (wl_output keeps the old mode)
//    until the monitor rule is re-applied. Only `hyprctl monitors` shows the
//    new size, so poll it and re-apply the rule on a mismatch with what this
//    client sees.
import QtQuick
import Quickshell
import Quickshell.Io
import Quickshell.Wayland

ShellRoot {
  id: root

  readonly property var screen: Quickshell.screens.length > 0 ? Quickshell.screens[0] : null
  readonly property string size: screen ? screen.width + "x" + screen.height : ""
  readonly property bool followWindow: true
  readonly property string scale: Quickshell.env("OMADEV_SCALE") || "1"

  onSizeChanged: if (followWindow && size !== "") rearm.restart()

  property int rearranged: 0

  IpcHandler {
    target: "keeper"
    function status(): string {
      return JSON.stringify({ size: root.size, follow: root.followWindow, scale: root.scale, rearranged: root.rearranged,
                              screens: Quickshell.screens.map(s => s.name + ":" + s.width + "x" + s.height) })
    }
    function rearrange(): void { rearm.restart() }
  }

  Timer {
    interval: 500
    repeat: true
    running: root.followWindow
    onTriggered: if (!probe.running) probe.running = true
  }

  Process {
    id: probe
    command: ["hyprctl", "monitors", "-j"]
    stdout: StdioCollector {
      onStreamFinished: {
        try {
          var m = JSON.parse(text)[0]
          if (m && (m.width + "x" + m.height) !== root.size) rearm.restart()
        } catch (e) {}
      }
    }
  }

  Timer {
    id: rearm
    interval: 150
    onTriggered: if (!rearrange.running) rearrange.running = true
  }

  Process {
    id: rearrange
    command: ["hyprctl", "eval",
      'hl.monitor({ output = "WAYLAND-1", mode = "preferred", position = "0x0", scale = ' + root.scale + ' })']
    onExited: root.rearranged += 1
  }

  PanelWindow {
    id: keeper
    anchors { left: true; bottom: true }
    implicitWidth: 1
    implicitHeight: 1
    exclusiveZone: 0
    color: "transparent"
    mask: Region {}
    WlrLayershell.layer: WlrLayer.Bottom
    WlrLayershell.namespace: "omadev-keeper"

    IdleInhibitor {
      window: keeper
      enabled: Quickshell.env("OMADEV_IDLE") !== "1"
    }
  }
}
