import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

ApplicationWindow {
    id: root
    title: "Muninn"
    width: 960
    height: 640
    minimumWidth: 800
    minimumHeight: 500
    visible: true
    color: "#0f1115"

    // Helper to check if we are in navigation mode
    property bool isNormalMode: vimEditor && vimEditor.mode === "NORMAL"

    // Initial focus on the chat pane so Vim commands work immediately
    Component.onCompleted: {
        chatPane.forceActiveFocus()
    }

    // ------------------------------------------------------------------
    // Main Layout
    // ------------------------------------------------------------------

    ColumnLayout {
        anchors.fill: parent
        spacing: 0

        RowLayout {
            id: mainLayout
            Layout.fillWidth: true
            Layout.fillHeight: true
            spacing: 0

            PeerList {
                id: peerList
                Layout.preferredWidth: 240
                Layout.fillHeight: true
                onConvSelected: (cid) => {
                    bridge.setActiveConv(cid)
                    chatPane.forceActiveFocus()
                }
            }

            Rectangle {
                Layout.preferredWidth: 1
                Layout.fillHeight: true
                color: Theme.surfaceRaised
            }

            ChatView {
                id: chatPane
                Layout.fillWidth: true
                Layout.fillHeight: true
                
                // Clicking the chat area always restores Vim command focus
                MouseArea {
                    anchors.fill: parent
                    onClicked: chatPane.forceActiveFocus()
                    z: -1 // Behind messages
                }
            }
        }

        Rectangle {
            id: statusBar
            Layout.fillWidth: true
            Layout.preferredHeight: 24
            color: Theme.surfaceRaised
            z: 5

            Row {
                anchors.fill: parent
                anchors.leftMargin: 12
                anchors.rightMargin: 12
                spacing: 20

                Text {
                    anchors.verticalCenter: parent.verticalCenter
                    text: bridge.localName ? "nick: " + bridge.localName : "nick: (none)"
                    color: Theme.textMuted
                    font.pixelSize: 11
                }
                Text {
                    anchors.verticalCenter: parent.verticalCenter
                    text: "peers: " + bridge.connectedPeerCount + " connected"
                    color: Theme.textMuted
                    font.pixelSize: 11
                }
                Text {
                    anchors.verticalCenter: parent.verticalCenter
                    text: "mode: " + (bridge.isWriter ? "WRITER" : "READER")
                    color: bridge.isWriter ? Theme.success : Theme.textMuted
                    font.pixelSize: 11
                    font.bold: bridge.isWriter
                }
            }
        }
    }

    // ------------------------------------------------------------------
    // Global Shortcuts
    // ------------------------------------------------------------------

    Shortcut { sequence: "Ctrl+P"; onActivated: cmdPalette.open() }
    
    Shortcut { 
        sequence: "Esc"
        onActivated: {
            vimEditor.handleKey("", Qt.Key_Escape, false, false, false)
            chatPane.forceActiveFocus()
        }
    }

    Shortcut {
        sequence: "Ctrl+H"
        onActivated: peerList.forceActiveFocus()
    }
    Shortcut {
        sequence: "Ctrl+L"
        onActivated: chatPane.forceActiveFocus()
    }

    // ------------------------------------------------------------------
    // Overlays
    // ------------------------------------------------------------------

    ScanDialog { id: scanDialog; anchors.fill: parent; z: 10 }

    CommandPalette {
        id: cmdPalette
        anchors.fill: parent
        z: 10
        onConvSelected: (convId) => {
            bridge.setActiveConv(convId)
            chatPane.forceActiveFocus()
        }
    }

    // Error toast
    Rectangle {
        id: errorToast
        visible: false
        anchors.bottom: parent.bottom; anchors.bottomMargin: 32; anchors.horizontalCenter: parent.horizontalCenter
        width: errorLabel.implicitWidth + 24; height: 32; radius: 6; color: Theme.error; z: 20
        Text { id: errorLabel; anchors.centerIn: parent; color: "white"; font.pixelSize: 12 }
        Timer { id: toastTimer; interval: 3000; onTriggered: errorToast.visible = false }
    }

    Connections {
        target: bridge
        function onErrorOccurred(msg) {
            errorLabel.text = msg
            errorToast.visible = true
            toastTimer.restart()
        }
        function onActiveConvChanged(convId) {
            chatPane.forceActiveFocus()
        }
    }

    Connections {
        target: vimEditor
        function onQuitRequested() { Qt.quit() }
    }
}
