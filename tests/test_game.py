import math
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from types import ModuleType
from unittest.mock import patch

from opendance.game import (
    GameSession,
    PlayerSlots,
    load_catalog,
    named_pose,
    pose_similarity,
    stars_for_accuracy,
    target_pose,
)
from opendance.vision import PoseEngine
from opendance.extract import parse_lrc


def shifted(name, dx=0.0):
    return [(x + dx, y, confidence) for x, y, confidence in named_pose(name)]


class GameCoreTest(unittest.TestCase):
    def test_six_player_slots_are_supported(self):
        self.assertEqual(len(PlayerSlots().slots), 6)

    def test_session_does_not_duplicate_dense_choreography(self):
        choreography = {
            "timeline": [
                {"time": 0, "keypoints": named_pose("ready")},
            ]
        }
        session = GameSession(
            {"id": "dense", "duration": 1, "choreography": choreography}
        )
        self.assertIs(session.song["choreography"], choreography)

    def test_vision_disables_dependency_analytics_before_model_load(self):
        with patch.dict(os.environ, {"OPENDANCE_CACHE": "/tmp/opendance-test"}):
            os.environ.pop("OPENDANCE_MODEL", None)
            model_path = Path(PoseEngine().model_name)
        self.assertEqual((model_path.parent.name, model_path.name), ("models", "yolo26n-pose.pt"))

        ultralytics = ModuleType("ultralytics")
        ultralytics.__path__ = []
        ultralytics.settings = {"sync": True}
        seen = []
        ultralytics.YOLO = lambda _model: seen.append(ultralytics.settings["sync"])
        utils = ModuleType("ultralytics.utils")
        utils.__path__ = []
        events_module = ModuleType("ultralytics.utils.events")
        events = type("Events", (), {"enabled": True, "events": ["queued"]})()
        events_module.events = events
        modules = {
            "ultralytics": ultralytics,
            "ultralytics.utils": utils,
            "ultralytics.utils.events": events_module,
        }
        with patch.dict("sys.modules", modules):
            PoseEngine("pose.pt").load()
        self.assertEqual(seen, [False])
        self.assertFalse(events.enabled)
        self.assertEqual(events.events, [])

    def test_pose_matching_ignores_position_scale_and_mirror(self):
        target = named_pose("disco_left")
        self.assertTrue(all(0 <= axis <= 1 for point in target for axis in point[:2]))
        transformed = [(x * 1.8 + 3, y * 1.8 - 2, confidence) for x, y, confidence in target]
        mirrored = [(1 - x, y, confidence) for x, y, confidence in target]
        self.assertGreater(pose_similarity(transformed, target), 0.99)
        self.assertGreater(pose_similarity(mirrored, target), 0.99)
        self.assertLess(pose_similarity(named_pose("squat"), target), 0.8)

    def test_authored_dance_interpolation_uses_projected_3d_limbs(self):
        song = {
            "moves": [
                {"time": 0, "name": "ready"},
                {"time": 2, "name": "clap"},
                {"time": 4, "name": "star"},
            ]
        }
        bones = (
            (5, 7, 0.21), (7, 9, 0.21), (6, 8, 0.21), (8, 10, 0.21),
            (11, 13, 0.245), (13, 15, 0.245),
            (12, 14, 0.245), (14, 16, 0.245),
        )
        previous = None
        projected_lengths = []
        for step in range(81):
            pose = target_pose(song, step / 20)
            self.assertTrue(
                all(math.isfinite(value) for point in pose for value in point)
            )
            self.assertTrue(
                all(-0.02 <= value <= 1.02 for point in pose for value in point[:2])
            )
            for parent, child, expected in bones:
                length = math.dist(pose[parent][:2], pose[child][:2])
                self.assertLessEqual(length, expected + 1e-9)
            projected_lengths.append(math.dist(pose[5][:2], pose[7][:2]))
            if previous is not None:
                self.assertLess(
                    max(math.dist(a[:2], b[:2]) for a, b in zip(previous, pose)),
                    0.06,
                )
            previous = pose
        self.assertGreater(max(projected_lengths) - min(projected_lengths), 0.08)

        explicit = [list(point) for point in named_pose("star")]
        explicit[9][:2] = [0.5, 0.5]
        explicit_song = {
            "moves": [
                {"time": 0, "pose": named_pose("ready")},
                {"time": 1, "pose": explicit},
            ]
        }
        self.assertEqual(target_pose(explicit_song, 1), tuple(map(tuple, explicit)))

    def test_players_leave_and_rejoin_their_slots(self):
        slots = PlayerSlots(max_players=2, leave_after=0.5, rebind_seconds=2.0)
        slots.update({11: shifted("ready", -0.2), 22: shifted("ready", 0.2)}, 0.0)
        joined_at = slots.slots[0].joined_at
        slots.update({22: shifted("ready", 0.2)}, 0.6)
        self.assertFalse(slots.slots[0].active)
        slots.update({33: shifted("ready", -0.19), 22: shifted("ready", 0.2)}, 0.7)
        self.assertEqual(slots.slots[0].track_id, 33)
        self.assertEqual(slots.slots[0].joined_at, joined_at)

    def test_new_player_does_not_inherit_an_expired_slots_score(self):
        song = {
            "id": "handoff",
            "title": "Handoff",
            "duration": 6,
            "moves": [
                {"time": 0, "name": "ready"},
                {"time": 5, "name": "ready"},
            ],
        }
        session = GameSession(song, max_players=1)
        session.update(0, {7: named_pose("ready")})
        session.update(1, {})
        session.update(4.1, {99: named_pose("ready")})
        self.assertEqual(session.ui_players()[0]["score"], 0)
        self.assertEqual(session.update(5, {99: named_pose("ready")})[0].points, 1_000)

    def test_scoring_stars_and_ui_players(self):
        song = {
            "id": "test",
            "title": "Test",
            "duration": 3,
            "moves": [
                {"time": 0, "name": "ready"},
                {"time": 1, "name": "clap"},
                {"time": 2, "name": "star"},
            ],
        }
        session = GameSession(song, max_players=1)
        self.assertEqual(session.update(0, {7: named_pose("ready")})[0].grade, "PERFECT")
        session.update(1, {7: named_pose("clap")})
        self.assertEqual(session.update(2, {7: [(0, 0, 0)] * 17})[0].grade, "MISS")
        result = session.results()["players"][0]
        self.assertEqual((result["score"], result["stars"]), (2_000, 3))
        self.assertEqual(session.ui_players()[0]["combo"], 0)
        self.assertEqual(stars_for_accuracy(0.9), 5)

    def test_catalog_and_extracted_choreography(self):
        catalog = load_catalog()
        self.assertEqual(
            [song["id"] for song in catalog],
            ["neon_first_light", "pixel_heart_rush", "cosmic_afterburn"],
        )
        for song in catalog:
            for move in song["moves"]:
                self.assertEqual(len(named_pose(move["name"])), 17)
        extracted = {
            "id": "video",
            "title": "Video",
            "duration": 2,
            "moves": [],
            "choreography": {
                "lead_track_id": 9,
                "timeline": [
                    {
                        "timestamp_ms": 0,
                        "people": [{"track_id": 9, "keypoints": named_pose("ready")}],
                    },
                    {
                        "timestamp_ms": 1000,
                        "people": [{"track_id": 9, "keypoints": named_pose("star")}],
                    },
                ],
            },
        }
        session = GameSession(extracted, max_players=1)
        self.assertEqual(len(target_pose(extracted, 0.5)), 17)
        session.update(0, {1: named_pose("ready")})
        self.assertEqual(session.update(1, {1: named_pose("star")})[0].grade, "PERFECT")

        with TemporaryDirectory() as directory:
            lrc = Path(directory) / "lyrics.lrc"
            lrc.write_text("[offset:100]\n[00:01.50]Dance now!\n", encoding="utf-8")
            lyrics = parse_lrc(lrc)
            self.assertEqual(lyrics[0]["text"], "Dance now!")
            self.assertAlmostEqual(lyrics[0]["time"], 1.6)


if __name__ == "__main__":
    unittest.main()
