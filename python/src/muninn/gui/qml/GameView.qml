import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

// Host for JavaScript game plugins (see muninn/games/README.md). The plugin
// code comes from the session's invite, so all players run identical rules.
Rectangle {
    id: root
    color: Theme.bg
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
    // host-level zoom/pan: wheel zooms at the cursor, drag pans, 0 resets
    property real zoom: 1
    property real panX: 0
    property real panY: 0

    function clampPan() {
        if (zoom <= 1) { zoom = 1; panX = 0; panY = 0; return }
        panX = Math.min(0, Math.max(board.width - board.width * zoom, panX))
        panY = Math.min(0, Math.max(board.height - board.height * zoom, panY))
    }
    function zoomAt(f, x, y) {
        const nz = Math.max(1, Math.min(8, zoom * f))
        panX = x - (x - panX) * nz / zoom
        panY = y - (y - panY) * nz / zoom
        zoom = nz
        clampPan(); board.requestPaint()
    }

    function open(sid) {
        session = sid
        error = ""; ui = ({}); game = null; zoom = 1; panX = 0; panY = 0
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
    function grabFocus() { forceActiveFocus() }
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
            let s = game.setup(info.players, info.seed, info.opts || {})
            const moves = info.moves || []
            for (let i = 0; i < moves.length; i++) {
                const t = turnOf(s)
                if (t !== null && t !== undefined && t !== moves[i].sender) continue
                try { s = game.move(s, moves[i].sender, moves[i].m) } catch (e) { /* illegal: ignored everywhere */ }
            }
            state = s
            st = game.status ? game.status(state, info.names || {}) : { over: false, text: "" }
            // over but never recorded (an older client, or the mover's view was
            // closed): a player records it now — the first result counts
            if (st.over && !info.result && info.amPlayer && info.started)
                bridge.gameAction(session, "result", { winners: st.winners || [], text: st.text || "" })
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
            let after
            try { after = game.move(state, info.me, r.move) } catch (e) { bridge.errorOccurred("illegal move: " + e); return }
            ui = ({})
            bridge.gameAction(session, "move", r.move)
            // the player whose move ends the game records the result (scoreboard)
            try {
                const end = game.status ? game.status(after, info.names || {}) : null
                if (end && end.over && !info.result)
                    bridge.gameAction(session, "result", { winners: end.winners || [], text: end.text || "" })
            } catch (e) {}
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
            GButton { text: "⤢ Fit (0)"; visible: root.zoom > 1
                      onClicked: { root.zoom = 1; root.clampPan(); board.requestPaint() } }
            Text {
                visible: text !== ""
                text: root.info.app ? bridge.gameScoreLine(root.info.conv || "", root.info.app) : ""
                color: Theme.textMuted; font.pixelSize: 12
                Layout.rightMargin: 8
            }
            GButton { text: "Unsend invite"; danger: true
                      visible: !!(root.info.creator === root.info.me && !root.info.started && !root.info.withdrawn)
                      onClicked: { bridge.gameWithdraw(root.session); root.close() } }
            GButton { text: "Resign"; danger: true
                      visible: !!(root.info.started && !root.info.ended && root.info.amPlayer && !root.st.over)
                      onClicked: bridge.gameAction(root.session, "resign", {}) }
            GButton { text: "Close  Esc"; onClicked: root.close() }
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
                : (root.st.text || "") + (root.info.spectator ? "  ·  👀 watching"
                                         : root.myTurn() ? "  ·  your move" : "  ·  waiting for opponent")
        }

        RowLayout {
            visible: !!root.info.session && !root.info.started && !root.info.ended
            spacing: 8
            GButton { text: "Join"; accent: true; visible: !!(root.info.invitedMe === true)
                      onClicked: bridge.gameAction(root.session, "join", {}) }
            GButton { text: "Decline"; visible: !!(root.info.invitedMe === true)
                      onClicked: { bridge.gameAction(root.session, "decline", {}); root.close() } }
            GButton { text: "Start now"; accent: true
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
                    ctx.translate(root.panX, root.panY)
                    ctx.scale(root.zoom, root.zoom)
                    try { root.game.render(ctx, root.state, root.view()) }
                    catch (e) { ctx.fillStyle = "#ef4444"; ctx.fillText("render error: " + e, 10, 20) }
                }
                MouseArea {
                    anchors.fill: parent
                    hoverEnabled: true
                    property point start
                    property real sx; property real sy
                    property bool dragging: false
                    cursorShape: dragging ? Qt.ClosedHandCursor : Qt.PointingHandCursor
                    onPressed: (m) => { start = Qt.point(m.x, m.y); sx = root.panX; sy = root.panY; dragging = false }
                    onPositionChanged: (m) => {
                        if (!pressed || root.zoom <= 1) return
                        if (!dragging && Math.hypot(m.x - start.x, m.y - start.y) > 6) dragging = true
                        if (dragging) { root.panX = sx + m.x - start.x; root.panY = sy + m.y - start.y
                                        root.clampPan(); board.requestPaint() }
                    }
                    onReleased: (m) => {
                        if (dragging) { dragging = false; return }
                        root.input({ type: "click", x: (m.x - root.panX) / root.zoom, y: (m.y - root.panY) / root.zoom })
                    }
                    onWheel: (w) => root.zoomAt(w.angleDelta.y > 0 ? 1.2 : 1 / 1.2, w.x, w.y)
                }
            }
        }
    }

    component GButton: Rectangle {
        id: gb
        property alias text: gbl.text
        property bool accent: false
        property bool danger: false
        signal clicked()
        implicitHeight: 30; radius: 15; implicitWidth: gbl.implicitWidth + 26
        color: accent ? (gbm.containsMouse ? Qt.lighter(Theme.accent, 1.15) : Theme.accent)
                      : gbm.containsMouse ? Theme.surfaceRaised : "transparent"
        border.color: accent ? "transparent" : danger ? Theme.error : Theme.accentSoft
        Text { id: gbl; anchors.centerIn: parent; font.pixelSize: 12
               color: gb.accent ? "white" : gb.danger ? Theme.error : Theme.textPrimary }
        MouseArea { id: gbm; anchors.fill: parent; hoverEnabled: true; cursorShape: Qt.PointingHandCursor
                    onClicked: gb.clicked() }
    }

    Keys.onEscapePressed: root.close()
    Keys.onPressed: (e) => {
        if (e.text === "0" || e.text === "+" || e.text === "-") {
            if (e.text === "0") { zoom = 1; clampPan(); board.requestPaint() }
            else zoomAt(e.text === "+" ? 1.25 : 0.8, board.width / 2, board.height / 2)
            e.accepted = true; return
        }
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
