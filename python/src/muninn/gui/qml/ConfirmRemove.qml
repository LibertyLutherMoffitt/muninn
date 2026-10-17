import QtQuick
import QtQuick.Controls

// Confirms deleting chats / forgetting / blocking people before anything is
// removed. Opened by the peer list (selection, right-click) and by :clear,
// :forget, :block via bridge.removeRequested. Enter confirms, Esc cancels.
Rectangle {
    id: root
    visible: opacity > 0
    opacity: 0
    color: Qt.rgba(0, 0, 0, 0.6)
    Behavior on opacity { NumberAnimation { duration: 140 } }

    property var convs: []
    property string how: "delete"     // delete | forget | block
    property var names: []
    signal done()

    readonly property bool people: convs.length > 0 && convs.every(c => c.startsWith("dm:"))

    function open(cs, h) {
        convs = cs; how = h
        names = cs.map(c => bridge.convTitle(c))
        opacity = 1
        grabFocus()
    }
    function grabFocus() { confirmBtn.forceActiveFocus() }
    function close() { opacity = 0 }
    function confirm() { bridge.removeConvs(convs, how); close(); done() }

    MouseArea { anchors.fill: parent; onClicked: root.close() }

    Rectangle {
        anchors.centerIn: parent
        width: Math.min(460, parent.width - 40)
        height: col.implicitHeight + 40
        radius: 12
        color: Theme.surfaceRaised
        border.color: root.how === "delete" ? Theme.accentSoft : Theme.error
        MouseArea { anchors.fill: parent }

        Column {
            id: col
            x: 20; y: 20; width: parent.width - 40
            spacing: 12
            Text {
                width: parent.width; wrapMode: Text.Wrap
                color: Theme.textPrimary; font.pixelSize: 16; font.bold: true
                text: {
                    const who = root.names.length <= 3 ? root.names.join(", ") : root.names.length + " chats"
                    return root.how === "block" ? "Block " + who + "?"
                         : root.how === "forget" ? "Forget " + who + "?"
                         : "Delete " + who + "?"
                }
            }
            Text {
                width: parent.width; wrapMode: Text.Wrap
                color: Theme.textMuted; font.pixelSize: 12
                text: root.how === "block"
                    ? "Deletes the chat, saved addresses and Bluetooth pairing, and refuses their connections and messages from now on. Unblock any time with :blocked."
                    : root.how === "forget"
                    ? "Deletes the chat, saved addresses and Bluetooth pairing; Muninn stops reconnecting. If they write to you again they show up as a new chat."
                    : "Removes the messages, files and games on this device only — the others keep theirs."
                      + (root.convs.some(c => c.startsWith("group:")) ? " Rooms are left: the members are told you left." : "")
            }
            Row {
                anchors.right: parent.right
                spacing: 8
                Btn { text: "Cancel"; onClicked: root.close() }
                Btn {
                    id: confirmBtn
                    text: root.how === "block" ? "Block" : root.how === "forget" ? "Forget" : "Delete"
                    danger: true
                    onClicked: root.confirm()
                    Keys.onReturnPressed: root.confirm()
                    Keys.onEnterPressed: root.confirm()
                    Keys.onEscapePressed: root.close()
                }
            }
        }
    }

    component Btn: Rectangle {
        id: b
        property alias text: t.text
        property bool danger: false
        signal clicked()
        implicitWidth: t.implicitWidth + 28; implicitHeight: 32; radius: 16
        color: danger ? (m.containsMouse || activeFocus ? Qt.lighter(Theme.error, 1.1) : Theme.error)
                      : (m.containsMouse ? Theme.surface : "transparent")
        border.color: danger ? "transparent" : Theme.accentSoft
        border.width: activeFocus && !danger ? 2 : 1
        Text { id: t; anchors.centerIn: parent; color: b.danger ? "white" : Theme.textPrimary; font.pixelSize: 13 }
        MouseArea { id: m; anchors.fill: parent; hoverEnabled: true; cursorShape: Qt.PointingHandCursor; onClicked: b.clicked() }
    }

    Keys.onEscapePressed: close()
}
