import unittest

from opendance.game import GameSession, interpolate_pose, move_similarity, named_pose


def motion(phase):
    if phase < 0.55:
        pose = interpolate_pose(named_pose("ready"), named_pose("disco_left"), phase / 0.55)
    else:
        pose = interpolate_pose(named_pose("disco_left"), named_pose("star"), (phase - 0.55) / 0.45)
    dx = 0.16 * phase + 0.04 * phase * phase
    dy = -0.06 * phase * (1.0 - phase)
    return [(x + dx, y + dy, confidence) for x, y, confidence in pose]


def segmented_song():
    poses = [motion(phase) for phase in (0.0, 0.2, 0.4, 0.6, 0.8, 1.0)]
    return {
        "id": "segments",
        "title": "Segments",
        "duration": 2,
        "moves": [],
        "choreography": {
            "dancer_track_ids": [10],
            "timeline": [
                {
                    "timestamp_ms": time_s * 1_000,
                    "people": [{"dancer_index": 0, "keypoints": pose}],
                }
                for time_s, pose in ((0, poses[0]), (2, poses[-1]))
            ],
            "move_scoring": {
                "schema_version": 1,
                "definitions": {
                    "first": {"poses": poses},
                    "second": {"poses": poses},
                },
                "segments": [
                    {
                        "start": start,
                        "end": start + 1,
                        "dancers": [{"dancer_index": 0, "definition": definition}],
                    }
                    for start, definition in ((0, "first"), (1, "second"))
                ],
            },
        },
    }


def observed(phase):
    return [(3.0 - 1.7 * x, -2.0 + 1.7 * y, confidence) for x, y, confidence in motion(phase)]


class MoveSimilarityTest(unittest.TestCase):
    def test_motion_is_position_scale_timing_and_mirror_independent(self):
        target = [motion(phase) for phase in (0.0, 0.2, 0.4, 0.6, 0.8, 1.0)]
        stretched = [motion(phase) for phase in (0.0, 0.08, 0.18, 0.31, 0.47, 0.62, 0.76, 0.9, 1.0)]
        transformed = [
            [(3.0 - 1.7 * x, -2.0 + 1.7 * y, confidence) for x, y, confidence in pose]
            for pose in stretched
        ]
        transformed[4][9] = (99.0, -99.0, 0.01)
        self.assertGreater(move_similarity(transformed, target), 0.9)
        self.assertLess(move_similarity(transformed, target, allow_mirror=False), 0.5)

    def test_wrong_reversed_and_stationary_motion_score_low(self):
        target = [motion(phase) for phase in (0.0, 0.2, 0.4, 0.6, 0.8, 1.0)]
        wrong = [named_pose(name) for name in ("squat", "cross", "clap", "squat", "cross", "clap")]
        still = [target[0]] * len(target)
        self.assertLess(move_similarity(wrong, target), 0.5)
        self.assertLess(move_similarity(list(reversed(target)), target), 0.5)
        self.assertLess(move_similarity(still, target), 0.1)

    def test_session_scores_one_complete_motion_not_periodic_poses(self):
        session = GameSession(segmented_song(), max_players=1)
        feedback = []
        for time_s in (0, 0.2, 0.4, 0.6, 0.8, 1.0):
            feedback.extend(session.update(time_s, {7: observed(time_s)}))
            if time_s < 1:
                self.assertEqual(feedback, [])
        self.assertEqual(len(feedback), 1)
        self.assertEqual(feedback[0].move_name, "MOVE 1")
        self.assertEqual(feedback[0].grade, "PERFECT")
        self.assertEqual(session.ui_players()[0]["possible_score"], 1_000)

    def test_partial_join_is_skipped_then_next_complete_move_scores(self):
        session = GameSession(segmented_song(), max_players=1)
        feedback = []
        for time_s in (0.5, 0.75):
            feedback.extend(session.update(time_s, {7: observed(time_s)}))
        feedback.extend(session.update(1.0, {7: observed(0)}))
        self.assertEqual(feedback, [])
        self.assertEqual(session.ui_players()[0]["possible_score"], 0)

        for time_s in (1.2, 1.4, 1.6, 1.8, 2.0):
            feedback.extend(session.update(time_s, {7: observed(time_s - 1)}))
        self.assertEqual(len(feedback), 1)
        self.assertEqual(feedback[0].move_name, "MOVE 2")
        self.assertEqual(feedback[0].grade, "PERFECT")

    def test_missing_middle_of_move_is_not_scored(self):
        session = GameSession(segmented_song(), max_players=1)
        feedback = []
        for time_s in (0, 0.2, 0.8, 1.0):
            feedback.extend(session.update(time_s, {7: observed(time_s)}))
        self.assertEqual(feedback, [])
        self.assertEqual(session.ui_players()[0]["possible_score"], 0)

    def test_media_clock_jitter_does_not_discard_the_move(self):
        session = GameSession(segmented_song(), max_players=1)
        feedback = []
        for time_s in (0, 0.2, 0.19, 0.4, 0.39, 0.6, 0.59, 0.8, 0.79, 1.0):
            feedback.extend(session.update(time_s, {7: observed(time_s)}))
        self.assertEqual(len(feedback), 1)
        self.assertEqual(feedback[0].grade, "PERFECT")

    def test_finish_flushes_the_last_move_inside_the_latency_window(self):
        session = GameSession(segmented_song(), max_players=1)
        for time_s in (0, 0.2, 0.4, 0.6, 0.8, 1.0):
            session.update(time_s, {7: observed(time_s)})
        for time_s in (1.2, 1.4, 1.6, 1.8, 1.92):
            session.update(time_s, {7: observed(time_s - 1)})

        self.assertEqual(session.ui_players()[0]["possible_score"], 1_000)
        feedback = session.finish()
        self.assertEqual(len(feedback), 1)
        self.assertEqual(session.ui_players()[0]["possible_score"], 2_000)


if __name__ == "__main__":
    unittest.main()
