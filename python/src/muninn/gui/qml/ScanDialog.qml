import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

// "People nearby" — find Muninn devices and connect. Opens with <space>s or
// :scan, scans by itself, and keeps each device's state live while probes
// finish in the background. Mirrors the Android Nearby screen.
Rectangle {
    id: root
    visible: _open || opacity > 0
    opacity: 0
    color: Qt.rgba(0, 0, 0, 0.6)
    anchors.fill: parent

    property bool _open: false
    property bool scanning: false
    property bool showAll: false
    property int scans: 0
    property var states: ({})          // addr -> live state
    property var connecting: ({})      // addr -> true while dialling
    property var results: []

    readonly property var stateLabel: ({
        "connected": "connected", "checking": "checking for Muninn…", "muninn": "running Muninn",
        "not_muninn": "not running Muninn", "ignored": "not a phone or computer — left alone",
        "unknown": "" })

    Behavior on opacity { NumberAnimation { duration: 160; easing.type: Easing.OutCubic } }
    property real _scale: _open ? 1.0 : 0.94
    Behavior on _scale { NumberAnimation { duration: 200; easing.type: Easing.OutBack } }

    MouseArea { anchors.fill: parent; onClicked: root.close() }

    function stateOf(d) { return states[d.mac] || d.state || "unknown" }
    function isMuninn(d) { const s = stateOf(d); return d.muninn || s === "muninn" || s === "connected" }
    function rebuild() {
        const sel = deviceModel.count ? deviceModel.get(list.currentIndex) : null
        const keep = sel ? sel.mac : ""
        deviceModel.clear()
        const rows = results.filter(d => showAll || isMuninn(d))
        rows.sort((a, b) => (isMuninn(b) - isMuninn(a)) || (b.rssi - a.rssi))
        for (let i = 0; i < rows.length; i++) deviceModel.append(rows[i])
        for (let i = 0; i < deviceModel.count; i++) if (deviceModel.get(i).mac === keep) list.currentIndex = i
    }
    function counts() {
        let m = 0, c = 0
        for (let i = 0; i < results.length; i++) {
            if (isMuninn(results[i])) m++
            if (stateOf(results[i]) === "checking") c++
        }
        return { muninn: m, checking: c }
    }

    Timer {   // probes resolve after the scan returns: refresh states live
        interval: 900; repeat: true; running: root._open && root.results.length > 0
        onTriggered: {
            root.states = bridge.deviceStates(root.results.map(d => d.mac))
            root.rebuild()
        }
    }

    Rectangle {
        id: panel
        anchors.centerIn: parent
        width: Math.min(560, parent.width - 40)
        height: Math.min(520, parent.height - 40)
        color: Theme.surfaceRaised
        radius: 12
        border.color: Theme.accentSoft
        border.width: 1
        transform: Scale { origin.x: panel.width / 2; origin.y: panel.height / 2; xScale: root._scale; yScale: root._scale }
        MouseArea { anchors.fill: parent }

        ColumnLayout {
            anchors.fill: parent
            anchors.margins: 18
            spacing: 10

            RowLayout {
                Layout.fillWidth: true
                Text { text: "People nearby"; color: Theme.textPrimary; font.pixelSize: 17; font.bold: true }
                Item { Layout.fillWidth: true }
                Pill { text: root.showAll ? "Muninn only (a)" : "Show all (a)"; onClicked: { root.showAll = !root.showAll; root.rebuild() } }
                Pill { text: root.scanning ? "Looking…" : "⟳ Look again (s)"; accent: true; enabled: !root.scanning
                       onClicked: root.startScan() }
                Pill { text: "✕"; onClicked: root.close() }
            }

            RowLayout {
                Layout.fillWidth: true
                spacing: 8
                BusyIndicator { running: root.scanning; visible: running; implicitWidth: 18; implicitHeight: 18 }
                Text {
                    Layout.fillWidth: true
                    color: Theme.textMuted; font.pixelSize: 12; wrapMode: Text.Wrap
                    text: {
                        if (root.scanning) return "Looking around (about 6 s)…"
                        if (!root.scans) return ""
                        const c = root.counts()
                        return "Saw " + root.results.length + " Bluetooth device" + (root.results.length === 1 ? "" : "s")
                            + " · " + c.muninn + " running Muninn"
                            + (c.checking ? " · checking " + c.checking + "…" : "")
                    }
                }
            }

            Rectangle { Layout.fillWidth: true; height: 1; color: Theme.bg }

            ListView {
                id: list
                Layout.fillWidth: true
                Layout.fillHeight: true
                model: ListModel { id: deviceModel }
                clip: true
                spacing: 2
                currentIndex: 0
                focus: true
                keyNavigationEnabled: true
                highlightMoveDuration: 100
                ScrollBar.vertical: ScrollBar { policy: ScrollBar.AsNeeded }

                delegate: Rectangle {
                    id: dev
                    width: list.width
                    height: 54
                    radius: 8
                    readonly property string st: root.stateOf(model)
                    readonly property bool mun: root.isMuninn(model)
                    readonly property bool busy: !!root.connecting[model.mac] || st === "checking"
                    color: ListView.isCurrentItem ? Theme.accentSoft : rowMouse.containsMouse ? Theme.surface : "transparent"
                    MouseArea { id: rowMouse; anchors.fill: parent; hoverEnabled: true; onClicked: list.currentIndex = index
                                onDoubleClicked: root.connectTo(model) }
                    RowLayout {
                        anchors.fill: parent; anchors.leftMargin: 10; anchors.rightMargin: 10
                        spacing: 10
                        Rectangle {
                            width: 34; height: 34; radius: 17
                            color: dev.mun ? Theme.accent : Theme.surface
                            Text { anchors.centerIn: parent; color: "white"; font.pixelSize: 14; font.bold: true
                                   text: (model.name || "?").charAt(0).toUpperCase() }
                            Rectangle { visible: dev.st === "connected"; width: 10; height: 10; radius: 5
                                        color: Theme.success; border.color: Theme.surfaceRaised; border.width: 2
                                        anchors.right: parent.right; anchors.bottom: parent.bottom }
                        }
                        Column {
                            Layout.fillWidth: true
                            spacing: 2
                            Text { text: model.name + (model.known ? "  · talked before" : ""); color: Theme.textPrimary
                                   font.pixelSize: 13; elide: Text.ElideRight; width: parent.width }
                            Text {
                                text: (root.stateLabel[dev.st] || model.kind) + "  ·  " + model.mac
                                color: dev.mun ? Theme.accent : Theme.textMuted
                                font.pixelSize: 11; elide: Text.ElideRight; width: parent.width
                            }
                        }
                        BusyIndicator { visible: dev.busy; running: visible; implicitWidth: 22; implicitHeight: 22 }
                        Pill {
                            visible: !dev.busy && dev.st !== "connected" && dev.st !== "ignored"
                            text: dev.mun ? "Connect" : "Try"
                            accent: dev.mun
                            onClicked: root.connectTo(model)
                        }
                    }
                }
            }

            Text {
                visible: deviceModel.count === 0 && !root.scanning && root.scans > 0
                Layout.fillWidth: true
                wrapMode: Text.Wrap
                color: Theme.textMuted; font.pixelSize: 12
                text: root.results.length && !root.showAll
                    ? "No Muninn devices in sight yet (press a to see everything nearby). "
                      + "Phones only show up while visible: on the phone open Muninn → Nearby → “Make me visible”, then press s here."
                    : "Nothing nearby answered. Is Bluetooth on? Phones must be made visible from Muninn's Nearby screen."
            }

            Text {
                Layout.fillWidth: true
                wrapMode: Text.Wrap
                color: Theme.textMuted; font.pixelSize: 10; opacity: 0.8
                text: "j/k move · Enter connect · s look again · a all devices · Esc close — "
                      + "this computer stays visible to nearby phones while Muninn runs; strangers' headsets and watches are never contacted."
            }
        }
    }

    component Pill: Rectangle {
        id: pill
        property alias text: lbl.text
        property bool accent: false
        signal clicked()
        implicitHeight: 28; radius: 14
        implicitWidth: lbl.implicitWidth + 22
        opacity: enabled ? 1 : 0.5
        color: accent ? (pm.containsMouse ? Qt.lighter(Theme.accent, 1.15) : Theme.accent)
                      : (pm.containsMouse ? Theme.surface : "transparent")
        border.color: accent ? "transparent" : Theme.accentSoft
        Text { id: lbl; anchors.centerIn: parent; color: pill.accent ? "white" : Theme.textPrimary; font.pixelSize: 12 }
        MouseArea { id: pm; anchors.fill: parent; hoverEnabled: true; cursorShape: Qt.PointingHandCursor
                    enabled: pill.enabled; onClicked: pill.clicked() }
    }

    function connectTo(d) {
        if (!d || !d.mac) return
        const c = Object.assign({}, connecting); c[d.mac] = true; connecting = c
        bridge.pairDevice(d.mac)
    }
    function open() {
        root._open = true
        root.opacity = 1
        list.forceActiveFocus()
        if (!scanning) startScan()
    }
    function grabFocus() { list.forceActiveFocus() }
    function close() { root._open = false; root.opacity = 0 }
    function startScan() {
        if (scanning) return
        scanning = true
        bridge.startScan()
    }

    Connections {
        target: bridge
        function onScanResultsReady(res) {
            root.results = res
            root.states = ({})
            root.scanning = false
            root.scans++
            root.rebuild()
        }
        // a dial finished (either way): stop that row's spinner
        function onNotify(_m) { root.connecting = ({}) }
        function onErrorOccurred(_m) { root.connecting = ({}) }
    }

    Keys.onEscapePressed: root.close()
    Keys.onPressed: (e) => {
        if (e.modifiers & Qt.ControlModifier) return
        if (e.key === Qt.Key_S) { startScan(); e.accepted = true }
        else if (e.key === Qt.Key_A) { showAll = !showAll; rebuild(); e.accepted = true }
        else if (e.key === Qt.Key_J) { list.incrementCurrentIndex(); e.accepted = true }
        else if (e.key === Qt.Key_K) { list.decrementCurrentIndex(); e.accepted = true }
        else if (e.key === Qt.Key_Return || e.key === Qt.Key_Enter) {
            if (deviceModel.count) connectTo(deviceModel.get(list.currentIndex))
            else startScan()
            e.accepted = true
        }
    }
    focus: _open
}
