pragma ComponentBehavior: Bound

import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Item {
    id: root

    property var people: []
    property string heading: "UP NEXT"
    property string moveName: ""
    property int selectedDancer: -1
    property real uiScale: 1
    signal dancerSelected(int dancerIndex)

    function dancerIndex(person, fallback) {
        return Number(person && person.dancer_index !== undefined
                      ? person.dancer_index : fallback)
    }

    function dancerColor(index) {
        return ["#55f7ff", "#ff4fcb", "#ffe66d", "#7cff9b", "#ff855f", "#b896ff"]
               [Math.abs(index) % 6]
    }

    function colorName(index) {
        return ["CYAN", "PINK", "GOLD", "GREEN", "CORAL", "VIOLET"]
               [Math.abs(index) % 6]
    }

    ColumnLayout {
        anchors.fill: parent
        spacing: 5 * root.uiScale

        RowLayout {
            Layout.fillWidth: true

            Label {
                text: root.heading
                color: "#ffffff"
                font.pixelSize: 10 * root.uiScale
                font.weight: Font.Black
                font.letterSpacing: 1.4
            }

            Label {
                Layout.fillWidth: true
                text: root.moveName
                visible: text.length > 0
                color: "#ffe66d"
                font.pixelSize: 10 * root.uiScale
                font.weight: Font.Black
                elide: Text.ElideRight
                horizontalAlignment: Text.AlignRight
            }
        }

        RowLayout {
            Layout.fillWidth: true
            Layout.fillHeight: true
            spacing: 6 * root.uiScale

            Repeater {
                model: root.people || []

                Rectangle {
                    id: card
                    required property int index
                    required property var modelData
                    readonly property int dancer: root.dancerIndex(modelData, index)
                    readonly property color accent: root.dancerColor(dancer)

                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    radius: 10 * root.uiScale
                    color: "#b50b0f1c"
                    border.width: root.selectedDancer === dancer ? 3 : 1
                    border.color: accent

                    SkeletonView {
                        anchors.fill: parent
                        anchors.margins: 5 * root.uiScale
                        people: [card.modelData]
                        lineColor: card.accent
                        arrowColor: card.accent
                        mirror: false
                        showBoxes: false
                        showLabels: false
                        fitSinglePerson: true
                        lineScale: 1.05
                    }

                    Label {
                        anchors.left: parent.left
                        anchors.bottom: parent.bottom
                        anchors.margins: 5 * root.uiScale
                        text: root.colorName(card.dancer)
                        color: card.accent
                        font.pixelSize: 8 * root.uiScale
                        font.weight: Font.Black
                    }

                    TapHandler { onTapped: root.dancerSelected(card.dancer) }
                }
            }
        }
    }
}
