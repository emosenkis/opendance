import QtQuick

Rectangle {
    property color accent: "#55f7ff"
    property real accentOpacity: 0.13

    radius: 18
    color: "#d9131725"
    border.width: 1
    border.color: Qt.rgba(accent.r, accent.g, accent.b, accentOpacity + 0.12)

    Rectangle {
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.top: parent.top
        anchors.margins: 1
        height: Math.max(1, parent.height * 0.28)
        radius: parent.radius - 1
        opacity: 0.25
        gradient: Gradient {
            GradientStop { position: 0.0; color: Qt.rgba(accent.r, accent.g, accent.b, 0.18) }
            GradientStop { position: 1.0; color: "transparent" }
        }
    }
}
