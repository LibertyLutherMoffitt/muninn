import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Item {
    id: root
    property string convId: bridge.activeConvId
    clip: true
    
    // Pass focus down to composer
    onActiveFocusChanged: if (activeFocus) composer.forceActiveFocus()

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
                font.family: "monospace"
            }
        }

        // Scrollback
        ListView {
            id: msgList
            Layout.fillWidth: true
            Layout.fillHeight: true
            clip: true
            model: msgModel
            spacing: 12
            verticalLayoutDirection: ListView.BottomToTop
            z: 1

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

                Rectangle {
                    id: bubbleRect
                    anchors.right: outbound ? parent.right : undefined
                    anchors.left: outbound ? undefined : parent.left
                    anchors.rightMargin: 12
                    anchors.leftMargin: 12
                    
                    width: Math.min(msgList.width * 0.75, contentColumn.implicitWidth + 24)
                    height: contentColumn.implicitHeight + 16
                    
                    color: outbound ? Theme.outgoingBubble : Theme.incomingBubble
                    radius: 12

                    Column {
                        id: contentColumn
                        width: Math.min(msgList.width * 0.75 - 24, bodyText.implicitWidth)
                        anchors.centerIn: parent
                        spacing: 4

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
                            id: bodyText
                            text: model.text
                            color: Theme.textPrimary
                            font.pixelSize: 13
                            wrapMode: Text.Wrap
                            width: parent.width
                            lineHeight: 1.1
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
