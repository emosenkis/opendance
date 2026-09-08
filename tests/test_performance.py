from types import SimpleNamespace
import unittest
from unittest.mock import patch

from opendance.app import framing_nudge
from opendance.vision import PoseEngine, RTMPoseEngine, create_pose_engine


class PoseDiagnosticsTest(unittest.TestCase):
    @patch("opendance.extract.diagnostics", return_value=0)
    def test_extractor_diagnostics_needs_no_video(self, diagnostic):
        from opendance.extract import main

        self.assertEqual(main(["--diagnostics"]), 0)
        diagnostic.assert_called_once_with()

    def test_framing_nudges_validate_box_height_and_clipping(self):
        self.assertEqual(framing_nudge([0.2, 0.1, 0.4, 0.7]), "")
        self.assertEqual(framing_nudge([0.2, 0.2, 0.3, 0.4]), "MOVE FORWARD")
        self.assertEqual(framing_nudge([0.2, 0.0, 0.5, 0.72]), "MOVE BACK")
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


if __name__ == "__main__":
    unittest.main()
