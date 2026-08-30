import QtQuick

/**
 * Centred placeholder for a pane with nothing in it yet.
 *
 * The chat area used to be a bare void with no explanation of what to do,
 * which is the first thing a new user sees.
 */
Column {
    id: root

    property string glyph: ""
    property string title: ""
    property string subtitle: ""
    property string hint: ""

    spacing: Theme.spaceSm
    width: Math.min(parent ? parent.width - Theme.spaceXl * 2 : 320, 360)

    Text {
        anchors.horizontalCenter: parent.horizontalCenter
        text: root.glyph
        color: Theme.textFaint
        font.pixelSize: 34
        visible: root.glyph !== ""
    }
    Text {
        anchors.horizontalCenter: parent.horizontalCenter
        text: root.title
        color: Theme.textPrimary
        font.pixelSize: Theme.fontTitle
        font.bold: true
        visible: root.title !== ""
    }
    Text {
        width: parent.width
        text: root.subtitle
        color: Theme.textMuted
        font.pixelSize: Theme.fontSmall
        horizontalAlignment: Text.AlignHCenter
        wrapMode: Text.Wrap
        visible: root.subtitle !== ""
    }
    Item { width: 1; height: Theme.spaceXs; visible: root.hint !== "" }
    Text {
        anchors.horizontalCenter: parent.horizontalCenter
        text: root.hint
        color: Theme.textFaint
        font.pixelSize: Theme.fontTiny
        font.family: "monospace"
        visible: root.hint !== ""
    }
}
