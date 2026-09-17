import QtQuick
import Quickshell
import Quickshell.Io
ShellRoot {
  id: root
  property var events: []
  FloatingWindow {
    visible: true
    title: "Omadev keyboard gate probe"
    implicitWidth: 620
    implicitHeight: 260
    color: "#202030"
    Item {
      anchors.fill: parent
      focus: true
      Keys.onPressed: event => {
        root.events = root.events.concat([{kind:"down", key:event.key, text:event.text, modifiers:event.modifiers, repeat:event.isAutoRepeat}])
        event.accepted = true
      }
      Keys.onReleased: event => {
        root.events = root.events.concat([{kind:"up", key:event.key, text:event.text, modifiers:event.modifiers, repeat:event.isAutoRepeat}])
        event.accepted = true
      }
      Text {
        anchors.fill: parent
        anchors.margins: 20
        color: "white"
        font.pixelSize: 16
        wrapMode: Text.Wrap
        text: "Host-backed keyboard gate test\nReceived events: " + root.events.length + "\n" + JSON.stringify(root.events.slice(-8))
      }
    }
  }
  IpcHandler {
    target: "inputproof"
    function status(): string { return JSON.stringify(root.events) }
    function clear(): void { root.events = [] }
  }
}
