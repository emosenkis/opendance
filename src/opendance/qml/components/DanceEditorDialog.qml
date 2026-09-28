pragma ComponentBehavior: Bound

import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import QtMultimedia

Dialog {
    id: dialog
    objectName: "danceEditorDialog"

    required property var appBackend
    property real uiScale: 1
    property int selectedMove: -1
    property int selectedCueDancer: -1
    property bool synchronizedCues: false
    property real rangeStart: 0
    property real rangeEnd: 0

    function value(name, fallback) {
        var state = appBackend.danceEditor || ({})
        return state[name] !== undefined ? state[name] : fallback
    }

    function chooseMove(index) {
        var moves = value("segments", [])
        if (index < 0 || index >= moves.length)
            return
        selectedMove = index
        rangeStart = Number(moves[index].start)
        rangeEnd = Number(moves[index].end)
        rangeStartField.text = rangeStart.toFixed(2)
        rangeEndField.text = rangeEnd.toFixed(2)
        selectedCueDancer = moves[index].dancers.length ? moves[index].dancers[0] : -1
        playhead.value = rangeStart
        editorPlayer.position = rangeStart * 1000
        refreshCue()
        if (synchronizedCues)
            editorPlayer.play()
    }

    function refreshCue() {
        appBackend.previewDanceCue(playhead.value, synchronizedCues, selectedMove)
    }

    function toggleCueTiming() {
        synchronizedCues = !synchronizedCues
        if (synchronizedCues && selectedMove >= 0) {
            editorPlayer.position = rangeStart * 1000
            editorPlayer.play()
        }
        refreshCue()
    }

    function edit(action, extras) {
        var request = extras || ({})
        request.action = action
        if (request.segment === undefined)
            request.segment = selectedMove
        appBackend.applyDanceEdit(request)
        if (action === "cue" || action === "joint")
            refreshCue()
    }

    function begin() {
        appBackend.openDanceEditor()
        selectedMove = -1
        open()
        chooseMove(0)
    }

    function jointName(index) {
        return ["", "", "", "", "", "L shoulder", "R shoulder", "L elbow",
                "R elbow", "L wrist", "R wrist", "L hip", "R hip", "L knee",
                "R knee", "L ankle", "R ankle"][index] || "Joint"
    }

    parent: Overlay.overlay
    modal: true
    focus: true
    width: Math.min(parent ? parent.width * 0.96 : 1160, 1160 * uiScale)
    height: Math.min(parent ? parent.height * 0.94 : 680, 680 * uiScale)
    anchors.centerIn: parent
    padding: 18 * uiScale
    header: null
    footer: null
    onClosed: editorPlayer.stop()

    background: Rectangle {
        color: "#f5080a12"
        radius: 18 * dialog.uiScale
        border.width: 1
        border.color: "#55f7ff"
    }

    contentItem: ColumnLayout {
        spacing: 10 * dialog.uiScale

        RowLayout {
            Layout.fillWidth: true

            Label {
                Layout.fillWidth: true
                text: "DANCE EDITOR — " + dialog.value("title", "")
                color: "#ffffff"
                font.pixelSize: 17 * dialog.uiScale
                font.weight: Font.Black
                font.letterSpacing: 1.2
            }

            NeonButton {
                text: "CLOSE"
                compact: true
                accent: "#ff4fcb"
                onClicked: dialog.close()
            }
        }

        Label {
            Layout.fillWidth: true
            visible: String(dialog.value("status", "")).length > 0
            text: dialog.value("status", "")
            color: String(text).indexOf("failed") >= 0 || String(text).indexOf("Cannot") >= 0
                   ? "#ff70a8" : "#7cff9b"
            wrapMode: Text.WordWrap
        }

        RowLayout {
            Layout.fillWidth: true
            Layout.fillHeight: true
            spacing: 12 * dialog.uiScale

            ColumnLayout {
                Layout.fillWidth: true
                Layout.fillHeight: true
                spacing: 8 * dialog.uiScale

                Rectangle {
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    color: "#05070d"
                    radius: 12 * dialog.uiScale
                    clip: true

                    MediaPlayer {
                        id: editorPlayer
                        source: dialog.value("video", "")
                        videoOutput: editorVideo
                        audioOutput: AudioOutput { id: editorAudio; volume: 0.45; muted: true }
                        onPositionChanged: position => {
                            if (!playhead.pressed) {
                                if (dialog.synchronizedCues && dialog.selectedMove >= 0
                                        && position >= dialog.rangeEnd * 1000) {
                                    editorPlayer.position = dialog.rangeStart * 1000
                                    editorPlayer.play()
                                }
                                playhead.value = position / 1000
                                dialog.appBackend.seekDanceEditor(playhead.value)
                                dialog.refreshCue()
                            }
                        }
                    }

                    VideoOutput {
                        id: editorVideo
                        anchors.fill: parent
                        fillMode: VideoOutput.PreserveAspectFit
                    }

                    SkeletonView {
                        x: editorVideo.contentRect.x
                        y: editorVideo.contentRect.y
                        width: editorVideo.contentRect.width
                        height: editorVideo.contentRect.height
                        people: dialog.value("people", [])
                        mirror: false
                        showBoxes: true
                        showLabels: true
                    }

                    Label {
                        anchors.centerIn: parent
                        visible: !dialog.value("video", "")
                        text: "No source video in this package"
                        color: "#8f9bb1"
                    }
                }

                RowLayout {
                    Layout.fillWidth: true

                    NeonButton {
                        compact: true
                        text: editorPlayer.playbackState === MediaPlayer.PlayingState ? "PAUSE" : "PLAY"
                        onClicked: editorPlayer.playbackState === MediaPlayer.PlayingState
                                   ? editorPlayer.pause() : editorPlayer.play()
                    }

                    Slider {
                        id: playhead
                        Layout.fillWidth: true
                        from: 0
                        to: Math.max(0.1, Number(dialog.value("duration", 0)))
                        onMoved: {
                            editorPlayer.position = value * 1000
                            dialog.appBackend.seekDanceEditor(value)
                            dialog.refreshCue()
                        }
                    }

                    Label {
                        text: playhead.value.toFixed(2) + "s"
                        color: "#ffffff"
                    }
                }

                GlassPanel {
                    Layout.fillWidth: true
                    Layout.preferredHeight: 172 * dialog.uiScale
                    accent: "#ffe66d"

                    ColumnLayout {
                        anchors.fill: parent
                        anchors.margins: 9 * dialog.uiScale
                        spacing: 5 * dialog.uiScale

                        RowLayout {
                            Layout.fillWidth: true

                            NeonButton {
                                compact: true
                                text: dialog.synchronizedCues ? "MOVE SYNC" : "GAME TIMING"
                                accent: dialog.synchronizedCues ? "#ff4fcb" : "#55f7ff"
                                onClicked: dialog.toggleCueTiming()
                            }

                            NeonButton {
                                compact: true
                                text: editorAudio.muted ? "AUDIO OFF" : "AUDIO ON"
                                accent: "#9c7cff"
                                onClicked: editorAudio.muted = !editorAudio.muted
                            }

                            Label {
                                Layout.fillWidth: true
                                text: dialog.synchronizedCues
                                      ? "Selected move loops with its cue"
                                      : "Cues appear at gameplay lead time"
                                color: "#8f9bb1"
                                font.pixelSize: 9 * dialog.uiScale
                                horizontalAlignment: Text.AlignRight
                            }
                        }

                        CueCards {
                            Layout.fillWidth: true
                            Layout.fillHeight: true
                            people: dialog.value("cue_people", [])
                            heading: dialog.synchronizedCues ? "MOVE CUE" : "UP NEXT"
                            moveName: String(dialog.value("cue_name", "")).replace(/_/g, " ").toUpperCase()
                            selectedDancer: dialog.selectedCueDancer
                            uiScale: dialog.uiScale
                            onDancerSelected: dancerIndex => dialog.selectedCueDancer = dancerIndex
                        }
                    }
                }
            }

            ScrollView {
                Layout.preferredWidth: Math.max(410, 500 * dialog.uiScale)
                Layout.fillHeight: true
                clip: true
                contentWidth: availableWidth
                ScrollBar.vertical.policy: ScrollBar.AlwaysOn

                ColumnLayout {
                    width: parent.availableWidth
                    spacing: 7 * dialog.uiScale

                Label {
                    text: "MOVES"
                    color: "#55f7ff"
                    font.weight: Font.Black
                    font.letterSpacing: 1.5
                }

                ListView {
                    id: moveList
                    Layout.fillWidth: true
                    Layout.preferredHeight: 205 * dialog.uiScale
                    clip: true
                    spacing: 4
                    model: dialog.value("segments", [])

                    delegate: Rectangle {
                        id: moveDelegate
                        required property int index
                        required property var modelData
                        width: moveList.width
                        height: 42 * dialog.uiScale
                        radius: 8
                        color: dialog.selectedMove === index ? "#40305f75" : "#22151a29"
                        border.width: modelData.power ? 2 : 1
                        border.color: modelData.power ? "#ffe66d" : "#37445b"

                        Label {
                            anchors.centerIn: parent
                            text: (moveDelegate.modelData.power ? "YEAH!  " : "")
                                  + "MOVE " + (moveDelegate.index + 1) + "   "
                                  + Number(moveDelegate.modelData.start).toFixed(2) + "–"
                                  + Number(moveDelegate.modelData.end).toFixed(2)
                                  + "s   CUE " + moveDelegate.modelData.cue
                            color: "#ffffff"
                            font.pixelSize: 11 * dialog.uiScale
                            font.weight: Font.Bold
                        }

                        TapHandler { onTapped: dialog.chooseMove(moveDelegate.index) }
                    }
                }

                RowLayout {
                    Layout.fillWidth: true
                    Label {
                        text: dialog.jointName(cueJoint.value).toUpperCase()
                        color: "#ffe66d"
                        font.weight: Font.Bold
                    }
                    SpinBox { id: cueJoint; from: 5; to: 16; value: 9 }
                    NeonButton {
                        Layout.fillWidth: true
                        compact: true
                        text: "TOGGLE ARROW"
                        enabled: dialog.selectedMove >= 0
                        onClicked: dialog.edit("joint", {"joint": cueJoint.value,
                                                         "dancer_index": dialog.selectedCueDancer})
                    }
                }

                RowLayout {
                    Layout.fillWidth: true
                    NeonButton {
                        Layout.fillWidth: true
                        compact: true
                        text: "SPLIT AT PLAYHEAD"
                        enabled: dialog.selectedMove >= 0
                        onClicked: dialog.edit("split", {"time": playhead.value})
                    }
                    NeonButton {
                        Layout.fillWidth: true
                        compact: true
                        text: "MERGE NEXT"
                        enabled: dialog.selectedMove >= 0
                        onClicked: dialog.edit("merge")
                    }
                }

                RowLayout {
                    Layout.fillWidth: true
                    NeonButton {
                        Layout.fillWidth: true
                        compact: true
                        text: "CUE EARLIER"
                        enabled: dialog.selectedMove >= 0
                        onClicked: dialog.edit("cue", {"delta": -1,
                                                       "dancer_index": dialog.selectedCueDancer})
                    }
                    NeonButton {
                        Layout.fillWidth: true
                        compact: true
                        text: "CUE LATER"
                        enabled: dialog.selectedMove >= 0
                        onClicked: dialog.edit("cue", {"delta": 1,
                                                       "dancer_index": dialog.selectedCueDancer})
                    }
                }

                NeonButton {
                    Layout.fillWidth: true
                    compact: true
                    text: dialog.selectedMove >= 0
                          && dialog.value("segments", [])[dialog.selectedMove].power
                          ? "REMOVE POWER MOVE" : "MARK AS POWER MOVE"
                    enabled: dialog.selectedMove >= 0
                    onClicked: dialog.edit("power", {
                        "enabled": !dialog.value("segments", [])[dialog.selectedMove].power
                    })
                }

                RowLayout {
                    Layout.fillWidth: true
                    Label { text: "RANGE"; color: "#8f9bb1" }
                    TextField {
                        id: rangeStartField
                        Layout.fillWidth: true
                        text: dialog.rangeStart.toFixed(2)
                        validator: DoubleValidator { bottom: 0 }
                        onEditingFinished: dialog.rangeStart = Number(text)
                    }
                    TextField {
                        id: rangeEndField
                        Layout.fillWidth: true
                        text: dialog.rangeEnd.toFixed(2)
                        validator: DoubleValidator { bottom: 0 }
                        onEditingFinished: dialog.rangeEnd = Number(text)
                    }
                }

                NeonButton {
                    Layout.fillWidth: true
                    compact: true
                    text: "MASK RANGE FROM CUES AND SCORING"
                    onClicked: dialog.edit("mask", {"start": dialog.rangeStart,
                                                    "end": dialog.rangeEnd})
                }

                RowLayout {
                    Layout.fillWidth: true
                    visible: Number(dialog.value("dancer_count", 0)) > 1
                    Label { text: "FIX SWAP"; color: "#ff70d5"; font.weight: Font.Bold }
                    SpinBox { id: firstDancer; from: 1; to: dialog.value("dancer_count", 1) }
                    Label { text: "↔"; color: "#ffffff" }
                    SpinBox { id: secondDancer; from: 1; to: dialog.value("dancer_count", 1); value: 2 }
                }

                NeonButton {
                    Layout.fillWidth: true
                    visible: Number(dialog.value("dancer_count", 0)) > 1
                    compact: true
                    text: "SWAP DANCERS IN RANGE"
                    onClicked: dialog.edit("swap", {"start": dialog.rangeStart,
                                                    "end": dialog.rangeEnd,
                                                    "first": firstDancer.value - 1,
                                                    "second": secondDancer.value - 1})
                }

                RowLayout {
                    Layout.fillWidth: true
                    visible: dialog.value("masks", []).length > 0
                    Label {
                        Layout.fillWidth: true
                        text: dialog.value("masks", []).length + " scoring mask(s)"
                        color: "#ffe66d"
                    }
                    SpinBox { id: maskIndex; from: 1; to: dialog.value("masks", []).length }
                    NeonButton {
                        compact: true
                        text: "REMOVE MASK"
                        onClicked: dialog.edit("unmask", {"mask": maskIndex.value - 1})
                    }
                }
                }
            }
        }
    }
}
