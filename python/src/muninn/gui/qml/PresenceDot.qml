import QtQuick

/**
 * The single source of truth for what a peer's connectivity looks like.
 *
 * Used by the peer list and the chat header. When these were drawn
 * independently the two could disagree about the same peer, which reads as a
 * bug rather than as two views of one fact.
 *
 * `state` matches the vocabulary of `presence.py` / `bridge.peerPresence`:
 * direct, relay, unreachable, nearby, offline, group.
 */
Rectangle {
    id: root

    // Deliberately NOT called `state`: Item.state is the built-in QML state
    // machine, and shadowing it binds the wrong property.
    property string presenceState: "offline"
    property bool showPulse: true

    readonly property color stateColor:
          presenceState === "direct"      ? Theme.success
        : presenceState === "relay"       ? Theme.warning
        : presenceState === "unreachable" ? Theme.error
        : presenceState === "nearby"      ? Theme.warning
        : presenceState === "group"       ? Theme.accent
                                  : Theme.textFaint

    // A device that is right there and still will not connect is a different
    // problem from one out of range, so it gets full weight; merely "nearby"
    // is dimmed to read as provisional.
    readonly property real stateOpacity: presenceState === "nearby" ? 0.6 : 1.0

    implicitWidth: 10
    implicitHeight: 10
    radius: width / 2
    color: stateColor
    opacity: stateOpacity

    Behavior on color { ColorAnimation { duration: 180 } }
    Behavior on opacity { NumberAnimation { duration: 180 } }

    // A slow halo on a live link — the one state worth drawing the eye to.
    Rectangle {
        anchors.centerIn: parent
        width: parent.width
        height: parent.height
        radius: width / 2
        color: "transparent"
        border.color: root.stateColor
        border.width: 1
        visible: root.showPulse && root.presenceState === "direct"

        SequentialAnimation on scale {
            running: root.showPulse && root.presenceState === "direct"
            loops: Animation.Infinite
            NumberAnimation { from: 1.0; to: 2.2; duration: 1600; easing.type: Easing.OutQuad }
            PauseAnimation { duration: 400 }
        }
        SequentialAnimation on opacity {
            running: root.showPulse && root.presenceState === "direct"
            loops: Animation.Infinite
            NumberAnimation { from: 0.5; to: 0.0; duration: 1600; easing.type: Easing.OutQuad }
            PauseAnimation { duration: 400 }
        }
    }
}
