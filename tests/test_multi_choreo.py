import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from opendance.extract import extract_song
from opendance.game import (
    GameSession,
    assign_dancers,
    named_pose,
    pose_similarity,
    target_dancers,
    target_pose,
    target_poses,
)


def shifted(name, dx):
    return [(x + dx, y, confidence) for x, y, confidence in named_pose(name)]


def person(track_id, name, dx, bbox):
    return {
        "track_id": track_id,
        "confidence": 0.9,
        "bbox": bbox,
        "keypoints": shifted(name, dx),
    }


class MultiDancerTest(unittest.TestCase):
    def test_balanced_position_assignment(self):
        dancers = [0.2, 0.8]
        self.assertEqual(assign_dancers({7: 0.75}, dancers), {7: 1})
        self.assertEqual(assign_dancers({7: 0.1, 8: 0.9}, dancers), {7: 0, 8: 1})
        three = assign_dancers({7: 0.1, 8: 0.3, 9: 0.9}, dancers)
        self.assertEqual(sorted(three.values()), [0, 0, 1])
        four = assign_dancers({7: 0.1, 8: 0.3, 9: 0.7, 10: 0.9}, dancers)
        self.assertEqual(sorted(four.values()), [0, 0, 1, 1])
        one_each = assign_dancers(
            {7: 0.05, 8: 0.35, 9: 0.65, 10: 0.95},
            [0.05, 0.35, 0.65, 0.95],
        )
        self.assertEqual(one_each, {7: 0, 8: 1, 9: 2, 10: 3})

    def test_runtime_targets_and_scores_each_assigned_dancer(self):
        song = {
            "id": "duet",
            "title": "Duet",
            "duration": 2,
            "moves": [],
            "choreography": {
                "lead_track_id": 20,
                "dancer_track_ids": [10, 20],
                "timeline": [
                    {
                        "timestamp_ms": timestamp,
                        "render_order": [1, 0],
                        "depth_order": [0, 1],
                        "people": [
                            person(10, "ready", -0.3, [0.1, 0.1, 0.2, 0.8])
                            | {"dancer_index": 0},
                            person(20, "star", 0.3, [0.7, 0.1, 0.2, 0.8])
                            | {"dancer_index": 1},
                        ],
                    }
                    for timestamp in (0, 1000)
                ],
            },
        }
        self.assertGreater(pose_similarity(target_pose(song, 0), named_pose("star")), 0.99)
        self.assertGreater(
            pose_similarity(target_pose(song, 0, 0), named_pose("ready")), 0.99
        )
        self.assertEqual(len(target_poses(song, 0)), 2)
        rendered = target_dancers(song, 0.5)
        self.assertEqual([dancer["dancer_index"] for dancer in rendered], [1, 0])
        self.assertEqual(rendered[-1]["depth_rank"], 0)
        self.assertEqual(rendered[-1]["track_id"], 10)

        session = GameSession(song, max_players=2, mirror_player_positions=False)
        feedback = session.update(
            0,
            {7: shifted("ready", -0.3), 8: shifted("star", 0.3)},
        )
        self.assertEqual(session.dancer_assignments, {0: 0, 1: 1})
        self.assertEqual([item.grade for item in feedback], ["PERFECT", "PERFECT"])
        self.assertEqual(
            [player["dancer_index"] for player in session.ui_players()], [0, 1]
        )

    def test_extractor_selects_dancers_and_records_depth_order(self):
        frames = [
            {
                "frame": 0,
                "timestamp_ms": 0.0,
                "people": [
                    person(11, "ready", -0.3, [0.1, 0.2, 0.2, 0.4]),
                    person(22, "star", 0.3, [0.6, 0.1, 0.3, 0.8]),
                    person(99, "clap", 0.0, [0.45, 0.3, 0.1, 0.2]),
                ],
                "timing": {},
            },
            {
                "frame": 1,
                "timestamp_ms": 1000.0,
                "people": [
                    person(11, "ready", 0.25, [0.6, 0.1, 0.3, 0.8]),
                    person(22, "star", -0.3, [0.1, 0.2, 0.2, 0.4]),
                ],
                "timing": {},
            },
            {
                "frame": 2,
                "timestamp_ms": 1040.0,
                "people": [
                    person(33, "ready", 0.24, [0.59, 0.1, 0.3, 0.8]),
                    person(44, "star", -0.29, [0.11, 0.2, 0.2, 0.4]),
                ],
                "timing": {},
            },
            {
                "frame": 3,
                "timestamp_ms": 1080.0,
                "scene_cut": True,
                "people": [
                    person(55, "star", -0.3, [0.1, 0.2, 0.2, 0.4]),
                    person(66, "ready", 0.25, [0.6, 0.1, 0.3, 0.8]),
                ],
                "timing": {},
            },
        ]
        analysis = {
            "source": {
                "path": "/video.mp4",
                "width": 1280,
                "height": 720,
                "fps": 30.0,
                "reported_frame_count": 4,
                "decoded_frame_count": 4,
                "duration_ms": 1120.0,
            },
            "lead_track_id": 22,
            "track_ids": [11, 22, 99],
            "frames": frames,
            "processing": {"elapsed_seconds": 1.0, "average_fps": 2.0},
        }
        engine = type(
            "Engine",
            (),
            {
                "model_name": "pose.pt",
                "imgsz": 640,
                "device": None,
                "max_people": 4,
            },
        )()
        with TemporaryDirectory() as directory:
            source = Path(directory) / "video.mp4"
            source.touch()
            with patch("opendance.extract.analyze_video", return_value=analysis):
                output = extract_song(
                    source,
                    Path(directory) / "song",
                    engine=engine,
                    dancer_count=2,
                    dancer_track_ids=[22, 11],
                    show_progress=False,
                )
            choreography = json.loads(output.read_text())["choreography"]

        self.assertEqual(choreography["dancer_track_ids"], [11, 22])
        self.assertEqual(
            [
                {person["track_id"]: person["dancer_index"] for person in frame["people"]}
                for frame in choreography["timeline"]
            ],
            [
                {11: 0, 22: 1},
                {11: 0, 22: 1},
                {33: 0, 44: 1},
                {55: 0, 66: 1},
            ],
        )
        self.assertEqual(choreography["timeline"][0]["depth_order"], [1, 0])
        self.assertEqual(choreography["timeline"][0]["render_order"], [0, 1])
        self.assertTrue(choreography["timeline"][3]["scene_cut"])
        self.assertIn(33, choreography["dancers"][0]["track_ids"])
        self.assertIn(44, choreography["dancers"][1]["track_ids"])


if __name__ == "__main__":
    unittest.main()
