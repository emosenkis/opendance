import unittest

from opendance.game import interpolate_pose, move_similarity, named_pose


def motion(phase):
    if phase < 0.55:
        pose = interpolate_pose(named_pose("ready"), named_pose("disco_left"), phase / 0.55)
    else:
        pose = interpolate_pose(named_pose("disco_left"), named_pose("star"), (phase - 0.55) / 0.45)
    dx = 0.16 * phase + 0.04 * phase * phase
    dy = -0.06 * phase * (1.0 - phase)
    return [(x + dx, y + dy, confidence) for x, y, confidence in pose]


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


if __name__ == "__main__":
    unittest.main()
