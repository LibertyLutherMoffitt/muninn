import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Item {
    id: root
    property string convId: bridge.activeConvId
    readonly property bool isDm: convId.startsWith("dm:")
    readonly property string peerAddr: isDm ? convId.substring(3) : ""
    clip: true

    // Reachability of the peer this conversation is with. Re-read whenever the
    // conversation changes or any peer connects/disconnects, so the header can
    // never disagree with the row in the sidebar it was opened from.
    property var presence: ({ state: "none", text: "", reachable: false })
    function _refreshPresence() {
        // Derive from convId here rather than reading the isDm / peerAddr
        // bindings: onConvIdChanged can run before those re-evaluate, which
        // silently took the group branch for every DM.
        const cid = root.convId
        presence = cid.startsWith("dm:")
            ? bridge.peerPresence(cid.substring(3))
            : cid === ""
                ? ({ state: "none", text: "", reachable: false })
                : ({ state: "group", text: "", reachable: true })
    }
    onConvIdChanged: _refreshPresence()
    Component.onCompleted: _refreshPresence()
    Connections {
        target: bridge
        function onPresenceChanged() { root._refreshPresence() }
        function onProfileUpdated(_addr, _name) { root._refreshPresence() }
    }

    // Pass focus down to composer
    onActiveFocusChanged: if (activeFocus) composer.forceActiveFocus()

    // Window-space position of the composer cursor, used by the cursor-
    // trail overlay so trails can fly out of / into the typing position.
    function cursorPos(target) {
        return composer.cursorPos(target)
    }

    Rectangle {
        anchors.fill: parent
        color: Theme.bg
    }

    ColumnLayout {
        anchors.fill: parent
        spacing: 0

        // ---------------------------------------------------------------
        // Header — who you are talking to, and whether you can reach them
        // ---------------------------------------------------------------
        Rectangle {
            id: header
            Layout.fillWidth: true
            Layout.preferredHeight: Theme.headerHeight
            color: Theme.surfaceRaised
            z: 2

            Rectangle {
                anchors.bottom: parent.bottom
                width: parent.width
                height: 1
                color: Theme.border
            }

            Row {
                anchors.left: parent.left
                anchors.leftMargin: Theme.spaceLg
                anchors.verticalCenter: parent.verticalCenter
                anchors.right: headerRight.left
                anchors.rightMargin: Theme.spaceMd
                spacing: Theme.spaceSm

                PresenceDot {
                    anchors.verticalCenter: parent.verticalCenter
                    presenceState: root.presence.state
                    visible: root.convId !== ""
                }

                Column {
                    anchors.verticalCenter: parent.verticalCenter
                    spacing: 1

                    Text {
                        text: root.convId
                                ? (root.isDm
                                    ? bridge.displayName(root.peerAddr)
                                    : root.convId.substring(6))
                                : "no conversation"
                        color: root.convId ? Theme.textPrimary : Theme.textFaint
                        font.pixelSize: Theme.fontTitle
                        font.bold: true
                    }

                    Text {
                        text: root.presence.text
                        color: root.presence.state === "unreachable"
                            ? Theme.error
                            : root.presence.reachable ? Theme.success : Theme.textMuted
                        font.pixelSize: Theme.fontTiny
                        visible: text !== ""
                    }
                }
            }

            Text {
                id: headerRight
                anchors.right: parent.right
                anchors.rightMargin: Theme.spaceLg
                anchors.verticalCenter: parent.verticalCenter
                text: root.isDm ? root.peerAddr : ""
                color: Theme.textFaint
                font.pixelSize: Theme.fontSmall
                font.family: "monospace"
            }
        }

        // ---------------------------------------------------------------
        // Scrollback — oldest at top, newest at bottom
        // ---------------------------------------------------------------
        Item {
            Layout.fillWidth: true
            Layout.fillHeight: true

            ListView {
                id: msgList
                anchors.fill: parent
                clip: true
                model: msgModel
                spacing: Theme.spaceXs
                topMargin: Theme.spaceMd
                bottomMargin: Theme.spaceMd
                z: 1
                visible: count > 0

                // Day dividers come from the model's daySection role so the
                // append and reload paths cannot disagree about them.
                section.property: "daySection"
                section.criteria: ViewSection.FullString
                section.delegate: Item {
                    width: msgList.width
                    height: 34

                    Rectangle {
                        anchors.centerIn: parent
                        width: dayLabel.implicitWidth + Theme.spaceMd * 2
                        height: 20
                        radius: height / 2
                        color: Theme.surface
                        border.color: Theme.border
                        border.width: 1

                        Text {
                            id: dayLabel
                            anchors.centerIn: parent
                            text: section
                            color: Theme.textMuted
                            font.pixelSize: Theme.fontTiny
                            font.bold: true
                        }
                    }
                }

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
                    height: bubbleRect.height

                    property bool outbound: model.isOutbound
                    // First message of a burst from the same sender.
                    property bool startsRun: model.showSender
                    // Only the first of a run carries the sender name;
                    // repeating it on every line of a burst is noise.
                    property bool showSender: !outbound && startsRun
                    readonly property real maxBubbleWidth: msgList.width * 0.72
                    readonly property real horizontalPadding: Theme.spaceXl
                    readonly property real headerWidth:
                        showSender ? senderText.implicitWidth : 0
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
                        anchors.rightMargin: Theme.spaceMd
                        anchors.leftMargin: Theme.spaceMd

                        width: Math.min(
                            bubble.maxBubbleWidth,
                            Math.max(bubble.contentMin, bodyText.implicitWidth)
                                + bubble.horizontalPadding)
                        height: contentColumn.implicitHeight + Theme.spaceLg

                        color: outbound ? Theme.outgoingBubble : Theme.incomingBubble
                        // Square off the corner nearest the run it belongs to,
                        // so a burst reads as one block rather than as pills.
                        radius: Theme.radiusLg
                        // Flatten the corner facing the message above so a
                        // burst reads as one block rather than as loose pills.
                        topLeftRadius: (!outbound && !bubble.startsRun)
                            ? Theme.radiusSm : Theme.radiusLg
                        topRightRadius: (outbound && !bubble.startsRun)
                            ? Theme.radiusSm : Theme.radiusLg

                        Column {
                            id: contentColumn
                            width: bubbleRect.width - bubble.horizontalPadding
                            anchors.centerIn: parent
                            spacing: Theme.spaceXs

                            Text {
                                id: senderText
                                visible: bubble.showSender
                                height: visible ? implicitHeight : 0
                                text: model.senderName || model.senderMac
                                color: Theme.accent
                                font.pixelSize: Theme.fontSmall
                                font.bold: true
                                wrapMode: Text.NoWrap
                            }

                            Text {
                                id: bodyText
                                text: model.text
                                color: Theme.textPrimary
                                font.pixelSize: Theme.fontBody
                                wrapMode: Text.Wrap
                                width: parent.width
                                lineHeight: 1.15
                                textFormat: Text.PlainText
                            }

                            Row {
                                id: footerRow
                                spacing: Theme.spaceXs + 2
                                anchors.right: parent.right

                                Text {
                                    id: timeText
                                    text: Qt.formatTime(
                                        new Date(model.timestamp * 1000), "HH:mm")
                                    color: Theme.textFaint
                                    font.pixelSize: Theme.fontTiny
                                }

                                Text {
                                    id: ackText
                                    visible: outbound
                                    text: model.ackState === "read"  ? "✓✓"
                                        : model.ackState === "acked" ? "✓"
                                                                     : "◑"
                                    color: model.ackState === "read"
                                        ? Theme.success : Theme.textFaint
                                    font.pixelSize: Theme.fontTiny
                                }
                            }
                        }
                    }
                }
            }

            // Nothing to show: say what to do rather than leaving a void.
            EmptyState {
                anchors.centerIn: parent
                visible: msgList.count === 0 && root.convId === ""
                glyph: "◌"
                title: "No conversation open"
                subtitle: bridge.connectedPeerCount > 0
                    ? "Pick a peer on the left to start talking."
                    : "Muninn is looking for peers nearby. Anyone running it in range shows up on the left."
                hint: "␣f  palette   ·   :peers   ·   :whoami"
            }

            EmptyState {
                anchors.centerIn: parent
                visible: msgList.count === 0 && root.convId !== ""
                glyph: "✉"
                title: root.isDm
                    ? "No messages with " + bridge.displayName(root.peerAddr) + " yet"
                    : "No messages in this group yet"
                subtitle: root.presence.reachable || !root.isDm
                    ? "Messages are encrypted end to end and go straight over Bluetooth."
                    : "They are not reachable right now — anything you send is queued and delivered when they come back."
                hint: "press 'i' to type"
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
