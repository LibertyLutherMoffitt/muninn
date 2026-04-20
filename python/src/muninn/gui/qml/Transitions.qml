pragma Singleton
import QtQuick

// Reusable animation durations / easing — import via Theme singleton.
// Usage: NumberAnimation { duration: Transitions.chatSwitch; easing.type: Transitions.ease }
QtObject {
    readonly property int chatSwitch: 80
    readonly property int bubbleFade: 120
    readonly property int peerPulse: 200
    readonly property int modalOpen: 100
    readonly property int modeBorder: 60
    readonly property int scrollSnap: 150
    readonly property int easingType: Easing.OutQuad
}
