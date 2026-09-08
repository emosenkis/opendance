from importlib.resources import files
import json
from pathlib import Path
import runpy
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from opendance.app import (
    Backend,
    MAX_PLAYERS,
    _enabled,
    _interpolated_media_time,
    song_manifest_metadata,
    song_media_path,
)
from opendance.game import load_catalog
from opendance.game import named_pose
from opendance.player import load_player_song


class PlaybackPolicyTest(unittest.TestCase):
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

    def test_runtime_always_has_four_dynamic_slots(self):
        self.assertEqual(MAX_PLAYERS, 4)

    def test_media_clock_waits_for_playback_then_interpolates(self):
        self.assertEqual(_interpolated_media_time(0, None, 12, True), 0)
        self.assertEqual(_interpolated_media_time(2.5, 10, 10.2, False), 2.5)
        self.assertAlmostEqual(_interpolated_media_time(2.5, 10, 10.2, True), 2.7)

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
