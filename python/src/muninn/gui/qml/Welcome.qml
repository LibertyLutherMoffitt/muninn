import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

// First run: ask for a display name (peers otherwise see a bare id).
Rectangle {
    id: root
    color: Theme.scrim
    visible: opacity > 0
    opacity: bridge.localName === "" ? 1 : 0
    Behavior on opacity { NumberAnimation { duration: 160 } }
    function grabFocus() { nameField.forceActiveFocus() }
    onVisibleChanged: if (visible) nameField.forceActiveFocus()
    Component.onCompleted: if (visible) nameField.forceActiveFocus()

    MouseArea { anchors.fill: parent }

    Rectangle {
        anchors.centerIn: parent
        width: 440; height: col.implicitHeight + 40; radius: 12
        color: Theme.surface; border.color: Theme.accent
        ColumnLayout {
            id: col
            anchors.fill: parent; anchors.margins: 20; spacing: 12
            Text { text: "🪶  Welcome to Muninn"; color: Theme.textPrimary; font.pixelSize: 18; font.bold: true }
            Text {
                Layout.fillWidth: true; wrapMode: Text.Wrap
                color: Theme.textMuted; font.pixelSize: 12
                text: "Encrypted chat over Bluetooth — no internet needed. People nearby running Muninn " +
                      "show up on the left by themselves, and messages hop through others to reach " +
                      "people further away.\n\nWhat should people call you?"
            }
            TextField {
                id: nameField
                Layout.fillWidth: true
                text: bridge.suggestedName
                selectByMouse: true
                color: Theme.textPrimary
                background: Rectangle { color: Theme.bg; radius: 6; border.color: Theme.surfaceRaised }
                Keys.onReturnPressed: go()
                Keys.onEnterPressed: go()
                function go() { if (text.trim() !== "") bridge.setDisplayName(text.trim()) }
            }
            Text { text: "Press Enter to start · change it later with :nick <name>"
                   color: Theme.textMuted; font.pixelSize: 10 }
        }
    }
}
