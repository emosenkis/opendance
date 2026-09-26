import QtQuick
import QtMultimedia

Item {
    id: root

    required property url videoSource
    required property url audioSource
    required property int startMs
    required property int durationMs
    required property real outputVolume
    readonly property bool hasVideo: String(videoSource) !== ""
    readonly property bool separateAudio: hasVideo && String(audioSource) !== ""
    readonly property url primarySource: hasVideo ? videoSource : audioSource
    readonly property int fadeMs: Math.min(700, Math.floor(durationMs / 3))
    property bool started: false

    function loaded(player) {
        return player.mediaStatus === MediaPlayer.LoadedMedia
                || player.mediaStatus === MediaPlayer.BufferedMedia
    }

    function maybeStart() {
        if (durationMs <= 0 || !loaded(primaryPlayer)
                || (separateAudio && !loaded(audioPlayer)))
            return
        if (started)
            return
        primaryPlayer.position = startMs
        audioPlayer.position = startMs
        started = true
        primaryPlayer.play()
        if (separateAudio)
            audioPlayer.play()
        envelope.start()
    }

    function stopPlayers() {
        primaryPlayer.stop()
        audioPlayer.stop()
    }

    function resetPlayers() {
        envelope.stop()
        primaryAudio.volume = 0
        separateAudioOutput.volume = 0
        primaryPlayer.pause()
        audioPlayer.pause()
        primaryPlayer.position = startMs
        audioPlayer.position = startMs
        started = false
    }

    Component.onDestruction: stopPlayers()

    VideoOutput {
        id: previewVideo
        anchors.fill: parent
        visible: root.hasVideo
        fillMode: VideoOutput.PreserveAspectCrop
    }

    MediaPlayer {
        id: primaryPlayer
        source: root.primarySource
        videoOutput: root.hasVideo ? previewVideo : null
        audioOutput: AudioOutput {
            id: primaryAudio
            muted: root.separateAudio
            volume: 0
        }
        onMediaStatusChanged: root.maybeStart()
    }

    MediaPlayer {
        id: audioPlayer
        source: root.separateAudio ? root.audioSource : ""
        audioOutput: AudioOutput {
            id: separateAudioOutput
            muted: !root.separateAudio
            volume: 0
        }
        onMediaStatusChanged: root.maybeStart()
    }

    SequentialAnimation {
        id: envelope

        ParallelAnimation {
            NumberAnimation {
                target: primaryAudio
                property: "volume"
                from: 0
                to: root.outputVolume
                duration: root.fadeMs
            }
            NumberAnimation {
                target: separateAudioOutput
                property: "volume"
                from: 0
                to: root.outputVolume
                duration: root.fadeMs
            }
        }
        PauseAnimation { duration: Math.max(0, root.durationMs - 2 * root.fadeMs) }
        ParallelAnimation {
            NumberAnimation {
                target: primaryAudio
                property: "volume"
                to: 0
                duration: root.fadeMs
            }
            NumberAnimation {
                target: separateAudioOutput
                property: "volume"
                to: 0
                duration: root.fadeMs
            }
        }
        ScriptAction { script: root.resetPlayers() }
    }
}
