import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

// Modal BT scan / pair dialog. Toggle with gs or :scan.
Rectangle {
    id: root
    visible: false
    color: Qt.rgba(0, 0, 0, 0.6)
    anchors.fill: parent

    // Scale-in animation on open
    property real _scale: visible ? 1.0 : 0.9
    Behavior on _scale {
        NumberAnimation { duration: 100; easing.type: Easing.OutBack }
    }

    MouseArea {
        anchors.fill: parent
        onClicked: root.close()
    }

    Rectangle {
        anchors.centerIn: parent
        width: Math.min(480, parent.width - 40)
        height: Math.min(420, parent.height - 40)
        color: Theme.surfaceRaised
        radius: 10
        transform: Scale { origin.x: width / 2; origin.y: height / 2; xScale: root._scale; yScale: root._scale }

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
                    text: "✕"
                    flat: true
                    onClicked: root.close()
                    contentItem: Text { text: parent.text; color: Theme.textMuted }
                    background: Rectangle { color: "transparent" }
                }
            }

            // Scan button + status
            RowLayout {
                Button {
                    id: scanBtn
                    property bool scanning: false
                    text: scanning ? "Scanning…" : "Scan (5 s)"
                    enabled: !scanning
                    onClicked: {
                        scanning = true
                        statusText.text = ""
                        deviceModel.clear()
                        bridge.startScan()
                    }
                    contentItem: Text { text: parent.text; color: Theme.textPrimary }
                    background: Rectangle {
                        color: parent.enabled ? Theme.accent : Theme.textMuted
                        radius: 6
                        opacity: parent.enabled ? 1.0 : 0.5
                    }
                }
                Text {
                    id: statusText
                    color: Theme.textMuted
                    font.pixelSize: 12
                    Layout.fillWidth: true
                }
            }

            // Device list
            ListView {
                id: deviceList
                Layout.fillWidth: true
                Layout.fillHeight: true
                model: ListModel { id: deviceModel }
                clip: true
                spacing: 2

                delegate: ItemDelegate {
                    width: deviceList.width
                    height: 44
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
                                font.family: "monospace"
                                visible: model.name !== model.mac
                            }
                        }
                        Item { Layout.fillWidth: true }
                        Button {
                            text: "Pair"
                            onClicked: {
                                statusText.text = "Pairing " + model.mac + "…"
                                bridge.pairDevice(model.mac)
                            }
                            contentItem: Text { text: parent.text; color: "white" }
                            background: Rectangle {
                                color: Theme.accent; radius: 5
                                opacity: parent.hovered ? 0.85 : 1.0
                            }
                        }
                    }
                    background: Rectangle {
                        color: parent.hovered ? Theme.bg : "transparent"
                    }
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

    function open() { root.visible = true }
    function close() { root.visible = false }

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

    // Keyboard: Esc closes
    Keys.onEscapePressed: root.close()
    focus: visible
}
