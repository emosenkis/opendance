import QtQuick
import QtQuick.Controls

GlassPanel {
    id: root

    property var player: null
    property int playerNumber: 1
    property color playerColor: playerNumber === 1 ? "#55f7ff" : "#ff4fcb"
    property bool reducedMotion: false
    readonly property real contentScale: Math.min(1, height / 96)

    function value(name, fallback) {
        return player && player[name] !== undefined ? player[name] : fallback
    }

    accent: playerColor
    implicitWidth: 248
    implicitHeight: 96

    Row {
        anchors.fill: parent
        anchors.margins: 13 * root.contentScale
        spacing: 12 * root.contentScale

        Rectangle {
            anchors.verticalCenter: parent.verticalCenter
            width: 48 * root.contentScale
            height: 48 * root.contentScale
            radius: width / 2
            color: root.player
                   ? Qt.rgba(root.playerColor.r, root.playerColor.g, root.playerColor.b, 0.22)
                   : "#202638"
            border.width: 2
            border.color: root.player ? root.playerColor : "#555e72"

            Label {
                anchors.centerIn: parent
                text: root.player ? "P" + root.playerNumber : "+"
                color: root.player ? "#ffffff" : "#8d97ab"
                font.pixelSize: 17 * root.contentScale
                font.weight: Font.Black
            }

            SequentialAnimation on scale {
                running: !!root.player && !root.reducedMotion
                loops: Animation.Infinite
                NumberAnimation { to: 1.08; duration: 650; easing.type: Easing.InOutSine }
                NumberAnimation { to: 1.0; duration: 650; easing.type: Easing.InOutSine }
            }
        }

        Column {
            anchors.verticalCenter: parent.verticalCenter
            width: parent.width - 60 * root.contentScale
            spacing: 2 * root.contentScale

            Row {
                width: parent.width

                Label {
                    width: parent.width - comboLabel.width
                    text: root.player ? root.value("name", "PLAYER " + root.playerNumber) : "STEP IN TO JOIN"
                    color: root.player ? "#f8fbff" : "#8d97ab"
                    font.pixelSize: 12 * root.contentScale
                    font.weight: Font.Bold
                    font.letterSpacing: 1
                    elide: Text.ElideRight
                }

                Label {
                    id: comboLabel
                    visible: root.player && root.value("combo", root.value("max_combo", 0)) > 1
                    text: root.value("combo", root.value("max_combo", 0)) + "x"
                    color: root.playerColor
                    font.pixelSize: 12 * root.contentScale
                    font.weight: Font.Black
                }
            }

            Label {
                text: root.player ? Number(root.value("score", 0)).toLocaleString(Qt.locale(), "f", 0) : "GET READY"
                color: "#ffffff"
                font.pixelSize: 25 * root.contentScale
                font.weight: Font.Black
            }

            StarMeter {
                value: root.value("stars", 0)
                starSize: 14 * root.contentScale
                accent: root.playerColor
                animateChanges: !root.reducedMotion
            }
        }
    }
}
