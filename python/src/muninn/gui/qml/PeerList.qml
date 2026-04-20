import QtQuick
import QtQuick.Controls

Rectangle {
    id: root
    color: Theme.surface

    property string activeConvId: bridge.activeConvId
    property alias listView: listView

    signal convSelected(string convId)

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

                // peer-connect/disconnect pulse
                ColorAnimation on color {
                    id: pulseAnim
                    duration: 200
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
                         : model.status === "relay"   ? "#f59e0b"
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
                        text: model.lastMessage || ""
                        color: Theme.textMuted
                        font.pixelSize: 11
                        elide: Text.ElideRight
                        visible: model.lastMessage !== ""
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
