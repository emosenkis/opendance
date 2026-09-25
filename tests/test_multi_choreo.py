import gc
import json
from pathlib import Path
import sys
import threading
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import weakref

from opendance.extract import (
    _build_move_scoring,
    _role_timeline,
    analyze_video,
    extract_song,
)
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


def transformed(name, dx, scale):
    return [
        [0.5 + dx + (x - 0.5) * scale, 0.5 + (y - 0.5) * scale, confidence]
        for x, y, confidence in named_pose(name)
    ]


def placed_person(track_id, x):
    return person(track_id, "ready", x - 0.5, [x - 0.05, 0.1, 0.1, 0.8])


def assigned_timeline(frames):
    first_people = frames[0]["people"]
    seed_ids = [item["track_id"] for item in first_people]
    stats = {
        item["track_id"]: {
            "frames": 1.0,
            "x": item["bbox"][0] + item["bbox"][2] / 2,
        }
        for item in first_people
    }
    return _role_timeline(frames, len(seed_ids), seed_ids, stats, 0)[0]


class MultiDancerTest(unittest.TestCase):
    def test_scene_cut_rebinds_recycled_ids_by_location(self):
        frames = [
            {
                "timestamp_ms": 0,
                "people": [
                    placed_person(track_id, x)
                    for track_id, x in zip((1, 2, 3), (0.15, 0.5, 0.85))
                ],
            },
            {
                "timestamp_ms": 1_000,
                "people": [
                    placed_person(track_id, x)
                    for track_id, x in zip((1, 2, 3), (0.85, 0.5, 0.15))
                ],
            },
            {
                "timestamp_ms": 1_033,
                "scene_cut": True,
                "people": [
                    placed_person(track_id, x)
                    for track_id, x in zip((1, 2, 3), (0.15, 0.5, 0.85))
                ],
            },
            {
                "timestamp_ms": 1_066,
                "people": [
                    placed_person(track_id, x)
                    for track_id, x in zip((1, 2, 3), (0.15, 0.5, 0.85))
                ],
            },
        ]

        timeline = assigned_timeline(frames)

        self.assertEqual(
            [
                {item["track_id"]: item["dancer_index"] for item in frame["people"]}
                for frame in timeline[1:]
            ],
            [{1: 0, 2: 1, 3: 2}, {1: 2, 2: 1, 3: 0}, {1: 2, 2: 1, 3: 0}],
        )

    def test_role_assignment_rejects_an_instant_four_dancer_cycle(self):
        frames = [
            {
                "timestamp_ms": 0,
                "people": [
                    placed_person(track_id, x)
                    for track_id, x in zip((1, 2, 3, 4), (0.125, 0.375, 0.625, 0.875))
                ],
            },
            {
                "timestamp_ms": 33,
                "people": [
                    placed_person(track_id, x)
                    for track_id, x in zip((4, 1, 2, 3), (0.125, 0.375, 0.625, 0.875))
                ],
            },
            {
                "timestamp_ms": 66,
                "people": [
                    placed_person(track_id, x)
                    for track_id, x in zip((4, 1, 2, 3), (0.125, 0.375, 0.625, 0.875))
                ],
            },
        ]

        timeline = assigned_timeline(frames)

        self.assertEqual(
            [
                {item["track_id"]: item["dancer_index"] for item in frame["people"]}
                for frame in timeline
            ],
            [
                {1: 0, 2: 1, 3: 2, 4: 3},
                {4: 0, 1: 1, 2: 2, 3: 3},
                {4: 0, 1: 1, 2: 2, 3: 3},
            ],
        )

    def test_role_assignment_keeps_ids_through_a_gradual_crossing(self):
        frames = [
            {
                "timestamp_ms": timestamp,
                "people": [placed_person(1, left), placed_person(2, right)],
            }
            for timestamp, left, right in (
                (0, 0.25, 0.75),
                (250, 0.4, 0.6),
                (500, 0.55, 0.45),
                (750, 0.7, 0.3),
            )
        ]

        timeline = assigned_timeline(frames)

        self.assertTrue(
            all(
                {item["track_id"]: item["dancer_index"] for item in frame["people"]}
                == {1: 0, 2: 1}
                for frame in timeline
            )
        )

    def test_stale_track_cannot_evict_the_current_role_occupant(self):
        frames = [
            {
                "timestamp_ms": 0,
                "people": [
                    placed_person(track_id, x)
                    for track_id, x in zip((95, 85, 107, 88), (0.125, 0.375, 0.77, 0.875))
                ],
            },
            {
                "timestamp_ms": 33,
                "people": [
                    placed_person(track_id, x)
                    for track_id, x in zip((100, 85, 107, 88), (0.61, 0.375, 0.77, 0.875))
                ],
            },
            {
                "timestamp_ms": 66,
                "people": [
                    placed_person(track_id, x)
                    for track_id, x in zip((95, 85, 100, 88), (0.43, 0.375, 0.61, 0.875))
                ],
            },
        ]

        timeline = assigned_timeline(frames)
        final = {
            item["track_id"]: item["dancer_index"]
            for item in timeline[-1]["people"]
        }

        self.assertEqual(final, {100: 0, 85: 1, 95: 2, 88: 3})

    def test_switching_songs_releases_the_previous_dense_timeline(self):
        class Timeline(list):
            pass

        def song(timeline, track_id):
            return {
                "moves": [],
                "choreography": {
                    "dancer_track_ids": [track_id],
                    "lead_dancer_index": 0,
                    "timeline": timeline,
                },
            }

        first = Timeline([
            {
                "timestamp_ms": 0,
                "people": [dict(person(1, "ready", 0, [0, 0, 1, 1]), dancer_index=0)],
            }
        ])
        first_reference = weakref.ref(first)
        target_dancers(song(first, 1), 0)
        second = Timeline([
            {
                "timestamp_ms": 0,
                "people": [dict(person(2, "star", 0, [0, 0, 1, 1]), dancer_index=0)],
            }
        ])
        target_dancers(song(second, 2), 0)
        del first
        gc.collect()

        self.assertIsNone(first_reference())

    def test_move_scoring_segments_normalized_multi_dancer_motion(self):
        timeline = []
        for frame_index in range(24):
            time_s = frame_index * 0.25
            pose = "ready" if time_s < 3 else "star"
            timeline.append(
                {
                    "timestamp_ms": time_s * 1000,
                    "people": [
                        {
                            "dancer_index": 0,
                            "keypoints": transformed(pose, -0.2, 0.8),
                        },
                        {
                            "dancer_index": 1,
                            "keypoints": transformed(pose, 0.2, 1.3),
                        },
                    ],
                }
            )

        artifact = _build_move_scoring(timeline, 2)
        self.assertEqual(artifact, _build_move_scoring(timeline, 2))
        self.assertEqual(artifact["schema_version"], 1)
        self.assertEqual(artifact["phase_count"], 12)
        self.assertEqual(
            [(segment["start"], segment["end"]) for segment in artifact["segments"]],
            [(0.0, 3.0), (3.0, 5.75)],
        )
        self.assertTrue(
            all(len(segment["dancers"]) == 2 for segment in artifact["segments"])
        )
        self.assertEqual(len(artifact["definitions"]), 4)
        first, second = artifact["definitions"]["m0000"], artifact["definitions"]["m0001"]
        self.assertNotEqual(first["poses"], second["poses"])
        for definition in artifact["definitions"].values():
            self.assertEqual(len(definition["poses"]), 12)
            self.assertEqual(len(definition["weights"]), 17)
            self.assertLess(definition["cue_sample"], 12)
            self.assertLessEqual(len(definition["important_joints"]), 3)

    def test_move_scoring_preserves_root_travel_in_definition(self):
        timeline = []
        for frame_index in range(9):
            time_s = frame_index * 0.25
            timeline.append(
                {
                    "timestamp_ms": time_s * 1000,
                    "people": [
                        {
                            "dancer_index": 0,
                            "keypoints": transformed(
                                "ready", -0.15 + 0.075 * time_s, 0.8
                            ),
                        }
                    ],
                }
            )

        definition = _build_move_scoring(timeline, 1)["definitions"]["m0000"]
        first, last = definition["poses"][0], definition["poses"][-1]
        first_root = (first[11][0] + first[12][0]) / 2
        last_root = (last[11][0] + last[12][0]) / 2
        self.assertAlmostEqual(last_root - first_root, 0.15, places=3)

    def test_move_scoring_is_empty_for_an_insufficient_timeline(self):
        artifact = _build_move_scoring(
            [
                {
                    "timestamp_ms": 0,
                    "people": [
                        {
                            "dancer_index": 0,
                            "keypoints": transformed("ready", 0, 1),
                        }
                    ],
                }
            ],
            1,
        )
        self.assertEqual(artifact["definitions"], {})
        self.assertEqual(artifact["segments"], [])

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
        self.assertEqual(
            {dancer["dancer_index"]: dancer["bbox"][0] for dancer in rendered},
            {0: 0.1, 1: 0.7},
        )

    def test_video_analysis_does_not_infer_before_requested_time(self):
        class Capture:
            def __init__(self):
                self.index = -1

            def isOpened(self):
                return True

            def read(self):
                self.index += 1
                return (self.index < 10, self.index)

            def get(self, field):
                return {
                    "fps": 1.0,
                    "count": 10.0,
                    "time": self.index * 1_000.0,
                }[field]

            def release(self):
                pass

        capture = Capture()
        fake_cv2 = SimpleNamespace(
            VideoCapture=lambda _path: capture,
            CAP_PROP_FPS="fps",
            CAP_PROP_FRAME_COUNT="count",
            CAP_PROP_POS_MSEC="time",
            INTER_AREA=0,
            resize=lambda frame, _size, interpolation: frame,
            absdiff=lambda first, second: SimpleNamespace(
                mean=lambda: abs(first - second)
            ),
        )

        class Engine:
            def __init__(self):
                self.calls = []

            def reset_tracking(self):
                pass

            def process(self, frame, *, timestamp_ms):
                self.calls.append((frame, timestamp_ms))
                return {
                    "source": {"width": 1, "height": 1},
                    "people": [person(1, "ready", 0, [0.2, 0.1, 0.6, 0.8])],
                    "timing": {},
                }

        engine = Engine()
        progress = []
        previews = []
        with TemporaryDirectory() as directory:
            video = Path(directory) / "video.mp4"
            video.touch()
            with patch.dict(sys.modules, {"cv2": fake_cv2}):
                result = analyze_video(
                    video,
                    engine,
                    skip_before_s=3,
                    trim_end_s=2,
                    show_progress=False,
                    progress_callback=lambda *values: progress.append(values),
                    preview_callback=lambda frame, people: previews.append(
                        (frame, people)
                    ),
                )

        self.assertEqual([frame for frame, _time in engine.calls], list(range(3, 8)))
        self.assertEqual(result["frames"][0]["timestamp_ms"], 3_000)
        self.assertEqual(result["source"]["duration_ms"], 10_000)
        self.assertEqual(result["source"]["analyzed_frame_count"], 5)
        self.assertEqual(progress[0][:2], (0, 8))
        self.assertEqual(progress[-1][:2], (8, 8))
        self.assertGreater(progress[-1][2], 0)
        self.assertEqual(len(previews), 1)
        self.assertEqual(previews[0][0], 3)
        self.assertEqual(previews[0][1][0]["track_id"], 1)

        capture = Capture()
        cancelled = threading.Event()
        cancelled.set()
        with TemporaryDirectory() as directory:
            video = Path(directory) / "video.mp4"
            video.touch()
            with patch.dict(sys.modules, {"cv2": fake_cv2}), self.assertRaisesRegex(
                InterruptedError, "cancelled"
            ):
                analyze_video(
                    video,
                    engine,
                    show_progress=False,
                    cancel_event=cancelled,
                )

    def test_extractor_retimes_trimmed_media_lyrics_and_hidden_intro(self):
        analysis = {
            "source": {
                "path": "/video.mp4",
                "width": 1280,
                "height": 720,
                "fps": 1.0,
                "reported_frame_count": 10,
                "decoded_frame_count": 10,
                "analyzed_frame_count": 4,
                "duration_ms": 10_000.0,
            },
            "frames": [
                {
                    "frame": index,
                    "timestamp_ms": timestamp * 1_000.0,
                    "people": [person(11, "ready", 0, [0.2, 0.1, 0.6, 0.8])],
                    "timing": {},
                }
                for index, timestamp in enumerate((3, 4, 7, 8))
            ],
            "processing": {"elapsed_seconds": 1.0, "average_fps": 4.0},
        }
        engine = SimpleNamespace(
            model_name="pose.pt",
            imgsz=640,
            device="cpu",
            max_people=1,
            smooth_frames=0,
        )
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "video.mp4"
            source.touch()
            lrc = root / "lyrics.lrc"
            lrc.write_text(
                "[00:01.00]Opening\n[00:03.00]Dance\n[00:08.00]End\n[00:09.00]Outro\n",
                encoding="utf-8",
            )
            with patch("opendance.extract.analyze_video", return_value=analysis) as mocked:
                output = extract_song(
                    source,
                    root / "song",
                    engine=engine,
                    lrc=lrc,
                    trim_start=2,
                    trim_end=2,
                    hide_video_intro=1,
                    copy_video=True,
                    move_video=True,
                    show_progress=False,
                )
            song = json.loads(output.read_text(encoding="utf-8"))
            self.assertFalse(source.exists())
            self.assertTrue((output.parent / "video.mp4").is_file())

        mocked.assert_called_once_with(
            source,
            engine,
            skip_before_s=3.0,
            trim_end_s=2.0,
            show_progress=False,
            progress_callback=None,
            preview_callback=None,
            cancel_event=None,
        )
        self.assertEqual(song["duration"], 6)
        self.assertEqual(song["media_start"], 2)
        self.assertEqual(song["video_hidden_until"], 1)
        self.assertEqual(song["video"], "video.mp4")
        self.assertEqual(song["lyrics"], [
            {"time": 0.0, "text": "Opening"},
            {"time": 1.0, "text": "Dance"},
        ])
        self.assertEqual(
            [frame["timestamp_ms"] for frame in song["choreography"]["timeline"]],
            [1_000, 2_000, 5_000],
        )
        source_metadata = song["choreography"]["source"]
        self.assertEqual(source_metadata["original_duration_ms"], 10_000)
        self.assertEqual(source_metadata["duration_ms"], 6_000)
        self.assertEqual(source_metadata["trim_end_ms"], 2_000)
        session = GameSession(song, max_players=1)
        self.assertEqual(
            session.update(0, {99: named_pose("ready")}),
            [],
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
                {66: 0, 55: 1},
            ],
        )
        self.assertEqual(choreography["timeline"][0]["depth_order"], [1, 0])
        self.assertEqual(choreography["timeline"][0]["render_order"], [0, 1])
        self.assertTrue(choreography["timeline"][3]["scene_cut"])
        self.assertIn(33, choreography["dancers"][0]["track_ids"])
        self.assertIn(44, choreography["dancers"][1]["track_ids"])
        self.assertEqual(choreography["move_scoring"]["schema_version"], 1)


if __name__ == "__main__":
    unittest.main()
