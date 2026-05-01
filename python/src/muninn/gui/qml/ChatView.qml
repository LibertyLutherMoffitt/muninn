import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Item {
    id: root
    property string convId: bridge.activeConvId
    clip: true

    // Pass focus down to composer
    onActiveFocusChanged: if (activeFocus) composer.forceActiveFocus()

    // Window-space position of the composer cursor, used by the cursor-
    // trail overlay so trails can fly out of / into the typing position.
    function cursorPos(target) {
        return composer.cursorPos(target)
    }

    // Background
    Rectangle {
        anchors.fill: parent
        color: Theme.bg
    }

    ColumnLayout {
        anchors.fill: parent
        spacing: 0

        // Header
        Rectangle {
            id: header
            Layout.fillWidth: true
            Layout.preferredHeight: 48
            color: Theme.surfaceRaised
            z: 2

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
            }
        }

        // Scrollback (top-to-bottom: oldest at top, newest at bottom).
        ListView {
            id: msgList
            Layout.fillWidth: true
            Layout.fillHeight: true
            clip: true
            model: msgModel
            spacing: 12
            z: 1

            // Auto-scroll to bottom on new messages or model reset, but only
            // when user is already near the bottom — otherwise leave their
            // scroll position alone. Switching conversations always snaps
            // to the bottom so the most recent message is visible.
            property bool _atBottom: true
            onContentYChanged: {
                if (visibleArea.heightRatio >= 1.0) {
                    _atBottom = true
                    return
                }
                _atBottom = (contentY + height) >= (contentHeight - 24)
            }
            onCountChanged: if (_atBottom) Qt.callLater(positionViewAtEnd)
            Component.onCompleted: Qt.callLater(positionViewAtEnd)

            Connections {
                target: bridge
                function onActiveConvChanged(_cid) {
                    msgList._atBottom = true
                    Qt.callLater(msgList.positionViewAtEnd)
                }
            }

            ScrollBar.vertical: ScrollBar { policy: ScrollBar.AsNeeded }

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
                height: bubbleRect.height + 4

                property bool outbound: model.isOutbound
                readonly property real maxBubbleWidth: msgList.width * 0.75
                readonly property real horizontalPadding: 24
                // Floor for content width: whichever of the header / footer
                // rows is widest must fit without elision.
                readonly property real headerWidth:
                    outbound ? 0 : senderText.implicitWidth
                readonly property real footerWidth:
                    timeText.implicitWidth
                    + (outbound ? ackText.implicitWidth + footerRow.spacing : 0)
                readonly property real contentMin:
                    Math.min(maxBubbleWidth - horizontalPadding,
                             Math.max(headerWidth, footerWidth))

                Rectangle {
                    id: bubbleRect
                    anchors.right: outbound ? parent.right : undefined
                    anchors.left: outbound ? undefined : parent.left
                    anchors.rightMargin: 12
                    anchors.leftMargin: 12

                    width: Math.min(
                        bubble.maxBubbleWidth,
                        Math.max(bubble.contentMin, bodyText.implicitWidth)
                            + bubble.horizontalPadding)
                    height: contentColumn.implicitHeight + 16

                    color: outbound ? Theme.outgoingBubble : Theme.incomingBubble
                    radius: 12

                    Column {
                        id: contentColumn
                        width: bubbleRect.width - bubble.horizontalPadding
                        anchors.centerIn: parent
                        spacing: 4

                        Text {
                            id: senderText
                            visible: !outbound
                            text: model.senderName || model.senderMac
                            color: Theme.accent
                            font.pixelSize: 11
                            font.bold: true
                            wrapMode: Text.NoWrap
                        }

                        Text {
                            id: bodyText
                            text: model.text
                            color: Theme.textPrimary
                            font.pixelSize: 13
                            wrapMode: Text.Wrap
                            width: parent.width
                            lineHeight: 1.1
                        }

                        Row {
                            id: footerRow
                            spacing: 6
                            anchors.right: parent.right

                            Text {
                                id: timeText
                                text: Qt.formatTime(new Date(model.timestamp * 1000), "HH:mm")
                                color: Theme.textMuted
                                font.pixelSize: 10
                            }

                            Text {
                                id: ackText
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

        // Composer (fixed at bottom)
        Composer {
            id: composer
            Layout.fillWidth: true
            Layout.preferredHeight: height
            convId: root.convId
            z: 2
            
            // Pass focus down to its own internal editor
            onActiveFocusChanged: if (activeFocus) forceActiveFocus()
        }
    }
}
