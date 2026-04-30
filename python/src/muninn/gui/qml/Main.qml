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
        onActivated: {
            const prev = bridge.activeConvId
            bridge.cycleConv(1)
            root._trailConvCycle(prev, bridge.activeConvId)
        }
    }
    Shortcut {
        sequence: "Ctrl+P"
        context: Qt.ApplicationShortcut
        enabled: !root.overlayOpen
        onActivated: {
            const prev = bridge.activeConvId
            bridge.cycleConv(-1)
            root._trailConvCycle(prev, bridge.activeConvId)
        }
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

    // Top-most overlay: animated dots that fly along a polyline whenever
    // focus jumps between the composer, the palette, or a peer-list row.
    // Drawn above every dialog so trails are visible as overlays open.
    CursorTrail {
        id: cursorTrail
        anchors.fill: parent
        z: 100
    }

    // mapToItem requires a QQuickItem; ApplicationWindow's `root` is a
    // Window, not an Item — so all coordinates are mapped into the trail
    // overlay's own coord space (it already fills the content area).
    function _trailComposerToPalette() {
        const a = chatPane.cursorPos(cursorTrail)
        const b = cmdPalette.inputPos(cursorTrail)
        cursorTrail.trail([a, b])
    }
    function _trailPaletteToComposer() {
        const a = cmdPalette.inputPos(cursorTrail)
        const b = chatPane.cursorPos(cursorTrail)
        cursorTrail.trail([a, b])
    }
    function _trailConvCycle(prevConv, nextConv) {
        const composer = chatPane.cursorPos(cursorTrail)
        const rowA = peerList.rowPos(prevConv, cursorTrail)
        const rowB = peerList.rowPos(nextConv, cursorTrail)
        const path = [composer]
        if (rowA) path.push(rowA)
        if (rowB) path.push(rowB)
        path.push(composer)
        cursorTrail.trail(path)
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
            // Save the outgoing conv's draft and load this one's. Per-conv
            // drafts let the user start a message in DM A, switch to DM B,
            // then return to DM A and finish without losing anything.
            vimEditor.swapDraft(convId)
            chatPane.forceActiveFocus()
        }
        function onQuitRequested() { Qt.quit() }
        function onScanRequested() { scanDialog.open() }
        function onPaletteRequested() {
            if (!cmdPalette.visible) cmdPalette.open("")
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
        function onPaletteRequested(initial) { cmdPalette.open(initial) }
        function onScanRequested() { scanDialog.open() }
        function onCommandRequested(cmd) { bridge.runCommand(cmd) }
    }

    // Restore focus to the chat pane whenever an overlay closes, and fire
    // a cursor trail in/out of the palette as it opens/closes.
    Connections {
        target: cmdPalette
        function onVisibleChanged() {
            if (cmdPalette.visible) {
                root._trailComposerToPalette()
            } else {
                if (!infoMenu.visible) chatPane.forceActiveFocus()
                root._trailPaletteToComposer()
            }
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
