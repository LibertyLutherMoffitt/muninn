import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Item {
    id: root
    property string convId: bridge.activeConvId

    // Fade + slide on conv switch
    Behavior on opacity {
        NumberAnimation { duration: 80; easing.type: Easing.OutQuad }
    }

    // Header
    Rectangle {
        id: header
        anchors.top: parent.top
        anchors.left: parent.left
        anchors.right: parent.right
        height: 48
        color: Theme.surfaceRaised

        Text {
            anchors.left: parent.left
            anchors.leftMargin: 16
            anchors.verticalCenter: parent.verticalCenter
            text: root.convId
                    ? (root.convId.startsWith("dm:")
                        ? bridge.displayName(root.convId.substring(3))
                        : root.convId.substring(6))
                    : "— no conversation —"
            color: Theme.textPrimary
            font.pixelSize: 15
            font.bold: true
        }

        Text {
            anchors.right: parent.right
            anchors.rightMargin: 16
            anchors.verticalCenter: parent.verticalCenter
            text: root.convId && root.convId.startsWith("dm:")
                        ? root.convId.substring(3)
                        : ""
            color: Theme.textMuted
            font.pixelSize: 11
            font.family: "monospace"
        }
    }

    // Scrollback
    ListView {
        id: msgList
        anchors.top: header.bottom
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.bottom: composer.top
        anchors.bottomMargin: 0
        clip: true
        model: msgModel
        spacing: 4
        verticalLayoutDirection: ListView.BottomToTop

        ScrollBar.vertical: ScrollBar { policy: ScrollBar.AsNeeded }

        // Scroll-to-bottom on new message
        onCountChanged: {
            if (atYEnd || count <= 1) positionViewAtEnd()
        }

        Connections {
            target: vimEditor
            function onScrollRequested(fraction) {
                var delta = fraction * msgList.height
                msgList.contentY = Math.max(
                    msgList.originY,
                    Math.min(msgList.contentY + delta,
                             msgList.originY + msgList.contentHeight
                             - msgList.height))
            }
        }

        delegate: Item {
            id: bubble
            width: msgList.width
            height: bubbleRect.height + 8

            property bool outbound: model.isOutbound

            // Fade + slide-up on arrival
            opacity: 0
            y: 10
            Component.onCompleted: appearAnim.start()
            ParallelAnimation {
                id: appearAnim
                NumberAnimation {
                    target: bubble; property: "opacity"
                    from: 0; to: 1
                    duration: 120; easing.type: Easing.OutQuad
                }
                NumberAnimation {
                    target: bubble; property: "y"
                    from: 10; to: 0
                    duration: 120; easing.type: Easing.OutQuad
                }
            }

            Rectangle {
                id: bubbleRect
                anchors.right: outbound ? parent.right : undefined
                anchors.left: outbound ? undefined : parent.left
                anchors.margins: 12
                width: Math.min(bubbleContent.implicitWidth + 20,
                                bubble.width * 0.75)
                height: bubbleContent.implicitHeight + 14
                color: outbound ? Theme.outgoingBubble : Theme.incomingBubble
                radius: 10

                Column {
                    id: bubbleContent
                    anchors {
                        left: parent.left; leftMargin: 10
                        right: parent.right; rightMargin: 10
                        top: parent.top; topMargin: 7
                    }
                    spacing: 2

                    Text {
                        visible: !outbound
                        text: model.senderName || model.senderMac
                        color: Theme.accent
                        font.pixelSize: 11
                        font.bold: true
                        width: parent.width
                        elide: Text.ElideRight
                    }

                    Text {
                        text: model.text
                        color: Theme.textPrimary
                        font.pixelSize: 13
                        wrapMode: Text.WrapAtWordBoundaryOrAnywhere
                        width: parent.width
                    }

                    Row {
                        spacing: 6
                        anchors.right: parent.right

                        Text {
                            text: Qt.formatTime(new Date(model.timestamp * 1000), "HH:mm")
                            color: Theme.textMuted
                            font.pixelSize: 10
                        }

                        Text {
                            visible: outbound
                            text: model.ackState === "read"  ? "✓✓"
                                : model.ackState === "acked" ? "✓"
                                                             : "◑"
                            color: model.ackState === "read" ? Theme.success
                                                              : Theme.textMuted
                            font.pixelSize: 10
                        }
                    }
                }
            }
        }
    }

    // Composer
    Composer {
        id: composer
        anchors.bottom: parent.bottom
        anchors.left: parent.left
        anchors.right: parent.right
        convId: root.convId
    }
}
