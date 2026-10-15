import QtQuick

// Full-window image / GIF viewer. Click or Esc closes.
Rectangle {
    id: root
    color: "#e6000000"
    visible: opacity > 0
    opacity: 0
    focus: visible
    property string source: ""
    Behavior on opacity { NumberAnimation { duration: 120 } }

    function open(url) { source = url; opacity = 1; forceActiveFocus() }
    function close() { opacity = 0; source = "" }

    AnimatedImage {
        anchors.fill: parent; anchors.margins: 30
        source: root.source
        fillMode: Image.PreserveAspectFit
        playing: root.visible
        asynchronous: true
    }
    MouseArea { anchors.fill: parent; onClicked: root.close() }
    Keys.onEscapePressed: root.close()
}
