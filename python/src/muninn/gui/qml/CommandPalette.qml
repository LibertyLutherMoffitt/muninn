import QtQuick
import QtQuick.Controls

// Fuzzy command palette (<space>f). Supports two modes:
//   Fuzzy:   text doesn't start with ":". Filters peers + named commands.
//   Raw cmd: text starts with ":". Hides results, runs as bridge.runCommand
//            on Enter, tab-completes via bridge.completeCommand.
Rectangle {
    id: root
    visible: opacity > 0
    opacity: 0
    color: Qt.rgba(0, 0, 0, 0.55)
    anchors.fill: parent

    property bool _open: false
    readonly property bool isRawCommand: searchInput.text.startsWith(":")

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

                TextField {
                    id: searchInput
                    anchors {
                        left: parent.left; leftMargin: 10
                        right: parent.right; rightMargin: 10
                        verticalCenter: parent.verticalCenter
                    }
                    color: Theme.textPrimary
                    font.pixelSize: 14
                    // Default font (JetBrains Mono) is monospace already.
                    placeholderText: "search peers / commands  (or :cmd args)"
                    background: null
                    selectByMouse: true
                    Keys.onEscapePressed: root.close()
                    Keys.onReturnPressed: root.activate()
                    Keys.onEnterPressed: root.activate()
                    Keys.onPressed: (event) => {
                        // Tab — complete the current command/argument.
                        if (event.key === Qt.Key_Tab) {
                            if (root.isRawCommand) {
                                const next = bridge.completeCommand(text)
                                if (next && next !== text) {
                                    text = next
                                    cursorPosition = text.length
                                }
                            } else if (resultList.currentIndex >= 0) {
                                const item = filteredModel.get(resultList.currentIndex)
                                if (item && item.label) {
                                    // For command items (label starts ":"),
                                    // dropping into raw mode lets the user
                                    // keep typing args; for peer items, just
                                    // fill the name.
                                    text = item.label.startsWith(":")
                                        ? item.label + " "
                                        : item.label
                                    cursorPosition = text.length
                                }
                            }
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
                    onTextChanged: filterModel()
                    Component.onCompleted: if (root.visible) forceActiveFocus()
                }
            }

            // Hint shown in raw-command mode (results hidden).
            Text {
                visible: root.isRawCommand
                width: parent.width
                color: Theme.textMuted
                font.pixelSize: 12
                wrapMode: Text.Wrap
                text: "Enter to run · Tab to complete · Esc to cancel"
            }

            // Results
            ListView {
                id: resultList
                visible: !root.isRawCommand
                width: parent.width
                height: parent.height - inputBg.height - 8
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

    function open() {
        searchInput.text = ""
        filterModel()
        root._open = true
        root.opacity = 1
        searchInput.forceActiveFocus()
    }

    function close() {
        root._open = false
        root.opacity = 0
    }

    function activate() {
        if (root.isRawCommand) {
            const cmd = searchInput.text.substring(1)
            bridge.runCommand(cmd)
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
            // Commands ending in a space expect an argument — switch the
            // input into raw mode rather than running incomplete commands.
            if (item.cmd.endsWith(" ")) {
                searchInput.text = ":" + item.cmd
                searchInput.cursorPosition = searchInput.text.length
                return
            }
            bridge.runCommand(item.cmd)
            // Same data-command rule as raw mode.
            const head = item.cmd.split(/\s+/)[0]
            const dataCmd = ["list", "peers", "known", "help"]
                .indexOf(head) >= 0
            if (!dataCmd) root.close()
            return
        }
        root.close()
    }

    function filterModel() {
        filteredModel.clear()
        if (root.isRawCommand) return  // results hidden in raw mode
        const q = searchInput.text.toLowerCase()
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
        const cmds = [
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
        for (let j = 0; j < cmds.length; j++) {
            if (!q || cmds[j].label.includes(q) || cmds[j].sub.toLowerCase().includes(q))
                filteredModel.append(cmds[j])
        }
        resultList.currentIndex = 0
    }

    Keys.onEscapePressed: root.close()
    focus: _open
}
