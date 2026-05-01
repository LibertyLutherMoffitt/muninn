import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

// Modal BT scan / pair dialog. Toggle with <space>s or :scan.
Rectangle {
    id: root
    // Become visible eagerly when opening so child controls are focusable
    // before the fade-in finishes; stay visible during the fade-out.
    visible: _open || opacity > 0
    opacity: 0
    color: Qt.rgba(0, 0, 0, 0.55)
    anchors.fill: parent

    property bool _open: false

    Behavior on opacity {
        NumberAnimation { duration: 160; easing.type: Easing.OutCubic }
    }

    // Scale-in animation on open
    property real _scale: _open ? 1.0 : 0.92
    Behavior on _scale {
        NumberAnimation { duration: 200; easing.type: Easing.OutBack }
    }

    MouseArea {
        anchors.fill: parent
        onClicked: root.close()
    }

    Rectangle {
        id: panel
        anchors.centerIn: parent
        width: Math.min(480, parent.width - 40)
        height: Math.min(420, parent.height - 40)
        color: Theme.surfaceRaised
        radius: 10
        border.color: Theme.accent
        border.width: 1
        transform: Scale { origin.x: panel.width / 2; origin.y: panel.height / 2; xScale: root._scale; yScale: root._scale }

        MouseArea { anchors.fill: parent }  // block clicks reaching background

        ColumnLayout {
            anchors.fill: parent
            anchors.margins: 16
            spacing: 10

            RowLayout {
                Text {
                    text: "Bluetooth Scan"
                    color: Theme.textPrimary
                    font.pixelSize: 16
                    font.bold: true
                    Layout.fillWidth: true
                }
                Button {
                    id: closeBtn
                    text: "✕"
                    flat: true
                    activeFocusOnTab: true
                    Keys.onReturnPressed: root.close()
                    Keys.onEnterPressed: root.close()
                    onClicked: root.close()
                    contentItem: Text {
                        text: closeBtn.text
                        color: closeBtn.activeFocus ? Theme.textPrimary : Theme.textMuted
                        horizontalAlignment: Text.AlignHCenter
                        verticalAlignment: Text.AlignVCenter
                    }
                    background: Rectangle {
                        color: closeBtn.activeFocus ? Theme.bg : "transparent"
                        radius: 4
                    }
                }
            }

            // Scan button + status
            RowLayout {
                Button {
                    id: scanBtn
                    property bool scanning: false
                    text: scanning ? "Scanning…" : "Scan (5 s)  [Enter / s]"
                    enabled: !scanning
                    focus: true
                    activeFocusOnTab: true
                    Keys.onReturnPressed: root.startScan()
                    Keys.onEnterPressed: root.startScan()
                    onClicked: root.startScan()
                    contentItem: Text {
                        text: scanBtn.text
                        color: Theme.textPrimary
                        horizontalAlignment: Text.AlignHCenter
                        verticalAlignment: Text.AlignVCenter
                    }
                    background: Rectangle {
                        color: scanBtn.enabled ? Theme.accent : Theme.textMuted
                        radius: 6
                        opacity: scanBtn.enabled ? 1.0 : 0.5
                        border.color: scanBtn.activeFocus ? "white" : "transparent"
                        border.width: 1
                        Behavior on color {
                            ColorAnimation { duration: 140; easing.type: Easing.OutQuad }
                        }
                    }
                }
                Text {
                    id: statusText
                    color: Theme.textMuted
                    font.pixelSize: 12
                    Layout.fillWidth: true
                }
            }

            // Device list — keyboard navigable. j/k or Up/Down moves selection,
            // Enter pairs the highlighted device.
            ListView {
                id: deviceList
                Layout.fillWidth: true
                Layout.fillHeight: true
                model: ListModel { id: deviceModel }
                clip: true
                spacing: 2
                activeFocusOnTab: true
                currentIndex: 0
                keyNavigationEnabled: true
                highlightMoveDuration: 120
                Keys.onPressed: (event) => {
                    if (event.key === Qt.Key_J) {
                        if (currentIndex + 1 < count) currentIndex++
                        event.accepted = true
                    } else if (event.key === Qt.Key_K) {
                        if (currentIndex > 0) currentIndex--
                        event.accepted = true
                    } else if (event.key === Qt.Key_Return || event.key === Qt.Key_Enter) {
                        if (currentIndex >= 0) {
                            const item = deviceModel.get(currentIndex)
                            if (item) {
                                statusText.text = "Pairing " + item.mac + "…"
                                bridge.pairDevice(item.mac)
                            }
                        }
                        event.accepted = true
                    }
                }

                delegate: ItemDelegate {
                    id: devDel
                    width: deviceList.width
                    height: 44
                    highlighted: ListView.isCurrentItem
                    contentItem: RowLayout {
                        spacing: 8
                        Column {
                            spacing: 2
                            Text {
                                text: model.name || model.mac
                                color: Theme.textPrimary
                                font.pixelSize: 13
                            }
                            Text {
                                text: model.mac
                                color: Theme.textMuted
                                font.pixelSize: 11
                                visible: model.name !== model.mac
                            }
                        }
                        Item { Layout.fillWidth: true }
                        Button {
                            id: pairBtn
                            text: "Pair"
                            activeFocusOnTab: true
                            Keys.onReturnPressed: clicked()
                            Keys.onEnterPressed: clicked()
                            onClicked: {
                                statusText.text = "Pairing " + model.mac + "…"
                                bridge.pairDevice(model.mac)
                            }
                            contentItem: Text {
                                text: pairBtn.text
                                color: "white"
                                horizontalAlignment: Text.AlignHCenter
                                verticalAlignment: Text.AlignVCenter
                            }
                            background: Rectangle {
                                color: Theme.accent; radius: 5
                                opacity: pairBtn.hovered ? 0.85 : 1.0
                                border.color: pairBtn.activeFocus ? "white" : "transparent"
                                border.width: 1
                            }
                        }
                    }
                    background: Rectangle {
                        color: devDel.highlighted ? Qt.lighter(Theme.bg, 1.2)
                             : devDel.hovered     ? Theme.bg
                                                  : "transparent"
                        radius: 4
                        Behavior on color {
                            ColorAnimation { duration: 120; easing.type: Easing.OutQuad }
                        }
                    }
                    onClicked: deviceList.currentIndex = index
                }
            }

            Text {
                visible: deviceModel.count === 0 && !scanBtn.scanning
                text: "No devices found. Make sure remote is discoverable."
                color: Theme.textMuted
                font.pixelSize: 12
                wrapMode: Text.Wrap
                Layout.fillWidth: true
            }
        }
    }

    function open() {
        root._open = true
        root.opacity = 1
        // Claim focus immediately *and* via Qt.callLater. The synchronous
        // call wins when no other handler is competing; the deferred call
        // wins when (e.g.) the palette's onVisibleChanged tries to bounce
        // focus back to the chat pane while we're still opening.
        scanBtn.forceActiveFocus()
        Qt.callLater(function() { scanBtn.forceActiveFocus() })
    }
    function close() {
        root._open = false
        root.opacity = 0
    }
    function startScan() {
        if (!scanBtn.enabled) return
        scanBtn.scanning = true
        statusText.text = ""
        deviceModel.clear()
        bridge.startScan()
    }

    Connections {
        target: bridge
        function onScanResultsReady(results) {
            deviceModel.clear()
            for (let i = 0; i < results.length; i++)
                deviceModel.append(results[i])
            // reset scanning flag on scan button — find it via parent chain
            scanBtn.scanning = false
            statusText.text = results.length + " device(s) found"
        }
    }

    // Keyboard: Esc closes; `s` triggers a (re-)scan from anywhere in the
    // dialog, so the user never has to tab back to the Scan button.
    // Enter/Return is also handled here as a fallback when focus has moved
    // to the device list (where Enter normally pairs the highlighted item):
    // if there are no devices yet, route Enter to the scan button.
    Keys.onEscapePressed: root.close()
    Keys.onPressed: (event) => {
        if (event.modifiers & Qt.ControlModifier) return
        if (event.key === Qt.Key_S) {
            root.startScan()
            event.accepted = true
            return
        }
        if (event.key === Qt.Key_Return || event.key === Qt.Key_Enter) {
            if (deviceModel.count === 0) {
                root.startScan()
                event.accepted = true
            }
        }
    }
    focus: _open
}
