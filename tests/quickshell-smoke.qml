import QtQuick
import Quickshell

ShellRoot {
    FloatingWindow {
        visible: true
        implicitWidth: 480
        implicitHeight: 160
        color: "#173042"
        Text {
            anchors.centerIn: parent
            color: "white"
            text: "uConsole Quickshell runtime check"
        }
    }
    Timer {
        interval: 1500
        running: true
        onTriggered: {
            console.log("UCONSOLE_RUNTIME_OK")
            Qt.quit()
        }
    }
}
