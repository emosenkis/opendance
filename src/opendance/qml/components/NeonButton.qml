import QtQuick
import QtQuick.Controls

Button {
    id: control

    property color accent: "#55f7ff"
    property bool primary: false
    property bool compact: false
    property string hint: ""

    implicitWidth: compact ? 116 : 178
    implicitHeight: compact ? 42 : (hint.length > 0 ? 66 : 54)
    padding: compact ? 10 : 14
    focusPolicy: Qt.StrongFocus
    hoverEnabled: true

    Accessible.name: text
    Accessible.description: hint

    contentItem: Column {
        spacing: 2
        anchors.centerIn: parent

        Label {
            anchors.horizontalCenter: parent.horizontalCenter
            text: control.text
            color: control.enabled ? (control.primary ? "#071019" : "#f8fbff") : "#677083"
            font.pixelSize: control.compact ? 14 : 16
            font.weight: Font.Bold
            font.letterSpacing: 1.1
            horizontalAlignment: Text.AlignHCenter
        }

        Label {
            visible: control.hint.length > 0
            anchors.horizontalCenter: parent.horizontalCenter
            text: control.hint
            color: control.primary ? "#183441" : "#91a0b9"
            font.pixelSize: 10
            font.weight: Font.Medium
            horizontalAlignment: Text.AlignHCenter
        }
    }

    background: Rectangle {
        radius: control.compact ? 10 : 14
        color: control.down
               ? Qt.darker(control.accent, 1.35)
               : control.primary
                 ? control.accent
                 : control.hovered || control.visualFocus
                   ? Qt.rgba(control.accent.r, control.accent.g, control.accent.b, 0.18)
                   : "#171b2b"
        border.width: control.visualFocus ? 3 : 1
        border.color: control.enabled
                      ? (control.visualFocus ? "#ffffff" : control.accent)
                      : "#3b4152"

        Behavior on color {
            ColorAnimation { duration: 100 }
        }
    }

    scale: down ? 0.97 : (hovered || visualFocus ? 1.025 : 1.0)
    opacity: enabled ? 1.0 : 0.55

    Behavior on scale {
        NumberAnimation { duration: 90; easing.type: Easing.OutQuad }
    }
}
