import json
import os
import subprocess
import sys
import threading
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

from opendance.importer import (
    DEFAULT_CONFIG_PATH,
    ImportOptions,
    config_path,
    download_url,
    extract_imported_song,
    library_path,
    load_url_helpers,
    probe_video_metadata,
)


class ImporterTests(unittest.TestCase):
    def test_config_uses_xdg_and_requires_argv(self):
        with TemporaryDirectory() as directory, patch.dict(
            os.environ, {"XDG_CONFIG_HOME": directory}
        ):
            self.assertEqual(config_path(), Path(directory) / "opendance/config.toml")
            config = Path(directory) / "bad.toml"
            config.write_text(
                '[[url_helpers]]\ndomains = ["video.example"]\ncommand = "unsafe --shell"\n',
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "argv array"):
                load_url_helpers(config)

    def test_import_library_uses_the_configured_or_xdg_data_directory(self):
        with TemporaryDirectory() as directory, patch.dict(
            os.environ,
            {"XDG_DATA_HOME": directory},
        ):
            with patch.dict(os.environ, {"OPENDANCE_LIBRARY": ""}):
                self.assertEqual(
                    library_path(), Path(directory) / "opendance/songs"
                )
            with patch.dict(os.environ, {"OPENDANCE_LIBRARY": "~/my-dances"}):
                self.assertEqual(library_path(), Path("~/my-dances").expanduser())

    def test_url_helper_matches_domain_boundary_and_returns_file(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            downloaded = root / "download with spaces.mp4"
            downloaded.touch()
            config = root / "config.toml"
            config.write_text(
                """[[url_helpers]]
domains = ["example.com"]
command = ["broad-helper"]

[[url_helpers]]
domains = ["video.example.com"]
command = ["specific-helper", "--safe"]
""",
                encoding="utf-8",
            )
            completed = subprocess.CompletedProcess(
                [], 0, stdout=f"{downloaded}\n", stderr=""
            )
            with patch(
                "opendance.importer.subprocess.run", return_value=completed
            ) as run:
                result = download_url("https://cdn.video.example.com/watch?v=1", config)

            self.assertEqual(result, downloaded.resolve())
            self.assertEqual(run.call_args.args[0], ("specific-helper", "--safe"))
            self.assertEqual(
                run.call_args.kwargs["env"]["URL"],
                "https://cdn.video.example.com/watch?v=1",
            )
            self.assertEqual(
                run.call_args.kwargs["env"].get("PATH"), os.environ.get("PATH")
            )
            with patch.dict(os.environ, {"OPENDANCE_LIBRARY": directory}), patch(
                "opendance.importer.subprocess.run", return_value=completed
            ) as fallback:
                download_url("https://notexample.com/watch", config)
            command = fallback.call_args.args[0]
            self.assertEqual(command[0], "yt-dlp")
            self.assertIn("https://notexample.com/watch", command)
            self.assertIn(str(root / ".downloads"), command)

    def test_default_config_uses_yt_dlp_as_a_catch_all(self):
        self.assertTrue(DEFAULT_CONFIG_PATH.is_file())
        with TemporaryDirectory() as directory, patch.dict(
            os.environ, {"OPENDANCE_LIBRARY": directory}
        ):
            helpers = load_url_helpers(Path(directory) / "missing.toml")
            self.assertEqual(helpers[-1][0], ("*",))
            downloaded = Path(directory) / "clip.mp4"
            downloaded.touch()
            completed = subprocess.CompletedProcess(
                [], 0, stdout=f"{downloaded}\n", stderr=""
            )
            with patch(
                "opendance.importer.subprocess.run", return_value=completed
            ) as run:
                self.assertEqual(
                    download_url(
                        "https://a-site-supported-by-ytdlp.example/watch",
                        Path(directory) / "missing.toml",
                    ),
                    downloaded.resolve(),
                )
            self.assertEqual(
                run.call_args.args[0],
                (
                    "yt-dlp",
                    "--no-playlist",
                    "--no-progress",
                    "--paths",
                    str(Path(directory) / ".downloads"),
                    "--print",
                    "after_move:filepath",
                    "--",
                    "https://a-site-supported-by-ytdlp.example/watch",
                ),
            )

    def test_url_helper_rejects_unsafe_url_and_bad_process_output(self):
        with TemporaryDirectory() as directory:
            config = Path(directory) / "config.toml"
            config.write_text(
                '[[url_helpers]]\ndomains = ["example.com"]\ncommand = ["helper"]\n',
                encoding="utf-8",
            )
            for url in (
                "file:///tmp/video.mp4",
                "https://user:secret@example.com/video",
                " https://example.com/video",
                "https://example.com/video\nBAD=1",
            ):
                with self.subTest(url=url), self.assertRaises(ValueError):
                    download_url(url, config)
            completed = subprocess.CompletedProcess(
                [], 0, stdout="log\n/path.mp4\n", stderr=""
            )
            with patch(
                "opendance.importer.subprocess.run", return_value=completed
            ):
                with self.assertRaisesRegex(RuntimeError, "exactly one"):
                    download_url("https://example.com/video", config)

    def test_url_helper_can_be_cancelled_during_shutdown(self):
        with TemporaryDirectory() as directory:
            config = Path(directory) / "config.toml"
            config.write_text(
                "[[url_helpers]]\n"
                'domains = ["example.com"]\n'
                f"command = {json.dumps([sys.executable, '-c', 'import time; time.sleep(5)'])}\n",
                encoding="utf-8",
            )
            cancelled = threading.Event()
            cancelled.set()
            with patch("opendance.importer.subprocess.Popen") as popen:
                with self.assertRaisesRegex(InterruptedError, "cancelled"):
                    download_url(
                        "https://example.com/video", config, cancel_event=cancelled
                    )
            popen.assert_not_called()

    def test_probe_reads_embedded_metadata_and_falls_back_without_ffprobe(self):
        with TemporaryDirectory() as directory:
            video = Path(directory) / "file-name.mp4"
            video.touch()
            payload = {
                "format": {
                    "duration": "12.75",
                    "tags": {"TITLE": " Embedded Title ", "album_artist": "Artist"},
                }
            }
            completed = subprocess.CompletedProcess([], 0, stdout=json.dumps(payload), stderr="")
            with patch("opendance.importer.subprocess.run", return_value=completed):
                self.assertEqual(
                    probe_video_metadata(video),
                    {"title": "Embedded Title", "artist": "Artist", "duration": 12.75},
                )
            with patch("opendance.importer._qt_video_metadata", return_value={}):
                self.assertEqual(
                    probe_video_metadata(video, ffprobe=None),
                    {
                        "title": "file-name",
                        "artist": "Unknown Artist",
                        "duration": None,
                    },
                )
            named = Path(directory) / "File Artist - File Title.mp4"
            named.touch()
            with patch(
                "opendance.importer._qt_video_metadata",
                return_value={"title": "Qt Title", "duration": 9.5},
            ):
                self.assertEqual(
                    probe_video_metadata(named, ffprobe=None),
                    {"title": "Qt Title", "artist": "File Artist", "duration": 9.5},
                )

    def test_extraction_wrapper_validates_user_options_and_avoids_overwrite(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            video = root / "clip.mp4"
            video.touch()
            (root / "library/artist-title").mkdir(parents=True)
            engine = SimpleNamespace(max_people=6)
            options = ImportOptions(
                dancer_count=6,
                trim_start=1,
                trim_end=2,
                hide_video_intro=3,
                copy_video=False,
            )

            def succeed(_video, destination, **_options):
                manifest = destination / "song.json"
                manifest.touch()
                return manifest

            with patch(
                "opendance.importer.extract_song", side_effect=succeed
            ) as extract:
                result = extract_imported_song(
                    video,
                    root / "library",
                    engine=engine,
                    options=options,
                    metadata={"title": "Title", "artist": "Artist", "duration": 12},
                    existing_ids={"artist-title-2"},
                )
            self.assertEqual(result, root / "library/artist-title-3/song.json")
            self.assertEqual(
                extract.call_args.args[:2],
                (video, root / "library/artist-title-3"),
            )
            self.assertEqual(extract.call_args.kwargs["song_id"], "artist-title-3")
            self.assertEqual(extract.call_args.kwargs["dancer_count"], 6)
            self.assertFalse(extract.call_args.kwargs["copy_video"])
            self.assertEqual(extract.call_args.kwargs["trim_start"], 1)
            with self.assertRaisesRegex(ValueError, "between 1 and 6"):
                extract_imported_song(
                    video,
                    root / "library",
                    engine=engine,
                    options=ImportOptions(dancer_count=7),
                    metadata={"title": "Title", "artist": "Artist"},
                )
            with self.assertRaisesRegex(ValueError, "remove the entire"):
                extract_imported_song(
                    video,
                    root / "library",
                    engine=engine,
                    options=ImportOptions(trim_start=6, trim_end=6),
                    metadata={"title": "Title", "artist": "Artist", "duration": 12},
                )

            def fail_after_creating(_video, destination, **_options):
                (destination / "partial.mp4").touch()
                raise RuntimeError("decoder failed")

            with patch(
                "opendance.importer.extract_song", side_effect=fail_after_creating
            ), self.assertRaisesRegex(RuntimeError, "decoder failed"):
                extract_imported_song(
                    video,
                    root / "library",
                    engine=engine,
                    metadata={"title": "Title", "artist": "Artist"},
                )
            self.assertFalse((root / "library/artist-title-2").exists())


if __name__ == "__main__":
    unittest.main()
