import QtQuick
import QtQuick.Controls

Switch {
    id: control

    property color accent: "#55f7ff"
    property string description: ""

    implicitWidth: 244
    implicitHeight: description.length > 0 ? 56 : 44
    spacing: 12
    focusPolicy: Qt.StrongFocus
    hoverEnabled: true

    Accessible.name: text
    Accessible.description: description

    indicator: Rectangle {
        implicitWidth: 48
        implicitHeight: 26
        x: control.leftPadding
        y: (control.height - height) / 2
        radius: height / 2
        color: control.checked
               ? Qt.rgba(control.accent.r, control.accent.g, control.accent.b, 0.34)
               : "#252a3b"
        border.width: control.visualFocus ? 3 : 1
        border.color: control.visualFocus ? "#ffffff" : (control.checked ? control.accent : "#535c72")

        Rectangle {
            width: 18
            height: 18
            radius: 9
            y: 4
            x: control.checked ? parent.width - width - 4 : 4
            color: control.checked ? control.accent : "#9aa4b9"

            Behavior on x {
                NumberAnimation { duration: 130; easing.type: Easing.OutCubic }
            }
        }
    }

    contentItem: Column {
        leftPadding: control.indicator.width + control.spacing
        anchors.verticalCenter: parent.verticalCenter
        spacing: 1

        Label {
            text: control.text
            color: control.enabled ? "#f8fbff" : "#697186"
            font.pixelSize: 14
            font.weight: Font.DemiBold
        }

        Label {
            visible: control.description.length > 0
            text: control.description
            color: "#8b96ad"
            font.pixelSize: 10
        }
    }
}
