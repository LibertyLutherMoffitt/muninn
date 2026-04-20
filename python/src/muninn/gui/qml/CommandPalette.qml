import QtQuick
import QtQuick.Controls

// Fuzzy command palette (Ctrl-P).
Rectangle {
    id: root
    visible: false
    color: Qt.rgba(0, 0, 0, 0.6)
    anchors.fill: parent

    signal convSelected(string convId)

    property real _scale: visible ? 1.0 : 0.92
    Behavior on _scale {
        NumberAnimation { duration: 100; easing.type: Easing.OutBack }
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
        transform: Scale {
            origin.x: width / 2; origin.y: height / 2
            xScale: root._scale; yScale: root._scale
        }

        MouseArea { anchors.fill: parent }

        Column {
            anchors.fill: parent
            anchors.margins: 12
            spacing: 8

            // Search input
            Rectangle {
                width: parent.width
                height: 36
                color: Theme.bg
                radius: 6

                TextInput {
                    id: searchInput
                    anchors {
                        left: parent.left; leftMargin: 10
                        right: parent.right; rightMargin: 10
                        verticalCenter: parent.verticalCenter
                    }
                    color: Theme.textPrimary
                    font.pixelSize: 14
                    placeholderText: "Search peers, commands…"
                    Keys.onEscapePressed: root.close()
                    Keys.onReturnPressed: activateItem()
                    Keys.onDownPressed: {
                        if (resultList.currentIndex + 1 < resultList.count)
                            resultList.currentIndex++
                    }
                    Keys.onUpPressed: {
                        if (resultList.currentIndex > 0)
                            resultList.currentIndex--
                    }
                    onTextChanged: filterModel()
                    Component.onCompleted: if (root.visible) forceActiveFocus()
                }
            }

            // Results
            ListView {
                id: resultList
                width: parent.width
                height: parent.height - searchInput.parent.height - 8
                model: ListModel { id: filteredModel }
                currentIndex: 0
                clip: true
                spacing: 2

                delegate: ItemDelegate {
                    width: resultList.width
                    height: 40
                    highlighted: resultList.currentIndex === index
                    background: Rectangle {
                        color: parent.highlighted ? Theme.accent
                             : parent.hovered     ? Theme.bg
                                                  : "transparent"
                        radius: 5
                        opacity: parent.highlighted ? 0.6 : 1.0
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
                    onClicked: { resultList.currentIndex = index; activateItem() }
                }
            }
        }
    }

    function open() {
        root.visible = true
        searchInput.text = ""
        filterModel()
        searchInput.forceActiveFocus()
    }

    function close() {
        root.visible = false
    }

    function activateItem() {
        if (resultList.currentIndex < 0) return
        const item = filteredModel.get(resultList.currentIndex)
        if (!item) return
        if (item.convId) {
            root.convSelected(item.convId)
        } else if (item.action === "scan") {
            root.close()
            scanDialog.open()
        } else if (item.action === "quit") {
            Qt.quit()
        }
        root.close()
    }

    function filterModel() {
        filteredModel.clear()
        const q = searchInput.text.toLowerCase()
        // Peers
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
                    action: ""
                })
            }
        }
        // Commands
        const cmds = [
            { icon: "⚡", label: ":scan",     sub: "Scan for nearby devices",  action: "scan",  convId: "" },
            { icon: "✕",  label: ":quit / ZZ", sub: "Exit Muninn",              action: "quit",  convId: "" },
        ]
        for (let j = 0; j < cmds.length; j++) {
            if (!q || cmds[j].label.includes(q) || cmds[j].sub.toLowerCase().includes(q))
                filteredModel.append(cmds[j])
        }
        resultList.currentIndex = 0
    }

    Keys.onEscapePressed: root.close()
    focus: visible
}
