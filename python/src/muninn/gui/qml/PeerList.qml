import QtQuick
import QtQuick.Controls

Rectangle {
    id: root
    color: Theme.surface

    property string activeConvId: bridge.activeConvId
    property alias listView: listView

    signal convSelected(string convId)
    signal removeRequested(var convs, string how)
    property bool confirmOpen: false   // set by Main while the confirm dialog shows
    property bool menuOpen: false

    // Multi-select: Ctrl/Shift-click picks rows; right-click acts on the
    // picked rows (or just the clicked one).
    property var picked: ({})
    readonly property var pickedList: Object.keys(picked)
    function togglePick(c) { const p = Object.assign({}, picked); if (p[c]) delete p[c]; else p[c] = true; picked = p }
    function clearPicked() { picked = ({}) }
    readonly property bool allPeople: pickedList.length > 0 && pickedList.every(c => c.startsWith("dm:"))

    // Window-space center of the row for `convId`, or null if the row is
    // not currently realized (off-screen / unknown). Used by the cursor-
    // trail overlay.
    function rowPos(convId, target) {
        for (let i = 0; i < listView.count; i++) {
            const idx = listView.model.index(i, 0)
            const conv = listView.model.data(idx, Qt.UserRole + 3)
            if (conv === convId) {
                const item = listView.itemAtIndex(i)
                if (!item) return null
                return listView.mapToItem(target,
                    item.x + item.width / 2,
                    item.y + item.height / 2)
            }
        }
        return null
    }

    // selection bar
    Rectangle {
        id: selBar
        anchors.bottom: parent.bottom; anchors.left: parent.left; anchors.right: parent.right
        // only for Ctrl/Shift-picked selections, not a lone right-click
        height: root.pickedList.length && !root.menuOpen ? 74 : 0
        visible: height > 0
        color: Theme.surfaceRaised
        z: 2
        Column {
            anchors.fill: parent; anchors.margins: 8; spacing: 6
            Row {
                spacing: 6
                Text { text: root.pickedList.length + " selected"; color: Theme.textPrimary; font.pixelSize: 12; font.bold: true }
                Text { text: "✕ clear"; color: Theme.textMuted; font.pixelSize: 11
                       MouseArea { anchors.fill: parent; anchors.margins: -4; cursorShape: Qt.PointingHandCursor
                                   onClicked: root.clearPicked() } }
            }
            Row {
                spacing: 4
                SelBtn { label: "Delete"; onClicked: root.removeRequested(root.pickedList, "delete") }
                SelBtn { label: "Forget"; visible: root.allPeople; onClicked: root.removeRequested(root.pickedList, "forget") }
                SelBtn { label: "Block"; visible: root.allPeople; danger: true
                         onClicked: root.removeRequested(root.pickedList, "block") }
            }
        }
    }

    component SelBtn: Rectangle {
        id: sb
        property string label
        property bool danger: false
        signal clicked()
        width: sbl.implicitWidth + 18; height: 24; radius: 12
        color: sbm.containsMouse ? (danger ? "#3f1d24" : Theme.accentSoft) : "transparent"
        border.color: danger ? Theme.error : Theme.accentSoft
        Text { id: sbl; anchors.centerIn: parent; text: sb.label; font.pixelSize: 11
               color: sb.danger ? Theme.error : Theme.textPrimary }
        MouseArea { id: sbm; anchors.fill: parent; hoverEnabled: true; cursorShape: Qt.PointingHandCursor
                    onClicked: sb.clicked() }
    }

    component RowItem: MenuItem {
        id: ri
        property bool danger: false
        property bool shown: true
        visible: shown
        height: shown ? 30 : 0
        contentItem: Text { text: ri.text; font.pixelSize: 13; verticalAlignment: Text.AlignVCenter
                            color: ri.danger ? Theme.error : Theme.textPrimary }
        background: Rectangle { radius: 6; color: ri.highlighted ? (ri.danger ? "#3f1d24" : Theme.accentSoft) : "transparent" }
    }

    ListView {
        id: listView
        anchors.top: parent.top; anchors.left: parent.left
        anchors.right: parent.right; anchors.bottom: selBar.top
        model: peerModel
        clip: true
        currentIndex: -1

        ScrollBar.vertical: ScrollBar { policy: ScrollBar.AsNeeded }

        // j/k navigation from global key handler
        function selectNext() {
            if (currentIndex + 1 < count) currentIndex++
        }
        function selectPrev() {
            if (currentIndex > 0) currentIndex--
        }
        function activateCurrent() {
            if (currentIndex >= 0) {
                const conv = model.data(model.index(currentIndex, 0),
                    Qt.UserRole + 3)  // convId role
                if (conv) root.convSelected(conv)
            }
        }

        delegate: ItemDelegate {
            id: del
            width: listView.width
            height: 60
            highlighted: model.convId === root.activeConvId
            readonly property bool isPicked: !!root.picked[model.convId]

            background: Rectangle {
                border.color: del.isPicked ? Theme.accent : "transparent"
                border.width: del.isPicked ? 2 : 0
                radius: 6
                color: del.isPicked ? Theme.accentSoft
                     : del.highlighted ? Theme.surfaceRaised
                     : del.hovered     ? Qt.lighter(Theme.surface, 1.12)
                                       : "transparent"

                Behavior on color {
                    ColorAnimation { duration: 140; easing.type: Easing.OutQuad }
                }

                // peer-connect/disconnect pulse
                ColorAnimation on color {
                    id: pulseAnim
                    duration: 240
                    easing.type: Easing.OutQuad
                }
            }

            // Flash on peer status change
            Connections {
                target: bridge
                function onPeerChanged(addr, connected) {
                    if (addr === model.mac) {
                        pulseAnim.from = connected
                            ? Theme.success : Theme.error
                        pulseAnim.to = del.highlighted
                            ? Theme.surfaceRaised : "transparent"
                        pulseAnim.start()
                    }
                }
            }

            contentItem: Item {
                // Avatar circle
                Rectangle {
                    id: avatar
                    anchors.left: parent.left
                    anchors.leftMargin: 10
                    anchors.verticalCenter: parent.verticalCenter
                    width: 36; height: 36; radius: 18
                    color: Theme.accent
                    opacity: 0.8

                    Text {
                        anchors.centerIn: parent
                        text: (model.displayName || "?").charAt(0).toUpperCase()
                        color: Theme.textPrimary
                        font.pixelSize: 16
                        font.bold: true
                    }
                }

                // Status dot
                Rectangle {
                    anchors.right: avatar.right
                    anchors.bottom: avatar.bottom
                    width: 10; height: 10; radius: 5
                    color: model.status === "direct"  ? Theme.success
                         : model.status === "relay"   ? Theme.warning
                         : model.status === "nearby"  ? "#7fa7d9"
                         : model.status === "group"   ? Theme.accent
                                                      : Theme.textMuted
                    border.color: Theme.surface
                    border.width: 2
                }

                Column {
                    anchors.left: avatar.right
                    anchors.leftMargin: 10
                    anchors.right: badge.left
                    anchors.rightMargin: 4
                    anchors.verticalCenter: parent.verticalCenter
                    spacing: 2

                    Text {
                        width: parent.width
                        text: model.displayName || model.mac
                        color: Theme.textPrimary
                        font.pixelSize: 13
                        font.bold: del.highlighted
                        elide: Text.ElideRight
                    }
                    Text {
                        width: parent.width
                        text: model.typing ? "typing…" : (model.lastMessage || model.via)
                        color: model.typing ? Theme.accent : Theme.textMuted
                        font.italic: model.typing
                        font.pixelSize: 11
                        elide: Text.ElideRight
                        visible: text !== ""
                    }
                }

                // Unread badge
                Rectangle {
                    id: badge
                    anchors.right: parent.right
                    anchors.rightMargin: 8
                    anchors.verticalCenter: parent.verticalCenter
                    width: Math.max(20, badgeText.implicitWidth + 8)
                    height: 20; radius: 10
                    color: model.unreadCount > 0 ? Theme.accent : Theme.surfaceRaised
                    visible: model.unreadCount > 0 || model.pending > 0
                    opacity: model.unreadCount > 0 ? 1 : 0.6

                    Text {
                        id: badgeText
                        anchors.centerIn: parent
                        text: model.unreadCount > 0 ? (model.unreadCount > 99 ? "99+" : model.unreadCount)
                                                    : "◌" + model.pending
                        color: model.unreadCount > 0 ? "white" : Theme.textMuted
                        font.pixelSize: 11
                        font.bold: true
                    }
                }
            }

            onClicked: root.convSelected(model.convId)

            // Ctrl/Shift-click = pick; right-click = menu for the picked rows
            MouseArea {
                anchors.fill: parent
                acceptedButtons: Qt.LeftButton | Qt.RightButton
                propagateComposedEvents: true
                onPressed: (m) => {
                    if (m.button === Qt.RightButton) {
                        if (!del.isPicked) { root.menuOpen = true; root.picked = ({ [model.convId]: true }) }
                        rowMenu.popup()
                        m.accepted = true
                    } else if (m.modifiers & (Qt.ControlModifier | Qt.ShiftModifier)) {
                        root.togglePick(model.convId)
                        m.accepted = true
                    } else {
                        if (root.pickedList.length) root.clearPicked()
                        m.accepted = false
                    }
                }
            }

            Menu {
                id: rowMenu
                padding: 6
                background: Rectangle { implicitWidth: 220; color: Theme.surfaceRaised; radius: 10
                                        border.color: Theme.accentSoft }
                RowItem { text: root.pickedList.length > 1 ? "Delete " + root.pickedList.length + " chats"
                                : model.convId.startsWith("group:") ? "Leave & delete room" : "Delete chat"
                          onTriggered: root.removeRequested(root.pickedList, "delete") }
                RowItem { text: "Forget" + (root.pickedList.length > 1 ? " " + root.pickedList.length + " people" : "")
                          shown: root.allPeople; onTriggered: root.removeRequested(root.pickedList, "forget") }
                RowItem { text: "Block"; danger: true; shown: root.allPeople
                          onTriggered: root.removeRequested(root.pickedList, "block") }
                onOpened: root.menuOpen = root.pickedList.length === 1
                // a one-row "selection" made by the right-click itself ends with the menu
                onClosed: {
                    if (root.menuOpen && !root.confirmOpen) Qt.callLater(root.clearPicked)
                    root.menuOpen = false
                }
            }
        }
    }
}
