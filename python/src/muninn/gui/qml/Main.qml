import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

ApplicationWindow {
    id: root
    title: "Muninn"
    width: 960
    height: 640
    minimumWidth: 800
    minimumHeight: 500
    visible: true
    color: "#0f1115"

    // Helper to check if we are in navigation mode
    property bool isNormalMode: vimEditor && vimEditor.mode === "NORMAL"

    // Initial focus on the chat pane so Vim commands work immediately
    Component.onCompleted: {
        chatPane.forceActiveFocus()
    }

    // ------------------------------------------------------------------
    // Main Layout
    // ------------------------------------------------------------------

    ColumnLayout {
        anchors.fill: parent
        spacing: 0

        RowLayout {
            id: mainLayout
            Layout.fillWidth: true
            Layout.fillHeight: true
            spacing: 0

            PeerList {
                id: peerList
                Layout.preferredWidth: 240
                Layout.fillHeight: true
                onConvSelected: (cid) => {
                    bridge.setActiveConv(cid)
                    chatPane.forceActiveFocus()
                }
            }

            Rectangle {
                Layout.preferredWidth: 1
                Layout.fillHeight: true
                color: Theme.surfaceRaised
            }

            ChatView {
                id: chatPane
                Layout.fillWidth: true
                Layout.fillHeight: true
                
                // Clicking the chat area always restores Vim command focus
                MouseArea {
                    anchors.fill: parent
                    onClicked: chatPane.forceActiveFocus()
                    z: -1 // Behind messages
                }
            }
        }

        Rectangle {
            id: statusBar
            Layout.fillWidth: true
            Layout.preferredHeight: 24
            color: Theme.surfaceRaised
            z: 5

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
    }

    // ------------------------------------------------------------------
    // Global Shortcuts
    // ------------------------------------------------------------------

    // Overlays handle their own Esc — gate global Esc so palette/scan/info
    // can close themselves first.
    property bool overlayOpen:
        cmdPalette.visible || scanDialog.visible || infoMenu.visible

    Shortcut {
        sequence: "Esc"
        enabled: !root.overlayOpen
        onActivated: {
            vimEditor.handleKey("", Qt.Key_Escape, false, false, false)
            chatPane.forceActiveFocus()
        }
    }

    Shortcut {
        sequence: "Ctrl+H"
        enabled: !root.overlayOpen
        onActivated: peerList.forceActiveFocus()
    }
    Shortcut {
        sequence: "Ctrl+L"
        enabled: !root.overlayOpen
        onActivated: chatPane.forceActiveFocus()
    }
    // Cycle conversations regardless of focus. Disabled while the palette is
    // open so it can use Ctrl-N/Ctrl-P to navigate its own list.
    Shortcut {
        sequence: "Ctrl+N"
        context: Qt.ApplicationShortcut
        enabled: !root.overlayOpen
        onActivated: bridge.cycleConv(1)
    }
    Shortcut {
        sequence: "Ctrl+P"
        context: Qt.ApplicationShortcut
        enabled: !root.overlayOpen
        onActivated: bridge.cycleConv(-1)
    }

    // ------------------------------------------------------------------
    // Overlays
    // ------------------------------------------------------------------

    ScanDialog { id: scanDialog; anchors.fill: parent; z: 10 }

    CommandPalette {
        id: cmdPalette
        anchors.fill: parent
        z: 10
        onConvSelected: (convId) => {
            bridge.setActiveConv(convId)
            chatPane.forceActiveFocus()
        }
    }

    InfoMenu {
        id: infoMenu
        anchors.fill: parent
        z: 11
        onConvSelected: (convId) => {
            bridge.setActiveConv(convId)
            chatPane.forceActiveFocus()
        }
    }

    // Toast (errors red, info neutral). Fades in/out for smoothness.
    Rectangle {
        id: toast
        property bool isError: true
        visible: opacity > 0
        opacity: 0
        anchors.bottom: parent.bottom; anchors.bottomMargin: 40
        anchors.horizontalCenter: parent.horizontalCenter
        width: Math.min(parent.width - 80, toastLabel.implicitWidth + 32)
        height: 34; radius: 8
        color: isError ? Theme.error : Theme.surfaceRaised
        border.color: isError ? Theme.error : Theme.accent
        border.width: 1
        z: 20

        Behavior on opacity {
            NumberAnimation { duration: 180; easing.type: Easing.OutCubic }
        }

        Text {
            id: toastLabel
            anchors.centerIn: parent
            color: "white"
            font.pixelSize: 12
            elide: Text.ElideRight
            width: Math.min(parent.parent.width - 80, implicitWidth)
        }

        Timer {
            id: toastTimer
            interval: 3000
            onTriggered: toast.opacity = 0
        }

        function show(text, error) {
            toastLabel.text = text
            isError = error
            opacity = 1
            toastTimer.restart()
        }
    }

    Connections {
        target: bridge
        function onErrorOccurred(msg) { toast.show(msg, true) }
        function onNotify(msg) { toast.show(msg, false) }
        function onActiveConvChanged(convId) {
            chatPane.forceActiveFocus()
        }
        function onQuitRequested() { Qt.quit() }
        function onScanRequested() { scanDialog.open() }
        function onPaletteRequested() {
            if (!cmdPalette.visible) cmdPalette.open()
        }
        function onInfoMenuRequested(title, items) {
            // If the palette is still open (raw `:` command path), close it
            // first so the info menu owns focus cleanly.
            if (cmdPalette.visible) cmdPalette.close()
            infoMenu.show(title, items)
        }
    }

    Connections {
        target: vimEditor
        function onQuitRequested() { Qt.quit() }
        function onConvCycleRequested(d) { bridge.cycleConv(d) }
        function onPaletteRequested() { cmdPalette.open() }
        function onScanRequested() { scanDialog.open() }
        function onCommandRequested(cmd) { bridge.runCommand(cmd) }
    }

    // Restore focus to the chat pane whenever an overlay closes.
    Connections {
        target: cmdPalette
        function onVisibleChanged() {
            if (!cmdPalette.visible && !infoMenu.visible)
                chatPane.forceActiveFocus()
        }
    }
    Connections {
        target: scanDialog
        function onVisibleChanged() {
            if (!scanDialog.visible) chatPane.forceActiveFocus()
        }
    }
    Connections {
        target: infoMenu
        function onVisibleChanged() {
            if (!infoMenu.visible) chatPane.forceActiveFocus()
        }
    }
}
