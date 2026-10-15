import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

// Emoji picker overlay. mode "insert" types into the composer, "react"
// toggles a reaction on `targetMsg`. Type to filter by shortcode; Enter picks
// the first match; Esc closes.
Item {
    id: root
    visible: opacity > 0
    opacity: 0
    property string mode: "insert"
    property string targetMsg: ""
    property var cats: []
    Behavior on opacity { NumberAnimation { duration: 120 } }

    function open(m, msg) {
        mode = m; targetMsg = msg || ""
        if (cats.length === 0) cats = bridge.emojiCategories()
        search.text = ""
        rebuild()
        opacity = 1
        search.forceActiveFocus()
    }
    function close() { opacity = 0 }
    function pick(e) {
        if (mode === "react") bridge.toggleReaction(targetMsg, e)
        else vimEditor.insertText(e)
        close()
    }
    function rebuild() {
        model.clear()
        const q = search.text.toLowerCase().replace(/:/g, "")
        if (!q) {
            const quick = bridge.quickReactions()
            for (let i = 0; i < quick.length; i++) model.append({ e: quick[i], c: "", h: i === 0 ? "frequent" : "" })
        }
        for (let i = 0; i < cats.length; i++) {
            let first = true
            for (let j = 0; j < cats[i].items.length; j++) {
                const it = cats[i].items[j]
                if (q && it.code.indexOf(q) < 0) continue
                model.append({ e: it.emoji, c: it.code, h: first && !q ? cats[i].name : "" })
                first = false
            }
        }
    }

    Rectangle { anchors.fill: parent; color: "#80000000"
        MouseArea { anchors.fill: parent; onClicked: root.close() } }

    Rectangle {
        anchors.centerIn: parent
        width: 420; height: 380; radius: 10
        color: Theme.surface; border.color: Theme.accent
        MouseArea { anchors.fill: parent }  // swallow clicks
        ColumnLayout {
            anchors.fill: parent; anchors.margins: 10; spacing: 8
            Text { text: root.mode === "react" ? "React" : "Insert emoji"
                   color: Theme.textPrimary; font.bold: true; font.pixelSize: 13 }
            TextField {
                id: search
                Layout.fillWidth: true
                placeholderText: "search shortcodes… (thumbsup, plane, fire)"
                color: Theme.textPrimary
                background: Rectangle { color: Theme.bg; radius: 6 }
                onTextChanged: root.rebuild()
                Keys.onEscapePressed: root.close()
                Keys.onReturnPressed: if (model.count > 0) root.pick(model.get(0).e)
            }
            GridView {
                Layout.fillWidth: true; Layout.fillHeight: true
                clip: true
                cellWidth: 40; cellHeight: 40
                model: ListModel { id: model }
                delegate: Rectangle {
                    width: 38; height: 38; radius: 6
                    color: em.containsMouse ? Theme.accentSoft : "transparent"
                    Text { anchors.centerIn: parent; text: e; font.pixelSize: 22 }
                    ToolTip.visible: em.containsMouse && c !== ""
                    ToolTip.text: ":" + c + ":"
                    MouseArea { id: em; anchors.fill: parent; hoverEnabled: true; onClicked: root.pick(e) }
                }
                ScrollBar.vertical: ScrollBar {}
            }
        }
    }
}
