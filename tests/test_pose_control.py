import unittest

from opendance.game import named_pose
from opendance.vision import (
    GestureController,
    PoseEngine,
    TemporalPoseFilter,
    classify_pose_gesture,
)


def person(track_id, *, dx=0.0, gesture=None):
    pose = [list(point) for point in named_pose("ready")]
    box = [0.2 + dx, 0.1, 0.4, 0.8]
    for point in pose:
        point[0] += dx
    if gesture == "join":
        pose[9][1] = pose[10][1] = 0.01
    elif gesture == "accept":
        pose[9][:2], pose[10][:2] = [0.49, 0.32], [0.51, 0.32]
    elif gesture == "left":
        pose[9][:2] = [0.05, 0.28]
    elif gesture == "right":
        pose[10][:2] = [0.95, 0.28]
    return {"track_id": track_id, "bbox": box, "keypoints": pose, "confidence": 1.0}


class PoseControlTest(unittest.TestCase):
    def test_filter_holds_transient_jump_and_accepts_persistent_move(self):
        smoother = TemporalPoseFilter(alpha=0.5, confirm_frames=3, center_jump=0.15)
        self.assertEqual(smoother.update([person(7)])[0]["bbox"][0], 0.2)
        self.assertEqual(smoother.update([person(7, dx=0.4)])[0]["bbox"][0], 0.2)
        self.assertEqual(smoother.update([person(7)])[0]["bbox"][0], 0.2)
        self.assertEqual(smoother.update([person(7, dx=0.4)])[0]["bbox"][0], 0.2)
        self.assertEqual(smoother.update([person(7, dx=0.4)])[0]["bbox"][0], 0.2)
        self.assertAlmostEqual(
            smoother.update([person(7, dx=0.4)])[0]["bbox"][0], 0.6
        )

    def test_filter_smooths_normal_motion(self):
        smoother = TemporalPoseFilter(alpha=0.5, center_jump=0.2)
        smoother.update([person(3)])
        result = smoother.update([person(3, dx=0.1)])[0]
        self.assertAlmostEqual(result["bbox"][0], 0.25)

    def test_filter_holds_an_implausible_one_frame_posture(self):
        smoother = TemporalPoseFilter(confirm_frames=3)
        accepted = smoother.update([person(9)])[0]["keypoints"]
        glitch = person(9)
        glitch["keypoints"] = [list(point) for point in named_pose("star")]
        self.assertEqual(smoother.update([glitch])[0]["keypoints"], accepted)
        self.assertEqual(smoother.update([person(9)])[0]["keypoints"], accepted)

    def test_pose_engine_can_reset_smoothing_without_resetting_tracking(self):
        engine = PoseEngine("unused.pt", smooth_frames=3)
        assert engine._pose_filter is not None
        engine._pose_filter.update([person(4)])
        generation = engine._tracking_generation
        engine.reset_smoothing()
        self.assertEqual(engine._pose_filter._states, {})
        self.assertEqual(engine._tracking_generation, generation)

    def test_gestures_claim_control_debounce_actions_and_gate_joining(self):
        controller = GestureController(
            join_required=True, claim_hold=0.5, action_hold=0.25
        )
        self.assertEqual(controller.update([person(5, gesture="join")], 0.0), [])
        events = controller.update([person(5, gesture="join")], 0.6)
        self.assertEqual([event["action"] for event in events], ["join", "claim"])
        self.assertTrue(controller.admits(5))
        self.assertFalse(controller.admits(6))

        controller.update([person(5)], 0.7)
        self.assertEqual(controller.update([person(5, gesture="right")], 1.0), [])
        self.assertEqual(
            controller.update([person(5, gesture="right")], 1.3)[0]["action"],
            "right",
        )
        self.assertEqual(controller.update([person(5, gesture="right")], 2.0), [])
        controller.update([person(5)], 2.1)
        controller.update([person(5, gesture="accept")], 2.2)
        self.assertEqual(
            controller.update([person(5, gesture="accept")], 2.5)[0]["action"],
            "accept",
        )

    def test_gesture_vocabulary_requires_confident_deliberate_poses(self):
        for gesture in ("join", "accept", "left", "right"):
            self.assertEqual(classify_pose_gesture(person(1, gesture=gesture)["keypoints"]), gesture)
        uncertain = person(1, gesture="join")["keypoints"]
        uncertain[9][2] = 0.1
        self.assertIsNone(classify_pose_gesture(uncertain))


if __name__ == "__main__":
    unittest.main()
