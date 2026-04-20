import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

ApplicationWindow {
    id: root
    title: "Muninn"
    width: 960
    height: 640
    minimumWidth: 600
    minimumHeight: 400
    visible: true
    color: Theme.bg

    // ------------------------------------------------------------------
    // Layout
    // ------------------------------------------------------------------

    RowLayout {
        id: mainLayout
        anchors {
            top: parent.top
            left: parent.left
            right: parent.right
            bottom: statusBar.top
        }
        spacing: 0

        // Left rail: peer list
        PeerList {
            id: peerList
            Layout.preferredWidth: 220
            Layout.fillHeight: true

            onConvSelected: function(convId) {
                bridge.setActiveConv(convId)
                chatPane.opacity = 1
            }
        }

        // Divider
        Rectangle {
            Layout.preferredWidth: 1
            Layout.fillHeight: true
            color: Theme.surfaceRaised
        }

        // Right pane: chat
        ChatView {
            id: chatPane
            Layout.fillWidth: true
            Layout.fillHeight: true

            Behavior on opacity {
                NumberAnimation { duration: 80; easing.type: Easing.OutQuad }
            }
        }
    }

    // ------------------------------------------------------------------
    // Status bar
    // ------------------------------------------------------------------

    Rectangle {
        id: statusBar
        anchors.bottom: parent.bottom
        anchors.left: parent.left
        anchors.right: parent.right
        height: 24
        color: Theme.surfaceRaised

        Row {
            anchors.fill: parent
            anchors.leftMargin: 12
            anchors.rightMargin: 12
            spacing: 20

            Text {
                anchors.verticalCenter: parent.verticalCenter
                text: bridge.localName ? "nick: " + bridge.localName : "nick: (none)"
                color: Theme.textMuted
                font.pixelSize: 11
            }
            Text {
                anchors.verticalCenter: parent.verticalCenter
                text: "peers: " + bridge.connectedPeerCount + " connected"
                color: Theme.textMuted
                font.pixelSize: 11
            }
            Text {
                anchors.verticalCenter: parent.verticalCenter
                text: "mode: " + (bridge.isWriter ? "WRITER" : "READER")
                color: bridge.isWriter ? Theme.success : Theme.textMuted
                font.pixelSize: 11
                font.bold: bridge.isWriter
            }
        }
    }

    // ------------------------------------------------------------------
    // Overlays (scan dialog, command palette)
    // ------------------------------------------------------------------

    ScanDialog {
        id: scanDialog
        anchors.fill: parent
        z: 10
    }

    CommandPalette {
        id: cmdPalette
        anchors.fill: parent
        z: 10
        onConvSelected: function(convId) {
            bridge.setActiveConv(convId)
            chatPane.opacity = 1
        }
    }

    // Error toast
    Rectangle {
        id: errorToast
        visible: false
        anchors.bottom: statusBar.top
        anchors.horizontalCenter: parent.horizontalCenter
        anchors.bottomMargin: 8
        width: errorLabel.implicitWidth + 24
        height: 32; radius: 6
        color: Theme.error
        z: 20

        Text {
            id: errorLabel
            anchors.centerIn: parent
            color: "white"
            font.pixelSize: 12
        }

        Timer {
            id: toastTimer
            interval: 3000
            onTriggered: errorToast.visible = false
        }
    }

    Connections {
        target: bridge
        function onErrorOccurred(msg) {
            errorLabel.text = msg
            errorToast.visible = true
            toastTimer.restart()
        }
    }

    // ------------------------------------------------------------------
    // Global key handling
    // ------------------------------------------------------------------

    // Focus main window for global keys when overlays are hidden
    focus: !scanDialog.visible && !cmdPalette.visible

    Keys.onPressed: function(event) {
        // Ctrl-P → command palette
        if ((event.modifiers & Qt.ControlModifier) && event.key === Qt.Key_P) {
            cmdPalette.open()
            event.accepted = true
            return
        }
        // gs → scan dialog (when composer not in INSERT/CMDLINE)
        if (event.key === Qt.Key_G && !event.modifiers) {
            pendingG = true
            event.accepted = true
            return
        }
        if (pendingG) {
            pendingG = false
            if (event.key === Qt.Key_S) {
                scanDialog.open()
                event.accepted = true
                return
            }
            if (event.key === Qt.Key_N) {
                cmdPalette.open()
                event.accepted = true
                return
            }
        }
        // j / k → peer list nav
        if (event.key === Qt.Key_J && !event.modifiers) {
            peerList.listView.selectNext()
            event.accepted = true; return
        }
        if (event.key === Qt.Key_K && !event.modifiers) {
            peerList.listView.selectPrev()
            event.accepted = true; return
        }
        // Ctrl-h / Ctrl-l → focus peer list / chat
        if ((event.modifiers & Qt.ControlModifier) && event.key === Qt.Key_H) {
            peerList.forceActiveFocus()
            event.accepted = true; return
        }
        if ((event.modifiers & Qt.ControlModifier) && event.key === Qt.Key_L) {
            chatPane.forceActiveFocus()
            event.accepted = true; return
        }
        // Ctrl-n / Ctrl-Shift-N → next/prev unread (no-op scaffold)
        // i → focus composer in insert
        if (event.key === Qt.Key_I && !event.modifiers) {
            chatPane.forceActiveFocus()
            vimEditor.handleKey("i", Qt.Key_I, false, false, false)
            event.accepted = true; return
        }
        // Esc → focus peer list
        if (event.key === Qt.Key_Escape) {
            peerList.forceActiveFocus()
            event.accepted = true; return
        }
        // ZZ quit
        if (event.key === Qt.Key_Z && !event.modifiers) {
            if (pendingZ) { Qt.quit(); return }
            pendingZ = true
            zTimer.restart()
            event.accepted = true; return
        }
    }

    property bool pendingG: false
    property bool pendingZ: false

    Timer { id: zTimer; interval: 1000; onTriggered: root.pendingZ = false }

    Connections {
        target: vimEditor
        function onQuitRequested() { Qt.quit() }
    }

    // Auto-select first conv when peers appear
    Connections {
        target: bridge
        function onActiveConvChanged(convId) {
            chatPane.opacity = 1
        }
    }
}
