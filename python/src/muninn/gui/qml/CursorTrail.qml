import QtQuick

// Transparent overlay that draws a short cursor trail along a polyline.
// Call `trail([{x,y}, {x,y}, ...])` to animate from the first point through
// all subsequent points. Each segment runs in `segmentDuration` ms.
//
// The trail is rendered as N small dots: a "head" dot follows the segment
// progress, and trailing dots follow at staggered offsets, fading as they
// go. Once the last segment finishes the dots fade out.
Item {
    id: root
    z: 1000
    // Pass-through: never block input.
    enabled: false

    property color trailColor: Theme.accent
    property int dotCount: 9
    property int segmentDuration: 180
    // How far back in time (within a segment) each successive dot trails.
    // 1.0 = dotCount * trailSpacing covers the whole segment.
    property real trailSpacing: 0.10

    // ---- Internal state ----
    property real _srcX: 0
    property real _srcY: 0
    property real _dstX: 0
    property real _dstY: 0
    property real _progress: 0   // 0..1 within current segment
    property real _alpha: 0      // global fade in/out
    property var _points: []
    property int _idx: 0

    Behavior on _alpha {
        NumberAnimation { duration: 120; easing.type: Easing.OutQuad }
    }

    function trail(points) {
        if (!points || points.length < 2) return
        // Filter out any null points (callers may fail to find a row).
        const pts = []
        for (let i = 0; i < points.length; i++) {
            const p = points[i]
            if (p && typeof p.x === "number" && typeof p.y === "number")
                pts.push(p)
        }
        if (pts.length < 2) return
        seg.stop()
        fadeOut.stop()
        root._points = pts
        root._idx = 0
        root._alpha = 1
        _playSegment()
    }

    function _playSegment() {
        if (root._idx >= root._points.length - 1) {
            fadeOut.start()
            return
        }
        const a = root._points[root._idx]
        const b = root._points[root._idx + 1]
        root._srcX = a.x; root._srcY = a.y
        root._dstX = b.x; root._dstY = b.y
        root._progress = 0
        seg.start()
    }

    NumberAnimation {
        id: seg
        target: root
        property: "_progress"
        from: 0
        to: 1
        duration: root.segmentDuration
        easing.type: Easing.OutCubic
        onFinished: {
            root._idx += 1
            root._playSegment()
        }
    }

    SequentialAnimation {
        id: fadeOut
        PauseAnimation { duration: 80 }
        NumberAnimation {
            target: root
            property: "_alpha"
            to: 0
            duration: 180
            easing.type: Easing.InQuad
        }
    }

    Repeater {
        model: root.dotCount
        delegate: Rectangle {
            // Each dot is at (progress - i*spacing), clamped to [0, 1].
            readonly property real pp: Math.max(
                0,
                Math.min(1, root._progress - index * root.trailSpacing))
            readonly property bool active: pp > 0 && pp <= 1
            readonly property real sz: Math.max(4, 14 - index * 1.1)
            width: sz; height: sz; radius: sz / 2
            color: root.trailColor
            // Head bright, tail faint. Multiplied by global _alpha so the
            // whole trail can fade out cleanly between calls.
            opacity: active
                ? root._alpha * (1 - index / root.dotCount) * 0.85
                : 0
            x: root._srcX + (root._dstX - root._srcX) * pp - sz / 2
            y: root._srcY + (root._dstY - root._srcY) * pp - sz / 2
            visible: opacity > 0.01
        }
    }
}
