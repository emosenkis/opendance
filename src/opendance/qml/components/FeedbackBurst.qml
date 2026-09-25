import QtQuick
import QtQuick.Controls

Item {
    id: root

    property var feedback: null
    property bool reducedMotion: false
    property bool compact: false
    readonly property real contentScale: compact ? Math.min(width / 270, height / 160) : 1

    function value(name, fallback) {
        return feedback && feedback[name] !== undefined ? feedback[name] : fallback
    }

    function feedbackColor() {
        var rating = String(value("grade", value("rating", value("text", "")))).toLowerCase()
        if (rating.indexOf("perfect") >= 0)
            return "#ffe66d"
        if (rating.indexOf("great") >= 0)
            return "#55f7ff"
        if (rating.indexOf("good") >= 0)
            return "#7cff9b"
        return "#ff70d5"
    }

    function showFeedback() {
        var message = value("grade", value("text", value("rating", "")))
        if (!message)
            return
        ratingLabel.text = String(message).toUpperCase()
        pointsLabel.text = value("points", 0) > 0 ? "+" + value("points", 0) : ""
        burst.stop()
        quietTimer.stop()
        if (reducedMotion) {
            root.opacity = 1
            root.scale = 1
            quietTimer.restart()
        } else {
            burst.restart()
        }
    }

    width: 270
    height: 160
    opacity: 0
    scale: 0.5
    clip: compact

    Rectangle {
        anchors.fill: parent
        visible: root.compact
        color: "#ef080a12"
        radius: 12
        border.width: 1
        border.color: root.feedbackColor()
    }

    Repeater {
        model: 10

        Rectangle {
            required property int index

            width: (index % 3 === 0 ? 18 : 8) * root.contentScale
            height: width
            radius: index % 2 === 0 ? width / 2 : 2
            color: index % 2 === 0 ? root.feedbackColor() : "#ffffff"
            x: root.width / 2 + Math.cos(index * 0.628) * (52 + (index % 3) * 18) * root.contentScale - width / 2
            y: root.height / 2 + Math.sin(index * 0.628) * (48 + (index % 3) * 14) * root.contentScale - height / 2
            rotation: index * 31
        }
    }

    Column {
        anchors.centerIn: parent
        spacing: root.compact ? -2 : -4

        Label {
            id: ratingLabel
            anchors.horizontalCenter: parent.horizontalCenter
            color: root.feedbackColor()
            font.pixelSize: root.compact ? 18 : 34
            font.weight: Font.Black
            font.letterSpacing: 2
            style: Text.Outline
            styleColor: "#87080b13"
        }

        Label {
            id: pointsLabel
            anchors.horizontalCenter: parent.horizontalCenter
            color: "#ffffff"
            font.pixelSize: root.compact ? 12 : 18
            font.weight: Font.Bold
        }
    }

    SequentialAnimation {
        id: burst
        ScriptAction {
            script: {
                root.opacity = 1
                root.scale = 0.72
            }
        }
        ParallelAnimation {
            NumberAnimation { target: root; property: "scale"; to: 1.15; duration: 190; easing.type: Easing.OutBack }
        }
        PauseAnimation { duration: 620 }
        ParallelAnimation {
            NumberAnimation { target: root; property: "opacity"; to: 0; duration: 260 }
            NumberAnimation { target: root; property: "scale"; to: 1.35; duration: 260; easing.type: Easing.InQuad }
        }
    }

    Timer {
        id: quietTimer
        interval: 700
        onTriggered: root.opacity = 0
    }

    onFeedbackChanged: showFeedback()
    Component.onCompleted: showFeedback()
}
