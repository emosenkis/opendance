import QtQuick

Item {
    id: root

    property bool reducedMotion: false
    property color firstAccent: "#55f7ff"
    property color secondAccent: "#ff4fcb"

    Rectangle {
        anchors.fill: parent
        gradient: Gradient {
            GradientStop { position: 0.0; color: "#090b18" }
            GradientStop { position: 0.52; color: "#121128" }
            GradientStop { position: 1.0; color: "#080a12" }
        }
    }

    Rectangle {
        id: cyanGlow
        width: Math.max(root.width, root.height) * 0.62
        height: width
        radius: width / 2
        x: -width * 0.34
        y: -height * 0.28
        color: root.firstAccent
        opacity: 0.08

        SequentialAnimation on scale {
            running: !root.reducedMotion
            loops: Animation.Infinite
            NumberAnimation { to: 1.13; duration: 3500; easing.type: Easing.InOutSine }
            NumberAnimation { to: 0.96; duration: 3500; easing.type: Easing.InOutSine }
        }
    }

    Rectangle {
        id: pinkGlow
        width: Math.max(root.width, root.height) * 0.72
        height: width
        radius: width / 2
        x: root.width - width * 0.58
        y: root.height - height * 0.58
        color: root.secondAccent
        opacity: 0.07

        SequentialAnimation on scale {
            running: !root.reducedMotion
            loops: Animation.Infinite
            NumberAnimation { to: 0.92; duration: 4200; easing.type: Easing.InOutSine }
            NumberAnimation { to: 1.1; duration: 4200; easing.type: Easing.InOutSine }
        }
    }

    Canvas {
        id: grid
        anchors.fill: parent
        opacity: 0.22

        onPaint: {
            var context = getContext("2d")
            context.reset()
            context.clearRect(0, 0, width, height)
            context.strokeStyle = "#2655f7ff"
            context.lineWidth = 1
            var spacing = Math.max(44, width / 22)
            for (var x = 0; x <= width; x += spacing) {
                context.beginPath()
                context.moveTo(x, 0)
                context.lineTo(x, height)
                context.stroke()
            }
            for (var y = 0; y <= height; y += spacing) {
                context.beginPath()
                context.moveTo(0, y)
                context.lineTo(width, y)
                context.stroke()
            }
        }

        onWidthChanged: requestPaint()
        onHeightChanged: requestPaint()
    }

    Repeater {
        model: 26

        Rectangle {
            required property int index

            readonly property real seedX: ((index * 47) % 101) / 101
            readonly property real seedY: ((index * 73) % 97) / 97

            x: seedX * root.width
            y: seedY * root.height
            width: 2 + index % 4
            height: width
            radius: width / 2
            color: index % 2 ? root.firstAccent : root.secondAccent
            opacity: 0.18 + (index % 5) * 0.07

            SequentialAnimation on opacity {
                running: !root.reducedMotion
                loops: Animation.Infinite
                PauseAnimation { duration: index * 71 }
                NumberAnimation { to: 0.75; duration: 500 + index * 21; easing.type: Easing.InOutSine }
                NumberAnimation { to: 0.16; duration: 900 + index * 17; easing.type: Easing.InOutSine }
            }
        }
    }
}
