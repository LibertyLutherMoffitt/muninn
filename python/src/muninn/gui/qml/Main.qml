import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import QtQuick.Dialogs

ApplicationWindow {
    id: root
    title: "Muninn"
    width: 1120
    height: 740
    minimumWidth: 800
    minimumHeight: 500
    visible: false  // main.py shows it (or keeps it in the tray with --background)
    // Close = hide to the tray while background mode is on: links, relaying
    // and notifications keep going. Quit from the tray menu or :q.
    onClosing: (close) => {
        if (bridge.keepRunning) { close.accepted = false; root.hide(); bridge.wentToBackground() }
    }
    color: Theme.bg

    // Helper to check if we are in navigation mode
    property bool isNormalMode: vimEditor && vimEditor.mode === "NORMAL"
    onActiveChanged: if (active) bridge.windowActivated()

    // Initial focus on the chat pane so Vim commands work immediately
    Component.onCompleted: {
        if (welcome.visible) overlayVisibility(welcome)  // first run: name prompt
        else chatPane.forceActiveFocus()
    }

    // ------------------------------------------------------------------
    // Main Layout
    // ------------------------------------------------------------------

    // Everything visual lives on a "stage" scaled by the zoom level
    // (Ctrl +/-/0, :zoom): crisp text, correct mouse mapping, layouts that
    // simply get more or less room.
    property real uiScale: bridge.uiScale
    Item {
        id: stage
        width: root.width / root.uiScale
        height: root.height / root.uiScale
        scale: root.uiScale
        transformOrigin: Item.TopLeft
    }
    Shortcut { sequences: ["Ctrl++", "Ctrl+="]; onActivated: bridge.setUiScale(root.uiScale + 0.1) }
    Shortcut { sequence: "Ctrl+-"; onActivated: bridge.setUiScale(root.uiScale - 0.1) }
    Shortcut { sequence: "Ctrl+0"; onActivated: bridge.setUiScale(1.0) }

    ColumnLayout { parent: stage;
        anchors.fill: parent
        spacing: 0

        RowLayout {
            id: mainLayout
            Layout.fillWidth: true
            Layout.fillHeight: true
            spacing: 0

            PeerList {
                id: peerList
                onRemoveRequested: (convs, how) => confirmRemove.open(convs, how)
                Layout.preferredWidth: 240
                Layout.fillHeight: true
                onConvSelected: (cid) => {
                    const prev = bridge.activeConvId
                    bridge.setActiveConv(cid)
                    root.refocus()
                    if (prev !== cid) root._trailConvCycle(prev, cid)
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
                onReactRequested: (mid) => emojiPicker.open("react", mid)
                
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
                anchors.left: parent.left
                anchors.leftMargin: 12
                anchors.verticalCenter: parent.verticalCenter
                spacing: 20

                Text {
                    anchors.verticalCenter: parent.verticalCenter
                    text: bridge.localName ? "nick: " + bridge.localName : "nick: (none)"
                    color: Theme.textMuted
                    font.pixelSize: 11
                }
                Text {
                    anchors.verticalCenter: parent.verticalCenter
                    // same wording as the terminal client
                    text: "● " + bridge.connectedPeerCount + " connected"
                          + (bridge.reachablePeerCount > bridge.connectedPeerCount
                             ? " · " + (bridge.reachablePeerCount - bridge.connectedPeerCount) + " via relay" : "")
                    color: bridge.connectedPeerCount > 0 ? Theme.success : Theme.textMuted
                    font.pixelSize: 11
                }
                Text {
                    id: findingText
                    anchors.verticalCenter: parent.verticalCenter
                    visible: text !== ""
                    color: Theme.accent
                    font.pixelSize: 11
                    Timer { interval: 1000; running: true; repeat: true; triggeredOnStart: true
                            onTriggered: findingText.text = bridge.findingStatus() }
                    MouseArea { anchors.fill: parent; cursorShape: Qt.PointingHandCursor
                                ToolTip.visible: containsMouse; hoverEnabled: true
                                ToolTip.text: "Looking for new people (scans + polite probes). Click to stop."
                                onClicked: { bridge.runCommand("find off"); findingText.text = "" } }
                }
                Text {
                    anchors.verticalCenter: parent.verticalCenter
                    text: bridge.transportName
                    color: Theme.textMuted
                    font.pixelSize: 11
                }
            }

            // Manual palette opener — keyboard shortcut is <space>f, but a
            // visible button helps discoverability for new users.
            Rectangle {
                id: paletteBtn
                anchors.right: parent.right
                anchors.rightMargin: 8
                anchors.verticalCenter: parent.verticalCenter
                width: paletteBtnLabel.implicitWidth + 18
                height: 18
                radius: 4
                color: paletteBtnArea.containsMouse
                    ? Theme.accent : Theme.surface
                border.color: Theme.accent
                border.width: 1

                Behavior on color {
                    ColorAnimation { duration: 120; easing.type: Easing.OutQuad }
                }

                Text {
                    id: paletteBtnLabel
                    anchors.centerIn: parent
                    text: "⌘  palette  ·  ␣f"
                    color: paletteBtnArea.containsMouse
                        ? "white" : Theme.textMuted
                    font.pixelSize: 10
                    font.bold: true
                }

                MouseArea {
                    id: paletteBtnArea
                    anchors.fill: parent
                    hoverEnabled: true
                    cursorShape: Qt.PointingHandCursor
                    onClicked: {
                        if (cmdPalette.visible) return
                        // Trail starts from the button so the comet flies
                        // from the click point (not from the composer caret
                        // the user isn't looking at).
                        const c = paletteBtn.mapToItem(cursorTrail,
                            paletteBtn.width / 2, paletteBtn.height / 2)
                        root._paletteTrailStart = c
                        cmdPalette.open("")
                    }
                }
            }

            // About button — sits to the left of the palette button. Routes
            // through bridge.runCommand("about") so the popup styling matches
            // every other info menu.
            Rectangle {
                id: aboutBtn
                anchors.right: paletteBtn.left
                anchors.rightMargin: 6
                anchors.verticalCenter: parent.verticalCenter
                width: aboutBtnLabel.implicitWidth + 18
                height: 18
                radius: 4
                color: aboutBtnArea.containsMouse
                    ? Theme.accent : Theme.surface
                border.color: Theme.accent
                border.width: 1

                Behavior on color {
                    ColorAnimation { duration: 120; easing.type: Easing.OutQuad }
                }

                Text {
                    id: aboutBtnLabel
                    anchors.centerIn: parent
                    text: "?  about"
                    color: aboutBtnArea.containsMouse
                        ? "white" : Theme.textMuted
                    font.pixelSize: 10
                    font.bold: true
                }

                MouseArea {
                    id: aboutBtnArea
                    anchors.fill: parent
                    hoverEnabled: true
                    cursorShape: Qt.PointingHandCursor
                    onClicked: bridge.runCommand("about")
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
        || emojiPicker.visible || imageViewer.visible || gameView.visible
        || confirmRemove.visible

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

    Shortcut { sequence: "Alt+Up"; enabled: !root.overlayOpen; onActivated: bridge.moveSelection(-1) }
    Shortcut { sequence: "Alt+Down"; enabled: !root.overlayOpen; onActivated: bridge.moveSelection(1) }
    Shortcut { sequence: "Alt+R"; enabled: !root.overlayOpen; onActivated: bridge.replyTo("") }
    Shortcut { sequence: "Alt+E"; enabled: !root.overlayOpen; onActivated: bridge.runCommand("react") }
    Shortcut { sequence: "Ctrl+E"; enabled: !root.overlayOpen; onActivated: bridge.runCommand("emoji") }
    Shortcut { sequence: "Ctrl+O"; enabled: !root.overlayOpen; onActivated: bridge.runCommand("file") }
    Shortcut { sequence: "Ctrl+G"; enabled: !root.overlayOpen; onActivated: bridge.runCommand("games") }

    // ------------------------------------------------------------------
    // Overlays
    // ------------------------------------------------------------------

    DropArea { parent: stage;
        anchors.fill: parent
        onDropped: (drop) => { if (drop.hasUrls) bridge.sendFiles(drop.urls) }
        Rectangle {
            anchors.fill: parent; color: "#407c3aed"; visible: parent.containsDrag
            Text { anchors.centerIn: parent; text: "Drop to send"; color: "white"; font.pixelSize: 22 }
        }
    }

    FileDialog {
        id: gameDialog
        title: "Install a Muninn game"
        nameFilters: ["Muninn games (*.js)", "All files (*)"]
        onAccepted: bridge.importGame(selectedFile.toString())
    }

    FileDialog {
        id: fileDialog
        title: "Send a file"
        fileMode: FileDialog.OpenFiles
        onAccepted: bridge.sendFiles(selectedFiles)
    }

    // ---- focus: the most recently opened overlay that is still visible owns
    // the keyboard; with none open, the chat (composer) does. Overlays never
    // hand focus back to the chat themselves — that used to steal it from an
    // overlay opened by another one (e.g. a game launched from a menu).
    property var focusStack: []
    function overlayVisibility(o) {
        const st = focusStack.filter(x => x !== o)
        if (o.visible) st.push(o)
        focusStack = st
        Qt.callLater(refocus)
    }
    function refocus() {
        for (let i = focusStack.length - 1; i >= 0; i--) {
            const o = focusStack[i]
            if (o.visible) { o.grabFocus(); return }
        }
        chatPane.forceActiveFocus()
    }

    Welcome { parent: stage; id: welcome; anchors.fill: parent; z: 50
        onVisibleChanged: root.overlayVisibility(welcome) }
    EmojiPicker { parent: stage; id: emojiPicker; anchors.fill: parent; z: 12
        onVisibleChanged: root.overlayVisibility(emojiPicker) }
    ImageViewer { parent: stage; id: imageViewer; anchors.fill: parent; z: 13
        onVisibleChanged: root.overlayVisibility(imageViewer) }
    GameView { parent: stage; id: gameView; anchors.fill: parent; z: 11
        onVisibleChanged: root.overlayVisibility(gameView) }

    ScanDialog { parent: stage; id: scanDialog; anchors.fill: parent; z: 10
        onVisibleChanged: root.overlayVisibility(scanDialog) }
    ConfirmRemove { parent: stage; id: confirmRemove; anchors.fill: parent; z: 14
        onVisibleChanged: { root.overlayVisibility(confirmRemove); peerList.confirmOpen = visible }
        onDone: peerList.clearPicked() }

    CommandPalette { parent: stage;
        id: cmdPalette
        anchors.fill: parent
        z: 10
        onConvSelected: (convId) => {
            bridge.setActiveConv(convId)
            root.refocus()
        }
        onVisibleChanged: root.overlayVisibility(cmdPalette)
    }

    InfoMenu { parent: stage;
        id: infoMenu
        anchors.fill: parent
        z: 11
        onConvSelected: (convId) => {
            bridge.setActiveConv(convId)
            root.refocus()
        }
        onGameSelected: (sid) => gameView.open(sid)
        onVisibleChanged: root.overlayVisibility(infoMenu)
    }

    // Top-most overlay: animated dots that fly along a polyline whenever
    // focus jumps between the composer, the palette, or a peer-list row.
    // Drawn above every dialog so trails are visible as overlays open.
    CursorTrail { parent: stage;
        id: cursorTrail
        anchors.fill: parent
        z: 100
    }

    // mapToItem requires a QQuickItem; ApplicationWindow's `root` is a
    // Window, not an Item — so all coordinates are mapped into the trail
    // overlay's own coord space (it already fills the content area).
    //
    // A click on the palette button stashes a one-shot start point here so
    // the comet flies from the click instead of the composer caret.
    property var _paletteTrailStart: null

    function _trailComposerToPalette() {
        const a = root._paletteTrailStart || chatPane.cursorPos(cursorTrail)
        root._paletteTrailStart = null
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
    Rectangle { parent: stage;
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
            color: toast.isError ? "white" : Theme.textPrimary
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
            root.refocus()
        }
        function onQuitRequested() { Qt.quit() }
        function onScanRequested() { scanDialog.open() }
        function onPaletteRequested() {
            if (!cmdPalette.visible) cmdPalette.open("")
        }
        function onFilePickRequested() { fileDialog.open() }
        function onGameImportRequested() { gameDialog.open() }
        function onEmojiPickerRequested(mode) { emojiPicker.open(mode, "") }
        function onImageViewRequested(url) { imageViewer.open(url) }
        function onGameOpenRequested(sid) { gameView.open(sid) }
        function onRemoveRequested(convs, how) { confirmRemove.open(convs, how) }
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
        function onMessageNav(d) { if (!bridge.moveSelection(d)) vimEditor.leaveMessages() }
        function onMessageAction(a) { bridge.messageAction(a) }
    }

    // Restore focus to the chat pane whenever an overlay closes, and fire
    // a cursor trail in/out of the palette as it opens/closes.
    Connections {
        target: cmdPalette
        function onVisibleChanged() {
            if (cmdPalette.visible) {
                root._trailComposerToPalette()
            } else {
                root._trailPaletteToComposer()
            }
        }
    }

}
