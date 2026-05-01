import QtQuick
import QtQuick.Controls

// Fuzzy command palette (<space>f). Supports two modes:
//   Fuzzy:   text doesn't start with ":". Filters peers + named commands.
//   Raw cmd: text starts with ":". Hides results, runs as bridge.runCommand
//            on Enter, tab-completes via bridge.completeCommand.
Rectangle {
    id: root
    // Eager visibility so child controls are focusable the instant open()
    // runs (same fix as ScanDialog).
    visible: _open || opacity > 0
    opacity: 0
    color: Qt.rgba(0, 0, 0, 0.55)
    anchors.fill: parent

    property bool _open: false
    // `_cmdMode` is the modern-Vim-plugin "command palette" mode: a `:`
    // is shown as a prompt prefix (NOT in the input), typing filters the
    // command list, Enter dispatches via vimEditor.execCommand.
    property bool _cmdMode: false
    // Old name kept for the orange border binding that already references it.
    readonly property bool isRawCommand: _cmdMode

    signal convSelected(string convId)

    Behavior on opacity {
        NumberAnimation { duration: 160; easing.type: Easing.OutCubic }
    }

    property real _scale: _open ? 1.0 : 0.94
    Behavior on _scale {
        NumberAnimation { duration: 200; easing.type: Easing.OutBack }
    }

    MouseArea { anchors.fill: parent; onClicked: root.close() }

    Rectangle {
        id: box
        anchors.top: parent.top
        anchors.horizontalCenter: parent.horizontalCenter
        anchors.topMargin: 80
        width: Math.min(560, parent.width - 40)
        height: Math.min(400, parent.height - 160)
        color: Theme.surfaceRaised
        radius: 10
        border.color: Theme.accent
        border.width: 1
        transform: Scale {
            origin.x: box.width / 2; origin.y: box.height / 2
            xScale: root._scale; yScale: root._scale
        }

        MouseArea { anchors.fill: parent }

        Column {
            anchors.fill: parent
            anchors.margins: 12
            spacing: 8

            // Search input
            Rectangle {
                id: inputBg
                width: parent.width
                height: 36
                color: Theme.bg
                radius: 6
                border.color: root.isRawCommand ? "#f59e0b" : "transparent"
                border.width: 1

                // ":" prompt shown only in command mode. Sized so the
                // TextField sits flush against it.
                Text {
                    id: cmdPrompt
                    visible: root._cmdMode
                    anchors.left: parent.left
                    anchors.leftMargin: 10
                    anchors.verticalCenter: parent.verticalCenter
                    text: ":"
                    color: "#f59e0b"
                    font.pixelSize: 16
                    font.bold: true
                }

                TextField {
                    id: searchInput
                    anchors {
                        left: root._cmdMode ? cmdPrompt.right : parent.left
                        leftMargin: root._cmdMode ? 4 : 10
                        right: parent.right; rightMargin: 10
                        verticalCenter: parent.verticalCenter
                    }
                    color: Theme.textPrimary
                    font.pixelSize: 14
                    // Default font (JetBrains Mono) is monospace already.
                    placeholderText: root._cmdMode
                        ? "command — Tab completes, Enter runs"
                        : "search peers / commands  (or :cmd args)"
                    background: null
                    selectByMouse: true
                    Keys.onEscapePressed: root.close()
                    Keys.onReturnPressed: root.activate()
                    Keys.onEnterPressed: root.activate()
                    Keys.onPressed: (event) => {
                        // Tab — complete the current command/argument.
                        if (event.key === Qt.Key_Tab) {
                            if (root._cmdMode) {
                                // bridge.completeCommand strips/preserves a
                                // leading colon — pass plain body and use
                                // the returned body unchanged.
                                const next = bridge.completeCommand(text)
                                const stripped = next.startsWith(":")
                                    ? next.substring(1) : next
                                if (stripped !== text) {
                                    text = stripped
                                    cursorPosition = text.length
                                }
                            } else if (resultList.currentIndex >= 0) {
                                const item = filteredModel.get(resultList.currentIndex)
                                if (item && item.label) {
                                    // For command items, drop into cmdMode
                                    // with the cmd loaded so the user can
                                    // append args.
                                    if (item.label.startsWith(":")) {
                                        root._cmdMode = true
                                        text = (item.cmd || item.label.substring(1))
                                        cursorPosition = text.length
                                        root.filterModel()
                                    } else {
                                        text = item.label
                                        cursorPosition = text.length
                                    }
                                }
                            }
                            event.accepted = true
                            return
                        }
                        // Backspace at empty input in cmdMode exits cmdMode.
                        if (event.key === Qt.Key_Backspace
                            && root._cmdMode && text.length === 0) {
                            root._cmdMode = false
                            root.filterModel()
                            event.accepted = true
                            return
                        }
                        // Up/Down + Ctrl-N/Ctrl-P move selection.
                        if (event.key === Qt.Key_Down ||
                            ((event.modifiers & Qt.ControlModifier) && event.key === Qt.Key_N)) {
                            if (resultList.currentIndex + 1 < resultList.count)
                                resultList.currentIndex++
                            event.accepted = true
                            return
                        }
                        if (event.key === Qt.Key_Up ||
                            ((event.modifiers & Qt.ControlModifier) && event.key === Qt.Key_P)) {
                            if (resultList.currentIndex > 0)
                                resultList.currentIndex--
                            event.accepted = true
                            return
                        }
                    }
                    onTextChanged: {
                        // Auto-promote to cmd mode if the user types `:`
                        // first thing while in fuzzy mode.
                        if (!root._cmdMode && text.startsWith(":")) {
                            root._cmdMode = true
                            text = text.substring(1)
                            cursorPosition = text.length
                        }
                        root.filterModel()
                    }
                    Component.onCompleted: if (root.visible) forceActiveFocus()
                }
            }

            // Hint shown in cmd mode below the input.
            Text {
                visible: root._cmdMode
                width: parent.width
                color: Theme.textMuted
                font.pixelSize: 11
                text: "Tab completes · Enter runs · Backspace at empty exits cmd mode · Esc closes"
            }

            // Results — shown in BOTH modes. In cmd mode it lists matching
            // commands; in fuzzy mode it lists peers + commands.
            ListView {
                id: resultList
                visible: true
                width: parent.width
                height: parent.height - inputBg.height
                    - (root._cmdMode ? 24 : 8)
                model: ListModel { id: filteredModel }
                currentIndex: 0
                clip: true
                spacing: 2
                highlightMoveDuration: 80

                delegate: ItemDelegate {
                    id: rowDel
                    width: resultList.width
                    height: 40
                    highlighted: resultList.currentIndex === index
                    background: Rectangle {
                        color: rowDel.highlighted ? Theme.accent
                             : rowDel.hovered     ? Theme.bg
                                                  : "transparent"
                        radius: 5
                        opacity: rowDel.highlighted ? 0.7 : 1.0
                        Behavior on color {
                            ColorAnimation { duration: 120; easing.type: Easing.OutQuad }
                        }
                    }
                    contentItem: Row {
                        spacing: 10
                        anchors.verticalCenter: parent.verticalCenter
                        Text {
                            text: model.icon || "   "
                            color: Theme.textMuted
                            font.pixelSize: 13
                        }
                        Text {
                            text: model.label
                            color: Theme.textPrimary
                            font.pixelSize: 13
                        }
                        Text {
                            text: model.sub || ""
                            color: Theme.textMuted
                            font.pixelSize: 11
                        }
                    }
                    onClicked: { resultList.currentIndex = index; root.activate() }
                }
            }
        }
    }

    function open(initial) {
        const wantsCmd = (initial || "").startsWith(":")
        // Strip the literal `:` — it's shown as a prompt, not text.
        const body = wantsCmd ? initial.substring(1) : (initial || "")
        root._cmdMode = wantsCmd
        searchInput.text = body
        searchInput.cursorPosition = body.length
        filterModel()
        root._open = true
        root.opacity = 1
        // Defer focus so the visibility binding has settled — same fix as
        // ScanDialog. Without this, `:` from Vim normal mode loses the focus
        // race against the Composer's TextEdit.
        Qt.callLater(function() { searchInput.forceActiveFocus() })
    }

    function close() {
        root._open = false
        root.opacity = 0
        root._cmdMode = false
    }

    // Window-space center of the search input's caret, used by the cursor-
    // trail overlay so the trail anchors to where typing actually happens
    // (not to the geometric center of the input box).
    function inputPos(target) {
        const r = searchInput.cursorRectangle
        // Fall back to the field center if the caret has no geometry yet
        // (e.g. before the field has been laid out).
        const cx = (r && r.width >= 0)
            ? r.x + r.width / 2
            : searchInput.width / 2
        const cy = (r && r.height > 0)
            ? r.y + r.height / 2
            : searchInput.height / 2
        return searchInput.mapToItem(target, cx, cy)
    }

    function activate() {
        if (root._cmdMode) {
            const cmd = searchInput.text.trim()
            if (!cmd) { root.close(); return }
            // Route through Vim's exec so :wq/:x/:send flush the composer
            // buffer first. execCommand strips a leading colon if present.
            vimEditor.execCommand(cmd)
            // Keep palette open for commands that pop their own info menu —
            // Main.qml closes us when infoMenuRequested fires.
            const head = cmd.split(/\s+/)[0]
            const dataCmd = ["list", "peers", "known", "help", "h", "?"]
                .indexOf(head) >= 0
            if (!dataCmd) root.close()
            return
        }
        if (resultList.currentIndex < 0) {
            root.close()
            return
        }
        const item = filteredModel.get(resultList.currentIndex)
        if (!item) { root.close(); return }
        if (item.convId) {
            root.convSelected(item.convId)
            root.close()
            return
        }
        if (item.action === "cmd" && item.cmd) {
            // Commands ending in a space expect an argument — drop into
            // cmd mode with the partial loaded so the user can type args.
            if (item.cmd.endsWith(" ")) {
                root._cmdMode = true
                searchInput.text = item.cmd
                searchInput.cursorPosition = searchInput.text.length
                root.filterModel()
                return
            }
            vimEditor.execCommand(item.cmd)
            const head = item.cmd.split(/\s+/)[0]
            const dataCmd = ["list", "peers", "known", "help"]
                .indexOf(head) >= 0
            if (!dataCmd) root.close()
            return
        }
        root.close()
    }

    readonly property var _cmds: [
        { icon: "⚡", label: ":scan",     sub: "open the bluetooth scan dialog",   action: "cmd", convId: "", cmd: "scan" },
        { icon: "≡",  label: ":list",     sub: "list conversations",               action: "cmd", convId: "", cmd: "list" },
        { icon: "●",  label: ":peers",    sub: "show direct + relay peers",        action: "cmd", convId: "", cmd: "peers" },
        { icon: "○",  label: ":known",    sub: "show every known peer",            action: "cmd", convId: "", cmd: "known" },
        { icon: "↻",  label: ":next",     sub: "next conversation (Ctrl-N)",       action: "cmd", convId: "", cmd: "next" },
        { icon: "↺",  label: ":prev",     sub: "previous conversation (Ctrl-P)",   action: "cmd", convId: "", cmd: "prev" },
        { icon: "✎",  label: ":nick",     sub: "set your display name",            action: "cmd", convId: "", cmd: "nick " },
        { icon: "+",  label: ":new",      sub: "create group",                     action: "cmd", convId: "", cmd: "new " },
        { icon: "↺",  label: ":history",  sub: "reload last N messages",           action: "cmd", convId: "", cmd: "history " },
        { icon: "⌫",  label: ":clear",    sub: "clear visible messages",           action: "cmd", convId: "", cmd: "clear" },
        { icon: "i",  label: ":help",     sub: "list all commands",                action: "cmd", convId: "", cmd: "help" },
        { icon: "✕",  label: ":quit",     sub: "exit Muninn",                      action: "cmd", convId: "", cmd: "quit" },
    ]

    function filterModel() {
        filteredModel.clear()
        const q = searchInput.text.toLowerCase()
        if (root._cmdMode) {
            // In cmd mode the body is the command query (no leading `:`).
            // Match against the command name and its description.
            const head = q.split(/\s+/)[0]
            for (let j = 0; j < _cmds.length; j++) {
                const c = _cmds[j]
                const name = c.label.startsWith(":") ? c.label.substring(1) : c.label
                if (!head || name.startsWith(head) || c.sub.toLowerCase().includes(head))
                    filteredModel.append(c)
            }
            resultList.currentIndex = filteredModel.count > 0 ? 0 : -1
            return
        }
        const peers = bridge.knownPeers()
        for (let i = 0; i < peers.length; i++) {
            const p = peers[i]
            const name = (p.name || p.mac).toLowerCase()
            if (!q || name.includes(q) || p.mac.toLowerCase().includes(q)) {
                filteredModel.append({
                    icon: p.status === "direct"  ? "●"
                        : p.status === "relay"   ? "◎"
                                                 : "○",
                    label: p.name || p.mac,
                    sub: p.name !== p.mac ? p.mac : "",
                    convId: "dm:" + p.mac,
                    action: "",
                    cmd: ""
                })
            }
        }
        for (let j = 0; j < _cmds.length; j++) {
            if (!q || _cmds[j].label.includes(q) || _cmds[j].sub.toLowerCase().includes(q))
                filteredModel.append(_cmds[j])
        }
        resultList.currentIndex = filteredModel.count > 0 ? 0 : -1
    }

    Keys.onEscapePressed: root.close()
    focus: _open
}
