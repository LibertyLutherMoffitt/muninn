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

    ListView {
        id: listView
        anchors.fill: parent
        model: peerModel
        clip: true
        currentIndex: -1

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

                // Status dot
                Rectangle {
                    anchors.right: avatar.right
                    anchors.bottom: avatar.bottom
                    width: 10; height: 10; radius: 5
                    color: model.status === "direct"  ? Theme.success
                         : model.status === "relay"   ? Theme.warning
                         : model.status === "group"   ? Theme.accent
                                                      : Theme.textMuted
                    border.color: Theme.surface
                    border.width: 2
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
                        text: model.typing ? "typing…"
                            : (model.lastMessage || (model.convType === "group" ? model.via
                               : model.status === "direct" ? "nearby" : model.status === "relay"
                               ? "in range via relay" : ""))
                        color: model.typing ? Theme.accent : Theme.textMuted
                        font.italic: model.typing
                        font.pixelSize: 11
                        elide: Text.ElideRight
                        visible: text !== ""
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
                    color: model.unreadCount > 0 ? Theme.accent : Theme.surfaceRaised
                    visible: model.unreadCount > 0 || model.pending > 0
                    opacity: model.unreadCount > 0 ? 1 : 0.6

                    Text {
                        id: badgeText
                        anchors.centerIn: parent
                        text: model.unreadCount > 0 ? (model.unreadCount > 99 ? "99+" : model.unreadCount)
                                                    : "◌" + model.pending
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
