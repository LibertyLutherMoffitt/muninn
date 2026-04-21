import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Rectangle {
    id: root
    color: Theme.surface
    height: Math.max(64, edit.implicitHeight + 24)
    
    // Explicitly pass focus down when we receive it
    onActiveFocusChanged: {
        if (activeFocus) {
            edit.forceActiveFocus()
        }
    }

    border.color: Theme.surfaceRaised
    border.width: 1

    property bool writerMode: bridge.isWriter
    property string convId: bridge.activeConvId

    // Vim mode highlight
    Rectangle {
        anchors.fill: parent
        anchors.margins: 1
        color: "transparent"
        border.width: 2
        border.color: {
            if (!vimEditor) return "transparent"
            switch (vimEditor.mode) {
                case "INSERT":      return Theme.accent
                case "VISUAL":
                case "VISUAL_LINE": return Theme.success
                case "CMDLINE":     return "#f59e0b"
                default:            return "transparent"
            }
        }
    }

    // Mode badge
    Rectangle {
        anchors.top: parent.top; anchors.right: parent.right; anchors.margins: 6
        width: modeLabel.implicitWidth + 24; height: 28; radius: 4
        color: {
            if (!vimEditor) return "transparent"
            switch (vimEditor.mode) {
                case "INSERT":      return Theme.accent
                case "VISUAL":
                case "VISUAL_LINE": return Theme.success
                case "CMDLINE":     return "#f59e0b"
                default:            return "transparent"
            }
        }
        visible: vimEditor && vimEditor.mode !== "NORMAL"
        z: 10 

        Text {
            id: modeLabel
            anchors.centerIn: parent
            text: vimEditor ? "-- " + vimEditor.mode + " --" : ""
            color: "white"
            font.pixelSize: 12
            font.bold: true
        }
    }

    // Command line
    Rectangle {
        id: cmdLine
        anchors.bottom: parent.bottom; anchors.left: parent.left; anchors.right: parent.right
        height: 24; color: Theme.bg; z: 4
        visible: vimEditor && vimEditor.mode === "CMDLINE"
        Text {
            anchors.left: parent.left; anchors.leftMargin: 8; anchors.verticalCenter: parent.verticalCenter
            text: (vimEditor && vimEditor.cmdLine) || ""; color: Theme.textPrimary; font.pixelSize: 13; font.family: "monospace"
        }
    }

    Flickable {
        id: flickable
        anchors {
            left: parent.left; leftMargin: 12
            right: parent.right; rightMargin: 12
            top: parent.top; topMargin: 10
            bottom: (cmdLine && cmdLine.visible) ? cmdLine.top : parent.bottom
            bottomMargin: 10
        }
        contentWidth: edit.implicitWidth; contentHeight: edit.implicitHeight; clip: true

        TextEdit {
            id: edit
            width: flickable.width
            readOnly: true
            wrapMode: TextEdit.Wrap
            color: root.writerMode ? Theme.textPrimary : Theme.textMuted
            font.pixelSize: 14; font.family: "monospace"
            
            // Custom Block Cursor
            cursorVisible: false // Hide default thin line
            
            Rectangle {
                id: blockCursor
                width: 8
                height: 16
                color: root.activeFocus || edit.activeFocus ? (vimEditor && vimEditor.mode === "INSERT" ? Theme.accent : "white") : Theme.textMuted
                opacity: 0.7
                visible: true // Always visible in the box
                x: edit.cursorRectangle.x
                y: edit.cursorRectangle.y
                
                // Blink animation
                Timer {
                    interval: 500; running: true; repeat: true
                    onTriggered: blockCursor.visible = !blockCursor.visible
                }
            }

            Text {
                visible: edit.text === "" && vimEditor && vimEditor.mode === "NORMAL"
                text: "Press 'i' to type..."
                color: Theme.textMuted; font: edit.font; opacity: 0.5
            }

            Keys.onPressed: (event) => {
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
        }
    }

    Connections {
        target: vimEditor
        function onBufferUpdated(text, pos) {
            edit.text = text
            edit.cursorPosition = pos
            var rect = edit.cursorRectangle
            flickable.contentY = Math.max(0, rect.y + rect.height - flickable.height + 4)
            // Ensure cursor is visible after movement
            blockCursor.visible = true
        }
        function onSendRequested(text) {
            if (root.convId) {
                bridge.setActiveConv(root.convId)
                bridge.sendMessage(root.convId, text)
            }
        }
    }

    ToolTip {
        visible: !root.writerMode
        text: "Another Muninn instance holds the writer lock."
        delay: 800; parent: root; anchors.centerIn: parent
    }

    MouseArea {
        anchors.fill: parent
        onClicked: {
            edit.forceActiveFocus()
            root.forceActiveFocus()
        }
        propagateComposedEvents: true
        onPressed: (mouse) => { mouse.accepted = false }
    }
}
