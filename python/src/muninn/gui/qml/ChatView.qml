import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Item {
    id: root
    property string convId: bridge.activeConvId
    clip: true

    signal reactRequested(string msgId)

    onActiveFocusChanged: if (activeFocus) composer.forceActiveFocus()

    function cursorPos(target) { return composer.cursorPos(target) }

    Rectangle { anchors.fill: parent; color: Theme.bg }

    ColumnLayout {
        anchors.fill: parent
        spacing: 0

        // ---- header ------------------------------------------------------
        Rectangle {
            Layout.fillWidth: true
            Layout.preferredHeight: 52
            color: Theme.surfaceRaised
            z: 2
            MouseArea {
                anchors.fill: parent
                enabled: root.convId.startsWith("group:")
                cursorShape: enabled ? Qt.PointingHandCursor : Qt.ArrowCursor
                onClicked: bridge.runCommand("members")
            }
            Column {
                anchors.left: parent.left; anchors.leftMargin: 16
                anchors.verticalCenter: parent.verticalCenter
                spacing: 2
                Text {
                    text: root.convId ? bridge.activeTitle : "— no conversation —"
                    color: Theme.textPrimary; font.pixelSize: 15; font.bold: true
                }
                Text {
                    text: bridge.typingText || bridge.activeSubtitle
                    color: bridge.typingText ? Theme.accent : Theme.textMuted
                    font.pixelSize: 11; font.italic: !!bridge.typingText
                    visible: text !== ""
                    width: root.width - 220; elide: Text.ElideRight
                }
            }
            Row {
                anchors.right: parent.right; anchors.rightMargin: 12
                anchors.verticalCenter: parent.verticalCenter
                spacing: 6
                visible: root.convId !== ""
                HeaderButton { label: "📎"; tip: "Send file (Ctrl+O)"; onClicked: bridge.runCommand("file") }
                HeaderButton { label: "😊"; tip: "Emoji (Ctrl+E)"; onClicked: bridge.runCommand("emoji") }
                HeaderButton { label: "🎮"; tip: "Games"; onClicked: bridge.runCommand("game") }
            }
        }

        // ---- messages ----------------------------------------------------
        ListView {
            id: msgList
            Layout.fillWidth: true
            Layout.fillHeight: true
            clip: true
            model: msgModel
            spacing: 4
            topMargin: 8; bottomMargin: 8
            z: 1
            cacheBuffer: 2000

            property bool _atBottom: true
            onContentYChanged: _atBottom = visibleArea.heightRatio >= 1.0
                || (contentY + height) >= (contentHeight - 40)
            onCountChanged: if (_atBottom) Qt.callLater(positionViewAtEnd)
            Component.onCompleted: Qt.callLater(positionViewAtEnd)

            Connections {
                target: bridge
                function onActiveConvChanged(_c) {
                    msgList._atBottom = true
                    Qt.callLater(msgList.positionViewAtEnd)
                }
                function onSelectionChanged(row) {
                    if (row >= 0) msgList.positionViewAtIndex(row, ListView.Contain)
                }
            }
            Connections {
                target: vimEditor
                function onScrollRequested(fraction) {
                    msgList.contentY = Math.max(msgList.originY - msgList.topMargin,
                        Math.min(msgList.contentY + fraction * msgList.height,
                                 msgList.originY + msgList.contentHeight - msgList.height + msgList.bottomMargin))
                }
            }
            ScrollBar.vertical: ScrollBar { policy: ScrollBar.AsNeeded }

            delegate: Item {
                id: row
                width: msgList.width
                height: col.implicitHeight + 4
                property bool outbound: model.isOutbound
                property bool system: model.kind === "system"
                property real maxW: Math.min(msgList.width * 0.72, 560)

                Column {
                    id: col
                    width: parent.width
                    spacing: 4

                    // day separator
                    Item {
                        width: parent.width; height: model.dayLabel ? 28 : 0
                        visible: model.dayLabel !== ""
                        Rectangle {
                            anchors.centerIn: parent
                            width: dayText.implicitWidth + 20; height: 20; radius: 10
                            color: Theme.surfaceRaised
                            Text { id: dayText; anchors.centerIn: parent; text: model.dayLabel
                                   color: Theme.textMuted; font.pixelSize: 10 }
                        }
                    }

                    // system line
                    Text {
                        visible: row.system
                        width: parent.width
                        horizontalAlignment: Text.AlignHCenter
                        text: model.text
                        color: Theme.textMuted; font.pixelSize: 11; font.italic: true
                        wrapMode: Text.Wrap
                        height: row.system ? implicitHeight + 6 : 0
                    }

                    // bubble
                    Item {
                        visible: !row.system
                        width: parent.width
                        height: row.system ? 0 : bubble.height + (reactRow.visible ? reactRow.height - 6 : 0)

                        Rectangle {
                            id: bubble
                            anchors.right: row.outbound ? parent.right : undefined
                            anchors.left: row.outbound ? undefined : parent.left
                            anchors.rightMargin: 14; anchors.leftMargin: 14
                            width: Math.min(row.maxW, Math.max(inner.implicitW, 90) + 24)
                            height: inner.implicitHeight + 16
                            radius: 12
                            color: row.outbound ? Theme.outgoingBubble : Theme.incomingBubble
                            border.width: model.selected ? 2 : 0
                            border.color: Theme.accent

                            Column {
                                id: inner
                                x: 12; y: 8
                                width: bubble.width - 24
                                spacing: 4
                                property real implicitW: Math.max(
                                    senderText.visible ? senderText.implicitWidth : 0,
                                    bodyText.visible ? bodyText.implicitWidth : 0,
                                    quote.visible ? Math.min(quoteText.implicitWidth + 16, 400) : 0,
                                    fileCard.visible ? 260 : 0,
                                    image.visible ? image.paintedW : 0,
                                    footer.implicitWidth)

                                Text {
                                    id: senderText
                                    visible: model.showSender
                                    text: model.senderName
                                    color: Theme.accent; font.pixelSize: 11; font.bold: true
                                }

                                // quoted reply
                                Rectangle {
                                    id: quote
                                    visible: model.replyId !== ""
                                    width: parent.width
                                    height: visible ? quoteCol.implicitHeight + 8 : 0
                                    radius: 6
                                    color: Qt.rgba(0, 0, 0, 0.25)
                                    Rectangle { width: 3; height: parent.height; radius: 2; color: Theme.accent }
                                    Column {
                                        id: quoteCol
                                        x: 10; y: 4; width: parent.width - 14
                                        Text { text: model.replyName; color: Theme.accent; font.pixelSize: 10; font.bold: true }
                                        Text { id: quoteText; text: model.replyText; color: Theme.textMuted
                                               font.pixelSize: 11; elide: Text.ElideRight; width: parent.width
                                               maximumLineCount: 2; wrapMode: Text.Wrap }
                                    }
                                    MouseArea {
                                        anchors.fill: parent
                                        cursorShape: Qt.PointingHandCursor
                                        onClicked: bridge.selectMessage(model.replyId)
                                    }
                                }

                                // image (thumbnail until downloaded, then full / animated)
                                Item {
                                    id: image
                                    visible: model.isImage && !model.deleted
                                    property real ratio: shown.implicitWidth > 0
                                        ? shown.implicitHeight / shown.implicitWidth : 0.66
                                    property real paintedW: Math.min(300, row.maxW - 24)
                                    width: paintedW
                                    height: visible ? Math.max(48, Math.min(360, paintedW * ratio)) : 0
                                    property bool full: model.fileState === "complete"
                                    AnimatedImage {
                                        id: shown
                                        anchors.fill: parent
                                        fillMode: Image.PreserveAspectFit
                                        horizontalAlignment: Image.AlignLeft
                                        source: image.full ? model.fileUrl : model.thumbUrl
                                        playing: image.full && model.isGif
                                        asynchronous: true
                                        cache: false
                                    }
                                    Rectangle {
                                        anchors.fill: parent; color: Qt.rgba(0,0,0,0.35)
                                        visible: !image.full; radius: 6
                                        Text {
                                            anchors.centerIn: parent; color: "white"; font.pixelSize: 12
                                            text: model.fileState === "downloading"
                                                ? Math.round(model.fileProgress * 100) + "%"
                                                : "⬇ " + model.fileSize
                                        }
                                    }
                                    MouseArea {
                                        anchors.fill: parent
                                        cursorShape: Qt.PointingHandCursor
                                        onClicked: bridge.openFile(model.msgId)
                                    }
                                }

                                // generic file card
                                Rectangle {
                                    id: fileCard
                                    visible: model.kind === "file" && !model.isImage && !model.deleted
                                    width: parent.width
                                    height: visible ? 52 : 0
                                    radius: 8
                                    color: Qt.rgba(0, 0, 0, 0.25)
                                    Text {
                                        x: 10; anchors.verticalCenter: parent.verticalCenter
                                        text: model.fileName.endsWith(".js") ? "🎮" : "📄"
                                        font.pixelSize: 22
                                    }
                                    Column {
                                        x: 44; anchors.verticalCenter: parent.verticalCenter
                                        width: parent.width - 54
                                        Text { text: model.fileName; color: Theme.textPrimary; font.pixelSize: 12
                                               elide: Text.ElideMiddle; width: parent.width }
                                        Text {
                                            color: Theme.textMuted; font.pixelSize: 10
                                            text: model.fileSize + " · " + (
                                                model.fileState === "complete" ? (model.fileName.endsWith(".js")
                                                    ? "right-click → install game" : "click to open")
                                                : model.fileState === "downloading"
                                                    ? "downloading " + Math.round(model.fileProgress * 100) + "%"
                                                    : "click to download")
                                        }
                                    }
                                    Rectangle {
                                        anchors.bottom: parent.bottom; height: 2; radius: 1
                                        width: parent.width * model.fileProgress
                                        color: Theme.accent
                                        visible: model.fileState === "downloading"
                                    }
                                    MouseArea {
                                        anchors.fill: parent
                                        cursorShape: Qt.PointingHandCursor
                                        onClicked: bridge.openFile(model.msgId)
                                    }
                                }

                                Text {
                                    id: bodyText
                                    visible: text !== ""
                                    width: parent.width
                                    text: model.deleted ? "message deleted" : model.text
                                    color: model.deleted ? Theme.textMuted : Theme.textPrimary
                                    font.italic: model.deleted
                                    font.pixelSize: 13
                                    wrapMode: Text.Wrap
                                    textFormat: Text.PlainText
                                    lineHeight: 1.1
                                }

                                Row {
                                    id: footer
                                    spacing: 6
                                    anchors.right: parent.right
                                    Text { visible: model.edited && !model.deleted; text: "edited"
                                           color: Theme.textMuted; font.pixelSize: 9; font.italic: true }
                                    Text { text: Qt.formatTime(new Date(model.timestamp * 1000), "HH:mm")
                                           color: Theme.textMuted; font.pixelSize: 10 }
                                    Text {
                                        visible: row.outbound
                                        text: model.ackState === "read" ? "✓✓"
                                            : model.ackState === "delivered" ? "✓" : "◌"
                                        color: model.ackState === "read" ? Theme.success : Theme.textMuted
                                        font.pixelSize: 10
                                        ToolTip.visible: tickArea.containsMouse
                                        ToolTip.text: model.ackState === "read" ? "read"
                                            : model.ackState === "delivered" ? "delivered"
                                            : "waiting — will be delivered when they're in range"
                                        MouseArea { id: tickArea; anchors.fill: parent; hoverEnabled: true }
                                    }
                                }
                            }

                            MouseArea {
                                id: bubbleMouse
                                anchors.fill: parent
                                hoverEnabled: true
                                acceptedButtons: Qt.RightButton
                                propagateComposedEvents: true
                                onClicked: (m) => { bridge.selectMessage(model.msgId); ctx.popup() }
                            }
                        }

                        // reactions under the bubble
                        Row {
                            id: reactRow
                            visible: model.reactions.length > 0
                            spacing: 4
                            anchors.top: bubble.bottom; anchors.topMargin: -6
                            anchors.right: row.outbound ? bubble.right : undefined
                            anchors.left: row.outbound ? undefined : bubble.left
                            anchors.leftMargin: 8; anchors.rightMargin: 8
                            Repeater {
                                model: reactions
                                Rectangle {
                                    height: 20; radius: 10
                                    width: chip.implicitWidth + 12
                                    color: modelData.mine ? Theme.accentSoft : Theme.surfaceRaised
                                    border.color: modelData.mine ? Theme.accent : Theme.bg
                                    border.width: 1
                                    Text {
                                        id: chip; anchors.centerIn: parent; font.pixelSize: 11
                                        color: Theme.textPrimary
                                        text: modelData.emoji + (modelData.count > 1 ? " " + modelData.count : "")
                                    }
                                    ToolTip.visible: chipMouse.containsMouse
                                    ToolTip.text: modelData.who
                                    MouseArea {
                                        id: chipMouse; anchors.fill: parent; hoverEnabled: true
                                        onClicked: bridge.toggleReaction(model.msgId, modelData.emoji)
                                    }
                                }
                            }
                        }

                        // hover action bar
                        Row {
                            id: actions
                            spacing: 2
                            visible: (bubbleMouse.containsMouse || barHover.hovered) && !model.deleted
                            anchors.verticalCenter: bubble.verticalCenter
                            anchors.right: row.outbound ? bubble.left : undefined
                            anchors.left: row.outbound ? undefined : bubble.right
                            anchors.margins: 6
                            HoverHandler { id: barHover }
                            Repeater {
                                model: ["👍", "❤️", "😂"]
                                ActionChip { label: modelData; onClicked: bridge.toggleReaction(model.msgId, modelData) }
                            }
                            ActionChip { label: "＋"; tip: "react"; onClicked: { bridge.selectMessage(model.msgId); root.reactRequested(model.msgId) } }
                            ActionChip { label: "↩"; tip: "reply"; onClicked: bridge.replyTo(model.msgId) }
                        }

                        Menu {
                            id: ctx
                            MenuItem { text: "Reply"; onTriggered: bridge.replyTo(model.msgId) }
                            MenuItem { text: "React…"; onTriggered: root.reactRequested(model.msgId) }
                            MenuItem { text: "Copy text"; enabled: model.text !== ""; onTriggered: bridge.copyText(model.msgId) }
                            MenuItem { text: "Edit"; visible: row.outbound && model.kind === "text"; height: visible ? implicitHeight : 0
                                       onTriggered: bridge.startEdit(model.msgId) }
                            MenuItem { text: "Delete for everyone"; visible: row.outbound; height: visible ? implicitHeight : 0
                                       onTriggered: bridge.deleteMessage(model.msgId) }
                            MenuItem { text: model.fileState === "complete" ? "Open file" : "Download"
                                       visible: model.kind === "file"; height: visible ? implicitHeight : 0
                                       onTriggered: bridge.openFile(model.msgId) }
                            MenuItem { text: "Show in folder"; visible: model.fileState === "complete"
                                       height: visible ? implicitHeight : 0; onTriggered: bridge.openFolder(model.msgId) }
                            MenuItem { text: "Save to my stickers"; visible: model.fileState === "complete" && model.isImage
                                       height: visible ? implicitHeight : 0; onTriggered: bridge.saveSticker(model.msgId) }
                            MenuItem { text: "Install game"; visible: model.fileState === "complete" && model.fileName.endsWith(".js")
                                       height: visible ? implicitHeight : 0; onTriggered: bridge.installGame(model.msgId) }
                        }
                    }
                }
            }
        }

        // empty state
        Text {
            visible: root.convId === ""
            Layout.alignment: Qt.AlignHCenter
            text: "Pick a conversation on the left, or press <space>s to find people nearby."
            color: Theme.textMuted; font.pixelSize: 12
        }

        // reply / edit banner
        Rectangle {
            Layout.fillWidth: true
            Layout.preferredHeight: visible ? 30 : 0
            visible: bridge.replyPreview !== "" || bridge.editPreview !== ""
            color: Theme.surfaceRaised
            Rectangle { width: 3; height: parent.height; color: Theme.accent }
            Text {
                x: 14; anchors.verticalCenter: parent.verticalCenter
                width: parent.width - 60; elide: Text.ElideRight
                text: bridge.editPreview || bridge.replyPreview
                color: Theme.textMuted; font.pixelSize: 11
            }
            Text {
                anchors.right: parent.right; anchors.rightMargin: 12
                anchors.verticalCenter: parent.verticalCenter
                text: "✕"; color: Theme.textMuted; font.pixelSize: 13
                MouseArea { anchors.fill: parent; anchors.margins: -6; onClicked: bridge.cancelReply() }
            }
        }

        Composer {
            id: composer
            Layout.fillWidth: true
            Layout.preferredHeight: height
            convId: root.convId
            z: 2
            onActiveFocusChanged: if (activeFocus) forceActiveFocus()
        }
    }

    component HeaderButton: Rectangle {
        property string label
        property string tip
        signal clicked()
        width: 30; height: 30; radius: 6
        color: hb.containsMouse ? Theme.surface : "transparent"
        Text { anchors.centerIn: parent; text: parent.label; font.pixelSize: 15 }
        ToolTip.visible: hb.containsMouse && tip !== ""
        ToolTip.text: tip
        MouseArea { id: hb; anchors.fill: parent; hoverEnabled: true; onClicked: parent.clicked() }
    }

    component ActionChip: Rectangle {
        property string label
        property string tip
        signal clicked()
        width: 26; height: 26; radius: 13
        color: ac.containsMouse ? Theme.accentSoft : Theme.surfaceRaised
        Text { anchors.centerIn: parent; text: parent.label; font.pixelSize: 12; color: Theme.textPrimary }
        MouseArea { id: ac; anchors.fill: parent; hoverEnabled: true; onClicked: parent.clicked() }
    }
}
