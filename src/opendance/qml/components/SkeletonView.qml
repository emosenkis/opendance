import QtQuick

Item {
    id: root

    property var people: []
    property color lineColor: "#55f7ff"
    property color arrowColor: "#ffe66d"
    property bool mirror: true
    property bool showBoxes: true
    property bool showLabels: true
    property bool fitSinglePerson: false
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

    function fittedBounds(person) {
        if (!fitSinglePerson)
            return null
        var visible = []
        var joints = keypoints(person)
        for (var index = 0; index < joints.length; ++index) {
            var joint = point(joints[index])
            if (joint.c >= minimumConfidence)
                visible.push(joint)
        }
        var arrows = person && person.cue_arrows ? person.cue_arrows : []
        for (var arrowIndex = 0; arrowIndex < arrows.length; ++arrowIndex) {
            if (arrows[arrowIndex].from)
                visible.push(point(arrows[arrowIndex].from))
            if (arrows[arrowIndex].to)
                visible.push(point(arrows[arrowIndex].to))
        }
        if (!visible.length)
            return null
        var xs = visible.map(function(value) { return root.mirror ? 1 - value.x : value.x })
        var ys = visible.map(function(value) { return value.y })
        return { "left": Math.min.apply(null, xs), "right": Math.max.apply(null, xs),
                 "top": Math.min.apply(null, ys), "bottom": Math.max.apply(null, ys) }
    }

    function canvasPoint(keypoint, bounds) {
        var value = point(keypoint)
        var x = root.mirror ? 1 - value.x : value.x
        if (!bounds)
            return { "x": x * canvas.width, "y": value.y * canvas.height }
        var margin = Math.min(canvas.width, canvas.height) * 0.12
        var spanX = Math.max(0.05, bounds.right - bounds.left)
        var spanY = Math.max(0.05, bounds.bottom - bounds.top)
        var scale = Math.min((canvas.width - 2 * margin) / spanX,
                             (canvas.height - 2 * margin) / spanY)
        var left = (canvas.width - spanX * scale) / 2
        var top = (canvas.height - spanY * scale) / 2
        return { "x": left + (x - bounds.left) * scale,
                 "y": top + (value.y - bounds.top) * scale }
    }

    function paintSkeleton(context, person, personIndex) {
        var joints = keypoints(person)
        if (!joints || joints.length < 1)
            return
        var bounds = fittedBounds(person)

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
            var firstCanvas = canvasPoint(first, bounds)
            var secondCanvas = canvasPoint(second, bounds)
            context.beginPath()
            context.moveTo(firstCanvas.x, firstCanvas.y)
            context.lineTo(secondCanvas.x, secondCanvas.y)
            context.stroke()
        }

        for (var joint = 0; joint < joints.length; ++joint) {
            var current = point(joints[joint])
            if (current.c < root.minimumConfidence)
                continue
            var currentCanvas = canvasPoint(current, bounds)
            context.beginPath()
            context.fillStyle = joint % 2 === 0 ? "#ffffff" : context.strokeStyle
            context.arc(currentCanvas.x, currentCanvas.y,
                        Math.max(2.3, context.lineWidth * 0.72), 0, Math.PI * 2)
            context.fill()
        }

        var arrows = person && person.cue_arrows ? person.cue_arrows : []
        context.strokeStyle = root.arrowColor
        context.fillStyle = root.arrowColor
        context.lineWidth = Math.max(3, context.lineWidth * 1.35)
        for (var arrowIndex = 0; arrowIndex < arrows.length; ++arrowIndex) {
            var arrow = arrows[arrowIndex]
            if (!arrow.from || !arrow.to)
                continue
            var from = canvasPoint(arrow.from, bounds)
            var to = canvasPoint(arrow.to, bounds)
            var fromX = from.x
            var fromY = from.y
            var toX = to.x
            var toY = to.y
            var angle = Math.atan2(toY - fromY, toX - fromX)
            var head = Math.max(7, context.lineWidth * 2.5)
            context.beginPath()
            context.moveTo(fromX, fromY)
            context.lineTo(toX, toY)
            context.stroke()
            context.beginPath()
            context.moveTo(toX, toY)
            context.lineTo(toX - head * Math.cos(angle - Math.PI / 6),
                           toY - head * Math.sin(angle - Math.PI / 6))
            context.lineTo(toX - head * Math.cos(angle + Math.PI / 6),
                           toY - head * Math.sin(angle + Math.PI / 6))
            context.closePath()
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
                      : "DANCER"
            var anchor = canvasPoint(joints[0], bounds)
            context.shadowBlur = 0
            context.fillStyle = "#ffffff"
            context.font = "bold " + Math.max(10, canvas.height * 0.045) + "px sans-serif"
            context.textAlign = "center"
            context.fillText(label,
                             anchor.x, Math.max(16, anchor.y - 14))
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
    onFitSinglePersonChanged: canvas.requestPaint()
}
