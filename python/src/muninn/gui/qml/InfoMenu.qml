import QtQuick
import QtQuick.Controls

// Generic popup shown by `bridge.infoMenuRequested(title, items)`.
// Each item: { label, sub, convId?, action? } — items with a convId can be
// activated (Enter / click) to switch to that conversation.
Rectangle {
    id: root
    visible: opacity > 0
    opacity: 0
    color: Qt.rgba(0, 0, 0, 0.55)
    anchors.fill: parent

    property bool _open: false
    property string title: ""

    signal convSelected(string convId)
    signal gameSelected(string session)

    Behavior on opacity {
        NumberAnimation { duration: 160; easing.type: Easing.OutCubic }
    }
    property real _scale: _open ? 1.0 : 0.94
    Behavior on _scale {
        NumberAnimation { duration: 200; easing.type: Easing.OutBack }
    }

    MouseArea { anchors.fill: parent; onClicked: root.close() }

    Rectangle {
        id: panel
        anchors.top: parent.top
        anchors.horizontalCenter: parent.horizontalCenter
        anchors.topMargin: 80
        width: Math.min(560, parent.width - 40)
        // fit the content; scroll only when it really doesn't fit
        height: Math.min(header.height + 1 + itemList.contentHeight + 44, parent.height - 120)
        color: Theme.surfaceRaised
        radius: 12
        border.color: Theme.accentSoft
        border.width: 1
        transform: Scale {
            origin.x: panel.width / 2; origin.y: panel.height / 2
            xScale: root._scale; yScale: root._scale
        }

        MouseArea { anchors.fill: parent }

        Column {
            anchors.fill: parent
            anchors.margins: 12
            spacing: 8

            Text {
                id: header
                width: parent.width
                text: root.title
                color: Theme.textPrimary
                font.pixelSize: 14
                font.bold: true
                elide: Text.ElideRight
            }

            Rectangle {
                width: parent.width
                height: 1
                color: Theme.surface
            }

            ListView {
                id: itemList
                width: parent.width
                height: parent.height - header.height - 9
                model: ListModel { id: itemModel; dynamicRoles: true }
                currentIndex: 0
                clip: true
                spacing: 2
                focus: true
                activeFocusOnTab: true
                highlightMoveDuration: 80

                Keys.onEscapePressed: root.close()
                Keys.onReturnPressed: root.activate()
                Keys.onEnterPressed: root.activate()
                Keys.onPressed: (event) => {
                    if (event.modifiers & Qt.ControlModifier) {
                        if (event.key === Qt.Key_N) {
                            if (currentIndex + 1 < count) currentIndex++
                            event.accepted = true
                        } else if (event.key === Qt.Key_P) {
                            if (currentIndex > 0) currentIndex--
                            event.accepted = true
                        }
                    } else if (event.key === Qt.Key_J) {
                        if (currentIndex + 1 < count) currentIndex++
                        event.accepted = true
                    } else if (event.key === Qt.Key_K) {
                        if (currentIndex > 0) currentIndex--
                        event.accepted = true
                    }
                }

                delegate: ItemDelegate {
                    id: rowDel
                    width: itemList.width
                    height: (model.sub || "") !== "" ? 46 : 34
                    padding: 0; leftPadding: 14; rightPadding: 10
                    highlighted: itemList.currentIndex === index
                    background: Rectangle {
                        color: rowDel.highlighted ? Theme.accentSoft
                             : rowDel.hovered     ? Theme.surface
                                                  : "transparent"
                        radius: 6
                        Rectangle { visible: rowDel.highlighted; width: 3; radius: 2; color: Theme.accent
                                    anchors.left: parent.left; anchors.top: parent.top; anchors.bottom: parent.bottom
                                    anchors.margins: 6 }
                        Behavior on color {
                            ColorAnimation { duration: 100; easing.type: Easing.OutQuad }
                        }
                    }
                    contentItem: Item {
                        Column {
                            anchors.verticalCenter: parent.verticalCenter
                            width: parent.width
                            spacing: 2
                            Text {
                                width: parent.width; elide: Text.ElideRight
                                text: model.label
                                color: Theme.textPrimary
                                font.pixelSize: 13
                            }
                            Text {
                                width: parent.width; elide: Text.ElideRight
                                text: model.sub || ""
                                color: Theme.textMuted
                                font.pixelSize: 11
                                visible: text.length > 0
                            }
                        }
                    }
                    onClicked: { itemList.currentIndex = index; root.activate() }
                }
            }
        }
    }

    function show(title, items) {
        root.title = title
        itemModel.clear()
        for (let i = 0; i < items.length; i++)
            itemModel.append(items[i])
        itemList.currentIndex = 0
        root._open = true
        root.opacity = 1
        itemList.forceActiveFocus()
    }

    function grabFocus() { itemList.forceActiveFocus() }
    function close() {
        root._open = false
        root.opacity = 0
    }

    function activate() {
        if (itemList.currentIndex < 0) { root.close(); return }
        const item = itemModel.get(itemList.currentIndex)
        if (!item) { root.close(); return }
        if (item.action === "cmd" && item.cmd) {
            root.close()
            bridge.runCommand(item.cmd)
            return
        } else if (item.action === "game" && item.session) {
            root.close()
            root.gameSelected(item.session)
            return
        } else if (item.convId) {
            root.convSelected(item.convId)
        } else if (item.action === "url" && item.url) {
            Qt.openUrlExternally(item.url)
        }
        root.close()
    }

    Keys.onEscapePressed: root.close()
    focus: _open
}
