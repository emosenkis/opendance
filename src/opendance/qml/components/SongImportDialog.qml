pragma ComponentBehavior: Bound

import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Popup {
    id: dialog

    component ImportField: TextField {
        selectByMouse: true
        color: "#f8fbff"
        placeholderTextColor: "#667188"
        font.pixelSize: 12 * dialog.uiScale

        background: Rectangle {
            radius: 9
            color: "#151a29"
            border.width: parent.activeFocus ? 2 : 1
            border.color: parent.activeFocus ? "#ffffff" : "#3e4860"
        }
    }

    required property var appBackend
    property real uiScale: 1
    readonly property var draft: appBackend.songImport || ({})
    readonly property bool busy: Boolean(appBackend.songImportBusy)
    readonly property real progress: Number(appBackend.songImportProgress)
    readonly property real duration: Number(value("duration", 0)) || 0
    readonly property real trimStart: Number(trimStartField.text) || 0
    readonly property real trimEnd: Number(trimEndField.text) || 0
    readonly property real hiddenIntro: Number(hiddenIntroField.text) || 0
    readonly property bool ready: String(value("source", "")).length > 0
    readonly property bool validOptions: ready
                                         && titleField.text.trim().length > 0
                                         && artistField.text.trim().length > 0
                                         && (!trimStartField.text.trim().length
                                             || trimStartField.acceptableInput)
                                         && (!trimEndField.text.trim().length
                                             || trimEndField.acceptableInput)
                                         && (!hiddenIntroField.text.trim().length
                                             || hiddenIntroField.acceptableInput)
                                         && trimStart >= 0 && trimEnd >= 0
                                         && hiddenIntro >= 0
                                         && (duration <= 0 || trimStart + trimEnd < duration)
                                         && (duration <= 0
                                             || hiddenIntro < duration - trimStart - trimEnd)
    property string loadedSource: ""

    function value(name, fallback) {
        return draft && draft[name] !== undefined && draft[name] !== null
               ? draft[name] : fallback
    }

    function syncDraft() {
        var source = String(value("source", ""))
        if (!source.length) {
            loadedSource = ""
            return
        }
        if (source === loadedSource)
            return
        loadedSource = source
        titleField.text = String(value("title", ""))
        artistField.text = String(value("artist", "Unknown Artist"))
        trimStartField.text = "0"
        trimEndField.text = "0"
        hiddenIntroField.text = "0"
        dancerCount.value = 1
        copyVideo.checked = true
    }

    function begin() {
        loadedSource = ""
        urlField.clear()
        appBackend.resetSongImport()
        open()
        Qt.callLater(fileButton.forceActiveFocus)
    }

    function extract() {
        appBackend.startSongImport({
            "title": titleField.text.trim(),
            "artist": artistField.text.trim(),
            "dancer_count": dancerCount.value,
            "trim_start": trimStart,
            "trim_end": trimEnd,
            "hide_video_intro": hiddenIntro,
            "copy_video": copyVideo.checked
        })
    }

    onDraftChanged: Qt.callLater(syncDraft)

    width: Math.min(parent ? parent.width - 40 * uiScale : 760 * uiScale,
                    760 * uiScale)
    height: Math.min(parent ? parent.height - 34 * uiScale : 650 * uiScale,
                     650 * uiScale)
    x: parent ? Math.round((parent.width - width) / 2) : 0
    y: parent ? Math.round((parent.height - height) / 2) : 0
    padding: 0
    modal: true
    focus: true
    closePolicy: busy ? Popup.NoAutoClose : Popup.CloseOnEscape

    Overlay.modal: Rectangle { color: "#b0060810" }

    background: GlassPanel { accent: "#ff4fcb" }

    contentItem: ColumnLayout {
        anchors.fill: parent
        anchors.margins: 22 * dialog.uiScale
        spacing: 12 * dialog.uiScale

        Accessible.name: "Add a song"
        Accessible.description: "Choose a dance video and review its extraction settings"

        RowLayout {
            Layout.fillWidth: true

            Column {
                Layout.fillWidth: true
                spacing: 1

                Label {
                    text: "ADD A SONG"
                    color: "#ffffff"
                    font.pixelSize: 24 * dialog.uiScale
                    font.weight: Font.Black
                    font.letterSpacing: 1.5
                }

                Label {
                    text: dialog.ready ? "Review before extracting choreography"
                                       : "Choose a local video or paste a supported URL"
                    color: "#9aa6bd"
                    font.pixelSize: 11 * dialog.uiScale
                }
            }

            NeonButton {
                text: "CLOSE"
                compact: true
                accent: "#9c7cff"
                enabled: !dialog.busy
                onClicked: dialog.close()
            }
        }

        RowLayout {
            Layout.fillWidth: true
            spacing: 9 * dialog.uiScale

            NeonButton {
                id: fileButton
                text: "VIDEO FILE"
                compact: true
                accent: "#55f7ff"
                enabled: !dialog.busy
                onClicked: dialog.appBackend.chooseSongImportFile()
            }

            ImportField {
                id: urlField
                Layout.fillWidth: true
                placeholderText: "https://example.com/dance-video"
                enabled: !dialog.busy
                Accessible.name: "Song video URL"
                onAccepted: if (text.trim().length) dialog.appBackend.prepareSongImportUrl(text.trim())
            }

            NeonButton {
                text: "FETCH"
                compact: true
                accent: "#ff4fcb"
                enabled: !dialog.busy && urlField.text.trim().length > 0
                onClicked: dialog.appBackend.prepareSongImportUrl(urlField.text.trim())
            }
        }

        Rectangle {
            Layout.fillWidth: true
            Layout.preferredHeight: 1
            color: "#30374a"
        }

        ScrollView {
            id: importScroll
            Layout.fillWidth: true
            Layout.fillHeight: true
            clip: true
            ScrollBar.horizontal.policy: ScrollBar.AlwaysOff

            ColumnLayout {
                width: importScroll.availableWidth
                spacing: 10 * dialog.uiScale

                Label {
                    Layout.fillWidth: true
                    visible: !dialog.ready && !dialog.busy
                    text: "For URLs, OpenDance uses a matching helper command from your config.\nMetadata is read from the downloaded video automatically."
                    wrapMode: Text.WordWrap
                    horizontalAlignment: Text.AlignHCenter
                    color: "#7f8ba2"
                    font.pixelSize: 11 * dialog.uiScale
                }

                Label {
                    Layout.fillWidth: true
                    visible: dialog.ready
                    text: String(dialog.value("source", ""))
                    elide: Text.ElideMiddle
                    color: "#7cff9b"
                    font.pixelSize: 10 * dialog.uiScale
                }

                GridLayout {
                    Layout.fillWidth: true
                    visible: dialog.ready
                    columns: 2
                    columnSpacing: 12 * dialog.uiScale
                    rowSpacing: 8 * dialog.uiScale

                    Label {
                        text: "TITLE"
                        color: "#55f7ff"
                        font.pixelSize: 10 * dialog.uiScale
                        font.weight: Font.Black
                    }

                    Label {
                        text: "ARTIST"
                        color: "#ff70d5"
                        font.pixelSize: 10 * dialog.uiScale
                        font.weight: Font.Black
                    }

                    ImportField {
                        id: titleField
                        Layout.fillWidth: true
                        Accessible.name: "Song title"
                    }

                    ImportField {
                        id: artistField
                        Layout.fillWidth: true
                        Accessible.name: "Song artist"
                    }

                    Label {
                        text: "TRIM FROM START (SECONDS)"
                        color: "#ffe66d"
                        font.pixelSize: 10 * dialog.uiScale
                        font.weight: Font.Black
                    }

                    Label {
                        text: "TRIM FROM END (SECONDS)"
                        color: "#ffe66d"
                        font.pixelSize: 10 * dialog.uiScale
                        font.weight: Font.Black
                    }

                    ImportField {
                        id: trimStartField
                        Layout.fillWidth: true
                        inputMethodHints: Qt.ImhFormattedNumbersOnly
                        validator: DoubleValidator { bottom: 0; decimals: 2 }
                        Accessible.name: "Seconds to trim from start"
                    }

                    ImportField {
                        id: trimEndField
                        Layout.fillWidth: true
                        inputMethodHints: Qt.ImhFormattedNumbersOnly
                        validator: DoubleValidator { bottom: 0; decimals: 2 }
                        Accessible.name: "Seconds to trim from end"
                    }

                    Label {
                        text: "HIDE OPENING VIDEO (SECONDS)"
                        color: "#9c7cff"
                        font.pixelSize: 10 * dialog.uiScale
                        font.weight: Font.Black
                    }

                    Label {
                        text: "DANCERS TO FOLLOW"
                        color: "#7cff9b"
                        font.pixelSize: 10 * dialog.uiScale
                        font.weight: Font.Black
                    }

                    ImportField {
                        id: hiddenIntroField
                        Layout.fillWidth: true
                        inputMethodHints: Qt.ImhFormattedNumbersOnly
                        validator: DoubleValidator { bottom: 0; decimals: 2 }
                        Accessible.name: "Seconds of opening video to hide"
                    }

                    SpinBox {
                        id: dancerCount
                        Layout.fillWidth: true
                        from: 1
                        to: 6
                        editable: true
                        Accessible.name: "Number of choreography dancers"
                    }
                }

                Label {
                    Layout.fillWidth: true
                    visible: dialog.ready && dialog.duration > 0
                    text: "Video length: " + Math.floor(dialog.duration / 60) + ":"
                          + (Math.floor(dialog.duration % 60) < 10 ? "0" : "")
                          + Math.floor(dialog.duration % 60)
                    color: "#8f9bb1"
                    font.pixelSize: 10 * dialog.uiScale
                }

                RowLayout {
                    Layout.fillWidth: true
                    visible: dialog.ready

                    NeonSwitch {
                        id: copyVideo
                        Layout.fillWidth: true
                        text: "COPY VIDEO INTO LIBRARY"
                        description: "Keep this song playable if the original file moves"
                        checked: true
                        accent: "#55f7ff"
                    }

                    NeonButton {
                        text: String(dialog.value("lyrics", "")).length ? "CHANGE LYRICS" : "LYRICS (.LRC)"
                        compact: true
                        accent: "#9c7cff"
                        enabled: !dialog.busy
                        onClicked: dialog.appBackend.chooseSongImportLyrics()
                    }
                }

                Label {
                    Layout.fillWidth: true
                    visible: dialog.ready && String(dialog.value("lyrics", "")).length > 0
                    text: "Lyrics: " + String(dialog.value("lyrics", ""))
                    elide: Text.ElideMiddle
                    color: "#9c7cff"
                    font.pixelSize: 10 * dialog.uiScale
                }

                Label {
                    Layout.fillWidth: true
                    visible: dialog.ready && !dialog.validOptions
                    text: "Enter a title and artist. Trims must leave some video for the dance."
                    wrapMode: Text.WordWrap
                    color: "#ff855f"
                    font.pixelSize: 10 * dialog.uiScale
                }
            }
        }

        ProgressBar {
            id: extractionProgress
            Layout.fillWidth: true
            Layout.preferredHeight: 9 * dialog.uiScale
            visible: dialog.busy && dialog.progress >= 0
            from: 0
            to: 1
            value: Math.max(0, Math.min(1, dialog.progress))

            background: Rectangle {
                radius: height / 2
                color: "#252b3c"
            }

            contentItem: Item {
                Rectangle {
                    width: parent.width * extractionProgress.visualPosition
                    height: parent.height
                    radius: height / 2
                    color: "#7cff9b"
                }
            }
        }

        RowLayout {
            Layout.fillWidth: true
            spacing: 10 * dialog.uiScale

            BusyIndicator {
                running: dialog.busy && dialog.progress < 0
                visible: running
                Layout.preferredWidth: 34 * dialog.uiScale
                Layout.preferredHeight: 34 * dialog.uiScale
            }

            Label {
                Layout.fillWidth: true
                text: String(dialog.appBackend.songImportStatus || "")
                wrapMode: Text.WordWrap
                color: text.toLowerCase().indexOf("error") >= 0 ? "#ff855f" : "#a9b4c9"
                font.pixelSize: 10 * dialog.uiScale
            }

            NeonButton {
                text: dialog.busy ? "WORKING…" : "EXTRACT SONG"
                hint: "This can take several minutes"
                primary: true
                accent: "#7cff9b"
                enabled: dialog.validOptions && !dialog.busy
                onClicked: dialog.extract()
            }
        }
    }
}
