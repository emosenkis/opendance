import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import QtMultimedia
import "components"

ApplicationWindow {
    id: window

    width: 1280
    height: 720
    minimumWidth: 900
    minimumHeight: 540
    visible: true
    color: "#080a12"
    title: backend.presentationMode ? "OpenDance Player — " + itemValue(song, "title", "Song")
                                    : "OpenDance"

    readonly property bool reducedMotion: backend.reducedMotion
    readonly property real uiScale: Math.max(0.72, Math.min(width / 1280, height / 720))
    readonly property string screenName: String(backend.screen || "library").toLowerCase() === "game"
                                         ? "play" : String(backend.screen || "library").toLowerCase()
    readonly property bool gameplayScreen: screenName === "countdown" || screenName === "play"
    readonly property bool setupScreen: screenName === "setup"
    readonly property var song: backend.selectedSong || ({})
    readonly property var playerList: backend.players || []
    readonly property real videoScale: clamped(itemValue(song, "video_scale", 1.0), 0.5, 1.0)
    readonly property bool videoIntroActive: gameplayScreen
                                              && backend.coachMode === "video"
                                              && Number(backend.videoHiddenUntil || 0) > 0
                                              && (screenName === "countdown"
                                                  || backend.songTime < backend.videoHiddenUntil)
    readonly property bool selectedSongLocked: itemValue(song, "locked", false)
                                                || itemValue(song, "unlocked", true) === false

    function itemValue(item, name, fallback) {
        return item && item[name] !== undefined && item[name] !== null ? item[name] : fallback
    }

    function clamped(value, lower, upper) {
        return Math.max(lower, Math.min(upper, Number(value) || 0))
    }

    function formatTime(seconds) {
        var total = Math.max(0, Math.floor(Number(seconds) || 0))
        var minutes = Math.floor(total / 60)
        var remainder = total % 60
        return minutes + ":" + (remainder < 10 ? "0" : "") + remainder
    }

    function songAccent(item, index) {
        var supplied = itemValue(item, "color", itemValue(item, "accent", ""))
        if (supplied)
            return supplied
        var accents = ["#55f7ff", "#ff4fcb", "#ffe66d", "#7cff9b", "#9c7cff"]
        return accents[index % accents.length]
    }

    function playerColor(index) {
        return ["#55f7ff", "#ff4fcb", "#ffe66d", "#7cff9b", "#ff855f", "#b896ff"][index % 6]
    }

    function feedbackForSlot(slot) {
        var items = backend.feedback || []
        for (var index = items.length - 1; index >= 0; --index) {
            if (Number(itemValue(items[index], "slot", -1)) === slot)
                return items[index]
        }
        return null
    }

    function sourceId(source) {
        if (source === undefined || source === null)
            return ""
        return String(source.id !== undefined ? source.id : source)
    }

    function targetPeople(pose) {
        if (!pose)
            return []
        if (pose.keypoints !== undefined)
            return [pose]
        if (pose.pose !== undefined)
            return [pose]
        return pose.length !== undefined ? [{ "keypoints": pose }] : []
    }

    function movePose(move) {
        if (move && move.pose !== undefined)
            return targetPeople(move.pose)
        if (move && move.keypoints !== undefined)
            return [move]
        return targetPeople(backend.targetPose)
    }

    function resultValue(name, fallback) {
        return itemValue(backend.result || ({}), name, fallback)
    }

    function setOption(name, value) {
        backend.setOption(name, value)
    }

    function focusForScreen() {
        if (screenName === "library")
            songList.forceActiveFocus()
        else if (screenName === "setup")
            setupBackButton.forceActiveFocus()
        else if (screenName === "settings")
            settingsBackButton.forceActiveFocus()
        else if (screenName === "play" && backend.paused)
            resumeButton.forceActiveFocus()
        else if (screenName === "results")
            retryButton.forceActiveFocus()
        else
            contentStack.forceActiveFocus()
    }

    function moveFocus(forward) {
        var item = activeFocusItem
        if (!item)
            return focusForScreen()
        var next = item.nextItemInFocusChain(forward)
        if (next)
            next.forceActiveFocus()
    }

    function moveSong(delta) {
        if (!songList.count)
            return
        songList.currentIndex = clamped(songList.currentIndex + delta, 0, songList.count - 1)
        songList.positionViewAtIndex(songList.currentIndex, ListView.Contain)
        backend.selectSong(songList.currentIndex)
    }

    function syncSongSelection() {
        var index = Number(backend.selectedSongIndex)
        if (songList.count > 0 && index >= 0 && index < songList.count
                && songList.currentIndex !== index) {
            songList.currentIndex = index
            songList.positionViewAtIndex(index, ListView.Contain)
        }
    }

    function goBack() {
        if (songImportDialog.visible) {
            if (!songImportDialog.busy)
                songImportDialog.close()
        } else if (screenName === "play") {
            if (backend.presentationMode)
                backend.leaveGame()
            else
                backend.togglePause()
        } else if (screenName === "settings") {
            backend.closeSettings()
        } else if (screenName === "setup" || screenName === "results") {
            backend.goLibrary()
        } else if (screenName === "countdown") {
            backend.leaveGame()
        }
    }

    function activateFocused() {
        if (screenName === "play" && !backend.paused) {
            backend.togglePause()
            return
        }
        var item = activeFocusItem
        if (screenName === "library" && (!item || item === songList)) {
            backend.openSetup()
            return
        }
        if (item && typeof item.click === "function")
            item.click()
        else if (item && typeof item.clicked === "function")
            item.clicked()
    }

    function handleGamepadAction(action) {
        var normalized = String(action).toLowerCase()
        if (normalized === "back" || normalized === "cancel" || normalized === "b") {
            goBack()
        } else if (normalized === "accept" || normalized === "select" || normalized === "a") {
            activateFocused()
        } else if ((normalized === "left" || normalized === "dpad_left") && screenName === "library") {
            moveSong(-1)
        } else if ((normalized === "right" || normalized === "dpad_right") && screenName === "library") {
            moveSong(1)
        } else if (normalized === "up" || normalized === "left" || normalized.indexOf("dpad_up") >= 0) {
            moveFocus(false)
        } else if (normalized === "down" || normalized === "right" || normalized.indexOf("dpad_down") >= 0) {
            moveFocus(true)
        } else if (normalized === "start" || normalized === "menu" || normalized === "pause") {
            if (screenName === "play")
                backend.togglePause()
        }
    }

    onScreenNameChanged: Qt.callLater(focusForScreen)

    Connections {
        target: backend
        ignoreUnknownSignals: true

        function onGamepadAction(action) {
            window.handleGamepadAction(action)
        }

        function onFullscreenRequested() {
            if (window.visibility === Window.FullScreen)
                window.showNormal()
            else
                window.showFullScreen()
        }

        function onChanged() {
            Qt.callLater(window.syncSongSelection)
        }
    }

    Shortcut {
        sequence: "Escape"
        context: Qt.ApplicationShortcut
        onActivated: window.goBack()
    }

    Shortcut {
        sequence: "F11"
        context: Qt.ApplicationShortcut
        onActivated: backend.requestFullscreen()
    }

    Shortcut {
        sequence: "P"
        enabled: window.screenName === "play"
        context: Qt.ApplicationShortcut
        onActivated: backend.togglePause()
    }

    Shortcut {
        sequence: "A"
        enabled: window.screenName === "library"
        context: Qt.ApplicationShortcut
        onActivated: window.moveSong(-1)
    }

    Shortcut {
        sequence: "D"
        enabled: window.screenName === "library"
        context: Qt.ApplicationShortcut
        onActivated: window.moveSong(1)
    }

    Shortcut {
        sequence: "W"
        context: Qt.ApplicationShortcut
        onActivated: window.moveFocus(false)
    }

    Shortcut {
        sequence: "S"
        context: Qt.ApplicationShortcut
        onActivated: window.moveFocus(true)
    }

    Shortcut {
        sequence: "Up"
        context: Qt.ApplicationShortcut
        onActivated: window.moveFocus(false)
    }

    Shortcut {
        sequence: "Down"
        context: Qt.ApplicationShortcut
        onActivated: window.moveFocus(true)
    }

    StageBackground {
        anchors.fill: parent
        reducedMotion: window.reducedMotion
    }

    // The coach sink stays attached for the lifetime of the window. Importing a
    // clip and switching between video and generated choreography does not tear
    // down the playback pipeline.
    VideoOutput {
        id: coachVideo
        anchors.centerIn: parent
        width: parent.width * window.videoScale
        height: parent.height * window.videoScale
        visible: window.gameplayScreen && backend.coachMode === "video"
                 && !window.videoIntroActive
        fillMode: backend.presentationMode ? VideoOutput.PreserveAspectFit
                                           : VideoOutput.PreserveAspectCrop
        opacity: visible ? 1 : 0

        Component.onCompleted: backend.attachCoachSink(videoSink)
    }

    Rectangle {
        anchors.fill: parent
        visible: window.gameplayScreen
        color: coachVideo.visible ? "#35070a12" : "transparent"
    }

    Item {
        id: generatedCoach
        anchors.centerIn: parent
        width: Math.min(parent.width * 0.58, parent.height * 0.78)
        height: width
        visible: window.gameplayScreen && backend.coachMode !== "video"

        Rectangle {
            anchors.centerIn: parent
            width: parent.width * 0.72
            height: width
            radius: width / 2
            color: "#1455f7ff"
            border.width: 2
            border.color: "#3455f7ff"

            SequentialAnimation on scale {
                running: generatedCoach.visible && !window.reducedMotion
                loops: Animation.Infinite
                NumberAnimation { to: 1.06; duration: 700; easing.type: Easing.InOutSine }
                NumberAnimation { to: 0.97; duration: 700; easing.type: Easing.InOutSine }
            }
        }

        SkeletonView {
            anchors.fill: parent
            anchors.margins: parent.width * 0.08
            people: generatedCoach.visible ? backend.targetDancers : null
            lineColor: "#55f7ff"
            mirror: false
            showBoxes: false
            showLabels: false
            lineScale: 1.8
        }

    }

    Item {
        id: introVisual
        anchors.fill: parent
        visible: window.screenName === "play" && window.videoIntroActive

        Item {
            anchors.centerIn: parent
            width: Math.min(parent.width, parent.height) * 0.62
            height: width

            Repeater {
                model: 3

                Rectangle {
                    required property int index
                    anchors.centerIn: parent
                    width: parent.width - index * 58 * window.uiScale
                    height: width
                    radius: width / 2
                    color: index === 2 ? "#1855f7ff" : "transparent"
                    border.width: 3
                    border.color: index % 2 ? "#ff4fcb" : "#55f7ff"
                    opacity: 0.22 + index * 0.13

                    Rectangle {
                        anchors.horizontalCenter: parent.horizontalCenter
                        y: -height / 2
                        width: 28 * window.uiScale
                        height: 7 * window.uiScale
                        radius: height / 2
                        color: parent.border.color
                    }

                    RotationAnimator on rotation {
                        running: introVisual.visible && !window.reducedMotion
                        from: index % 2 ? 0 : 360
                        to: index % 2 ? 360 : 0
                        duration: 2800 + index * 700
                        loops: Animation.Infinite
                    }
                }
            }

            Column {
                anchors.centerIn: parent
                spacing: 8 * window.uiScale

                Label {
                    anchors.horizontalCenter: parent.horizontalCenter
                    text: "GET READY"
                    color: "#ffffff"
                    font.pixelSize: 42 * window.uiScale
                    font.weight: Font.Black
                    font.letterSpacing: 3
                    style: Text.Outline
                    styleColor: "#88000000"
                }

                Label {
                    anchors.horizontalCenter: parent.horizontalCenter
                    text: Math.max(1, Math.ceil(Number(backend.videoHiddenUntil)
                                                - Number(backend.songTime)))
                          + "  •  FIND YOUR SPACE"
                    color: "#ffe66d"
                    font.pixelSize: 13 * window.uiScale
                    font.weight: Font.Black
                    font.letterSpacing: 1.5
                }
            }
        }
    }

    SkeletonView {
        x: coachVideo.x + coachVideo.contentRect.x
        y: coachVideo.y + coachVideo.contentRect.y
        width: coachVideo.contentRect.width
        height: coachVideo.contentRect.height
        visible: window.gameplayScreen && backend.presentationMode
                 && backend.coachMode === "video" && !window.videoIntroActive
        people: visible ? backend.targetDancers : null
        lineColor: "#55f7ff"
        mirror: false
        showBoxes: false
        showLabels: false
        lineScale: 1.45
    }

    Item {
        id: dancerAssignmentMarkers
        x: coachVideo.x + coachVideo.contentRect.x
        y: coachVideo.y + coachVideo.contentRect.y
        width: coachVideo.contentRect.width
        height: coachVideo.contentRect.height
        readonly property var dancers: visible ? backend.targetDancers : []
        visible: window.screenName === "play" && !backend.presentationMode
                 && coachVideo.visible
        clip: true
        z: 2

        function dancer(dancerIndex) {
            for (var index = 0; index < dancers.length; ++index) {
                if (Number(dancers[index].dancer_index) === dancerIndex)
                    return dancers[index]
            }
            return ({})
        }

        Repeater {
            model: dancerAssignmentMarkers.dancers.length

            Item {
                id: marker
                required property int index
                readonly property var modelData: dancerAssignmentMarkers.dancer(index)
                readonly property var box: modelData.bbox || []
                readonly property int dancerIndex: index
                readonly property color dancerColor: window.playerColor(dancerIndex)
                readonly property color indicatorColor: Qt.rgba(dancerColor.r,
                                                                  dancerColor.g,
                                                                  dancerColor.b, 0.48)
                readonly property real targetX: box.length < 4 ? 0 : Math.max(
                    0, Math.min(dancerAssignmentMarkers.width - width,
                                (Number(box[0]) + Number(box[2]) / 2)
                                * dancerAssignmentMarkers.width - width / 2))
                readonly property real targetY: box.length < 4 ? 0 : Math.max(
                    0, Math.min(dancerAssignmentMarkers.height - height,
                                (Number(box[1]) + Number(box[3]))
                                * dancerAssignmentMarkers.height + 4 * window.uiScale))
                property real filteredX: 0
                property real filteredY: 0
                property bool positioned: false
                visible: box.length >= 4
                x: filteredX
                y: filteredY
                width: 96 * window.uiScale
                height: 38 * window.uiScale

                Component.onCompleted: {
                    filteredX = targetX
                    filteredY = targetY
                    positioned = true
                }
                onTargetXChanged: if (positioned
                                      && Math.abs(targetX - filteredX)
                                         >= 4 * window.uiScale)
                                      filteredX = targetX
                onTargetYChanged: if (positioned
                                      && Math.abs(targetY - filteredY)
                                         >= 7 * window.uiScale)
                                      filteredY = targetY

                Behavior on x {
                    enabled: marker.positioned
                    NumberAnimation {
                        duration: Math.abs(marker.filteredX - marker.x)
                                  > 30 * window.uiScale ? 700 : 10000
                        easing.type: Easing.OutCubic
                    }
                }

                Behavior on y {
                    enabled: marker.positioned
                    NumberAnimation {
                        duration: Math.abs(marker.filteredY - marker.y)
                                  > 24 * window.uiScale ? 850 : 12000
                        easing.type: Easing.OutCubic
                    }
                }

                Canvas {
                    id: assignmentIndicator
                    anchors.fill: parent
                    antialiasing: true

                    onPaint: {
                        var context = getContext("2d")
                        var scale = window.uiScale
                        var color = marker.indicatorColor
                        context.reset()
                        context.clearRect(0, 0, width, height)
                        context.fillStyle = Qt.rgba(color.r, color.g, color.b,
                                                    color.a / 12)
                        for (var spread = 10; spread >= 0; --spread) {
                            var feather = spread * scale
                            var radius = (3 + spread * 0.55) * scale
                            context.beginPath()
                            context.roundedRect(12 * scale - feather,
                                                12 * scale - feather,
                                                72 * scale + 2 * feather,
                                                14 * scale + 2 * feather,
                                                radius, radius)
                            context.fill()
                        }
                    }

                    onWidthChanged: requestPaint()
                    onHeightChanged: requestPaint()
                }

                Label {
                    anchors.centerIn: parent
                    text: "D" + (parent.dancerIndex + 1)
                    color: "#ffffff"
                    font.pixelSize: 10 * window.uiScale
                    font.weight: Font.Black
                    style: Text.Outline
                    styleColor: "#80070a12"
                }
            }
        }
    }

    StackLayout {
        id: contentStack
        anchors.fill: parent
        currentIndex: window.screenName === "setup" ? 1
                    : window.screenName === "countdown" ? 2
                    : window.screenName === "play" ? 3
                    : window.screenName === "results" ? 4
                    : window.screenName === "settings" ? 5 : 0
        focus: true

        // SONG LIBRARY -------------------------------------------------------
        Item {
            id: libraryPage

            ColumnLayout {
                anchors.fill: parent
                anchors.leftMargin: 40 * window.uiScale
                anchors.rightMargin: 40 * window.uiScale
                anchors.topMargin: 25 * window.uiScale
                anchors.bottomMargin: 24 * window.uiScale
                spacing: 14 * window.uiScale

                RowLayout {
                    Layout.fillWidth: true
                    Layout.preferredHeight: 60 * window.uiScale

                    Row {
                        spacing: 12 * window.uiScale

                        Rectangle {
                            anchors.verticalCenter: parent.verticalCenter
                            width: 46 * window.uiScale
                            height: width
                            radius: 13 * window.uiScale
                            rotation: -7
                            gradient: Gradient {
                                GradientStop { position: 0; color: "#55f7ff" }
                                GradientStop { position: 1; color: "#ff4fcb" }
                            }

                            Label {
                                anchors.centerIn: parent
                                text: "OD"
                                color: "#071019"
                                font.pixelSize: 14 * window.uiScale
                                font.weight: Font.Black
                            }
                        }

                        Column {
                            anchors.verticalCenter: parent.verticalCenter
                            spacing: -3

                            Label {
                                text: "OPEN"
                                color: "#ffffff"
                                font.pixelSize: 17 * window.uiScale
                                font.weight: Font.Black
                                font.letterSpacing: 5
                            }

                            Label {
                                text: "DANCE"
                                color: "#55f7ff"
                                font.pixelSize: 17 * window.uiScale
                                font.weight: Font.Black
                                font.letterSpacing: 5
                            }
                        }
                    }

                    Item { Layout.fillWidth: true }

                    GlassPanel {
                        Layout.preferredWidth: 214 * window.uiScale
                        Layout.preferredHeight: 48 * window.uiScale
                        accent: "#ffe66d"

                        Row {
                            anchors.centerIn: parent
                            spacing: 9

                            Label {
                                text: "\u2726"
                                color: "#ffe66d"
                                font.pixelSize: 20 * window.uiScale
                            }

                            Label {
                                text: Number(backend.points || 0).toLocaleString(Qt.locale(), "f", 0) + " GROOVE POINTS"
                                color: "#ffffff"
                                font.pixelSize: 12 * window.uiScale
                                font.weight: Font.Bold
                                font.letterSpacing: 0.8
                            }
                        }
                    }

                    NeonButton {
                        text: "SETTINGS"
                        compact: true
                        accent: "#55f7ff"
                        onClicked: backend.openSettings()
                    }

                    NeonButton {
                        text: "FULLSCREEN"
                        compact: true
                        accent: "#9c7cff"
                        onClicked: backend.requestFullscreen()
                    }
                }

                Column {
                    Layout.fillWidth: true
                    spacing: 2

                    Label {
                        text: "CHOOSE YOUR TRACK"
                        color: "#f8fbff"
                        font.pixelSize: 30 * window.uiScale
                        font.weight: Font.Black
                        font.letterSpacing: 1.2
                    }

                    Label {
                        text: "Dance, earn stars, and unlock the whole floor."
                        color: "#98a4bb"
                        font.pixelSize: 14 * window.uiScale
                    }
                }

                ListView {
                    id: songList

                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    Layout.minimumHeight: 300 * window.uiScale
                    model: backend.songs
                    orientation: ListView.Horizontal
                    spacing: 18 * window.uiScale
                    clip: true
                    focus: true
                    keyNavigationWraps: false
                    boundsBehavior: Flickable.StopAtBounds
                    snapMode: ListView.SnapToItem
                    highlightMoveDuration: window.reducedMotion ? 0 : 220
                    preferredHighlightBegin: width * 0.08
                    preferredHighlightEnd: width * 0.76
                    highlightRangeMode: ListView.ApplyRange

                    Keys.onLeftPressed: function(event) {
                        window.moveSong(-1)
                        event.accepted = true
                    }
                    Keys.onRightPressed: function(event) {
                        window.moveSong(1)
                        event.accepted = true
                    }
                    Keys.onReturnPressed: function(event) {
                        if (!window.selectedSongLocked)
                            backend.openSetup()
                        event.accepted = true
                    }
                    Keys.onEnterPressed: function(event) {
                        if (!window.selectedSongLocked)
                            backend.openSetup()
                        event.accepted = true
                    }
                    Keys.onSpacePressed: function(event) {
                        if (!window.selectedSongLocked)
                            backend.openSetup()
                        event.accepted = true
                    }

                    onCurrentIndexChanged: {
                        if (currentIndex >= 0)
                            backend.selectSong(currentIndex)
                    }

                    delegate: Item {
                        id: songDelegate

                        required property int index
                        required property var modelData
                        readonly property bool selected: ListView.isCurrentItem
                        readonly property bool locked: window.itemValue(modelData, "locked", false)
                                                       || window.itemValue(modelData, "unlocked", true) === false
                        readonly property color accent: window.songAccent(modelData, index)

                        width: Math.min(300 * window.uiScale, songList.width * 0.27)
                        height: songList.height - 12 * window.uiScale
                        scale: selected ? 1.0 : 0.94
                        opacity: selected ? 1.0 : 0.72

                        Behavior on scale {
                            enabled: !window.reducedMotion
                            NumberAnimation { duration: 180; easing.type: Easing.OutCubic }
                        }

                        Behavior on opacity {
                            NumberAnimation { duration: 140 }
                        }

                        GlassPanel {
                            anchors.fill: parent
                            anchors.margins: songDelegate.selected ? 2 : 8
                            accent: songDelegate.accent
                            border.width: songDelegate.selected ? 3 : 1
                            border.color: songDelegate.selected ? songDelegate.accent
                                                                     : Qt.rgba(songDelegate.accent.r,
                                                                               songDelegate.accent.g,
                                                                               songDelegate.accent.b, 0.22)

                            Column {
                                anchors.fill: parent
                                anchors.margins: 12 * window.uiScale
                                spacing: 9 * window.uiScale

                                Rectangle {
                                    width: parent.width
                                    height: parent.height * 0.59
                                    radius: 13 * window.uiScale
                                    clip: true
                                    gradient: Gradient {
                                        GradientStop {
                                            position: 0
                                            color: Qt.rgba(songDelegate.accent.r, songDelegate.accent.g,
                                                           songDelegate.accent.b, 0.82)
                                        }
                                        GradientStop { position: 1; color: "#171229" }
                                    }

                                    Repeater {
                                        model: 9

                                        Rectangle {
                                            required property int index
                                            width: 5 + (index % 3) * 3
                                            height: width
                                            radius: width / 2
                                            x: ((index * 41) % 89) / 100 * parent.width
                                            y: ((index * 67) % 83) / 100 * parent.height
                                            color: index % 2 ? "#ffffff" : songDelegate.accent
                                            opacity: 0.35
                                        }
                                    }

                                    Loader {
                                        id: previewLoader
                                        anchors.fill: parent
                                        active: songDelegate.selected
                                                && window.screenName === "library"
                                                && previewDuration > 0
                                                && (String(previewVideo) !== ""
                                                    || String(previewAudio) !== "")
                                        readonly property url previewVideo: window.itemValue(
                                                                 songDelegate.modelData,
                                                                 "previewVideoUrl", "")
                                        readonly property url previewAudio: window.itemValue(
                                                                 songDelegate.modelData,
                                                                 "previewAudioUrl", "")
                                        readonly property int previewStart: window.itemValue(
                                                                  songDelegate.modelData,
                                                                  "previewStartMs", 0)
                                        readonly property int previewDuration: window.itemValue(
                                                                     songDelegate.modelData,
                                                                     "previewDurationMs", 0)

                                        sourceComponent: Component {
                                            SongPreview {
                                                videoSource: previewLoader.previewVideo
                                                audioSource: previewLoader.previewAudio
                                                startMs: previewLoader.previewStart
                                                durationMs: previewLoader.previewDuration
                                                outputVolume: backend.volume
                                            }
                                        }
                                    }

                                    Label {
                                        anchors.centerIn: parent
                                        visible: !previewLoader.active
                                                 || String(previewLoader.previewVideo) === ""
                                        text: songDelegate.locked ? "\u26BF" : "\u266B"
                                        color: songDelegate.locked ? "#d4d9e5" : "#ffffff"
                                        font.pixelSize: 76 * window.uiScale
                                        font.weight: Font.Black
                                        rotation: songDelegate.locked ? 0 : -8
                                    }

                                    Rectangle {
                                        anchors.left: parent.left
                                        anchors.right: parent.right
                                        anchors.bottom: parent.bottom
                                        height: 34 * window.uiScale
                                        color: "#a5070912"

                                        Row {
                                            anchors.centerIn: parent
                                            spacing: 5

                                            Repeater {
                                                model: 4
                                                Rectangle {
                                                    required property int index
                                                    width: 18 * window.uiScale
                                                    height: 4 + index * 4 * window.uiScale
                                                    radius: 2
                                                    color: index < Number(window.itemValue(songDelegate.modelData,
                                                                                           "difficulty", 2))
                                                           ? songDelegate.accent : "#4f5669"
                                                    anchors.bottom: parent.bottom
                                                }
                                            }
                                        }
                                    }
                                }

                                Label {
                                    width: parent.width
                                    text: window.itemValue(songDelegate.modelData, "title", "Untitled Groove")
                                    color: "#ffffff"
                                    font.pixelSize: 19 * window.uiScale
                                    font.weight: Font.Black
                                    elide: Text.ElideRight
                                }

                                Label {
                                    width: parent.width
                                    text: window.itemValue(songDelegate.modelData, "artist", "OpenDance Crew")
                                    color: "#a2aec3"
                                    font.pixelSize: 12 * window.uiScale
                                    elide: Text.ElideRight
                                }

                                Row {
                                    spacing: 8

                                    StarMeter {
                                        value: window.itemValue(songDelegate.modelData, "bestStars", 0)
                                        starSize: 14 * window.uiScale
                                        accent: songDelegate.accent
                                        animateChanges: false
                                    }

                                    Label {
                                        text: window.formatTime(window.itemValue(songDelegate.modelData, "duration", 0))
                                        color: "#78849c"
                                        font.pixelSize: 11 * window.uiScale
                                    }
                                }
                            }
                        }

                        MouseArea {
                            anchors.fill: parent
                            hoverEnabled: true
                            cursorShape: Qt.PointingHandCursor
                            onClicked: {
                                songList.currentIndex = songDelegate.index
                                songList.forceActiveFocus()
                            }
                            onDoubleClicked: {
                                songList.currentIndex = songDelegate.index
                                if (!songDelegate.locked)
                                    backend.openSetup()
                            }
                        }
                    }
                }

                RowLayout {
                    Layout.fillWidth: true
                    Layout.preferredHeight: 66 * window.uiScale

                    Column {
                        Layout.fillWidth: true
                        spacing: 2

                        Label {
                            text: window.selectedSongLocked
                                  ? "LOCKED \u2022 KEEP DANCING TO EARN MORE POINTS"
                                  : "SELECTED \u2022 " + window.itemValue(window.song, "title", "CHOOSE A TRACK")
                            color: window.selectedSongLocked ? "#ff87d9" : "#55f7ff"
                            font.pixelSize: 11 * window.uiScale
                            font.weight: Font.Bold
                            font.letterSpacing: 1.1
                        }

                        Label {
                            text: "\u2190 \u2192 / A D  Choose    Enter  Continue    F11  Fullscreen"
                            color: "#717c92"
                            font.pixelSize: 11 * window.uiScale
                        }
                    }

                    NeonButton {
                        id: libraryDanceButton
                        text: window.selectedSongLocked ? "LOCKED" : "LET'S DANCE"
                        hint: window.selectedSongLocked
                              ? window.itemValue(window.song, "unlock_cost", 0) + " points required"
                              : "Camera setup"
                        primary: !window.selectedSongLocked
                        accent: window.songAccent(window.song, songList.currentIndex < 0 ? 0 : songList.currentIndex)
                        enabled: !window.selectedSongLocked && songList.count > 0
                        onClicked: backend.openSetup()
                    }
                }
            }
        }

        // CAMERA + GAME SETUP -----------------------------------------------
        Item {
            id: setupPage

            ColumnLayout {
                anchors.fill: parent
                anchors.margins: 26 * window.uiScale
                spacing: 15 * window.uiScale

                RowLayout {
                    Layout.fillWidth: true
                    Layout.preferredHeight: 52 * window.uiScale
                    spacing: 14

                    NeonButton {
                        id: setupBackButton
                        text: "\u2190 LIBRARY"
                        compact: true
                        accent: "#9c7cff"
                        onClicked: backend.goLibrary()
                    }

                    NeonButton {
                        text: "SETTINGS"
                        compact: true
                        accent: "#55f7ff"
                        onClicked: backend.openSettings()
                    }

                    Column {
                        Layout.fillWidth: true
                        spacing: 0

                        Label {
                            text: "GET IN FRAME"
                            color: "#ffffff"
                            font.pixelSize: 25 * window.uiScale
                            font.weight: Font.Black
                            font.letterSpacing: 1.5
                        }

                        Label {
                            text: window.itemValue(window.song, "title", "Selected track")
                            color: "#55f7ff"
                            font.pixelSize: 12 * window.uiScale
                            font.weight: Font.DemiBold
                        }
                    }

                    Rectangle {
                        Layout.preferredWidth: 10
                        Layout.preferredHeight: 10
                        radius: 5
                        color: String(backend.modelStatus).toLowerCase().match(/error|unavailable/)
                               ? "#ff6c88"
                               : String(backend.modelStatus).toLowerCase().match(/load|start/)
                                 ? "#ffe66d" : "#7cff9b"
                    }

                    Label {
                        text: String(backend.modelStatus || "Loading pose model")
                        color: "#c5cede"
                        font.pixelSize: 11 * window.uiScale
                        font.weight: Font.DemiBold
                    }
                }

                RowLayout {
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    spacing: 18 * window.uiScale

                    GlassPanel {
                        Layout.fillWidth: true
                        Layout.fillHeight: true
                        Layout.minimumWidth: 510 * window.uiScale
                        accent: "#55f7ff"

                        Item {
                            id: setupCameraHost
                            anchors.fill: parent
                            anchors.margins: 10 * window.uiScale
                            clip: true

                            Rectangle {
                                anchors.fill: parent
                                color: "#05070d"
                                radius: 12
                            }

                            Label {
                                anchors.centerIn: parent
                                visible: !backend.posePeople || backend.posePeople.length === 0
                                text: String(backend.modelStatus).toLowerCase().match(/load|start/)
                                      ? "STARTING CAMERA\u2026" : "STEP INTO FRAME"
                                color: "#69748a"
                                font.pixelSize: 14 * window.uiScale
                                font.weight: Font.Bold
                                font.letterSpacing: 1.5
                            }

                            Rectangle {
                                anchors.left: parent.left
                                anchors.right: parent.right
                                anchors.top: parent.top
                                height: 45 * window.uiScale
                                color: "#b0090c17"

                                RowLayout {
                                    anchors.fill: parent
                                    anchors.leftMargin: 13
                                    anchors.rightMargin: 13

                                    Label {
                                        text: "LIVE POSE PREVIEW"
                                        color: "#ffffff"
                                        font.pixelSize: 11 * window.uiScale
                                        font.weight: Font.Black
                                        font.letterSpacing: 1.2
                                    }

                                    Item { Layout.fillWidth: true }

                                    Label {
                                        text: Number(backend.inferenceFps || 0).toFixed(0) + " FPS  \u2022  "
                                              + Number(backend.latencyMs || 0).toFixed(0) + " MS"
                                        color: Number(backend.latencyMs || 0) < 120 ? "#7cff9b" : "#ffe66d"
                                        font.pixelSize: 10 * window.uiScale
                                        font.weight: Font.Bold
                                    }
                                }
                            }

                            Row {
                                anchors.horizontalCenter: parent.horizontalCenter
                                anchors.bottom: parent.bottom
                                anchors.bottomMargin: 18 * window.uiScale
                                spacing: 12 * window.uiScale

                                Repeater {
                                    model: window.playerList

                                    Rectangle {
                                        id: setupPlayerChip
                                        required property int index
                                        required property var modelData
                                        readonly property int playerSlot: Number(window.itemValue(modelData, "slot", index))
                                        width: Math.min(132 * window.uiScale,
                                                        (setupCameraHost.width - 24 * window.uiScale)
                                                        / Math.max(1, window.playerList.length)
                                                        - 8 * window.uiScale)
                                        height: 38 * window.uiScale
                                        radius: 19 * window.uiScale
                                        color: "#cc132331"
                                        border.width: 1
                                        border.color: window.playerColor(playerSlot)

                                        Label {
                                            anchors.centerIn: parent
                                            text: "P" + Number(window.itemValue(setupPlayerChip.modelData,
                                                                                 "player_number",
                                                                                 setupPlayerChip.playerSlot + 1))
                                                  + " READY"
                                            color: "#ffffff"
                                            font.pixelSize: 10 * window.uiScale
                                            font.weight: Font.Bold
                                            font.letterSpacing: 0.8
                                        }
                                    }
                                }
                            }

                            Label {
                                anchors.horizontalCenter: parent.horizontalCenter
                                anchors.bottom: parent.bottom
                                anchors.bottomMargin: 68 * window.uiScale
                                visible: backend.posePeople && backend.posePeople.length > 0
                                         && String(backend.selectedSource) !== "demo"
                                text: backend.gestureStatus
                                color: String(backend.gestureStatus).indexOf("ACTIVE") >= 0
                                       ? "#7cff9b" : "#ffe66d"
                                font.pixelSize: 10 * window.uiScale
                                font.weight: Font.Black
                                font.letterSpacing: 0.7
                            }
                        }
                    }

                    GlassPanel {
                        Layout.preferredWidth: 370 * window.uiScale
                        Layout.fillHeight: true
                        accent: "#ff4fcb"

                        ScrollView {
                            id: setupScroll
                            anchors.left: parent.left
                            anchors.right: parent.right
                            anchors.top: parent.top
                            anchors.bottom: setupStartButton.top
                            anchors.margins: 16 * window.uiScale
                            anchors.bottomMargin: 10 * window.uiScale
                            clip: true
                            ScrollBar.horizontal.policy: ScrollBar.AlwaysOff

                            Column {
                                width: setupScroll.availableWidth
                                spacing: 11 * window.uiScale

                                Label {
                                    text: "VIDEO SOURCE"
                                    color: "#ffffff"
                                    font.pixelSize: 12 * window.uiScale
                                    font.weight: Font.Black
                                    font.letterSpacing: 1.4
                                }

                                ListView {
                                    id: cameraList
                                    width: parent.width
                                    height: Math.min(136 * window.uiScale,
                                                     Math.max(48 * window.uiScale,
                                                              count * 45 * window.uiScale))
                                    model: backend.cameras
                                    spacing: 5
                                    clip: true
                                    boundsBehavior: Flickable.StopAtBounds

                                    delegate: Rectangle {
                                        id: cameraDelegate
                                        required property int index
                                        required property var modelData
                                        readonly property string cameraId: window.sourceId(modelData)
                                        readonly property bool chosen: cameraId === window.sourceId(backend.selectedSource)

                                        width: cameraList.width
                                        height: 40 * window.uiScale
                                        activeFocusOnTab: true
                                        radius: 9
                                        color: chosen ? "#2455f7ff" : cameraMouse.containsMouse ? "#1affffff" : "#161a29"
                                        border.width: chosen || activeFocus ? 2 : 1
                                        border.color: activeFocus ? "#ffffff" : chosen ? "#55f7ff" : "#30374a"

                                        function click() {
                                            backend.selectSource(cameraId)
                                        }

                                        Keys.onReturnPressed: click()
                                        Keys.onEnterPressed: click()
                                        Keys.onSpacePressed: click()

                                        RowLayout {
                                            anchors.fill: parent
                                            anchors.leftMargin: 11
                                            anchors.rightMargin: 9
                                            spacing: 8

                                            Rectangle {
                                                Layout.preferredWidth: 8
                                                Layout.preferredHeight: 8
                                                radius: 4
                                                color: cameraDelegate.chosen ? "#7cff9b" : "#596277"
                                            }

                                            Label {
                                                Layout.fillWidth: true
                                                text: window.itemValue(cameraDelegate.modelData, "name",
                                                                       String(cameraDelegate.modelData))
                                                color: cameraDelegate.chosen ? "#ffffff" : "#b0bacd"
                                                font.pixelSize: 11 * window.uiScale
                                                font.weight: cameraDelegate.chosen ? Font.Bold : Font.Medium
                                                elide: Text.ElideRight
                                            }

                                            Label {
                                                visible: window.itemValue(cameraDelegate.modelData, "usb", false)
                                                         || String(window.itemValue(cameraDelegate.modelData,
                                                                                    "kind", "")).toLowerCase() === "usb"
                                                text: "USB"
                                                color: "#55f7ff"
                                                font.pixelSize: 9 * window.uiScale
                                                font.weight: Font.Black
                                            }
                                        }

                                        MouseArea {
                                            id: cameraMouse
                                            anchors.fill: parent
                                            hoverEnabled: true
                                            cursorShape: Qt.PointingHandCursor
                                            onClicked: cameraDelegate.click()
                                        }
                                    }
                                }

                                Row {
                                    width: parent.width
                                    spacing: 8

                                    NeonButton {
                                        text: "VIDEO FILE"
                                        compact: true
                                        accent: "#ff4fcb"
                                        visible: backend.alternateSourcesEnabled
                                        width: visible ? (parent.width - 8) * 0.58 : 0
                                        onClicked: backend.chooseVideoFile()
                                    }

                                    NeonButton {
                                        text: "REFRESH"
                                        compact: true
                                        accent: "#9c7cff"
                                        width: backend.alternateSourcesEnabled
                                               ? (parent.width - 8) * 0.42 : parent.width
                                        onClicked: backend.refreshCameras()
                                    }
                                }

                            }
                        }

                        NeonButton {
                            id: setupStartButton
                            anchors.left: parent.left
                            anchors.right: parent.right
                            anchors.bottom: parent.bottom
                            anchors.margins: 16 * window.uiScale
                            text: "START DANCING"
                            hint: window.playerList.length > 0 ? window.playerList.length + " dancer(s) detected"
                                                               : backend.joinGestureOnly
                                                                 ? "Raise both hands to join"
                                                                 : "Step in to start • others can join later"
                            primary: true
                            accent: "#7cff9b"
                            enabled: window.playerList.length > 0
                                     && String(backend.modelStatus).toLowerCase().indexOf("error") < 0
                                     && String(backend.modelStatus).toLowerCase().indexOf("unavailable") < 0
                            onClicked: backend.startGame()
                        }
                    }
                }
            }
        }

        // COUNTDOWN ----------------------------------------------------------
        Item {
            id: countdownPage

            Rectangle {
                anchors.fill: parent
                color: "#5c050711"
            }

            Column {
                anchors.horizontalCenter: parent.horizontalCenter
                anchors.top: parent.top
                anchors.topMargin: 30 * window.uiScale
                spacing: 4

                Label {
                    anchors.horizontalCenter: parent.horizontalCenter
                    text: window.itemValue(window.song, "title", "YOUR SONG")
                    color: "#ffffff"
                    font.pixelSize: 18 * window.uiScale
                    font.weight: Font.Black
                    font.letterSpacing: 1.5
                }

                Label {
                    anchors.horizontalCenter: parent.horizontalCenter
                    text: window.playerList.length + " DANCER" + (window.playerList.length === 1 ? "" : "S") + " READY"
                    color: "#55f7ff"
                    font.pixelSize: 11 * window.uiScale
                    font.weight: Font.Bold
                    font.letterSpacing: 2
                }
            }

            Item {
                anchors.centerIn: parent
                width: 330 * window.uiScale
                height: width

                Repeater {
                    model: 3

                    Rectangle {
                        required property int index
                        anchors.centerIn: parent
                        width: parent.width - index * 52 * window.uiScale
                        height: width
                        radius: width / 2
                        color: "transparent"
                        border.width: 2
                        border.color: index % 2 ? "#55f7ff" : "#ff4fcb"
                        opacity: 0.16 + index * 0.08

                        RotationAnimator on rotation {
                            running: !window.reducedMotion
                            from: index % 2 ? 0 : 360
                            to: index % 2 ? 360 : 0
                            duration: 3000 + index * 850
                            loops: Animation.Infinite
                        }
                    }
                }

                Label {
                    id: countdownLabel
                    anchors.centerIn: parent
                    text: String(backend.countdown || "GO!")
                    color: Number(backend.countdown) > 0 ? "#ffffff" : "#7cff9b"
                    font.pixelSize: 116 * window.uiScale
                    font.weight: Font.Black
                    style: Text.Outline
                    styleColor: "#55000000"

                    onTextChanged: {
                        if (!window.reducedMotion)
                            countdownPulse.restart()
                    }

                    SequentialAnimation {
                        id: countdownPulse
                        NumberAnimation { target: countdownLabel; property: "scale"; from: 1.45; to: 1.0; duration: 340; easing.type: Easing.OutBack }
                    }
                }
            }

            Row {
                anchors.horizontalCenter: parent.horizontalCenter
                anchors.bottom: parent.bottom
                anchors.bottomMargin: 30 * window.uiScale
                spacing: 11

                    Repeater {
                        model: window.playerList

                        GlassPanel {
                            id: countdownPlayerChip
                            required property int index
                            required property var modelData
                            readonly property int playerSlot: Number(window.itemValue(modelData, "slot", index))
                            width: 175 * window.uiScale
                            height: 46 * window.uiScale
                            accent: window.playerColor(playerSlot)

                            Label {
                                anchors.centerIn: parent
                                text: "P" + Number(window.itemValue(countdownPlayerChip.modelData,
                                                                     "player_number",
                                                                     countdownPlayerChip.playerSlot + 1))
                                      + " TRACKED"
                                color: "#ffffff"
                                font.pixelSize: 10 * window.uiScale
                                font.weight: Font.Bold
                            }
                        }
                    }
                }
            }

        // GAMEPLAY -----------------------------------------------------------
        Item {
            id: playPage

            ColumnLayout {
                anchors.fill: parent
                anchors.leftMargin: 20 * window.uiScale
                anchors.rightMargin: 20 * window.uiScale
                anchors.topMargin: 16 * window.uiScale
                anchors.bottomMargin: 14 * window.uiScale
                spacing: 8 * window.uiScale

                RowLayout {
                    Layout.fillWidth: true
                    Layout.fillHeight: false
                    Layout.minimumHeight: 96 * window.uiScale
                    Layout.preferredHeight: 96 * window.uiScale
                    Layout.maximumHeight: 96 * window.uiScale
                    spacing: 12 * window.uiScale

                    Repeater {
                        model: backend.presentationMode ? [] : window.playerList

                        PlayerHud {
                            required property int index
                            required property var modelData
                            readonly property int playerSlot: Number(window.itemValue(modelData, "slot", index))
                            Layout.preferredWidth: Math.min(248 * window.uiScale,
                                                           (playPage.width
                                                            - (songStatusPanel.visible ? 420 : 240)
                                                            * window.uiScale)
                                                           / Math.max(1, window.playerList.length))
                            Layout.fillHeight: true
                            player: modelData
                            playerNumber: Number(window.itemValue(modelData, "player_number",
                                                                   playerSlot + 1))
                            playerColor: window.playerColor(Number(window.itemValue(
                                                                      modelData,
                                                                      "dancer_index",
                                                                      playerSlot)))
                            feedback: window.feedbackForSlot(playerSlot)
                            reducedMotion: window.reducedMotion
                        }
                    }

                    Item { Layout.fillWidth: true }

                    GlassPanel {
                        id: songStatusPanel
                        visible: window.width >= 1100 || window.playerList.length < 5
                        Layout.preferredWidth: visible ? 165 * window.uiScale : 0
                        Layout.fillHeight: true
                        accent: "#ffe66d"

                        Column {
                            anchors.centerIn: parent
                            spacing: 3

                            Label {
                                anchors.horizontalCenter: parent.horizontalCenter
                                text: window.itemValue(window.song, "title", "NOW DANCING")
                                color: "#ffffff"
                                font.pixelSize: 11 * window.uiScale
                                font.weight: Font.Bold
                                width: 145 * window.uiScale
                                horizontalAlignment: Text.AlignHCenter
                                elide: Text.ElideRight
                            }

                            Label {
                                anchors.horizontalCenter: parent.horizontalCenter
                                text: window.formatTime(backend.songTime) + " / " + window.formatTime(backend.songDuration)
                                color: "#ffe66d"
                                font.pixelSize: 12 * window.uiScale
                                font.weight: Font.Black
                            }
                        }
                    }

                    NeonButton {
                        Layout.preferredWidth: 104 * window.uiScale
                        text: backend.paused ? "RESUME" : "PAUSE"
                        compact: true
                        accent: "#9c7cff"
                        onClicked: backend.togglePause()
                    }
                }

                Item {
                    Layout.fillWidth: true
                    Layout.fillHeight: true

                    GlassPanel {
                        id: cuePanel
                        visible: !backend.presentationMode && backend.cuesEnabled
                                 && backend.cueDancers.length > 0
                        anchors.right: parent.right
                        anchors.top: parent.top
                        width: 190 * window.uiScale
                        height: 228 * window.uiScale
                        accent: "#ffe66d"

                        Column {
                            anchors.fill: parent
                            anchors.margins: 10 * window.uiScale
                            spacing: 5

                            Row {
                                anchors.horizontalCenter: parent.horizontalCenter
                                spacing: 6

                                Rectangle {
                                    anchors.verticalCenter: parent.verticalCenter
                                    width: 7
                                    height: 7
                                    radius: 4
                                    color: "#ffe66d"
                                }

                                Label {
                                    text: "UP NEXT"
                                    color: "#ffffff"
                                    font.pixelSize: 10 * window.uiScale
                                    font.weight: Font.Black
                                    font.letterSpacing: 1.4
                                }
                            }

                            Item {
                                width: parent.width
                                height: parent.height - 50 * window.uiScale

                                Rectangle {
                                    anchors.centerIn: parent
                                    width: parent.width * 0.78
                                    height: width
                                    radius: width / 2
                                    color: "#16ffe66d"
                                    border.width: 1
                                    border.color: "#42ffe66d"
                                }

                                SkeletonView {
                                    anchors.fill: parent
                                    anchors.margins: 5
                                    people: playPage.visible && cuePanel.visible
                                            ? backend.cueDancers : null
                                    lineColor: window.playerColor(0)
                                    mirror: false
                                    showBoxes: false
                                    showLabels: false
                                    lineScale: 1.15
                                }
                            }

                            Label {
                                anchors.horizontalCenter: parent.horizontalCenter
                                text: String(backend.nextMove || "FOLLOW").replace(/_/g, " ").toUpperCase()
                                color: "#ffe66d"
                                font.pixelSize: 11 * window.uiScale
                                font.weight: Font.Black
                                font.letterSpacing: 0.8
                            }
                        }
                    }

                    GlassPanel {
                        id: playCameraHost
                        visible: !backend.presentationMode && backend.miniViewEnabled
                        anchors.right: parent.right
                        anchors.bottom: parent.bottom
                        width: 205 * window.uiScale
                        height: 132 * window.uiScale
                        accent: "#ff4fcb"
                        clip: true

                        Label {
                            anchors.left: parent.left
                            anchors.top: parent.top
                            anchors.margins: 9
                            z: 5
                            text: "YOU \u2022 " + Number(backend.inferenceFps || 0).toFixed(0) + " FPS"
                            color: "#ffffff"
                            font.pixelSize: 9 * window.uiScale
                            font.weight: Font.Black
                        }
                    }

                    Column {
                        anchors.left: parent.left
                        anchors.bottom: parent.bottom
                        anchors.bottomMargin: 8 * window.uiScale
                        width: Math.min(390 * window.uiScale, parent.width * 0.35)
                        spacing: 7
                        visible: !backend.presentationMode

                        Repeater {
                            model: backend.framingCues

                            GlassPanel {
                                required property var modelData
                                width: parent.width
                                height: 42 * window.uiScale
                                accent: modelData.label === "MOVE BACK" ? "#ffe66d" : "#55f7ff"

                                Label {
                                    anchors.centerIn: parent
                                    text: "P" + modelData.player + "  •  " + modelData.label
                                    color: "#ffffff"
                                    font.pixelSize: 11 * window.uiScale
                                    font.weight: Font.Black
                                    font.letterSpacing: 1.0
                                }
                            }
                        }

                        GlassPanel {
                            width: parent.width
                            height: 46 * window.uiScale
                            visible: window.playerList.length < Number(backend.maxPlayers || 2)
                            accent: "#7cff9b"

                            Row {
                                anchors.centerIn: parent
                                spacing: 8

                                Rectangle {
                                    width: 8
                                    height: 8
                                    radius: 4
                                    color: "#7cff9b"

                                    SequentialAnimation on opacity {
                                        running: !window.reducedMotion
                                        loops: Animation.Infinite
                                        NumberAnimation { to: 0.25; duration: 450 }
                                        NumberAnimation { to: 1.0; duration: 450 }
                                    }
                                }

                                Label {
                                    text: backend.joinGestureOnly
                                          ? "NEW DANCER? HANDS UP TO JOIN"
                                          : "NEW DANCER? STEP IN TO JOIN"
                                    color: "#ffffff"
                                    font.pixelSize: 10 * window.uiScale
                                    font.weight: Font.Black
                                    font.letterSpacing: 0.9
                                }
                            }
                        }

                        Label {
                            text: String(backend.modelStatus || "")
                            color: "#738097"
                            font.pixelSize: 9 * window.uiScale
                            font.weight: Font.Medium
                        }
                    }
                }

                ColumnLayout {
                    Layout.fillWidth: true
                    Layout.fillHeight: false
                    Layout.minimumHeight: 102 * window.uiScale
                    Layout.preferredHeight: 102 * window.uiScale
                    Layout.maximumHeight: 102 * window.uiScale
                    spacing: 8

                    Column {
                        Layout.alignment: Qt.AlignHCenter
                        Layout.fillWidth: true
                        Layout.maximumWidth: Math.max(450 * window.uiScale, playPage.width * 0.68)
                        spacing: 0

                        Label {
                            width: parent.width
                            text: String(backend.currentLyric || "")
                            color: "#ffffff"
                            font.pixelSize: 24 * window.uiScale
                            font.weight: Font.Black
                            horizontalAlignment: Text.AlignHCenter
                            elide: Text.ElideRight
                            style: Text.Outline
                            styleColor: "#99050912"
                        }

                        Label {
                            width: parent.width
                            text: String(backend.nextLyric || "")
                            color: "#9ca8bd"
                            font.pixelSize: 13 * window.uiScale
                            font.weight: Font.DemiBold
                            horizontalAlignment: Text.AlignHCenter
                            elide: Text.ElideRight
                        }
                    }

                    Item {
                        Layout.fillWidth: true
                        Layout.preferredHeight: 18 * window.uiScale

                        Rectangle {
                            anchors.left: parent.left
                            anchors.right: parent.right
                            anchors.verticalCenter: parent.verticalCenter
                            height: 7 * window.uiScale
                            radius: height / 2
                            color: "#34394a"
                        }

                        Rectangle {
                            anchors.left: parent.left
                            anchors.verticalCenter: parent.verticalCenter
                            width: parent.width * window.clamped(backend.progress, 0, 1)
                            height: 7 * window.uiScale
                            radius: height / 2
                            gradient: Gradient {
                                orientation: Gradient.Horizontal
                                GradientStop { position: 0; color: "#55f7ff" }
                                GradientStop { position: 0.55; color: "#9c7cff" }
                                GradientStop { position: 1; color: "#ff4fcb" }
                            }

                            Behavior on width {
                                enabled: !window.reducedMotion
                                NumberAnimation { duration: 100; easing.type: Easing.Linear }
                            }
                        }
                    }
                }
            }

            Rectangle {
                anchors.fill: parent
                visible: backend.paused
                color: "#dc070911"
                z: 50

                MouseArea { anchors.fill: parent }

                GlassPanel {
                    anchors.centerIn: parent
                    width: 410 * window.uiScale
                    height: (backend.presentationMode ? 250 : 390) * window.uiScale
                    accent: "#9c7cff"

                    Column {
                        anchors.fill: parent
                        anchors.margins: 30 * window.uiScale
                        spacing: 15 * window.uiScale

                        Label {
                            anchors.horizontalCenter: parent.horizontalCenter
                            text: backend.presentationFinished ? "FINISHED" : "PAUSED"
                            color: "#ffffff"
                            font.pixelSize: 34 * window.uiScale
                            font.weight: Font.Black
                            font.letterSpacing: 4
                        }

                        Label {
                            anchors.horizontalCenter: parent.horizontalCenter
                            text: backend.presentationMode
                                  ? (backend.presentationFinished ? "Replay the choreography?"
                                                                  : "Playback is paused.")
                                  : "Catch your breath. The beat will wait."
                            color: "#aab4c7"
                            font.pixelSize: 12 * window.uiScale
                        }

                        NeonButton {
                            id: resumeButton
                            width: parent.width
                            text: backend.presentationFinished ? "REPLAY" : "RESUME"
                            primary: true
                            accent: "#7cff9b"
                            onClicked: backend.togglePause()
                        }

                        NeonSwitch {
                            width: parent.width
                            text: "UPCOMING MOVE CUES"
                            checked: backend.cuesEnabled
                            accent: "#55f7ff"
                            onToggled: window.setOption("cues", checked)
                            visible: !backend.presentationMode
                        }

                        NeonSwitch {
                            width: parent.width
                            text: "MINI POSE VIEW"
                            checked: backend.miniViewEnabled
                            accent: "#ff4fcb"
                            onToggled: window.setOption("miniView", checked)
                            visible: !backend.presentationMode
                        }

                        NeonButton {
                            width: parent.width
                            text: backend.presentationMode ? "EXIT PLAYER" : "LEAVE SONG"
                            accent: "#ff6c88"
                            onClicked: backend.leaveGame()
                        }
                    }
                }
            }
        }

        // RESULTS ------------------------------------------------------------
        Item {
            id: resultsPage

            RowLayout {
                anchors.fill: parent
                anchors.margins: 42 * window.uiScale
                spacing: 28 * window.uiScale

                Item {
                    Layout.fillWidth: true
                    Layout.fillHeight: true

                    Column {
                        anchors.centerIn: parent
                        width: parent.width
                        spacing: 12 * window.uiScale

                        Label {
                            anchors.horizontalCenter: parent.horizontalCenter
                            text: window.resultValue("headline", "SONG COMPLETE!")
                            color: "#ffffff"
                            font.pixelSize: 36 * window.uiScale
                            font.weight: Font.Black
                            font.letterSpacing: 2
                        }

                        Label {
                            anchors.horizontalCenter: parent.horizontalCenter
                            text: window.itemValue(window.song, "title", "Great dance")
                            color: "#55f7ff"
                            font.pixelSize: 16 * window.uiScale
                            font.weight: Font.Bold
                        }

                        StarMeter {
                            anchors.horizontalCenter: parent.horizontalCenter
                            value: window.resultValue("team_stars", 0)
                            starSize: 52 * window.uiScale
                            spacing: 6
                            accent: "#ffe66d"
                            animateChanges: !window.reducedMotion
                        }

                        Label {
                            anchors.horizontalCenter: parent.horizontalCenter
                            text: Number(window.resultValue("team_score", 0)).toLocaleString(Qt.locale(), "f", 0)
                            color: "#ffffff"
                            font.pixelSize: 62 * window.uiScale
                            font.weight: Font.Black
                        }

                        Label {
                            anchors.horizontalCenter: parent.horizontalCenter
                            text: "+" + Number(window.resultValue("earned_points", 0)).toLocaleString(Qt.locale(), "f", 0)
                                  + " GROOVE POINTS"
                            color: "#7cff9b"
                            font.pixelSize: 15 * window.uiScale
                            font.weight: Font.Black
                            font.letterSpacing: 1.4
                        }

                        GlassPanel {
                            anchors.horizontalCenter: parent.horizontalCenter
                            visible: window.resultValue("newly_unlocked", []).length > 0
                            width: Math.min(parent.width * 0.8, 430 * window.uiScale)
                            height: 72 * window.uiScale
                            accent: "#ff4fcb"

                            Row {
                                anchors.centerIn: parent
                                spacing: 12

                                Label {
                                    text: "\u2726"
                                    color: "#ff4fcb"
                                    font.pixelSize: 28 * window.uiScale
                                }

                                Column {
                                    anchors.verticalCenter: parent.verticalCenter

                                    Label {
                                        text: "NEW SONG UNLOCKED"
                                        color: "#ffffff"
                                        font.pixelSize: 10 * window.uiScale
                                        font.weight: Font.Black
                                        font.letterSpacing: 1.4
                                    }

                                    Label {
                                        text: window.resultValue("newly_unlocked", []).length
                                              ? window.resultValue("newly_unlocked", [])[0] : "Keep dancing!"
                                        color: "#ff87d9"
                                        font.pixelSize: 16 * window.uiScale
                                        font.weight: Font.Bold
                                    }
                                }
                            }
                        }
                    }
                }

                GlassPanel {
                    Layout.preferredWidth: 390 * window.uiScale
                    Layout.fillHeight: true
                    accent: "#55f7ff"

                    ColumnLayout {
                        anchors.fill: parent
                        anchors.margins: 24 * window.uiScale
                        spacing: 15 * window.uiScale

                        Label {
                            Layout.alignment: Qt.AlignHCenter
                            text: "DANCE CREW"
                            color: "#ffffff"
                            font.pixelSize: 16 * window.uiScale
                            font.weight: Font.Black
                            font.letterSpacing: 2
                        }

                        Repeater {
                            model: window.playerList

                            PlayerHud {
                                required property int index
                                required property var modelData
                                Layout.fillWidth: true
                                Layout.preferredHeight: Math.min(
                                                            100 * window.uiScale,
                                                            (resultsPage.height - 390 * window.uiScale)
                                                            / Math.max(1, window.playerList.length))
                                player: modelData
                                playerNumber: Number(window.itemValue(modelData, "player_number", index + 1))
                                playerColor: window.playerColor(Number(window.itemValue(modelData, "slot", index)))
                                reducedMotion: window.reducedMotion
                            }
                        }

                        Item { Layout.fillHeight: true }

                        NeonButton {
                            id: retryButton
                            Layout.fillWidth: true
                            text: "DANCE AGAIN"
                            primary: true
                            accent: "#7cff9b"
                            onClicked: backend.retry()
                        }

                        NeonButton {
                            Layout.fillWidth: true
                            text: "SONG LIBRARY"
                            accent: "#9c7cff"
                            onClicked: backend.goLibrary()
                        }
                    }
                }
            }

            Repeater {
                model: window.reducedMotion ? 0 : 34

                Rectangle {
                    required property int index
                    property real targetY: resultsPage.height + 40
                    x: ((index * 83) % 997) / 997 * resultsPage.width
                    y: -30 - (index % 7) * 24
                    width: 5 + index % 8
                    height: width * (index % 3 === 0 ? 2.2 : 1)
                    radius: index % 2 ? width / 2 : 1
                    color: ["#55f7ff", "#ff4fcb", "#ffe66d", "#7cff9b"][index % 4]
                    rotation: index * 29
                    opacity: 0.8

                    SequentialAnimation on y {
                        running: resultsPage.visible
                        loops: Animation.Infinite
                        PauseAnimation { duration: index * 63 }
                        NumberAnimation {
                            to: resultsPage.height + 40
                            duration: 2600 + (index % 8) * 190
                            easing.type: Easing.InQuad
                        }
                        PropertyAction { value: -40 }
                    }

                    RotationAnimator on rotation {
                        running: resultsPage.visible
                        from: 0
                        to: index % 2 ? 360 : -360
                        duration: 1800 + index * 20
                        loops: Animation.Infinite
                    }
                }
            }
        }

        // SETTINGS -----------------------------------------------------------
        Item {
            id: settingsPage

            ColumnLayout {
                anchors.fill: parent
                anchors.margins: 28 * window.uiScale
                spacing: 14 * window.uiScale

                RowLayout {
                    Layout.fillWidth: true
                    Layout.preferredHeight: 54 * window.uiScale
                    spacing: 14 * window.uiScale

                    NeonButton {
                        id: settingsBackButton
                        text: "\u2190 BACK"
                        compact: true
                        accent: "#9c7cff"
                        onClicked: backend.closeSettings()
                    }

                    Column {
                        Layout.fillWidth: true
                        spacing: 0

                        Label {
                            text: "SETTINGS"
                            color: "#ffffff"
                            font.pixelSize: 27 * window.uiScale
                            font.weight: Font.Black
                            font.letterSpacing: 1.7
                        }

                        Label {
                            text: "Tune the room once, then just dance."
                            color: "#8f9bb1"
                            font.pixelSize: 11 * window.uiScale
                        }
                    }

                    NeonButton {
                        text: "ADD SONG"
                        compact: true
                        accent: "#ff4fcb"
                        onClicked: songImportDialog.begin()
                    }
                }

                RowLayout {
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    spacing: 16 * window.uiScale

                    GlassPanel {
                        Layout.fillWidth: true
                        Layout.fillHeight: true
                        accent: "#55f7ff"

                        ColumnLayout {
                            anchors.fill: parent
                            anchors.margins: 22 * window.uiScale
                            spacing: 8 * window.uiScale

                            Label {
                                text: "AUDIO"
                                color: "#55f7ff"
                                font.pixelSize: 12 * window.uiScale
                                font.weight: Font.Black
                                font.letterSpacing: 1.5
                            }

                            RowLayout {
                                Layout.fillWidth: true

                                NeonButton {
                                    text: "\u22125%"
                                    compact: true
                                    Layout.preferredWidth: 82 * window.uiScale
                                    accent: "#55f7ff"
                                    onClicked: window.setOption("volume", backend.volume - 0.05)
                                }

                                Label {
                                    Layout.fillWidth: true
                                    text: Math.round(Number(backend.volume || 0) * 100) + "% VOLUME"
                                    horizontalAlignment: Text.AlignHCenter
                                    color: "#ffffff"
                                    font.pixelSize: 13 * window.uiScale
                                    font.weight: Font.Black
                                }

                                NeonButton {
                                    text: "+5%"
                                    compact: true
                                    Layout.preferredWidth: 82 * window.uiScale
                                    accent: "#55f7ff"
                                    onClicked: window.setOption("volume", backend.volume + 0.05)
                                }
                            }

                            Label {
                                text: "INPUT TIMING"
                                color: "#ffe66d"
                                font.pixelSize: 12 * window.uiScale
                                font.weight: Font.Black
                                font.letterSpacing: 1.5
                            }

                            Label {
                                Layout.fillWidth: true
                                text: "Shift scoring only if your camera consistently feels early or late."
                                wrapMode: Text.WordWrap
                                color: "#8f9bb1"
                                font.pixelSize: 10 * window.uiScale
                            }

                            RowLayout {
                                Layout.fillWidth: true

                                NeonButton {
                                    text: "\u221220 MS"
                                    compact: true
                                    Layout.preferredWidth: 90 * window.uiScale
                                    accent: "#9c7cff"
                                    onClicked: window.setOption("latencyMs", backend.latencyMs - 20)
                                }

                                Label {
                                    Layout.fillWidth: true
                                    text: (backend.latencyMs >= 0 ? "+" : "") + backend.latencyMs + " MS"
                                    horizontalAlignment: Text.AlignHCenter
                                    color: "#ffe66d"
                                    font.pixelSize: 13 * window.uiScale
                                    font.weight: Font.Black
                                }

                                NeonButton {
                                    text: "+20 MS"
                                    compact: true
                                    Layout.preferredWidth: 90 * window.uiScale
                                    accent: "#9c7cff"
                                    onClicked: window.setOption("latencyMs", backend.latencyMs + 20)
                                }
                            }

                            Label {
                                text: "CAMERA FRAMING"
                                color: "#7cff9b"
                                font.pixelSize: 12 * window.uiScale
                                font.weight: Font.Black
                                font.letterSpacing: 1.5
                            }

                            Label {
                                Layout.fillWidth: true
                                text: "This is your target full-body height in the camera frame. The setup preview tells each player to move forward or back."
                                wrapMode: Text.WordWrap
                                color: "#8f9bb1"
                                font.pixelSize: 10 * window.uiScale
                            }

                            RowLayout {
                                Layout.fillWidth: true

                                NeonButton {
                                    text: "\u22125%"
                                    compact: true
                                    Layout.preferredWidth: 82 * window.uiScale
                                    accent: "#7cff9b"
                                    onClicked: window.setOption("framingHeight", backend.framingHeight - 0.05)
                                }

                                Label {
                                    Layout.fillWidth: true
                                    text: Math.round(Number(backend.framingHeight || 0.72) * 100) + "% OF FRAME"
                                    horizontalAlignment: Text.AlignHCenter
                                    color: "#7cff9b"
                                    font.pixelSize: 13 * window.uiScale
                                    font.weight: Font.Black
                                }

                                NeonButton {
                                    text: "+5%"
                                    compact: true
                                    Layout.preferredWidth: 82 * window.uiScale
                                    accent: "#7cff9b"
                                    onClicked: window.setOption("framingHeight", backend.framingHeight + 0.05)
                                }
                            }

                            Item { Layout.fillHeight: true }
                        }
                    }

                    GlassPanel {
                        Layout.fillWidth: true
                        Layout.fillHeight: true
                        accent: "#ff4fcb"

                        ColumnLayout {
                            anchors.fill: parent
                            anchors.margins: 22 * window.uiScale
                            spacing: 8 * window.uiScale

                            Label {
                                text: "PLAY EXPERIENCE"
                                color: "#ff70d5"
                                font.pixelSize: 12 * window.uiScale
                                font.weight: Font.Black
                                font.letterSpacing: 1.5
                            }

                            NeonSwitch {
                                Layout.fillWidth: true
                                text: "UPCOMING MOVE CUES"
                                description: "Show one move ahead while dancing"
                                checked: backend.cuesEnabled
                                accent: "#55f7ff"
                                onToggled: window.setOption("cues", checked)
                            }

                            NeonSwitch {
                                Layout.fillWidth: true
                                text: "MINI POSE VIEW"
                                description: "Keep your detected skeleton on screen"
                                checked: backend.miniViewEnabled
                                accent: "#ff4fcb"
                                onToggled: window.setOption("miniView", checked)
                            }

                            NeonSwitch {
                                Layout.fillWidth: true
                                text: "JOIN GESTURE ONLY"
                                description: "Hold both hands overhead before entering"
                                checked: backend.joinGestureOnly
                                accent: "#ffe66d"
                                onToggled: window.setOption("joinGestureOnly", checked)
                            }

                            NeonSwitch {
                                Layout.fillWidth: true
                                text: "REDUCE MOTION"
                                description: "Calmer menus and feedback effects"
                                checked: backend.reducedMotion
                                accent: "#9c7cff"
                                onToggled: window.setOption("reducedMotion", checked)
                            }

                            Item { Layout.fillHeight: true }

                            Label {
                                Layout.fillWidth: true
                                text: "Changes are saved automatically on this computer."
                                wrapMode: Text.WordWrap
                                color: "#77839a"
                                font.pixelSize: 10 * window.uiScale
                                horizontalAlignment: Text.AlignHCenter
                            }
                        }
                    }
                }
            }
        }
    }

    SongImportDialog {
        id: songImportDialog
        appBackend: backend
        uiScale: window.uiScale
    }

    // One capture sink, visually re-parented between setup and the in-song mini
    // view. The skeleton overlay uses the same normalized pose coordinates.
    Item {
        id: cameraFeed
        parent: window.setupScreen ? setupCameraHost : playCameraHost
        anchors.fill: parent
        visible: !backend.presentationMode && (window.setupScreen
                 || (window.screenName === "play" && backend.miniViewEnabled))
        z: 2
        clip: true

        VideoOutput {
            id: cameraPreview
            anchors.fill: parent
            fillMode: VideoOutput.PreserveAspectFit
            transform: Scale {
                origin.x: cameraPreview.width / 2
                xScale: -1
            }

            Component.onCompleted: backend.attachVideoSink(videoSink)
        }

        Rectangle {
            anchors.fill: parent
            color: "#18000000"
        }

        SkeletonView {
            readonly property real inset: window.setupScreen ? 14 * window.uiScale : 4
            x: cameraPreview.contentRect.x + inset
            y: cameraPreview.contentRect.y + inset
            width: Math.max(0, cameraPreview.contentRect.width - inset * 2)
            height: Math.max(0, cameraPreview.contentRect.height - inset * 2)
            people: cameraFeed.visible
                    ? (window.setupScreen ? backend.posePeople : window.playerList)
                    : null
            lineColor: "#55f7ff"
            mirror: true
            showBoxes: window.setupScreen
            showLabels: window.setupScreen
            lineScale: window.setupScreen ? 1.0 : 0.72
        }

        Row {
            anchors.horizontalCenter: parent.horizontalCenter
            anchors.top: parent.top
            anchors.topMargin: 56 * window.uiScale
            spacing: 7 * window.uiScale
            visible: window.setupScreen && backend.framingCues.length > 0
            z: 5

            Repeater {
                model: backend.framingCues

                Rectangle {
                    required property var modelData
                    width: framingLabel.implicitWidth + 24 * window.uiScale
                    height: 34 * window.uiScale
                    radius: height / 2
                    color: modelData.label === "MOVE BACK" ? "#dd5e4700" : "#cc073a55"
                    border.width: 1
                    border.color: modelData.label === "MOVE BACK" ? "#ffe66d" : "#55f7ff"

                    Label {
                        id: framingLabel
                        anchors.centerIn: parent
                        text: "P" + modelData.player + "  " + modelData.label
                        color: "#ffffff"
                        font.pixelSize: 10 * window.uiScale
                        font.weight: Font.Black
                        font.letterSpacing: 0.8
                    }
                }
            }
        }
    }

    Rectangle {
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.bottom: parent.bottom
        height: 3
        visible: window.screenName === "setup" && String(backend.modelStatus).toLowerCase().indexOf("load") >= 0
        color: "#22283a"
        z: 100

        Rectangle {
            width: parent.width * 0.26
            height: parent.height
            gradient: Gradient {
                orientation: Gradient.Horizontal
                GradientStop { position: 0; color: "transparent" }
                GradientStop { position: 0.5; color: "#55f7ff" }
                GradientStop { position: 1; color: "transparent" }
            }

            SequentialAnimation on x {
                running: !window.reducedMotion
                loops: Animation.Infinite
                NumberAnimation { from: -width; to: window.width; duration: 1200; easing.type: Easing.InOutQuad }
            }
        }
    }

    Component.onCompleted: Qt.callLater(focusForScreen)
}
