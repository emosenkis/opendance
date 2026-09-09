"""Engine-independent multi-person pose inference.

The optional ML stack is imported only when :class:`PoseEngine` first runs,
so menus, content tools, and tests can import this module without a GPU setup.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
import os
from pathlib import Path
import threading
import time
from typing import Any

from .audio import default_cache_dir


COCO17_KEYPOINTS = (
    "nose",
    "left_eye",
    "right_eye",
    "left_ear",
    "right_ear",
    "left_shoulder",
    "right_shoulder",
    "left_elbow",
    "right_elbow",
    "left_wrist",
    "right_wrist",
    "left_hip",
    "right_hip",
    "left_knee",
    "right_knee",
    "left_ankle",
    "right_ankle",
)

_MIN_DETECTION_CONFIDENCE = 0.30
_MIN_VISIBLE_KEYPOINT_CONFIDENCE = 0.25
_MIN_VISIBLE_KEYPOINTS = 8
_BODY_REGIONS = (
    range(0, 5),  # head
    range(5, 11),  # shoulders and arms
    range(11, 13),  # hips
    range(13, 17),  # legs
)


def _tolist(value: Any) -> list[Any]:
    """Convert a Torch/NumPy-like value without importing either package."""

    if value is None:
        return []
    detach = getattr(value, "detach", None)
    if callable(detach):
        value = detach()
    cpu = getattr(value, "cpu", None)
    if callable(cpu):
        value = cpu()
    tolist = getattr(value, "tolist", None)
    return tolist() if callable(tolist) else list(value)


def _unit(value: float) -> float:
    number = float(value)
    return max(0.0, min(1.0, number)) if math.isfinite(number) else 0.0


def is_player_detection(person: dict[str, Any]) -> bool:
    """Reject weak person-shaped detections before they can claim a player slot."""

    try:
        detector_confidence = float(person.get("confidence", 0.0))
    except (TypeError, ValueError):
        return False
    if (
        not math.isfinite(detector_confidence)
        or detector_confidence < _MIN_DETECTION_CONFIDENCE
    ):
        return False

    points = person.get("keypoints", ())
    try:
        valid_length = len(points) == len(COCO17_KEYPOINTS)
    except TypeError:
        return False
    if not valid_length:
        return False
    visible: set[int] = set()
    for index, point in enumerate(points):
        try:
            x, y, confidence = float(point[0]), float(point[1]), float(point[2])
        except (IndexError, TypeError, ValueError):
            continue
        if (
            all(math.isfinite(value) for value in (x, y, confidence))
            and confidence >= _MIN_VISIBLE_KEYPOINT_CONFIDENCE
        ):
            visible.add(index)
    if len(visible) < _MIN_VISIBLE_KEYPOINTS:
        return False
    return sum(bool(visible.intersection(region)) for region in _BODY_REGIONS) >= 3


def _copy_person(person: dict[str, Any]) -> dict[str, Any]:
    copied = dict(person)
    copied["bbox"] = [float(value) for value in person.get("bbox", ())]
    copied["keypoints"] = [
        [float(value) for value in point] for point in person.get("keypoints", ())
    ]
    return copied


def _pose_jump(
    first: dict[str, Any],
    second: dict[str, Any],
    center_limit: float,
    posture_limit: float,
) -> bool:
    first_box, second_box = first.get("bbox", ()), second.get("bbox", ())
    if len(first_box) != 4 or len(second_box) != 4:
        return False
    first_center = (first_box[0] + first_box[2] / 2, first_box[1] + first_box[3] / 2)
    second_center = (
        second_box[0] + second_box[2] / 2,
        second_box[1] + second_box[3] / 2,
    )
    if math.dist(first_center, second_center) > center_limit:
        return True

    scale_a, scale_b = max(float(first_box[3]), 0.1), max(float(second_box[3]), 0.1)
    pairs = [
        (a, b)
        for a, b in zip(first.get("keypoints", ()), second.get("keypoints", ()))
        if len(a) >= 3 and len(b) >= 3 and min(float(a[2]), float(b[2])) >= 0.25
    ]
    if len(pairs) < 6:
        return False
    posture_delta = sum(
        math.dist(
            ((float(a[0]) - first_center[0]) / scale_a, (float(a[1]) - first_center[1]) / scale_a),
            ((float(b[0]) - second_center[0]) / scale_b, (float(b[1]) - second_center[1]) / scale_b),
        )
        for a, b in pairs
    ) / len(pairs)
    return posture_delta > posture_limit


def _blend_person(first: dict[str, Any], second: dict[str, Any], amount: float) -> dict[str, Any]:
    blended = _copy_person(second)
    if len(first.get("bbox", ())) == len(second.get("bbox", ())) == 4:
        blended["bbox"] = [
            float(a) + (float(b) - float(a)) * amount
            for a, b in zip(first["bbox"], second["bbox"])
        ]
    if len(first.get("keypoints", ())) == len(second.get("keypoints", ())):
        blended["keypoints"] = [
            [
                float(a[0]) + (float(b[0]) - float(a[0])) * amount,
                float(a[1]) + (float(b[1]) - float(a[1])) * amount,
                float(b[2]) if len(b) > 2 else 1.0,
            ]
            for a, b in zip(first["keypoints"], second["keypoints"])
            if len(a) >= 2 and len(b) >= 2
        ]
    return blended


@dataclass
class _PoseFilterState:
    accepted: dict[str, Any]
    last_seen: int
    pending: dict[str, Any] | None = None
    confirmations: int = 0


class TemporalPoseFilter:
    """Smooth normal motion and reject one-frame pose/position jumps.

    A discontinuity is shown only after it remains consistent for a few frames.
    Identity comes from the local tracker and no image data is retained.
    """

    def __init__(
        self,
        *,
        alpha: float = 0.58,
        confirm_frames: int = 3,
        center_jump: float = 0.18,
        posture_jump: float = 0.16,
        max_missing: int = 30,
    ) -> None:
        if not 0.0 < float(alpha) <= 1.0:
            raise ValueError("alpha must be between zero and one")
        if isinstance(confirm_frames, bool) or int(confirm_frames) < 1:
            raise ValueError("confirm_frames must be a positive integer")
        if min(float(center_jump), float(posture_jump)) <= 0:
            raise ValueError("jump limits must be positive")
        self.alpha = float(alpha)
        self.confirm_frames = int(confirm_frames)
        self.center_jump = float(center_jump)
        self.posture_jump = float(posture_jump)
        self.max_missing = max(1, int(max_missing))
        self._frame = 0
        self._states: dict[Any, _PoseFilterState] = {}

    def reset(self) -> None:
        self._frame = 0
        self._states.clear()

    def update(
        self, people: list[dict[str, Any]], *, identity_key: str = "track_id"
    ) -> list[dict[str, Any]]:
        self._frame += 1
        output: list[dict[str, Any]] = []
        for person in people:
            current = _copy_person(person)
            identity = current.get(identity_key)
            if identity is None:
                output.append(current)
                continue
            state = self._states.get(identity)
            if state is None:
                self._states[identity] = _PoseFilterState(current, self._frame)
                output.append(current)
                continue
            state.last_seen = self._frame
            if _pose_jump(
                state.accepted,
                current,
                self.center_jump,
                self.posture_jump,
            ):
                if state.pending is not None and not _pose_jump(
                    state.pending,
                    current,
                    self.center_jump,
                    self.posture_jump,
                ):
                    state.pending = _blend_person(state.pending, current, self.alpha)
                    state.confirmations += 1
                else:
                    state.pending = current
                    state.confirmations = 1
                if state.confirmations >= self.confirm_frames:
                    state.accepted = state.pending
                    state.pending = None
                    state.confirmations = 0
            else:
                state.accepted = _blend_person(state.accepted, current, self.alpha)
                state.pending = None
                state.confirmations = 0
            held = _copy_person(state.accepted)
            held.update(
                (key, value)
                for key, value in current.items()
                if key not in {"bbox", "keypoints"}
            )
            held[identity_key] = identity
            output.append(held)

        stale = [
            identity
            for identity, state in self._states.items()
            if self._frame - state.last_seen > self.max_missing
        ]
        for identity in stale:
            del self._states[identity]
        return output


def classify_pose_gesture(points: Any) -> str | None:
    """Recognize the small, deliberate gesture vocabulary used by menus."""

    try:
        if len(points) != len(COCO17_KEYPOINTS):
            return None
        nose, left_shoulder, right_shoulder = points[0], points[5], points[6]
        left_wrist, right_wrist = points[9], points[10]
        left_hip, right_hip = points[11], points[12]
        required = (
            nose,
            left_shoulder,
            right_shoulder,
            left_wrist,
            right_wrist,
            left_hip,
            right_hip,
        )
        if any(len(point) < 3 or float(point[2]) < 0.35 for point in required):
            return None
        shoulder_mid = (
            (float(left_shoulder[0]) + float(right_shoulder[0])) / 2,
            (float(left_shoulder[1]) + float(right_shoulder[1])) / 2,
        )
        hip_mid = (
            (float(left_hip[0]) + float(right_hip[0])) / 2,
            (float(left_hip[1]) + float(right_hip[1])) / 2,
        )
        scale = max(
            0.08,
            math.dist(left_shoulder[:2], right_shoulder[:2]),
            math.dist(shoulder_mid, hip_mid),
        )
        wrists = (left_wrist, right_wrist)
        if all(float(wrist[1]) < float(nose[1]) - 0.12 * scale for wrist in wrists):
            return "join"
        if (
            math.dist(left_wrist[:2], right_wrist[:2]) < 0.48 * scale
            and max(float(wrist[1]) for wrist in wrists) < hip_mid[1]
        ):
            return "accept"
        shoulder_left = min(float(left_shoulder[0]), float(right_shoulder[0]))
        shoulder_right = max(float(left_shoulder[0]), float(right_shoulder[0]))
        shoulder_y = shoulder_mid[1]
        points_left = any(
            float(wrist[0]) < shoulder_left - 0.65 * scale
            and abs(float(wrist[1]) - shoulder_y) < 0.7 * scale
            for wrist in wrists
        )
        points_right = any(
            float(wrist[0]) > shoulder_right + 0.65 * scale
            and abs(float(wrist[1]) - shoulder_y) < 0.7 * scale
            for wrist in wrists
        )
        if points_left != points_right:
            return "left" if points_left else "right"
    except (TypeError, ValueError):
        return None
    return None


class GestureController:
    """Debounce pose gestures, own menu control, and optionally gate joining."""

    def __init__(
        self,
        *,
        join_required: bool = False,
        claim_hold: float = 0.65,
        action_hold: float = 0.30,
        controller_timeout: float = 2.0,
    ) -> None:
        if min(float(claim_hold), float(action_hold), float(controller_timeout)) <= 0:
            raise ValueError("gesture timings must be positive")
        self.join_required = bool(join_required)
        self.claim_hold = float(claim_hold)
        self.action_hold = float(action_hold)
        self.controller_timeout = float(controller_timeout)
        self.controller_id: Any = None
        self.joined_track_ids: set[Any] = set()
        self._claim_started: dict[Any, float] = {}
        self._controller_seen = 0.0
        self._candidate: str | None = None
        self._candidate_started = 0.0
        self._armed = True

    def reset(self) -> None:
        self.controller_id = None
        self.joined_track_ids.clear()
        self._claim_started.clear()
        self._controller_seen = 0.0
        self._candidate = None
        self._candidate_started = 0.0
        self._armed = True

    def admits(self, track_id: Any) -> bool:
        return not self.join_required or track_id in self.joined_track_ids

    def update(self, people: list[dict[str, Any]], now: float) -> list[dict[str, Any]]:
        timestamp = float(now)
        if not math.isfinite(timestamp) or timestamp < 0:
            raise ValueError("now must be a finite non-negative number")
        poses = {
            person.get("track_id"): classify_pose_gesture(person.get("keypoints", ()))
            for person in people
            if person.get("track_id") is not None
        }
        for track_id in self._claim_started.keys() - poses.keys():
            del self._claim_started[track_id]
        events: list[dict[str, Any]] = []
        for track_id, gesture in poses.items():
            if gesture != "join":
                self._claim_started.pop(track_id, None)
                continue
            started = self._claim_started.setdefault(track_id, timestamp)
            if timestamp - started < self.claim_hold:
                continue
            if track_id not in self.joined_track_ids:
                self.joined_track_ids.add(track_id)
                events.append({"track_id": track_id, "action": "join"})
            if self.controller_id is None:
                self.controller_id = track_id
                self._controller_seen = timestamp
                self._armed = False
                events.append({"track_id": track_id, "action": "claim"})

        visible_ids = set(poses)
        if self.controller_id in visible_ids:
            self._controller_seen = timestamp
        elif (
            self.controller_id is not None
            and timestamp - self._controller_seen >= self.controller_timeout
        ):
            self.controller_id = None
            self._candidate = None
            self._armed = True
        if self.controller_id is None:
            return events

        gesture = poses.get(self.controller_id)
        if gesture in (None, "join"):
            self._candidate = None
            if gesture is None:
                self._armed = True
            return events
        if gesture != self._candidate:
            self._candidate = gesture
            self._candidate_started = timestamp
        elif self._armed and timestamp - self._candidate_started >= self.action_hold:
            events.append({"track_id": self.controller_id, "action": gesture})
            self._armed = False
        return events


class PoseEngine:
    """Lazy Ultralytics YOLO pose tracker returning plain Python data."""

    def __init__(
        self,
        model: str | os.PathLike[str] | None = None,
        *,
        imgsz: int = 640,
        device: str | int | None = None,
        max_people: int = 4,
        smooth_frames: int = 3,
    ) -> None:
        configured_model = model if model is not None else os.environ.get("OPENDANCE_MODEL")
        if configured_model is None:
            configured_model = default_cache_dir() / "models" / "yolo26n-pose.pt"
        model_name = os.fspath(configured_model)
        if not model_name.strip():
            raise ValueError("model must not be empty")
        if isinstance(imgsz, bool) or not isinstance(imgsz, int) or imgsz <= 0:
            raise ValueError("imgsz must be a positive integer")
        if (
            isinstance(max_people, bool)
            or not isinstance(max_people, int)
            or max_people <= 0
        ):
            raise ValueError("max_people must be a positive integer")
        if isinstance(smooth_frames, bool) or not isinstance(smooth_frames, int) or smooth_frames < 0:
            raise ValueError("smooth_frames must be a non-negative integer")

        self.model_name = model_name
        self.imgsz = imgsz
        self.device = device
        self.max_people = max_people
        self.smooth_frames = smooth_frames
        self._pose_filter = (
            TemporalPoseFilter(confirm_frames=smooth_frames) if smooth_frames else None
        )
        self._model: Any | None = None
        self._lock = threading.RLock()
        self._sequence = 0
        self._tracking_generation = 0
        self.backend_name = "YOLO26"
        self.tracker_name = "bytetrack.yaml"

    @property
    def is_loaded(self) -> bool:
        return self._model is not None

    def load(self) -> None:
        """Load model weights now; otherwise the first ``process`` call does it."""

        with self._lock:
            self._ensure_model()

    def _ensure_model(self) -> Any:
        if self._model is None:
            # Prevent Ultralytics' import-time online probe; known model assets
            # can still be downloaded directly when they are not cached.
            os.environ.setdefault("YOLO_OFFLINE", "1")
            try:
                import ultralytics
            except ModuleNotFoundError as exc:
                raise RuntimeError(
                    "Pose inference requires Ultralytics; install the vision/ML "
                    "dependencies before using PoseEngine"
                ) from exc
            # Keep webcam inference private without changing the user's global
            # Ultralytics preference file. YOLO is imported lazily after this.
            dict.__setitem__(ultralytics.settings, "sync", False)
            try:
                from ultralytics.utils.events import events
            except ImportError:  # Preserve compatibility with minimal/test builds.
                events = None
            if events is not None:
                events.enabled = False
                events.events.clear()
            if "://" not in self.model_name:
                Path(self.model_name).parent.mkdir(parents=True, exist_ok=True)
            self._model = ultralytics.YOLO(self.model_name)
        return self._model

    def reset_tracking(self) -> None:
        """Clear tracker history without racing an in-flight inference call."""

        with self._lock:
            if self._pose_filter is not None:
                self._pose_filter.reset()
            if self._model is not None:
                predictor = getattr(self._model, "predictor", None)
                trackers = getattr(predictor, "trackers", None)
                try:
                    if trackers and all(callable(getattr(item, "reset", None)) for item in trackers):
                        for tracker in trackers:
                            tracker.reset()
                        if hasattr(predictor, "vid_path"):
                            predictor.vid_path = [None] * len(trackers)
                    elif predictor is not None:
                        self._model.predictor = None
                except Exception:
                    # A fresh predictor is slower once, but cannot retain a
                    # partially-reset tracker after an implementation change.
                    self._model.predictor = None
            self._tracking_generation += 1

    def reset_smoothing(self) -> None:
        """Forget pose-filter history while preserving tracker identities."""

        with self._lock:
            if self._pose_filter is not None:
                self._pose_filter.reset()

    def process(
        self, frame: Any, *, timestamp_ms: float | int | None = None
    ) -> dict[str, Any]:
        """Track every person in one BGR/RGB image and return normalized COCO-17.

        ``frame`` only needs a NumPy-compatible ``shape``; importing NumPy is
        intentionally left to the caller/capture implementation.
        """

        shape = getattr(frame, "shape", None)
        if shape is None or len(shape) < 2:
            raise TypeError("frame must be an image with a (height, width, ...) shape")
        height, width = int(shape[0]), int(shape[1])
        if height <= 0 or width <= 0:
            raise ValueError("frame width and height must be positive")
        if timestamp_ms is not None:
            timestamp_ms = float(timestamp_ms)
            if not math.isfinite(timestamp_ms) or timestamp_ms < 0:
                raise ValueError("timestamp_ms must be a finite non-negative number")

        captured_ns = time.monotonic_ns()
        total_started = time.perf_counter()
        with self._lock:
            model = self._ensure_model()
            inference_started = time.perf_counter()
            kwargs: dict[str, Any] = {
                "persist": True,
                "tracker": "bytetrack.yaml",
                "imgsz": self.imgsz,
                "max_det": self.max_people,
                "save": False,
                "verbose": False,
            }
            if self.device is not None:
                kwargs["device"] = self.device
            results = model.track(frame, **kwargs)
            result = results[0] if results else None
            people = [
                person
                for person in self._people(result, width, height)
                if is_player_detection(person)
            ]
            if self._pose_filter is not None:
                people = self._pose_filter.update(people)
            device = str(
                getattr(getattr(model, "predictor", None), "device", None)
                or self.device
                or "unknown"
            )
            inference_ms = (time.perf_counter() - inference_started) * 1000.0
            self._sequence += 1
            sequence = self._sequence
            generation = self._tracking_generation

        timing = {
            "inference_ms": round(inference_ms, 3),
            "total_ms": round((time.perf_counter() - total_started) * 1000.0, 3),
        }
        return {
            "type": "poses",
            "seq": sequence,
            "tracking_generation": generation,
            "captured_ns": captured_ns,
            "timestamp_ms": timestamp_ms,
            "source": {"width": width, "height": height},
            "device": device,
            "backend": self.backend_name,
            "people": people,
            "inference_ms": timing["inference_ms"],
            "timing": timing,
        }

    def infer(
        self, frame: Any, *, captured_ms: float | int | None = None
    ) -> dict[str, Any]:
        """Compatibility spelling used by the live camera thread."""

        return self.process(frame, timestamp_ms=captured_ms)

    def _people(self, result: Any, width: int, height: int) -> list[dict[str, Any]]:
        if result is None:
            return []
        boxes = getattr(result, "boxes", None)
        keypoints = getattr(result, "keypoints", None)
        if boxes is None or keypoints is None:
            return []

        raw_boxes = _tolist(getattr(boxes, "xyxy", None))
        raw_points = _tolist(getattr(keypoints, "data", None))
        raw_ids = _tolist(getattr(boxes, "id", None))
        raw_confidences = _tolist(getattr(boxes, "conf", None))
        count = min(len(raw_boxes), len(raw_points), self.max_people)
        people: list[dict[str, Any]] = []

        for index in range(count):
            points = raw_points[index]
            if len(points) != len(COCO17_KEYPOINTS):
                raise RuntimeError(
                    f"Pose model returned {len(points)} keypoints; COCO-17 is required"
                )
            x1, y1, x2, y2 = (float(value) for value in raw_boxes[index][:4])
            normalized_points = []
            for point in points:
                if len(point) < 2:
                    raise RuntimeError("Pose model returned a malformed keypoint")
                confidence = point[2] if len(point) > 2 else 0.0
                normalized_points.append(
                    [_unit(point[0] / width), _unit(point[1] / height), _unit(confidence)]
                )

            track_id = None
            if index < len(raw_ids) and raw_ids[index] is not None:
                track_id = int(raw_ids[index])
            confidence = (
                _unit(raw_confidences[index])
                if index < len(raw_confidences)
                else 0.0
            )
            left, top = _unit(x1 / width), _unit(y1 / height)
            right, bottom = _unit(x2 / width), _unit(y2 / height)
            people.append(
                {
                    "track_id": track_id,
                    "confidence": confidence,
                    "bbox": [
                        left,
                        top,
                        max(0.0, right - left),
                        max(0.0, bottom - top),
                    ],
                    "keypoints": normalized_points,
                }
            )
        return people


class RTMPoseEngine:
    """Experimental RTMPose Body adapter with short spatial continuity."""

    _INPUT_SIZES = {"lightweight": 416, "balanced": 640, "performance": 640}

    def __init__(
        self,
        *,
        mode: str = "lightweight",
        device: str | int | None = None,
        max_people: int = 4,
        smooth_frames: int = 3,
    ) -> None:
        if mode not in self._INPUT_SIZES:
            raise ValueError("RTMPose mode must be lightweight, balanced, or performance")
        if (
            isinstance(max_people, bool)
            or not isinstance(max_people, int)
            or not 1 <= max_people <= 4
        ):
            raise ValueError("RTMPose max_people must be between 1 and 4")
        if isinstance(smooth_frames, bool) or not isinstance(smooth_frames, int) or smooth_frames < 0:
            raise ValueError("smooth_frames must be a non-negative integer")
        self.mode = mode
        self.device = device
        self.max_people = max_people
        self.smooth_frames = smooth_frames
        self.model_name = f"rtmpose-body-{mode}"
        self.imgsz = self._INPUT_SIZES[mode]
        self.backend_name = f"RTMPOSE {mode.upper()}"
        self.tracker_name = "opendance-spatial-continuity-experimental"
        self._model: Any | None = None
        self._lock = threading.RLock()
        self._sequence = 0
        self._tracking_generation = 0
        self._pose_filter = (
            TemporalPoseFilter(confirm_frames=smooth_frames) if smooth_frames else None
        )
        from .game import PlayerSlots

        self._slots = PlayerSlots(
            max_people, leave_after=0.0, rebind_seconds=1.0, rebind_distance=0.40
        )
        self._slot_generations: dict[int, tuple[float | None, int]] = {}
        self._next_track_id = 1

    @property
    def is_loaded(self) -> bool:
        return self._model is not None

    def load(self) -> None:
        with self._lock:
            self._ensure_model()

    def _ensure_model(self) -> Any:
        if self._model is not None:
            return self._model
        try:
            import onnxruntime as ort
            from rtmlib import Body
        except ModuleNotFoundError as exc:
            raise RuntimeError(
                "RTMPose requires the optional dependencies; run "
                "`uv sync --extra rtmpose`"
            ) from exc

        requested = str(self.device if self.device is not None else "auto").casefold()
        if requested in {"0", "cuda", "cuda:0"}:
            runtime_device = "cuda"
            explicit_cuda = True
        elif requested.isdigit() or requested.startswith("cuda:"):
            raise ValueError("rtmlib currently supports only RTMPose GPU 0")
        elif requested == "cpu":
            runtime_device = "cpu"
            explicit_cuda = False
        elif requested == "auto":
            runtime_device = (
                "cuda"
                if "CUDAExecutionProvider" in ort.get_available_providers()
                else "cpu"
            )
            explicit_cuda = False
        else:
            raise ValueError("RTMPose device must be auto, cpu, cuda, or a GPU index")
        if runtime_device == "cuda":
            try:
                ort.preload_dlls(directory="")
            except (AttributeError, OSError):
                pass
        model = Body(
            mode=self.mode,
            to_openpose=False,
            backend="onnxruntime",
            device=runtime_device,
        )
        sessions = (
            getattr(getattr(model, "det_model", None), "session", None),
            getattr(getattr(model, "pose_model", None), "session", None),
        )
        using_cuda = all(
            session is not None
            and "CUDAExecutionProvider" in session.get_providers()
            for session in sessions
        )
        if explicit_cuda and not using_cuda:
            raise RuntimeError(
                "RTMPose CUDA was requested but ONNX Runtime fell back to CPU; "
                "check the NVIDIA driver and CUDA provider libraries"
            )
        self.device = "cuda" if using_cuda else "cpu"
        self._model = model
        return model

    def reset_tracking(self) -> None:
        with self._lock:
            self._slots.clear()
            self._slot_generations.clear()
            self._next_track_id = 1
            if self._pose_filter is not None:
                self._pose_filter.reset()
            self._tracking_generation += 1

    def reset_smoothing(self) -> None:
        with self._lock:
            if self._pose_filter is not None:
                self._pose_filter.reset()

    def process(
        self, frame: Any, *, timestamp_ms: float | int | None = None
    ) -> dict[str, Any]:
        shape = getattr(frame, "shape", None)
        if shape is None or len(shape) < 2:
            raise TypeError("frame must be an image with a (height, width, ...) shape")
        height, width = int(shape[0]), int(shape[1])
        if height <= 0 or width <= 0:
            raise ValueError("frame width and height must be positive")
        if timestamp_ms is not None:
            timestamp_ms = float(timestamp_ms)
            if not math.isfinite(timestamp_ms) or timestamp_ms < 0:
                raise ValueError("timestamp_ms must be a finite non-negative number")

        captured_ns = time.monotonic_ns()
        total_started = time.perf_counter()
        with self._lock:
            model = self._ensure_model()
            inference_started = time.perf_counter()
            boxes = _tolist(model.det_model(frame))[: self.max_people]
            if boxes:
                keypoints, scores = model.pose_model(
                    frame, bboxes=[box[:4] for box in boxes]
                )
                people = self._people(boxes, keypoints, scores, width, height)
                people = [person for person in people if is_player_detection(person)]
            else:
                people = []
            now = timestamp_ms / 1_000.0 if timestamp_ms is not None else time.monotonic()
            transient = {
                self._sequence * self.max_people + index: person["keypoints"]
                for index, person in enumerate(people, 1)
            }
            self._slots.update(transient, now)
            track_ids: dict[int, int] = {}
            for slot in self._slots.visible_slots:
                marker, track_id = self._slot_generations.get(slot.index, (None, 0))
                if marker != slot.joined_at:
                    track_id = self._next_track_id
                    self._next_track_id += 1
                    self._slot_generations[slot.index] = (slot.joined_at, track_id)
                if slot.track_id is not None:
                    track_ids[int(slot.track_id)] = track_id
            for transient_id, person in zip(transient, people):
                person["track_id"] = track_ids.get(transient_id)
            people = [person for person in people if person["track_id"] is not None]
            if self._pose_filter is not None:
                people = self._pose_filter.update(people)
            inference_ms = (time.perf_counter() - inference_started) * 1_000.0
            self._sequence += 1
            sequence = self._sequence
            generation = self._tracking_generation

        timing = {
            "inference_ms": round(inference_ms, 3),
            "total_ms": round((time.perf_counter() - total_started) * 1_000.0, 3),
        }
        return {
            "type": "poses",
            "seq": sequence,
            "tracking_generation": generation,
            "captured_ns": captured_ns,
            "timestamp_ms": timestamp_ms,
            "source": {"width": width, "height": height},
            "device": str(self.device),
            "backend": self.backend_name,
            "people": people,
            "inference_ms": timing["inference_ms"],
            "timing": timing,
        }

    def infer(
        self, frame: Any, *, captured_ms: float | int | None = None
    ) -> dict[str, Any]:
        return self.process(frame, timestamp_ms=captured_ms)

    def _people(
        self,
        boxes: Any,
        keypoints: Any,
        scores: Any,
        width: int,
        height: int,
    ) -> list[dict[str, Any]]:
        raw_points, raw_scores = _tolist(keypoints), _tolist(scores)
        people = []
        for box, points, confidence in zip(boxes, raw_points, raw_scores):
            if len(points) != len(COCO17_KEYPOINTS):
                raise RuntimeError(
                    f"RTMPose returned {len(points)} keypoints; COCO-17 is required"
                )
            x1, y1, x2, y2 = (float(value) for value in box[:4])
            left, top = _unit(x1 / width), _unit(y1 / height)
            right, bottom = _unit(x2 / width), _unit(y2 / height)
            normalized = [
                [
                    _unit(float(point[0]) / width),
                    _unit(float(point[1]) / height),
                    _unit(confidence[index]),
                ]
                for index, point in enumerate(points)
            ]
            # rtmlib 0.0.16 strips YOLOX scores after applying its threshold.
            # Four-coordinate boxes therefore carry the conservative guaranteed
            # confidence floor; custom detectors may preserve the actual score.
            detector_confidence = float(box[4]) if len(box) > 4 else 0.30
            people.append(
                {
                    "track_id": None,
                    "confidence": _unit(detector_confidence),
                    "bbox": [left, top, max(0.0, right - left), max(0.0, bottom - top)],
                    "keypoints": normalized,
                }
            )
        return people


def create_pose_engine(backend: str = "yolo26", **kwargs: Any) -> Any:
    """Create the selected optional pose implementation."""

    name = str(backend).strip().casefold()
    rtmpose_mode = kwargs.pop("rtmpose_mode", "lightweight")
    if name == "yolo26":
        return PoseEngine(**kwargs)
    if name == "rtmpose":
        if kwargs.pop("model", None) is not None:
            raise ValueError("--model applies to YOLO26; use --rtmpose-mode for RTMPose")
        kwargs.pop("imgsz", None)
        return RTMPoseEngine(mode=rtmpose_mode, **kwargs)
    raise ValueError("pose backend must be yolo26 or rtmpose")


__all__ = [
    "COCO17_KEYPOINTS",
    "GestureController",
    "PoseEngine",
    "RTMPoseEngine",
    "TemporalPoseFilter",
    "classify_pose_gesture",
    "create_pose_engine",
    "is_player_detection",
]
