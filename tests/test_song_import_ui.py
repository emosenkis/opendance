import threading
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import Mock, patch

from opendance.app import Backend
from opendance.importer import ImportOptions


def backend_stub() -> Backend:
    backend = Backend.__new__(Backend)
    backend._song_import = {}
    backend._song_import_busy = False
    backend._song_import_status = ""
    backend._song_import_progress = -1.0
    backend._song_import_worker = None
    backend._song_import_cancel = threading.Event()
    backend._import_capture_paused = False
    backend.songImportChanged = SimpleNamespace(emit=Mock())
    backend.changed = SimpleNamespace(emit=Mock())
    backend._import_prepared = SimpleNamespace(emit=Mock())
    backend._import_completed = SimpleNamespace(emit=Mock())
    backend._import_failed = SimpleNamespace(emit=Mock())
    backend._import_progress = SimpleNamespace(emit=Mock())
    backend._apply_source = Mock()
    return backend


class ImmediateThread:
    def __init__(self, *, target, **_kwargs):
        self.target = target

    def start(self):
        self.target()


class SongImportBridgeTests(unittest.TestCase):
    def test_prepared_and_failed_handlers_publish_ui_state_and_resume_capture(self):
        backend = backend_stub()
        details = {
            "source": "/tmp/video.mp4",
            "title": "Video Title",
            "artist": "Video Artist",
            "duration": 42.0,
        }

        backend._song_import_busy = True
        backend._on_import_prepared(details)

        self.assertEqual(backend._song_import, details)
        self.assertFalse(backend._song_import_busy)
        self.assertEqual(
            backend._song_import_status, "Ready to extract choreography."
        )

        backend._song_import_busy = True
        backend._import_capture_paused = True
        backend._on_import_failed("decoder stopped")

        self.assertFalse(backend._song_import_busy)
        self.assertEqual(backend._song_import_status, "Error: decoder stopped")
        self.assertFalse(backend._import_capture_paused)
        backend._apply_source.assert_called_once_with()
        self.assertEqual(backend.songImportChanged.emit.call_count, 2)

    def test_frame_progress_reports_percent_speed_and_eta(self):
        backend = backend_stub()

        backend._on_import_progress((250, 1_000, 25.0))

        self.assertEqual(backend._song_import_progress, 0.25)
        self.assertIn("25%", backend._song_import_status)
        self.assertIn("250/1,000 frames", backend._song_import_status)
        self.assertIn("25.0 fps", backend._song_import_status)
        self.assertIn("30 sec left", backend._song_import_status)

        backend._on_import_progress((1_000, 1_000, 25.0))
        self.assertEqual(backend._song_import_progress, 1.0)
        self.assertIn("Building stable dancer roles", backend._song_import_status)

    def test_completed_handler_refreshes_and_selects_imported_catalog_entry(self):
        with TemporaryDirectory() as directory:
            manifest = Path(directory) / "new-song/song.json"
            backend = backend_stub()
            backend._song_import = {"title": "New Song"}
            backend._song_import_busy = True
            backend._import_capture_paused = True
            backend._song_index = 0
            backend._loaded_song_index = 0
            backend._loaded_song = {"id": "old"}
            backend._load_songs = Mock(
                return_value=[
                    {"id": "old"},
                    {"id": "new", "_manifest": str(manifest)},
                ]
            )

            backend._on_import_completed(str(manifest))

        backend._load_songs.assert_called_once_with()
        self.assertEqual(backend._song_index, 1)
        self.assertEqual(backend._loaded_song_index, -1)
        self.assertIsNone(backend._loaded_song)
        self.assertEqual(backend._song_import, {})
        self.assertFalse(backend._song_import_busy)
        self.assertEqual(
            backend._song_import_status, "Added New Song to the song library."
        )
        backend._apply_source.assert_called_once_with()
        backend.songImportChanged.emit.assert_called_once_with()
        backend.changed.emit.assert_called_once_with()

    def test_start_forwards_only_user_options_and_reuses_stopped_pose_engine(self):
        backend = backend_stub()
        engine = SimpleNamespace(max_people=6)
        pose_thread = SimpleNamespace(engine=engine, close=Mock(), join=Mock())
        backend._pose_thread = pose_thread
        backend._stop_source = Mock()
        backend._max_players = 6
        backend._pose_backend = "yolo26"
        backend._rtmpose_mode = "lightweight"
        backend._catalog = [{"id": "already-there"}]
        backend._song_import = {
            "source": "/tmp/dance.mp4",
            "title": "Embedded title",
            "artist": "Embedded artist",
            "duration": 90.0,
            "lyrics": "/tmp/dance.lrc",
        }
        manifest = Path("/tmp/library/song/song.json")

        with (
            patch("opendance.app.threading.Thread", ImmediateThread),
            patch("opendance.app.library_path", return_value=Path("/tmp/library")),
            patch(
                "opendance.app.extract_imported_song", return_value=manifest
            ) as extract,
        ):
            backend.startSongImport(
                {
                    "title": "Edited title",
                    "artist": "Edited artist",
                    "dancer_count": 6,
                    "trim_start": 1.25,
                    "trim_end": 2.5,
                    "hide_video_intro": 3.75,
                    "copy_video": False,
                }
            )

        pose_thread.close.assert_called_once_with()
        pose_thread.join.assert_called_once_with()
        backend._stop_source.assert_called_once_with()
        self.assertIsNone(backend._pose_thread)
        self.assertTrue(backend._import_capture_paused)
        self.assertTrue(backend._song_import_busy)
        self.assertIn("Extracting choreography", backend._song_import_status)
        args, kwargs = extract.call_args
        self.assertEqual(args, ("/tmp/dance.mp4", Path("/tmp/library")))
        self.assertIs(kwargs["engine"], engine)
        self.assertEqual(
            kwargs["options"],
            ImportOptions(
                title="Edited title",
                artist="Edited artist",
                dancer_count=6,
                trim_start=1.25,
                trim_end=2.5,
                hide_video_intro=3.75,
                copy_video=False,
                lrc="/tmp/dance.lrc",
            ),
        )
        self.assertEqual(kwargs["metadata"], backend._song_import)
        self.assertIs(kwargs["cancel_event"], backend._song_import_cancel)
        self.assertEqual(kwargs["existing_ids"], {"already-there"})
        kwargs["progress_callback"](12, 40, 20.0)
        backend._import_progress.emit.assert_called_once_with((12, 40, 20.0))
        backend._import_completed.emit.assert_called_once_with(str(manifest))
        backend._import_failed.emit.assert_not_called()


if __name__ == "__main__":
    unittest.main()
