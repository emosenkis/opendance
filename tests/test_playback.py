from importlib.resources import files
import json
from pathlib import Path
import runpy
import sys
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from PySide6.QtCore import Qt
from PySide6.QtMultimedia import QMediaPlayer

from opendance.app import (
    Backend,
    FEEDBACK_INTERVAL_SECONDS,
    MAX_PLAYERS,
    SleepInhibitor,
    _face_descriptor,
    _video_thumbnail,
    _enabled,
    _feedback_due,
    _interpolated_media_time,
    song_preview,
    song_manifest_metadata,
    song_media_path,
)
from opendance.game import load_catalog
from opendance.game import named_pose
from opendance.player import load_player_song


class PlaybackPolicyTest(unittest.TestCase):
    def test_face_descriptor_is_compact_and_normalized(self):
        import numpy as np

        crop = np.zeros((48, 48, 3), dtype=np.uint8)
        crop[8:20, 8:20] = 180
        crop[8:20, 28:40] = 180
        crop[28:40, 16:32] = 230

        descriptor = _face_descriptor(crop)

        self.assertEqual(len(descriptor), 63)
        self.assertAlmostEqual(sum(value * value for value in descriptor), 1.0, places=5)

    def test_alternate_sources_require_an_explicit_true_value(self):
        for value in ("1", "true", "YES", "on"):
            self.assertTrue(_enabled(value))
        for value in (None, "", "0", "false", "anything"):
            self.assertFalse(_enabled(value))

    def test_song_media_is_resolved_relative_to_its_package(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            video = root / "coach.mp4"
            video.touch()
            song = {"_root": str(root), "video": video.name}
            self.assertEqual(song_media_path(song, "video"), video.resolve())
            video.unlink()
            self.assertIsNone(song_media_path(song, "video"))

    def test_song_preview_resolves_media_and_respects_trimmed_video_intro(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "coach.mp4").touch()
            (root / "mix.ogg").touch()
            preview = song_preview(
                {
                    "_root": directory,
                    "video": "coach.mp4",
                    "audio": "mix.ogg",
                    "duration": 20,
                    "media_start": 2.5,
                    "video_hidden_until": 6,
                    "preview_start": 3,
                    "preview_duration": 30,
                }
            )

            self.assertTrue(preview["previewVideoUrl"].endswith("/coach.mp4"))
            self.assertTrue(preview["previewAudioUrl"].endswith("/mix.ogg"))
            self.assertEqual(preview["previewStartMs"], 8_500)
            self.assertEqual(preview["previewDurationMs"], 14_000)

    def test_song_preview_defaults_to_an_eight_second_middle_excerpt(self):
        with TemporaryDirectory() as directory:
            audio = Path(directory) / "song.ogg"
            audio.touch()
            preview = song_preview(
                {"_root": directory, "audio": audio.name, "duration": 40}
            )

            self.assertEqual(preview["previewStartMs"], 14_000)
            self.assertEqual(preview["previewDurationMs"], 8_000)

    def test_fullscreen_preference_is_persisted(self):
        backend = Backend.__new__(Backend)
        backend.settings = SimpleNamespace(setValue=Mock())

        backend.rememberFullscreen(True)

        self.assertTrue(backend.fullscreen)
        backend.settings.setValue.assert_called_once_with("ui/fullscreen", True)

    def test_library_uses_two_complete_columns_and_video_thumbnails(self):
        qml = files("opendance").joinpath("qml/Main.qml").read_text()
        preview = files("opendance").joinpath(
            "qml/components/SongPreview.qml"
        ).read_text()

        self.assertIn("GridView {\n                    id: songList", qml)
        self.assertIn("readonly property int rowCount: 2", qml)
        self.assertIn("cellWidth: width / visibleColumns", qml)
        self.assertIn("function moveSongColumn(delta)", qml)
        self.assertIn("function moveSongRow(delta)", qml)
        self.assertIn("function moveVertical(delta)", qml)
        self.assertIn('onActivated: window.moveVertical(-1)', qml)
        self.assertIn('onActivated: window.moveVertical(1)', qml)
        self.assertIn('"previewThumbnailUrl", ""', qml)
        self.assertIn("active: songDelegate.selected", qml)
        self.assertNotIn("property bool playing", preview)

        hud = files("opendance").joinpath("qml/components/PlayerHud.qml").read_text()
        self.assertIn('source: root.value("face", "")', hud)
        self.assertIn("fillMode: Image.PreserveAspectCrop", hud)
        skeleton = files("opendance").joinpath(
            "qml/components/SkeletonView.qml"
        ).read_text()
        self.assertIn("person.cue_arrows", skeleton)

    def test_video_thumbnail_is_extracted_once_then_reused(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            video = root / "dance.mp4"
            video.write_bytes(b"video")

            def extract(command, **_kwargs):
                Path(command[-1]).write_bytes(b"jpeg")

            with (
                patch("opendance.app.default_cache_dir", return_value=root / "cache"),
                patch("opendance.app.shutil.which", return_value="ffmpeg"),
                patch("opendance.app.subprocess.run", side_effect=extract) as run,
            ):
                first = _video_thumbnail(video, 2_500)
                second = _video_thumbnail(video, 2_500)

            self.assertEqual(first, second)
            self.assertTrue(first.endswith(".jpg"))
            run.assert_called_once()

    def test_runtime_supports_six_dynamic_slots(self):
        self.assertEqual(MAX_PLAYERS, 6)

    def test_feedback_is_not_reshown_inside_the_minimum_interval(self):
        self.assertFalse(_feedback_due(3.9, 2.0))
        self.assertTrue(_feedback_due(2.0 + FEEDBACK_INTERVAL_SECONDS, 2.0))

        qml = files("opendance").joinpath("qml/components/PlayerHud.qml").read_text()
        feedback = qml.split("FeedbackBurst {", 1)[1]
        self.assertNotIn("anchors.fill: parent", feedback)
        self.assertIn("anchors.top: parent.bottom", feedback)

    def test_game_can_start_before_a_player_is_detected(self):
        backend = Backend.__new__(Backend)
        backend._presentation_mode = False
        backend._players = []
        backend._catalog = [{"id": "test"}]
        backend._song_index = 0
        backend._max_players = 6
        backend._selected_source = "camera"
        backend._feedback = []
        backend._sleep_inhibitor = SimpleNamespace(acquire=Mock())
        backend.feedbackChanged = SimpleNamespace(emit=Mock())
        backend._result = {}
        backend._prepare_song = Mock()
        backend.changed = SimpleNamespace(emit=Mock())

        with patch("opendance.app.GameSession") as session:
            backend.startGame()

        session.assert_called_once()
        self.assertEqual(backend._screen, "countdown")
        backend._sleep_inhibitor.acquire.assert_called_once_with()

    def test_sleep_inhibitor_uses_one_releasable_linux_process(self):
        process = SimpleNamespace(terminate=Mock())
        inhibitor = SleepInhibitor()

        with (
            patch("opendance.app.sys.platform", "linux"),
            patch("opendance.app.shutil.which", return_value="systemd-inhibit"),
            patch("opendance.app.subprocess.Popen", return_value=process) as popen,
        ):
            inhibitor.acquire()
            inhibitor.acquire()
            inhibitor.release()

        popen.assert_called_once()
        process.terminate.assert_called_once_with()

    def test_wake_resumes_media_at_current_song_time(self):
        player = SimpleNamespace(
            source=lambda: SimpleNamespace(isEmpty=lambda: False),
            setPosition=Mock(),
            play=Mock(),
        )
        backend = Backend.__new__(Backend)
        backend._inactive_at = 10.0
        backend._selected_source = "camera"
        backend._camera_devices = {"camera": object()}
        backend._camera = SimpleNamespace(start=Mock())
        backend._screen = "game"
        backend._paused = False
        backend._media_start_s = 2.5
        backend._song_time = 12.0
        backend._coach_player = player
        backend._music_player = player

        with patch("opendance.app.time.monotonic", return_value=20.0):
            backend.applicationStateChanged(Qt.ApplicationState.ApplicationActive)

        backend._camera.start.assert_called_once_with()
        player.setPosition.assert_called_with(14_500)
        self.assertEqual(backend._media_position_s, 12.0)

    def test_discovered_default_camera_is_activated_without_clicking_it(self):
        device = SimpleNamespace(
            id=lambda: b"usb-camera",
            description=lambda: "USB Camera",
        )
        backend = Backend.__new__(Backend)
        backend.settings = SimpleNamespace(value=lambda *_args, **_kwargs: "")
        backend._camera_devices = {}
        backend._cameras = []
        backend._selected_source = ""
        backend._alternate_sources_enabled = False
        backend._capture_sink = object()
        backend._camera = None
        backend.changed = SimpleNamespace(emit=Mock())
        backend._apply_source = Mock()

        with patch("opendance.app.QMediaDevices.videoInputs", return_value=[device]):
            backend.refreshCameras()

        self.assertEqual(backend._selected_source, b"usb-camera".hex())
        backend._apply_source.assert_called_once_with()

    def test_new_preferred_camera_replaces_an_automatic_fallback(self):
        integrated = SimpleNamespace(
            id=lambda: b"integrated-camera",
            description=lambda: "Integrated Camera",
        )
        usb = SimpleNamespace(
            id=lambda: b"usb-camera",
            description=lambda: "USB Camera",
        )
        backend = Backend.__new__(Backend)
        backend.settings = SimpleNamespace(value=lambda *_args, **_kwargs: "")
        backend._camera_devices = {}
        backend._cameras = []
        backend._selected_source = ""
        backend._alternate_sources_enabled = False
        backend._capture_sink = object()
        backend._camera = None
        backend.changed = SimpleNamespace(emit=Mock())
        backend._apply_source = Mock()

        with patch(
            "opendance.app.QMediaDevices.videoInputs",
            side_effect=[[integrated], [integrated, usb]],
        ):
            backend.refreshCameras()
            backend.refreshCameras()

        self.assertEqual(backend._selected_source, b"usb-camera".hex())
        self.assertEqual(backend._apply_source.call_count, 2)

    def test_manual_camera_choice_survives_a_device_refresh(self):
        integrated_id = b"integrated-camera".hex()
        integrated = SimpleNamespace(
            id=lambda: b"integrated-camera",
            description=lambda: "Integrated Camera",
        )
        usb = SimpleNamespace(
            id=lambda: b"usb-camera",
            description=lambda: "USB Camera",
        )
        values = {}
        backend = Backend.__new__(Backend)
        backend.settings = SimpleNamespace(
            value=lambda key, *_args, **_kwargs: values.get(key, ""),
            setValue=lambda key, value: values.__setitem__(key, value),
        )
        backend._camera_devices = {}
        backend._cameras = []
        backend._selected_source = ""
        backend._alternate_sources_enabled = False
        backend._capture_sink = object()
        backend._camera = None
        backend.changed = SimpleNamespace(emit=Mock())
        backend._apply_source = Mock()

        with patch(
            "opendance.app.QMediaDevices.videoInputs",
            return_value=[integrated, usb],
        ):
            backend.refreshCameras()
            backend.selectSource(integrated_id)
            calls_after_selection = backend._apply_source.call_count
            backend._camera = object()
            backend.refreshCameras()

        self.assertEqual(backend._selected_source, integrated_id)
        self.assertEqual(backend._apply_source.call_count, calls_after_selection)

    def test_media_clock_waits_for_playback_then_interpolates(self):
        self.assertEqual(_interpolated_media_time(0, None, 12, True), 0)
        self.assertEqual(_interpolated_media_time(2.5, 10, 10.2, False), 2.5)
        self.assertAlmostEqual(_interpolated_media_time(2.5, 10, 10.2, True), 2.7)

    def test_finishing_game_flushes_scoring_before_results(self):
        backend = Backend.__new__(Backend)
        backend._screen = "game"
        backend._session = Mock()
        backend._session.results.return_value = {"players": [], "team_stars": 0}
        backend._points = 0
        backend.settings = SimpleNamespace(value=lambda *_args: 0, setValue=Mock())
        backend._catalog = [{"id": "test"}]
        backend._song_index = 0
        backend._play_stinger = Mock()
        backend._coach_player = SimpleNamespace(stop=Mock())
        backend._music_player = SimpleNamespace(stop=Mock())
        backend._sleep_inhibitor = SimpleNamespace(release=Mock())
        backend.changed = SimpleNamespace(emit=Mock())

        backend._finish_game()

        backend._session.finish.assert_called_once_with()
        backend._session.results.assert_called_once_with()

    def test_trimmed_media_seeks_and_reports_song_relative_time(self):
        coach = SimpleNamespace(setPosition=Mock(), play=Mock())
        music = SimpleNamespace(setPosition=Mock(), play=Mock())
        backend = Backend.__new__(Backend)
        backend._coach_player = coach
        backend._music_player = music
        backend._clock_player = coach
        backend._media_start_s = 2.5
        backend._media_position_s = 0.0
        backend._media_position_at = None
        backend._screen = "game"

        backend._start_song_media()
        with patch("opendance.app.time.monotonic", return_value=10.0):
            backend._sync_media_position(coach, 4_000)

        coach.setPosition.assert_called_once_with(2_500)
        music.setPosition.assert_called_once_with(2_500)
        coach.play.assert_called_once_with()
        music.play.assert_called_once_with()
        self.assertEqual(backend._media_position_s, 1.5)
        self.assertEqual(backend._media_position_at, 10.0)

        coach.setPosition.reset_mock()
        backend._coach_seek_pending = True
        backend._song_time = 0.0
        backend._coach_status(QMediaPlayer.MediaStatus.LoadedMedia)
        coach.setPosition.assert_called_once_with(2_500)
        self.assertFalse(backend._coach_seek_pending)

    def test_failed_video_falls_back_to_monotonic_choreography_clock(self):
        with TemporaryDirectory() as directory:
            video = Path(directory) / "broken.mp4"
            video.touch()
            song = {
                "id": "broken-video",
                "title": "Broken Video",
                "duration": 5,
                "video": video.name,
                "_root": directory,
                "moves": [{"time": 0, "pose": named_pose("ready")}],
            }
            backend = Backend.__new__(Backend)
            backend._catalog = [song]
            backend._song_index = 0
            backend._loaded_song_index = 0
            backend._loaded_song = song
            backend._presentation_mode = True
            backend._presentation_video = True
            backend._presentation_finished = False
            backend._coach_video_failed = False
            backend._separate_audio = False
            backend._coach_player = object()
            backend._clock_player = backend._coach_player
            backend._has_song_media = True
            backend._media_position_at = 1.0
            backend._song_time = 2.0
            backend._screen = "game"
            backend._paused = False
            backend.changed = type("Signal", (), {"emit": lambda _self: None})()

            with patch("opendance.app.time.monotonic", return_value=10.0):
                backend._coach_error(None, "unsupported codec")

            self.assertFalse(backend._has_song_media)
            self.assertAlmostEqual(10.5 - backend._play_started_at, 2.5)

    def test_windowed_entrypoint_supplies_console_streams(self):
        ensure = runpy.run_path("packaging/entrypoint.py")["_ensure_console_streams"]
        original_stdout, original_stderr = sys.stdout, sys.stderr
        replacements = []
        try:
            sys.stdout = sys.stderr = None
            ensure()
            replacements = [sys.stdout, sys.stderr]
            self.assertTrue(all(stream is not None for stream in replacements))
        finally:
            sys.stdout, sys.stderr = original_stdout, original_stderr
            for stream in replacements:
                stream.close()

    def test_bundled_audio_is_compressed_and_present(self):
        package = files("opendance")
        audio = [song["audio"] for song in load_catalog()]
        audio += [f"assets/audio/stingers/{name}.ogg" for name in (
            "perfect", "great", "good", "miss", "star", "unlock"
        )]
        self.assertTrue(all(path.endswith(".ogg") for path in audio))
        self.assertTrue(all(package.joinpath(path).is_file() for path in audio))
        self.assertFalse(list(Path(str(package)).rglob("*.wav")))

    def test_qml_song_metadata_does_not_copy_dense_choreography(self):
        backend = Backend.__new__(Backend)
        backend._points = 0
        backend.settings = type(
            "Settings", (), {"value": lambda *_args, **_kwargs: 0}
        )()
        visible = backend._song_for_ui(
            {
                "id": "dense",
                "title": "Dense",
                "duration": 1,
                "moves": [1],
                "lyrics": [2],
                "choreography": {"timeline": [3]},
            }
        )
        self.assertNotIn("choreography", visible)
        self.assertNotIn("moves", visible)
        self.assertNotIn("lyrics", visible)

    def test_dancer_marker_is_one_feathered_slowly_settling_rectangle(self):
        qml = files("opendance.qml").joinpath("Main.qml").read_text(encoding="utf-8")
        marker = qml.split("id: dancerAssignmentMarkers", 1)[1].split(
            "StackLayout {", 1
        )[0]

        self.assertEqual(marker.count("Canvas {"), 1)
        self.assertNotIn("Rectangle {", marker)
        self.assertNotIn("SmoothedAnimation", marker)
        self.assertIn("for (var spread = 10; spread >= 0; --spread)", marker)
        self.assertIn(">= 4 * window.uiScale", marker)
        self.assertIn(">= 7 * window.uiScale", marker)
        self.assertIn("> 30 * window.uiScale ? 700 : 10000", marker)
        self.assertIn("> 24 * window.uiScale ? 850 : 12000", marker)
        self.assertIn("leftFoot", marker)
        self.assertIn("rightFoot", marker)
        self.assertIn("targetWidth", marker)
        self.assertIn("filteredWidth", marker)
        self.assertIn("- 40 * window.uiScale", marker)
        self.assertNotIn('text: "D" +', marker)

        player_ui = "\n".join(
            files("opendance.qml").joinpath(name).read_text(encoding="utf-8")
            for name in (
                "Main.qml",
                "components/PlayerHud.qml",
                "components/FeedbackBurst.qml",
                "components/SkeletonView.qml",
            )
        )
        self.assertNotIn('text: "P" +', player_ui)
        self.assertNotIn("→D", player_ui)

    def test_library_reads_metadata_before_dense_choreography(self):
        with TemporaryDirectory() as directory:
            manifest = Path(directory) / "song.json"
            manifest.write_text(
                json.dumps(
                    {
                        "id": "lazy",
                        "title": "Lazy",
                        "duration": 1,
                        "video": "clip.mp4",
                        "choreography": {"timeline": [{"people": [1, 2, 3]}]},
                        "extraction": {"elapsed_seconds": 1},
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )
            metadata = song_manifest_metadata(manifest)
            self.assertEqual(metadata["video"], "clip.mp4")
            self.assertNotIn("choreography", metadata)
            self.assertEqual(metadata["_manifest"], str(manifest.resolve()))

    def test_extracted_song_player_resolves_package_media_and_pose(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "song.ogg").touch()
            manifest = root / "song.json"
            manifest.write_text(
                json.dumps(
                    {
                        "id": "player-test",
                        "title": "Player Test",
                        "duration": 1,
                        "audio": "song.ogg",
                        "moves": [{"time": 0, "pose": named_pose("ready")}],
                    }
                ),
                encoding="utf-8",
            )

            song = load_player_song(root)

            self.assertEqual(song["_root"], str(root.resolve()))
            self.assertEqual(song_media_path(song, "audio"), (root / "song.ogg").resolve())

    def test_extracted_song_player_rejects_missing_media(self):
        with TemporaryDirectory() as directory:
            manifest = Path(directory) / "song.json"
            manifest.write_text(
                json.dumps(
                    {
                        "id": "silent-test",
                        "title": "Silent Test",
                        "duration": 1,
                        "moves": [{"time": 0, "pose": named_pose("ready")}],
                    }
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "no playable audio or video"):
                load_player_song(manifest)


if __name__ == "__main__":
    unittest.main()
