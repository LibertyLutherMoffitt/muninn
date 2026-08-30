import QtQuick
import QtQuick.Controls

Rectangle {
    id: root
    color: Theme.surface

    property string activeConvId: bridge.activeConvId
    property alias listView: listView

    signal convSelected(string convId)

    // Window-space center of the row for `convId`, or null if the row is
    // not currently realized (off-screen / unknown). Used by the cursor-
    // trail overlay.
    function rowPos(convId, target) {
        for (let i = 0; i < listView.count; i++) {
            const idx = listView.model.index(i, 0)
            const conv = listView.model.data(idx, Qt.UserRole + 3)
            if (conv === convId) {
                const item = listView.itemAtIndex(i)
                if (!item) return null
                return listView.mapToItem(target,
                    item.x + item.width / 2,
                    item.y + item.height / 2)
            }
        }
        return null
    }

    // A divider against the chat pane, so the two surfaces read as panes
    // rather than as one flat field.
    Rectangle {
        anchors.right: parent.right
        width: 1
        height: parent.height
        color: Theme.border
        z: 2
    }

    EmptyState {
        anchors.centerIn: parent
        width: parent.width - Theme.spaceLg * 2
        visible: listView.count === 0
        glyph: "⌁"
        title: "No peers yet"
        subtitle: "Muninn scans in the background. Peers running it nearby appear here on their own."
    }

    ListView {
        id: listView
        anchors.fill: parent
        model: peerModel
        clip: true
        currentIndex: -1
        visible: count > 0

        ScrollBar.vertical: ScrollBar { policy: ScrollBar.AsNeeded }

        // j/k navigation from global key handler
        function selectNext() {
            if (currentIndex + 1 < count) currentIndex++
        }
        function selectPrev() {
            if (currentIndex > 0) currentIndex--
        }
        function activateCurrent() {
            if (currentIndex >= 0) {
                const conv = model.data(model.index(currentIndex, 0),
                    Qt.UserRole + 3)  // convId role
                if (conv) root.convSelected(conv)
            }
        }

        delegate: ItemDelegate {
            id: del
            width: listView.width
            height: 60
            highlighted: model.convId === root.activeConvId

            background: Rectangle {
                color: del.highlighted ? Theme.surfaceRaised
                     : del.hovered     ? Qt.lighter(Theme.surface, 1.12)
                                       : "transparent"

                Behavior on color {
                    ColorAnimation { duration: 140; easing.type: Easing.OutQuad }
                }

                // peer-connect/disconnect pulse
                ColorAnimation on color {
                    id: pulseAnim
                    duration: 240
                    easing.type: Easing.OutQuad
                }
            }

            // Flash on peer status change
            Connections {
                target: bridge
                function onPeerChanged(addr, connected) {
                    if (addr === model.mac) {
                        pulseAnim.from = connected
                            ? Theme.success : Theme.error
                        pulseAnim.to = del.highlighted
                            ? Theme.surfaceRaised : "transparent"
                        pulseAnim.start()
                    }
                }
            }

            contentItem: Item {
                // Avatar circle
                Rectangle {
                    id: avatar
                    anchors.left: parent.left
                    anchors.leftMargin: 10
                    anchors.verticalCenter: parent.verticalCenter
                    width: 36; height: 36; radius: 18
                    color: Theme.accent
                    opacity: 0.8

                    Text {
                        anchors.centerIn: parent
                        text: (model.displayName || "?").charAt(0).toUpperCase()
                        color: Theme.textPrimary
                        font.pixelSize: 16
                        font.bold: true
                    }
                }

                // Shared with the chat header — see PresenceDot.qml.
                PresenceDot {
                    anchors.right: avatar.right
                    anchors.bottom: avatar.bottom
                    presenceState: model.status
                }

                Column {
                    anchors.left: avatar.right
                    anchors.leftMargin: 10
                    anchors.right: badge.left
                    anchors.rightMargin: 4
                    anchors.verticalCenter: parent.verticalCenter
                    spacing: 2

                    Text {
                        width: parent.width
                        text: model.displayName || model.mac
                        color: Theme.textPrimary
                        font.pixelSize: 13
                        font.bold: del.highlighted
                        elide: Text.ElideRight
                    }
                    Text {
                        width: parent.width
                        text: model.lastMessage || ""
                        color: Theme.textMuted
                        font.pixelSize: 11
                        elide: Text.ElideRight
                        visible: model.lastMessage !== ""
                    }
                    // Falls back to connectivity when there is no history yet,
                    // so a new or unreachable peer still says something useful.
                    Text {
                        width: parent.width
                        text: model.presenceText || ""
                        color: model.status === "unreachable" ? Theme.error : Theme.textMuted
                        font.pixelSize: 10
                        elide: Text.ElideRight
                        visible: model.lastMessage === "" && (model.presenceText || "") !== ""
                    }
                }

                // Unread badge
                Rectangle {
                    id: badge
                    anchors.right: parent.right
                    anchors.rightMargin: 8
                    anchors.verticalCenter: parent.verticalCenter
                    width: Math.max(20, badgeText.implicitWidth + 8)
                    height: 20; radius: 10
                    color: Theme.accent
                    visible: model.unreadCount > 0

                    Text {
                        id: badgeText
                        anchors.centerIn: parent
                        text: model.unreadCount > 99 ? "99+" : model.unreadCount
                        color: "white"
                        font.pixelSize: 11
                        font.bold: true
                    }
                }
            }

            onClicked: root.convSelected(model.convId)
        }
    }
}
