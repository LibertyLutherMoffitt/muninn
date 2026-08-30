import QtQuick

/**
 * The small outlined button used in the status bar.
 *
 * Extracted because the About and Palette buttons were ~40 lines of identical
 * markup differing only in label and click handler; any restyle had to be made
 * twice and inevitably would not be.
 */
Rectangle {
    id: root

    property string label: ""
    property string shortcut: ""
    signal clicked(real cx, real cy)

    readonly property bool hovered: area.containsMouse

    implicitWidth: text.implicitWidth + Theme.spaceLg
    implicitHeight: 18
    radius: Theme.radiusSm
    color: hovered ? Theme.accent : Theme.surface
    border.color: Theme.accent
    border.width: 1

    Behavior on color {
        ColorAnimation { duration: 120; easing.type: Easing.OutQuad }
    }

    Text {
        id: text
        anchors.centerIn: parent
        text: root.shortcut ? root.label + "  ·  " + root.shortcut : root.label
        color: root.hovered ? Theme.onAccent : Theme.textMuted
        font.pixelSize: Theme.fontTiny
        font.bold: true
    }

    MouseArea {
        id: area
        anchors.fill: parent
        hoverEnabled: true
        cursorShape: Qt.PointingHandCursor
        onClicked: root.clicked(root.width / 2, root.height / 2)
    }
}
