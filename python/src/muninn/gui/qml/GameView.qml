import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

// Host for JavaScript game plugins (see muninn/games/README.md). The plugin
// code comes from the session's invite, so all players run identical rules.
Rectangle {
    id: root
    color: "#ee0f1115"
    visible: opacity > 0
    opacity: 0
    focus: visible
    Behavior on opacity { NumberAnimation { duration: 140 } }

    property string session: ""
    property var info: ({})
    property var game: null
    property var state: null
    property var ui: ({})        // plugin-local view state (selection, hover…)
    property string error: ""
    property var st: ({ over: false, text: "" })

    function open(sid) {
        session = sid
        error = ""; ui = ({}); game = null
        const code = bridge.gameCode(sid)
        try {
            game = (new Function(code + "\n;return game;"))()
        } catch (e) {
            error = "Could not load game: " + e
        }
        reload()
        opacity = 1
        forceActiveFocus()
    }
    function close() { opacity = 0; rtTimer.running = false }

    function view() {
        return { w: board.width, h: board.height, me: info.me, players: info.players || [],
                 names: info.names || {}, ui: ui, myTurn: myTurn() }
    }
    function turnOf(s) {
        try { return game && game.turn ? game.turn(s) : null } catch (e) { return null }
    }
    function myTurn() {
        if (!info.started || info.ended || !state) return false
        const t = turnOf(state)
        return t === null || t === undefined || t === info.me
    }
    function reload() {
        info = bridge.gameSession(session)
        if (!game || !info.session) { board.requestPaint(); return }
        try {
            let s = game.setup(info.players, info.seed)
            const moves = info.moves || []
            for (let i = 0; i < moves.length; i++) {
                const t = turnOf(s)
                if (t !== null && t !== undefined && t !== moves[i].sender) continue
                try { s = game.move(s, moves[i].sender, moves[i].m) } catch (e) { /* illegal: ignored everywhere */ }
            }
            state = s
            st = game.status ? game.status(state, info.names || {}) : { over: false, text: "" }
        } catch (e) {
            error = "Game error: " + e
        }
        rtTimer.running = info.started && !info.ended && !!game.tick
        board.requestPaint()
    }
    function input(ev) {
        if (!game || !game.input || !state) return
        let r = null
        try { r = game.input(state, view(), ev) } catch (e) { return }
        if (!r) return
        if (r.ui) { ui = Object.assign({}, ui, r.ui); board.requestPaint() }
        if (r.move !== undefined && r.move !== null) {
            if (!myTurn()) { bridge.errorOccurred("not your turn"); return }
            try { game.move(state, info.me, r.move) } catch (e) { bridge.errorOccurred("illegal move: " + e); return }
            ui = ({})
            bridge.gameAction(session, "move", r.move)
        }
        if (r.rt !== undefined) bridge.gameRtSend(session, r.rt)
    }

    Connections {
        target: bridge
        function onGameEvent(sid) { if (sid === root.session && root.visible) root.reload() }
        function onGameRt(sid, sender, body) {
            if (sid !== root.session || !root.game || !root.game.onRt) return
            try { root.state = root.game.onRt(root.state, sender, body); board.requestPaint() } catch (e) {}
        }
    }

    Timer {
        id: rtTimer
        interval: 33; repeat: true
        property double last: 0
        onTriggered: {
            const now = Date.now()
            const dt = last ? (now - last) / 1000 : 0.033
            last = now
            try {
                const r = root.game.tick(root.state, dt, root.view())
                if (r) { root.state = r.state; if (r.rt !== undefined) bridge.gameRtSend(root.session, r.rt) }
            } catch (e) {}
            board.requestPaint()
        }
    }

    MouseArea { anchors.fill: parent }  // modal

    ColumnLayout {
        anchors.fill: parent; anchors.margins: 20; spacing: 10

        RowLayout {
            Layout.fillWidth: true
            Text { text: (root.info.icon || "🎮") + "  " + (root.info.name || "Game")
                   color: Theme.textPrimary; font.pixelSize: 18; font.bold: true }
            Item { Layout.fillWidth: true }
            Button { text: "Resign"; visible: !!(root.info.started && !root.info.ended && root.info.amPlayer && !root.st.over)
                     onClicked: bridge.gameAction(root.session, "resign", {}) }
            Button { text: "Close (Esc)"; onClicked: root.close() }
        }

        Text {
            Layout.fillWidth: true
            wrapMode: Text.Wrap
            color: root.error ? Theme.error : Theme.textMuted
            font.pixelSize: 13
            text: root.error ? root.error
                : !root.info.started ? ("Players: " + (root.info.joined || []).map(p => root.info.names[p] || p).join(", ")
                                        + " — " + root.info.status)
                : root.info.ended ? root.info.status
                : root.st.over ? root.st.text
                : (root.st.text || "") + (root.myTurn() ? "  ·  your move" : "  ·  waiting for opponent")
        }

        RowLayout {
            visible: !!root.info.session && !root.info.started && !root.info.ended
            spacing: 8
            Button { text: "Join"; visible: !!(root.info.invitedMe === true)
                     onClicked: bridge.gameAction(root.session, "join", {}) }
            Button { text: "Decline"; visible: !!(root.info.invitedMe === true)
                     onClicked: { bridge.gameAction(root.session, "decline", {}); root.close() } }
            Button { text: "Start now"
                     visible: !!(root.info.creator === root.info.me && (root.info.joined || []).length >= (root.info.min || 2))
                     onClicked: bridge.gameAction(root.session, "start", { players: root.info.joined }) }
        }

        Item {
            Layout.fillWidth: true; Layout.fillHeight: true
            Canvas {
                id: board
                property real aspect: (root.game && root.game.aspect) ? root.game.aspect : 1.0
                width: Math.min(parent.width, parent.height * aspect)
                height: width / aspect
                anchors.centerIn: parent
                renderStrategy: Canvas.Immediate
                onPaint: {
                    const ctx = getContext("2d")
                    ctx.reset()
                    ctx.fillStyle = "#151820"
                    ctx.fillRect(0, 0, width, height)
                    if (!root.game || !root.state) return
                    try { root.game.render(ctx, root.state, root.view()) }
                    catch (e) { ctx.fillStyle = "#ef4444"; ctx.fillText("render error: " + e, 10, 20) }
                }
                MouseArea {
                    anchors.fill: parent
                    hoverEnabled: true
                    onClicked: (m) => root.input({ type: "click", x: m.x, y: m.y })
                }
            }
        }
    }

    Keys.onEscapePressed: root.close()
    Keys.onPressed: (e) => {
        const names = { [Qt.Key_Left]: "ArrowLeft", [Qt.Key_Right]: "ArrowRight",
                        [Qt.Key_Up]: "ArrowUp", [Qt.Key_Down]: "ArrowDown", [Qt.Key_Space]: " " }
        const k = names[e.key] || e.text
        if (k) { root.input({ type: "key", key: k, down: true }); e.accepted = true }
    }
    Keys.onReleased: (e) => {
        if (e.isAutoRepeat) return
        const names = { [Qt.Key_Left]: "ArrowLeft", [Qt.Key_Right]: "ArrowRight",
                        [Qt.Key_Up]: "ArrowUp", [Qt.Key_Down]: "ArrowDown", [Qt.Key_Space]: " " }
        const k = names[e.key] || e.text
        if (k) root.input({ type: "key", key: k, down: false })
    }
}
