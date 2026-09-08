import QtQuick
import QtQuick.Controls

Row {
    id: root

    property real value: 0
    property int maximum: 5
    property int starSize: 22
    property color accent: "#ffe66d"
    property bool animateChanges: true

    spacing: Math.max(2, starSize * 0.08)
    Accessible.role: Accessible.StaticText
    Accessible.name: Math.round(value) + " of " + maximum + " stars"

    Repeater {
        model: root.maximum

        Label {
            required property int index

            text: "\u2605"
            font.pixelSize: root.starSize
            color: index < Math.floor(root.value) ? root.accent : "#3c4254"
            scale: index < Math.floor(root.value) ? 1.0 : 0.86

            Behavior on color {
                enabled: root.animateChanges
                ColorAnimation { duration: 180 }
            }

            Behavior on scale {
                enabled: root.animateChanges
                NumberAnimation { duration: 220; easing.type: Easing.OutBack }
            }
        }
    }
}
