import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from opendance.extract import ROLE_ASSIGNMENT_METHOD
from opendance.game import named_pose
from tools.reprocess_songs import refresh_manifest


class ReprocessSongsTest(unittest.TestCase):
    def test_role_shuffle_is_backed_up_refreshed_and_then_current(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "song.json"
            positions = (0.15, 0.5, 0.85)
            track_ids = (1, 2, 3)
            timeline = []
            for index in range(13):
                if index:
                    track_ids = (3, 1, 2)
                people = []
                for dancer_index, (track_id, x) in enumerate(zip(track_ids, positions)):
                    people.append(
                        {
                            "dancer_index": dancer_index,
                            "track_id": track_id,
                            "bbox": [x - 0.05, 0.1, 0.1, 0.8],
                            "confidence": 0.95,
                            "keypoints": named_pose("ready" if index < 4 else "star"),
                        }
                    )
                timeline.append(
                    {
                        "timestamp_ms": index * 250,
                        "scene_cut": False,
                        "people": people,
                    }
                )
            song = {
                "choreography": {
                    "role_assignment": "track_id_then_pose_position",
                    "dancer_track_ids": [1, 2, 3],
                    "dancers": [
                        {"index": index, "track_id": track_id, "seed_track_id": None}
                        for index, track_id in enumerate((1, 2, 3))
                    ],
                    "lead_track_id": 2,
                    "lead_dancer_index": 1,
                    "timeline": timeline,
                }
            }
            path.write_text(json.dumps(song), encoding="utf-8")
            original = json.loads(path.read_text())

            self.assertTrue(refresh_manifest(path))
            self.assertEqual(
                json.loads(path.with_name("song.json.bak").read_text()), original
            )
            refreshed = json.loads(path.read_text())
            choreography = refreshed["choreography"]
            self.assertEqual(choreography["role_assignment"], ROLE_ASSIGNMENT_METHOD)
            self.assertEqual(
                [
                    {person["track_id"]: person["dancer_index"] for person in frame["people"]}
                    for frame in choreography["timeline"][:2]
                ],
                [{1: 0, 2: 1, 3: 2}, {3: 0, 1: 1, 2: 2}],
            )
            self.assertEqual(choreography["dancer_track_ids"], [3, 1, 2])
            self.assertEqual(choreography["lead_dancer_index"], 1)
            self.assertEqual(choreography["lead_track_id"], 1)
            self.assertTrue(choreography["move_scoring"]["segments"])
            first_refresh = path.read_bytes()
            self.assertFalse(refresh_manifest(path))
            self.assertEqual(path.read_bytes(), first_refresh)

    def test_legacy_single_dancer_timeline_only_needs_scoring(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "song.json"
            (path.parent / "video.mp4").write_bytes(b"video")
            song = {
                "video": "video.mp4",
                "media_start": 0.25,
                "bpm": None,
                "choreography": {
                    "dancers": [{"index": 0, "track_id": 7}],
                    "timeline": [
                        {
                            "timestamp_ms": index * 250,
                            "people": [
                                {
                                    "dancer_index": 0,
                                    "keypoints": named_pose(
                                        "ready" if index < 4 else "star"
                                    ),
                                }
                            ],
                        }
                        for index in range(9)
                    ],
                }
            }
            path.write_text(json.dumps(song), encoding="utf-8")
            original = json.loads(path.read_text())

            with patch(
                "tools.reprocess_songs._detect_beat_grid",
                return_value={
                    "bpm": 120.0,
                    "beat_offset": 0.1,
                    "beat_confidence": 0.8,
                },
            ):
                self.assertTrue(refresh_manifest(path))

            refreshed = json.loads(path.read_text())
            choreography = refreshed["choreography"]
            self.assertEqual(refreshed["bpm"], 120.0)
            self.assertEqual(refreshed["beat_offset"], 0.35)
            self.assertEqual(
                choreography["timeline"], original["choreography"]["timeline"]
            )
            self.assertEqual(
                choreography["dancers"], original["choreography"]["dancers"]
            )
            self.assertEqual(choreography["role_assignment"], ROLE_ASSIGNMENT_METHOD)
            self.assertTrue(choreography["move_scoring"]["segments"])


if __name__ == "__main__":
    unittest.main()
