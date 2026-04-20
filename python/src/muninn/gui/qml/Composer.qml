import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Rectangle {
    id: root
    color: Theme.surface
    height: Math.max(56, edit.implicitHeight + 20)

    property bool writerMode: bridge.isWriter
    property string convId: bridge.activeConvId

    // Border color animates on Vim mode change
    border.width: 1
    border.color: {
        switch (vimEditor.mode) {
            case "INSERT":      return Theme.accent
            case "VISUAL":
            case "VISUAL_LINE": return Theme.success
            case "CMDLINE":     return "#f59e0b"
            default:            return Qt.darker(Theme.surfaceRaised, 1.5)
        }
    }
    Behavior on border.color {
        ColorAnimation { duration: 60; easing.type: Easing.OutQuad }
    }

    // Mode badge (top-right)
    Rectangle {
        anchors.top: parent.top
        anchors.right: parent.right
        anchors.margins: 4
        width: modeLabel.implicitWidth + 12
        height: 18; radius: 4
        color: {
            switch (vimEditor.mode) {
                case "INSERT":      return Theme.accent
                case "VISUAL":
                case "VISUAL_LINE": return Theme.success
                case "CMDLINE":     return "#f59e0b"
                default:            return "transparent"
            }
        }
        visible: vimEditor.mode !== "NORMAL"

        Text {
            id: modeLabel
            anchors.centerIn: parent
            text: vimEditor.mode
            color: "white"
            font.pixelSize: 10
            font.bold: true
        }
    }

    // Cmd-line bar (shown when CMDLINE)
    Rectangle {
        id: cmdLine
        anchors.bottom: parent.bottom
        anchors.left: parent.left
        anchors.right: parent.right
        height: 24
        color: Theme.bg
        visible: vimEditor.mode === "CMDLINE"

        Text {
            anchors.left: parent.left
            anchors.leftMargin: 8
            anchors.verticalCenter: parent.verticalCenter
            text: vimEditor.cmdLine || ""
            color: Theme.textPrimary
            font.pixelSize: 13
            font.family: "monospace"
        }
    }

    // Read-only TextEdit driven by VimEditor state machine
    Flickable {
        id: flickable
        anchors {
            left: parent.left; leftMargin: 10
            right: parent.right; rightMargin: 10
            top: parent.top; topMargin: 8
            bottom: cmdLine.visible ? cmdLine.top : parent.bottom
            bottomMargin: 8
        }
        contentWidth: edit.implicitWidth
        contentHeight: edit.implicitHeight
        clip: true

        TextEdit {
            id: edit
            width: flickable.width
            readOnly: true
            wrapMode: TextEdit.Wrap
            color: root.writerMode ? Theme.textPrimary : Theme.textMuted
            selectionColor: Qt.rgba(0.49, 0.23, 0.93, 0.4)
            font.pixelSize: 14
            font.family: "monospace"
            cursorVisible: vimEditor.mode !== "CMDLINE" && root.activeFocus

            Keys.onPressed: function(event) {
                if (!root.writerMode) { event.accepted = true; return }
                vimEditor.handleKey(
                    event.text,
                    event.key,
                    !!(event.modifiers & Qt.ControlModifier),
                    !!(event.modifiers & Qt.ShiftModifier),
                    !!(event.modifiers & Qt.AltModifier)
                )
                event.accepted = true
            }
            Keys.forwardTo: [root]
        }
    }

    // Apply buffer updates from Python
    Connections {
        target: vimEditor
        function onBufferUpdated(text, pos) {
            edit.text = text
            edit.cursorPosition = pos
            // Auto-scroll to cursor
            var rect = edit.cursorRectangle
            flickable.contentY = Math.max(0, rect.y + rect.height
                - flickable.height + 4)
        }
        function onSelectionChanged(start, end) {
            edit.select(start, end)
        }
        function onSelectionCleared() {
            edit.deselect()
        }
    }

    // Send from QML side (triggered by vimEditor.sendRequested)
    Connections {
        target: vimEditor
        function onSendRequested(text) {
            if (root.convId)
                bridge.sendMessage(root.convId, text)
        }
    }

    // Read-only tooltip
    ToolTip {
        visible: !root.writerMode
        text: "Another Muninn instance holds the writer lock."
        delay: 800
        parent: root
        anchors.centerIn: parent
    }

    MouseArea {
        anchors.fill: parent
        onClicked: {
            edit.forceActiveFocus()
            root.forceActiveFocus()
        }
        propagateComposedEvents: true
        onPressed: function(mouse) { mouse.accepted = false }
    }
}
