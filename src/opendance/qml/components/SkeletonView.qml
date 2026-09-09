import QtQuick

Item {
    id: root

    property var people: []
    property color lineColor: "#55f7ff"
    property bool mirror: true
    property bool showBoxes: true
    property bool showLabels: true
    property real minimumConfidence: 0.2
    property real lineScale: 1.0

    function number(value, fallback) {
        var converted = Number(value)
        return isNaN(converted) ? fallback : converted
    }

    function keypoints(person) {
        if (!person)
            return []
        if (person.keypoints !== undefined)
            return person.keypoints
        if (person.pose !== undefined)
            return person.pose
        return person.length !== undefined ? person : []
    }

    function point(keypoint) {
        if (!keypoint)
            return { "x": 0, "y": 0, "c": 0 }
        if (keypoint.length !== undefined) {
            return {
                "x": number(keypoint[0], 0),
                "y": number(keypoint[1], 0),
                "c": keypoint.length > 2 ? number(keypoint[2], 1) : 1
            }
        }
        return {
            "x": number(keypoint.x, 0),
            "y": number(keypoint.y, 0),
            "c": number(keypoint.confidence !== undefined ? keypoint.confidence
                                                           : keypoint.score !== undefined ? keypoint.score : 1, 1)
        }
    }

    function paintSkeleton(context, person, personIndex) {
        var joints = keypoints(person)
        if (!joints || joints.length < 1)
            return

        var links = [
            [5, 7], [7, 9], [6, 8], [8, 10], [5, 6],
            [5, 11], [6, 12], [11, 12], [11, 13], [13, 15],
            [12, 14], [14, 16], [0, 1], [0, 2], [1, 3], [2, 4]
        ]

        context.lineCap = "round"
        context.lineJoin = "round"
        context.lineWidth = Math.max(2, Math.min(root.width, root.height) * 0.012 * root.lineScale)
        var colorIndex = person && person.dancer_index !== undefined
                       ? number(person.dancer_index, personIndex) : personIndex
        context.strokeStyle = [root.lineColor, "#ff4fcb", "#ffe66d", "#7cff9b",
                               "#ff855f", "#b896ff"][Math.abs(Math.floor(colorIndex)) % 6]

        for (var i = 0; i < links.length; ++i) {
            var first = point(joints[links[i][0]])
            var second = point(joints[links[i][1]])
            if (first.c < root.minimumConfidence || second.c < root.minimumConfidence)
                continue
            context.beginPath()
            context.moveTo((root.mirror ? 1 - first.x : first.x) * canvas.width, first.y * canvas.height)
            context.lineTo((root.mirror ? 1 - second.x : second.x) * canvas.width, second.y * canvas.height)
            context.stroke()
        }

        for (var joint = 0; joint < joints.length; ++joint) {
            var current = point(joints[joint])
            if (current.c < root.minimumConfidence)
                continue
            context.beginPath()
            context.fillStyle = joint % 2 === 0 ? "#ffffff" : context.strokeStyle
            context.arc((root.mirror ? 1 - current.x : current.x) * canvas.width,
                        current.y * canvas.height,
                        Math.max(2.3, context.lineWidth * 0.72), 0, Math.PI * 2)
            context.fill()
        }

        if (root.showBoxes && person && person.bbox && person.bbox.length >= 4) {
            var box = person.bbox
            var bx = number(box[0], 0)
            var by = number(box[1], 0)
            var bw = number(box[2], 0)
            var bh = number(box[3], 0)
            context.shadowBlur = 0
            context.lineWidth = 1.2
            context.setLineDash([6, 5])
            context.strokeRect((root.mirror ? 1 - bx - bw : bx) * canvas.width,
                               by * canvas.height, bw * canvas.width, bh * canvas.height)
            context.setLineDash([])
        }

        if (root.showLabels) {
            var label = person && person.name !== undefined ? person.name
                      : person && person.player !== undefined ? "P" + person.player
                      : "DANCER " + (personIndex + 1)
            var anchor = point(joints[0])
            context.shadowBlur = 0
            context.fillStyle = "#ffffff"
            context.font = "bold " + Math.max(10, canvas.height * 0.045) + "px sans-serif"
            context.textAlign = "center"
            context.fillText(label,
                             (root.mirror ? 1 - anchor.x : anchor.x) * canvas.width,
                             Math.max(16, anchor.y * canvas.height - 14))
        }
    }

    Canvas {
        id: canvas
        anchors.fill: parent
        renderStrategy: Canvas.Threaded
        antialiasing: true

        onPaint: {
            var context = getContext("2d")
            context.reset()
            context.clearRect(0, 0, width, height)
            var list = root.people || []
            for (var i = 0; i < list.length; ++i)
                root.paintSkeleton(context, list[i], i)
        }
    }

    onPeopleChanged: canvas.requestPaint()
    onWidthChanged: canvas.requestPaint()
    onHeightChanged: canvas.requestPaint()
    onMirrorChanged: canvas.requestPaint()
}
