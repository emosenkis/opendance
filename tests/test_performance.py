from types import SimpleNamespace
import unittest
from unittest.mock import patch

from opendance.app import framing_nudge
from opendance.vision import (
    PoseEngine,
    RTMPoseEngine,
    create_pose_engine,
    is_player_detection,
)


def player_detection(confidence=0.9, visible=range(17)):
    visible = set(visible)
    return {
        "confidence": confidence,
        "bbox": [0.2, 0.1, 0.4, 0.8],
        "keypoints": [
            [0.4, 0.05 + index * 0.05, 0.9 if index in visible else 0.1]
            for index in range(17)
        ],
    }


class PoseDiagnosticsTest(unittest.TestCase):
    @patch("opendance.extract.diagnostics", return_value=0)
    def test_extractor_diagnostics_needs_no_video(self, diagnostic):
        from opendance.extract import main

        self.assertEqual(main(["--diagnostics"]), 0)
        diagnostic.assert_called_once_with()

    def test_framing_nudges_validate_box_height_and_clipping(self):
        self.assertEqual(framing_nudge([0.2, 0.1, 0.4, 0.7]), "")
        self.assertEqual(framing_nudge([0.2, 0.2, 0.3, 0.24]), "MOVE FORWARD")
        self.assertEqual(framing_nudge([0.2, 0.2, 0.3, 0.25]), "")
        self.assertEqual(framing_nudge([0.2, 0.05, 0.5, 0.9]), "")
        self.assertEqual(framing_nudge([0.2, 0.0, 0.5, 0.72]), "MOVE BACK")
        self.assertEqual(framing_nudge([0.2, 0.3, 0.5, 0.7]), "MOVE BACK")
        self.assertEqual(framing_nudge([0.2, 0.1, 0.4]), "")

    def test_reports_resolved_inference_device(self):
        model = SimpleNamespace(
            predictor=SimpleNamespace(device="cuda:0"),
            track=lambda *_args, **_kwargs: [],
        )
        engine = PoseEngine("unused.pt")
        engine._model = model

        result = engine.process(SimpleNamespace(shape=(10, 20, 3)))

        self.assertEqual(result["device"], "cuda:0")

    def test_player_detection_requires_confidence_count_and_body_coverage(self):
        self.assertTrue(is_player_detection(player_detection()))
        self.assertTrue(is_player_detection(player_detection(visible=range(5, 17))))
        self.assertFalse(is_player_detection(player_detection(confidence=0.29)))
        self.assertFalse(is_player_detection(player_detection(visible=range(7))))
        self.assertFalse(is_player_detection(player_detection(visible=range(11))))

    def test_yolo_filters_weak_people_before_temporal_smoothing(self):
        points = [
            [[40, 5 + index * 5, 0.9] for index in range(17)],
            [[70, 5 + index * 5, 0.9] for index in range(17)],
        ]
        result = SimpleNamespace(
            boxes=SimpleNamespace(
                xyxy=[[20, 5, 60, 95], [55, 5, 95, 95]],
                id=[1, 2],
                conf=[0.9, 0.2],
            ),
            keypoints=SimpleNamespace(data=points),
        )
        model = SimpleNamespace(
            predictor=SimpleNamespace(device="cpu"),
            track=lambda *_args, **_kwargs: [result],
        )
        engine = PoseEngine("unused.pt", smooth_frames=0)
        engine._model = model
        smoothed = []

        def smooth(people):
            smoothed.extend(people)
            return people

        engine._pose_filter = SimpleNamespace(update=smooth)

        people = engine.process(SimpleNamespace(shape=(100, 100, 3)))["people"]

        self.assertEqual([person["track_id"] for person in people], [1])
        self.assertEqual([person["track_id"] for person in smoothed], [1])

    def test_rtmpose_keeps_ids_when_detector_order_changes(self):
        state = {
            "boxes": [[10, 10, 40, 90], [60, 10, 90, 90]],
            "centers": [25, 75],
        }

        def detector(_frame):
            return state["boxes"]

        def pose(_frame, *, bboxes):
            points = [
                [[center, 50] for _ in range(17)] for center in state["centers"]
            ]
            return points[: len(bboxes)], [[0.9] * 17 for _ in bboxes]

        engine = RTMPoseEngine(device="cpu", max_people=2, smooth_frames=0)
        engine._model = SimpleNamespace(det_model=detector, pose_model=pose)
        frame = SimpleNamespace(shape=(100, 100, 3))
        first = engine.process(frame, timestamp_ms=0)["people"]
        state["boxes"] = list(reversed(state["boxes"]))
        state["centers"] = list(reversed(state["centers"]))
        second = engine.process(frame, timestamp_ms=33)["people"]

        first_ids = {round(person["bbox"][0], 1): person["track_id"] for person in first}
        second_ids = {round(person["bbox"][0], 1): person["track_id"] for person in second}
        self.assertEqual(first_ids, second_ids)
        self.assertEqual(second[0]["track_id"], first[1]["track_id"])

    def test_pose_backend_factory_keeps_yolo_default(self):
        self.assertIsInstance(create_pose_engine(), PoseEngine)
        self.assertIsInstance(
            create_pose_engine("rtmpose", rtmpose_mode="lightweight"),
            RTMPoseEngine,
        )
        self.assertEqual(create_pose_engine("rtmpose", max_people=6).max_people, 6)
        with self.assertRaisesRegex(ValueError, "model applies to YOLO26"):
            create_pose_engine("rtmpose", model="wrong.onnx")

    def test_rtmpose_skips_top_down_pose_work_when_no_person_is_detected(self):
        def should_not_run(*_args, **_kwargs):
            self.fail("pose model ran without a person box")

        engine = RTMPoseEngine(device="cpu", smooth_frames=0)
        engine._model = SimpleNamespace(
            det_model=lambda _frame: [], pose_model=should_not_run
        )
        result = engine.process(SimpleNamespace(shape=(100, 100, 3)))
        self.assertEqual(result["people"], [])

    def test_rtmpose_filters_weak_people_before_spatial_tracking(self):
        boxes = [[10, 5, 45, 95, 0.9], [55, 5, 90, 95, 0.2]]

        def pose(_frame, *, bboxes):
            self.assertEqual(bboxes, [box[:4] for box in boxes])
            points = [
                [[center, 5 + index * 5] for index in range(17)]
                for center in (25, 75)
            ]
            return points, [[0.9] * 17, [0.9] * 17]

        engine = RTMPoseEngine(device="cpu", max_people=2, smooth_frames=0)
        engine._model = SimpleNamespace(
            det_model=lambda _frame: boxes,
            pose_model=pose,
        )

        people = engine.process(SimpleNamespace(shape=(100, 100, 3)))["people"]

        self.assertEqual(len(people), 1)
        self.assertEqual(len(engine._slots.visible_slots), 1)


if __name__ == "__main__":
    unittest.main()
