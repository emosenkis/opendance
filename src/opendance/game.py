"""Core choreography, pose matching, player assignment, and scoring.

The module deliberately has no GUI or ML dependencies.  Camera backends only need
to supply a mapping of tracker ids to COCO-17 keypoints.
"""

from __future__ import annotations

from bisect import bisect_right
from copy import deepcopy
from dataclasses import dataclass, field
from itertools import product
import json
import math
from pathlib import Path
from typing import Hashable, Mapping, Sequence


Point = tuple[float, float, float]
Pose = tuple[Point, ...]
TrackId = Hashable

COCO_KEYPOINTS = (
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

LEFT_RIGHT_PAIRS = ((1, 2), (3, 4), (5, 6), (7, 8), (9, 10), (11, 12), (13, 14), (15, 16))

GRADE_THRESHOLDS = (
    ("PERFECT", 0.90, 1_000),
    ("GREAT", 0.78, 800),
    ("GOOD", 0.62, 600),
    ("OK", 0.45, 300),
    ("MISS", 0.00, 0),
)

_STAR_THRESHOLDS = (0.20, 0.40, 0.60, 0.75, 0.90)
_DEFAULT_CATALOG = Path(__file__).with_name("content") / "songs.json"


def load_catalog(path: str | Path | None = None) -> list[dict]:
    """Load and lightly validate the bundled (or an extracted) song catalog."""

    catalog_path = Path(path) if path is not None else _DEFAULT_CATALOG
    with catalog_path.open(encoding="utf-8") as source:
        payload = json.load(source)
    songs = (
        payload.get("songs", [payload] if "id" in payload else None)
        if isinstance(payload, dict)
        else payload
    )
    if not isinstance(songs, list):
        raise ValueError("song catalog must contain a 'songs' list")
    required = {"id", "title", "duration"}
    for song in songs:
        if not isinstance(song, dict) or not required.issubset(song):
            raise ValueError(f"invalid song entry; required fields are {sorted(required)}")
        if float(song["duration"]) <= 0 or (
            song.get("bpm") is not None and float(song["bpm"]) <= 0
        ):
            raise ValueError(f"song {song.get('id', '<unknown>')} has invalid timing")
        moves = song.get("moves", [])
        timeline = song.get("choreography", {}).get("timeline", [])
        if not moves and not timeline:
            raise ValueError(f"song {song['id']} has no choreography")
        if not isinstance(moves, list):
            raise ValueError(f"song {song['id']} has an invalid move timeline")
        if any(float(a["time"]) > float(b["time"]) for a, b in zip(moves, moves[1:])):
            raise ValueError(f"song {song['id']} moves are not sorted")
    return deepcopy(songs)


def get_song(song_id: str, catalog: Sequence[dict] | None = None) -> dict:
    """Return one song by id, raising ``KeyError`` when it is unavailable."""

    for song in catalog if catalog is not None else load_catalog():
        if song.get("id") == song_id:
            return deepcopy(song)
    raise KeyError(song_id)


def _coerce_pose(points: Sequence[Sequence[float] | Mapping[str, float]]) -> Pose:
    if len(points) != 17:
        raise ValueError(f"expected 17 COCO keypoints, got {len(points)}")
    result: list[Point] = []
    for point in points:
        if isinstance(point, Mapping):
            x = float(point["x"])
            y = float(point["y"])
            confidence = float(point.get("confidence", point.get("score", 1.0)))
        else:
            if len(point) < 2:
                raise ValueError("each keypoint needs x and y")
            x, y = float(point[0]), float(point[1])
            confidence = float(point[2]) if len(point) > 2 else 1.0
        result.append((x, y, max(0.0, min(1.0, confidence))))
    return tuple(result)


def _base_pose() -> list[list[float]]:
    # A front-facing neutral skeleton in normalized choreography coordinates.
    return [
        [0.00, 0.08, 1.0],
        [-0.035, 0.065, 1.0],
        [0.035, 0.065, 1.0],
        [-0.075, 0.085, 1.0],
        [0.075, 0.085, 1.0],
        [-0.17, 0.27, 1.0],
        [0.17, 0.27, 1.0],
        [-0.23, 0.46, 1.0],
        [0.23, 0.46, 1.0],
        [-0.18, 0.62, 1.0],
        [0.18, 0.62, 1.0],
        [-0.11, 0.57, 1.0],
        [0.11, 0.57, 1.0],
        [-0.12, 0.78, 1.0],
        [0.12, 0.78, 1.0],
        [-0.13, 0.99, 1.0],
        [0.13, 0.99, 1.0],
    ]


def _set(pose: list[list[float]], index: int, x: float, y: float) -> None:
    pose[index][0] = x
    pose[index][1] = y


def _reflect_template(pose: Pose) -> Pose:
    reflected = [(1.0 - x, y, c) for x, y, c in pose]
    for left, right in LEFT_RIGHT_PAIRS:
        reflected[left], reflected[right] = reflected[right], reflected[left]
    return tuple(reflected)


def named_pose(name: str, phase: float = 0.0) -> Pose:
    """Generate a normalized COCO-17 pose for a named dance move.

    ``phase`` is in the range 0..1 and adds a small rhythmic bounce, allowing the
    same definitions to animate both target silhouettes and generated avatars.
    Right-handed variants are mirrored from their left-handed counterparts.
    """

    move = name.strip().lower().replace("-", "_").replace(" ", "_")
    mirrored_names = {
        "step_right": "step_left",
        "reach_right": "reach_left",
        "disco_right": "disco_left",
        "punch_right": "punch_left",
        "wave_right": "wave_left",
        "groove_right": "groove_left",
        "side_right": "side_left",
    }
    if move in mirrored_names:
        return _reflect_template(named_pose(mirrored_names[move], phase))

    pose = _base_pose()
    bounce = 0.018 * math.sin(2.0 * math.pi * (float(phase) % 1.0))
    for index in range(5, 17):
        pose[index][1] += bounce

    if move in {"ready", "neutral"}:
        _set(pose, 7, -0.25, 0.43 + bounce)
        _set(pose, 8, 0.25, 0.43 + bounce)
        _set(pose, 9, -0.12, 0.56 + bounce)
        _set(pose, 10, 0.12, 0.56 + bounce)
    elif move in {"step_left", "side_left"}:
        _set(pose, 7, -0.31, 0.38 + bounce)
        _set(pose, 9, -0.40, 0.30 + bounce)
        _set(pose, 8, 0.20, 0.47 + bounce)
        _set(pose, 10, 0.08, 0.58 + bounce)
        _set(pose, 13, -0.23, 0.78 + bounce)
        _set(pose, 15, -0.38, 0.96 + bounce)
    elif move == "reach_left":
        _set(pose, 7, -0.19, 0.10 + bounce)
        _set(pose, 9, -0.24, -0.12 + bounce)
        _set(pose, 8, 0.25, 0.45 + bounce)
        _set(pose, 10, 0.12, 0.57 + bounce)
    elif move == "disco_left":
        _set(pose, 7, -0.27, 0.10 + bounce)
        _set(pose, 9, -0.38, -0.08 + bounce)
        _set(pose, 8, 0.26, 0.43 + bounce)
        _set(pose, 10, 0.10, 0.57 + bounce)
        _set(pose, 13, -0.20, 0.78 + bounce)
        _set(pose, 15, -0.30, 0.97 + bounce)
    elif move == "punch_left":
        _set(pose, 7, -0.34, 0.30 + bounce)
        _set(pose, 9, -0.55, 0.28 + bounce)
        _set(pose, 8, 0.20, 0.38 + bounce)
        _set(pose, 10, 0.02, 0.39 + bounce)
    elif move == "wave_left":
        wave = 0.035 * math.sin(4.0 * math.pi * (float(phase) % 1.0))
        _set(pose, 7, -0.26, 0.12 + bounce)
        _set(pose, 9, -0.38 + wave, 0.03 + bounce)
        _set(pose, 8, 0.24, 0.44 + bounce)
        _set(pose, 10, 0.12, 0.58 + bounce)
    elif move == "groove_left":
        _set(pose, 7, -0.31, 0.41 + bounce)
        _set(pose, 9, -0.39, 0.54 + bounce)
        _set(pose, 8, 0.19, 0.38 + bounce)
        _set(pose, 10, 0.03, 0.43 + bounce)
        _set(pose, 11, -0.17, 0.57 + bounce)
        _set(pose, 12, 0.05, 0.57 + bounce)
        _set(pose, 13, -0.24, 0.78 + bounce)
        _set(pose, 15, -0.31, 0.98 + bounce)
    elif move == "clap":
        _set(pose, 7, -0.14, 0.35 + bounce)
        _set(pose, 8, 0.14, 0.35 + bounce)
        _set(pose, 9, -0.025, 0.34 + bounce)
        _set(pose, 10, 0.025, 0.34 + bounce)
    elif move == "cross":
        _set(pose, 7, -0.13, 0.38 + bounce)
        _set(pose, 8, 0.13, 0.38 + bounce)
        _set(pose, 9, 0.16, 0.50 + bounce)
        _set(pose, 10, -0.16, 0.50 + bounce)
    elif move == "squat":
        _set(pose, 7, -0.30, 0.43 + bounce)
        _set(pose, 8, 0.30, 0.43 + bounce)
        _set(pose, 9, -0.40, 0.38 + bounce)
        _set(pose, 10, 0.40, 0.38 + bounce)
        _set(pose, 11, -0.13, 0.66 + bounce)
        _set(pose, 12, 0.13, 0.66 + bounce)
        _set(pose, 13, -0.25, 0.79 + bounce)
        _set(pose, 14, 0.25, 0.79 + bounce)
        _set(pose, 15, -0.34, 0.94 + bounce)
        _set(pose, 16, 0.34, 0.94 + bounce)
    elif move == "star":
        _set(pose, 7, -0.30, 0.14 + bounce)
        _set(pose, 8, 0.30, 0.14 + bounce)
        _set(pose, 9, -0.45, -0.02 + bounce)
        _set(pose, 10, 0.45, -0.02 + bounce)
        _set(pose, 13, -0.22, 0.78 + bounce)
        _set(pose, 14, 0.22, 0.78 + bounce)
        _set(pose, 15, -0.36, 0.98 + bounce)
        _set(pose, 16, 0.36, 0.98 + bounce)
    else:
        raise ValueError(f"unknown dance move: {name!r}")
    # Public poses use the same screen-normalized 0..1 coordinate space as the
    # live vision and extraction pipelines.  Internal templates are centered at
    # zero simply because that makes authoring symmetric moves less error-prone.
    return tuple(
        (
            max(0.02, min(0.98, float(x) + 0.5)),
            max(0.02, min(0.98, float(y))),
            float(c),
        )
        for x, y, c in pose
    )


def interpolate_pose(first: Sequence, second: Sequence, amount: float) -> Pose:
    """Linearly interpolate between two COCO-17 poses."""

    a, b = _coerce_pose(first), _coerce_pose(second)
    t = max(0.0, min(1.0, float(amount)))
    return tuple(
        (
            ax + (bx - ax) * t,
            ay + (by - ay) * t,
            ac + (bc - ac) * t,
        )
        for (ax, ay, ac), (bx, by, bc) in zip(a, b)
    )


_EXTRACTED_CACHE: dict[
    tuple[int, str], tuple[object, tuple[tuple[float, Pose], ...]]
] = {}
_TIMELINE_CACHE: dict[
    int, tuple[object, tuple[float, ...], tuple[Mapping, ...]]
] = {}


def _frame_time(frame: Mapping) -> float:
    if "time" in frame:
        return float(frame["time"])
    if "timestamp_ms" in frame:
        return float(frame["timestamp_ms"]) / 1_000.0
    if "time_ms" in frame:
        return float(frame["time_ms"]) / 1_000.0
    raise ValueError("choreography frame has no timestamp")


def _frame_pose(
    frame: Mapping,
    lead_track_id: object,
    dancer_index: int | None = None,
) -> Pose | None:
    direct = frame.get("keypoints", frame.get("pose"))
    if direct is not None:
        try:
            return _coerce_pose(direct)
        except (KeyError, TypeError, ValueError):
            return None
    people = frame.get("people", ())
    if not isinstance(people, Sequence) or isinstance(people, (str, bytes)):
        return None
    selected = None
    role_frame = dancer_index is not None and any(
        isinstance(person, Mapping) and person.get("dancer_index") is not None
        for person in people
    )
    if role_frame:
        selected = next(
            (
                person
                for person in people
                if isinstance(person, Mapping)
                and person.get("dancer_index") is not None
                and int(person["dancer_index"]) == dancer_index
            ),
            None,
        )
    if not role_frame and lead_track_id is not None:
        selected = next(
            (
                person
                for person in people
                if isinstance(person, Mapping)
                and str(person.get("track_id")) == str(lead_track_id)
            ),
            None,
        )
    if selected is None and dancer_index is None:
        selected = next((person for person in people if isinstance(person, Mapping)), None)
    if selected is None:
        return None
    direct = selected.get("keypoints", selected.get("pose"))
    try:
        return _coerce_pose(direct) if direct is not None else None
    except (KeyError, TypeError, ValueError):
        return None


def choreography_dancers(song: Mapping) -> tuple[object | None, ...]:
    """Return extracted dancer track ids in authored left-to-right order.

    Old single-lead song packages remain a one-dancer choreography.
    """

    choreography = song.get("choreography", {})
    if not isinstance(choreography, Mapping):
        return (None,)
    configured = choreography.get("dancers")
    if isinstance(configured, Sequence) and not isinstance(configured, (str, bytes)):
        track_ids = [
            dancer.get("track_id") if isinstance(dancer, Mapping) else dancer
            for dancer in configured
        ]
        if track_ids:
            return tuple(track_ids)
    configured = choreography.get("dancer_track_ids")
    if isinstance(configured, Sequence) and not isinstance(configured, (str, bytes)):
        track_ids = [track_id for track_id in configured if track_id is not None]
        if track_ids:
            return tuple(track_ids)
    lead = choreography.get("lead_track_id", song.get("lead_track_id"))
    return (lead,) if lead is not None else (None,)


def _extracted_keyframes(
    song: Mapping, dancer_index: int | None = None
) -> tuple[tuple[float, Pose], ...]:
    choreography = song.get("choreography", {})
    if not isinstance(choreography, Mapping):
        return ()
    timeline = choreography.get("timeline", choreography.get("frames", ()))
    if not isinstance(timeline, Sequence) or isinstance(timeline, (str, bytes)):
        return ()
    dancers = choreography_dancers(song)
    role_index: int | None = None
    if dancer_index is None:
        role_index = choreography.get("lead_dancer_index")
        if role_index is not None:
            role_index = int(role_index)
            if not 0 <= role_index < len(dancers):
                raise IndexError(f"lead dancer index {role_index} is out of range")
            track_id = dancers[role_index]
        else:
            track_id = choreography.get("lead_track_id", song.get("lead_track_id"))
            if track_id is None:
                track_id = dancers[0]
    else:
        if not 0 <= int(dancer_index) < len(dancers):
            raise IndexError(f"dancer index {dancer_index} is out of range")
        role_index = int(dancer_index)
        track_id = dancers[role_index]
    selector = f"role:{role_index}" if role_index is not None else f"track:{track_id}"
    cache_key = (id(timeline), selector)
    cached = _EXTRACTED_CACHE.get(cache_key)
    if cached is not None and cached[0] is timeline:
        return cached[1]
    keyframes: list[tuple[float, Pose]] = []
    for frame in timeline:
        if not isinstance(frame, Mapping):
            continue
        pose = _frame_pose(frame, track_id, role_index)
        if pose is None:
            continue
        try:
            timestamp = _frame_time(frame)
        except (TypeError, ValueError):
            continue
        if not keyframes or timestamp > keyframes[-1][0]:
            keyframes.append((timestamp, pose))
        elif timestamp == keyframes[-1][0]:
            keyframes[-1] = (timestamp, pose)
    result = tuple(keyframes)
    if len(_EXTRACTED_CACHE) >= 8:
        _EXTRACTED_CACHE.pop(next(iter(_EXTRACTED_CACHE)))
    _EXTRACTED_CACHE[cache_key] = (timeline, result)
    return result


def _move_pose(move: Mapping, phase: float = 0.0) -> Pose:
    explicit = move.get("keypoints", move.get("pose"))
    if explicit is not None:
        return _coerce_pose(explicit)
    return named_pose(str(move["name"]), phase)


def target_pose(
    song: Mapping, time_s: float, dancer_index: int | None = None
) -> Pose:
    """Return one dancer's smoothly interpolated pose at ``time_s``.

    Omitting ``dancer_index`` preserves the original lead-dancer behaviour.
    """

    moves = song.get("moves", ())
    if moves:
        if dancer_index not in (None, 0):
            raise IndexError(f"dancer index {dancer_index} is out of range")
        times = [float(move["time"]) for move in moves]
        index = max(0, bisect_right(times, float(time_s)) - 1)
        if index >= len(moves) - 1:
            return _move_pose(moves[-1], 1.0)
        start, end = moves[index], moves[index + 1]
        span = max(1e-9, float(end["time"]) - float(start["time"]))
        progress = max(0.0, min(1.0, (float(time_s) - float(start["time"])) / span))
        # Smoothstep avoids robotic stops while preserving exact cue poses.
        blend = progress * progress * (3.0 - 2.0 * progress)
        return interpolate_pose(_move_pose(start, progress), _move_pose(end, progress), blend)

    keyframes = _extracted_keyframes(song, dancer_index)
    if not keyframes:
        raise ValueError("song has no usable move or extracted-pose timeline")
    right = bisect_right(keyframes, float(time_s), key=lambda frame: frame[0])
    if right == 0:
        return keyframes[0][1]
    if right >= len(keyframes):
        return keyframes[-1][1]
    before, after = keyframes[right - 1], keyframes[right]
    progress = (float(time_s) - before[0]) / max(1e-9, after[0] - before[0])
    return interpolate_pose(before[1], after[1], progress)


def target_poses(song: Mapping, time_s: float) -> tuple[Pose, ...]:
    """Return every authored dancer pose in left-to-right dancer order."""

    if song.get("moves", ()):
        return (target_pose(song, time_s),)
    return tuple(
        target_pose(song, time_s, dancer_index)
        for dancer_index in range(len(choreography_dancers(song)))
    )


def _timeline_at(song: Mapping, time_s: float) -> Mapping | None:
    choreography = song.get("choreography", {})
    if not isinstance(choreography, Mapping):
        return None
    timeline = choreography.get("timeline", choreography.get("frames", ()))
    if not isinstance(timeline, Sequence) or isinstance(timeline, (str, bytes)):
        return None
    cache_key = id(timeline)
    cached = _TIMELINE_CACHE.get(cache_key)
    if cached is None or cached[0] is not timeline:
        timed = []
        for frame in timeline:
            if not isinstance(frame, Mapping):
                continue
            try:
                timed.append((_frame_time(frame), frame))
            except (TypeError, ValueError):
                continue
        timed.sort(key=lambda item: item[0])
        cached = (timeline, tuple(item[0] for item in timed), tuple(item[1] for item in timed))
        if len(_TIMELINE_CACHE) >= 8:
            _TIMELINE_CACHE.pop(next(iter(_TIMELINE_CACHE)))
        _TIMELINE_CACHE[cache_key] = cached
    _, times, frames = cached
    if not times:
        return None
    right = bisect_right(times, float(time_s))
    if right == 0:
        return frames[0]
    if right >= len(times):
        return frames[-1]
    before_is_nearer = (
        float(time_s) - times[right - 1] <= times[right] - float(time_s)
    )
    return frames[right - 1] if before_is_nearer else frames[right]


def _pose_bbox(pose: Pose) -> list[float]:
    visible = [(x, y) for x, y, confidence in pose if confidence >= 0.2]
    if not visible:
        return [0.0, 0.0, 0.0, 0.0]
    left, right = min(x for x, _ in visible), max(x for x, _ in visible)
    top, bottom = min(y for _, y in visible), max(y for _, y in visible)
    return [left, top, right - left, bottom - top]


def target_dancers(song: Mapping, time_s: float) -> list[dict]:
    """Return render-ready dancers, back-to-front, with interpolated poses."""

    poses = target_poses(song, time_s)
    frame = _timeline_at(song, time_s)
    source_people = frame.get("people", ()) if frame is not None else ()
    source_by_role = {
        int(person["dancer_index"]): person
        for person in source_people
        if isinstance(person, Mapping) and person.get("dancer_index") is not None
    }
    if not source_by_role:
        track_ids = choreography_dancers(song)
        source_by_role = {
            index: next(
                (
                    person
                    for person in source_people
                    if isinstance(person, Mapping)
                    and str(person.get("track_id")) == str(track_id)
                ),
                {},
            )
            for index, track_id in enumerate(track_ids)
        }
    order = list(frame.get("render_order", ())) if frame is not None else []
    order = [int(index) for index in order if 0 <= int(index) < len(poses)]
    order.extend(index for index in range(len(poses)) if index not in order)
    depth_order = list(frame.get("depth_order", ())) if frame is not None else []
    dancers = []
    for render_rank, index in enumerate(order):
        pose = poses[index]
        source = source_by_role.get(index, {})
        dancers.append(
            {
                "dancer_index": index,
                "track_id": source.get("track_id"),
                "keypoints": [list(point) for point in pose],
                "bbox": list(source.get("bbox", _pose_bbox(pose))),
                "depth_rank": depth_order.index(index) if index in depth_order else None,
                "render_rank": render_rank,
            }
        )
    return dancers


def _pose_anchor_scale(pose: Pose, minimum_confidence: float) -> tuple[float, float, float]:
    def midpoint(left: int, right: int) -> tuple[float, float] | None:
        if pose[left][2] < minimum_confidence or pose[right][2] < minimum_confidence:
            return None
        return ((pose[left][0] + pose[right][0]) / 2, (pose[left][1] + pose[right][1]) / 2)

    hips, shoulders = midpoint(11, 12), midpoint(5, 6)
    anchor = hips or shoulders
    if anchor is None:
        visible = [(x, y) for x, y, confidence in pose if confidence >= minimum_confidence]
        if not visible:
            return 0.0, 0.0, 0.0
        anchor = (sum(point[0] for point in visible) / len(visible), sum(point[1] for point in visible) / len(visible))

    scales: list[float] = []
    if hips and shoulders:
        scales.append(2.0 * math.dist(hips, shoulders))
    if pose[5][2] >= minimum_confidence and pose[6][2] >= minimum_confidence:
        scales.append(math.dist(pose[5][:2], pose[6][:2]))
    if pose[11][2] >= minimum_confidence and pose[12][2] >= minimum_confidence:
        scales.append(1.5 * math.dist(pose[11][:2], pose[12][:2]))
    return anchor[0], anchor[1], max(scales, default=0.0)


def _normalized(pose: Pose, minimum_confidence: float) -> Pose | None:
    center_x, center_y, scale = _pose_anchor_scale(pose, minimum_confidence)
    if scale <= 1e-9:
        return None
    return tuple(((x - center_x) / scale, (y - center_y) / scale, confidence) for x, y, confidence in pose)


def _mirror_for_comparison(pose: Pose, swap_sides: bool) -> Pose:
    result = [(-x, y, confidence) for x, y, confidence in pose]
    if swap_sides:
        for left, right in LEFT_RIGHT_PAIRS:
            result[left], result[right] = result[right], result[left]
    return tuple(result)


def _normalized_similarity(observed: Pose, target: Pose, minimum_confidence: float) -> float:
    weighted_error = 0.0
    weight_sum = 0.0
    for actual, wanted in zip(observed, target):
        weight = min(actual[2], wanted[2])
        if weight < minimum_confidence:
            continue
        weighted_error += math.dist(actual[:2], wanted[:2]) * weight
        weight_sum += weight
    if weight_sum < 4.0 * minimum_confidence:
        return 0.0
    mean_error = weighted_error / weight_sum
    return max(0.0, min(1.0, math.exp(-2.8 * mean_error)))


def pose_similarity(
    observed: Sequence,
    target: Sequence,
    *,
    allow_mirror: bool = True,
    minimum_confidence: float = 0.20,
) -> float:
    """Score pose shape from 0..1, independent of image position and uniform scale.

    When mirroring is enabled both common conventions are accepted: reflected
    screen coordinates and reflected coordinates with COCO left/right labels
    exchanged.  This lets a player naturally follow either a front-facing coach
    or a silhouette without forcing a camera setting.
    """

    actual = _normalized(_coerce_pose(observed), minimum_confidence)
    wanted = _normalized(_coerce_pose(target), minimum_confidence)
    if actual is None or wanted is None:
        return 0.0
    scores = [_normalized_similarity(actual, wanted, minimum_confidence)]
    if allow_mirror:
        scores.extend(
            _normalized_similarity(_mirror_for_comparison(actual, swap), wanted, minimum_confidence)
            for swap in (False, True)
        )
    return max(scores)


def grade_similarity(similarity: float) -> str:
    """Map a 0..1 similarity to a concise arcade judgement."""

    value = max(0.0, min(1.0, float(similarity)))
    return next(name for name, threshold, _ in GRADE_THRESHOLDS if value >= threshold)


def stars_for_accuracy(accuracy: float) -> int:
    """Return the familiar zero-to-five star result for a score ratio."""

    value = max(0.0, min(1.0, float(accuracy)))
    return sum(value >= threshold for threshold in _STAR_THRESHOLDS)


def _pose_center(pose: Pose) -> tuple[float, float]:
    visible = [(x, y, confidence) for x, y, confidence in pose if confidence >= 0.2]
    if not visible:
        return 0.0, 0.0
    weight = sum(point[2] for point in visible)
    return (
        sum(point[0] * point[2] for point in visible) / weight,
        sum(point[1] * point[2] for point in visible) / weight,
    )


def assign_dancers(
    player_positions: Mapping[int, float], dancer_positions: Sequence[float]
) -> dict[int, int]:
    """Match players to nearby dancers while distributing them evenly.

    With fewer players, every player gets a distinct dancer. With more players,
    every dancer is covered before duplicates and group sizes differ by at most
    one. The search is deliberately exhaustive: the game caps both sides at six.
    """

    players = sorted(
        ((slot, float(position)) for slot, position in player_positions.items()),
        key=lambda item: (item[1], item[0]),
    )
    dancers = tuple(float(position) for position in dancer_positions)
    if not dancers:
        raise ValueError("at least one dancer is required")
    if any(not math.isfinite(position) for _, position in players) or any(
        not math.isfinite(position) for position in dancers
    ):
        raise ValueError("player and dancer positions must be finite")
    if not players:
        return {}

    player_count, dancer_count = len(players), len(dancers)

    def valid(candidate: tuple[int, ...]) -> bool:
        counts = [candidate.count(index) for index in range(dancer_count)]
        if player_count <= dancer_count:
            return max(counts) <= 1
        return min(counts) >= 1 and max(counts) - min(counts) <= 1

    assignments = min(
        (
            candidate
            for candidate in product(range(dancer_count), repeat=player_count)
            if valid(candidate)
        ),
        key=lambda candidate: (
            sum(
                abs(player_position - dancers[dancer_index])
                for (_, player_position), dancer_index in zip(players, candidate)
            ),
            candidate,
        ),
    )
    return {slot: dancer for (slot, _), dancer in zip(players, assignments)}


@dataclass
class PlayerSlot:
    """A stable logical player identity, independent of left-to-right order."""

    index: int
    track_id: TrackId | None = None
    pose: Pose | None = None
    last_seen: float = -math.inf
    joined_at: float | None = None
    visible: bool = False
    active: bool = False

    @property
    def player_number(self) -> int:
        return self.index + 1

    @property
    def center(self) -> tuple[float, float]:
        return _pose_center(self.pose) if self.pose is not None else (0.0, 0.0)


class PlayerSlots:
    """Bind volatile tracker ids to up to six stable player slots."""

    def __init__(
        self,
        max_players: int = 6,
        *,
        leave_after: float = 0.75,
        rebind_seconds: float = 4.0,
        rebind_distance: float = 0.40,
    ) -> None:
        if not 1 <= max_players <= 6:
            raise ValueError("max_players must be between 1 and 6")
        self.leave_after = float(leave_after)
        self.rebind_seconds = float(rebind_seconds)
        self.rebind_distance = float(rebind_distance)
        self.slots = [PlayerSlot(index) for index in range(max_players)]

    @property
    def active_slots(self) -> list[PlayerSlot]:
        return [slot for slot in self.slots if slot.active]

    @property
    def visible_slots(self) -> list[PlayerSlot]:
        return [slot for slot in self.slots if slot.visible]

    def slot_for_track(self, track_id: TrackId) -> PlayerSlot | None:
        return next((slot for slot in self.slots if slot.track_id == track_id), None)

    def clear(self) -> None:
        for index, slot in enumerate(self.slots):
            self.slots[index] = PlayerSlot(index)

    def _bind(self, slot: PlayerSlot, track_id: TrackId, pose: Pose, now: float) -> None:
        if slot.joined_at is None:
            slot.joined_at = now
        slot.track_id = track_id
        slot.pose = pose
        slot.last_seen = now
        slot.visible = True
        slot.active = True

    def update(self, tracks: Mapping[TrackId, Sequence], now: float) -> list[PlayerSlot]:
        """Update bindings and return currently active slots in player order.

        Existing tracker ids always win, so players can cross on screen without
        swapping scores.  A changed id is rebound by proximity during a short
        grace window, which handles ordinary tracker loss and re-entry.
        """

        timestamp = float(now)
        incoming = {track_id: _coerce_pose(pose) for track_id, pose in tracks.items()}
        for slot in self.slots:
            slot.visible = False

        unmatched = dict(incoming)
        for slot in self.slots:
            if slot.track_id in unmatched:
                self._bind(slot, slot.track_id, unmatched.pop(slot.track_id), timestamp)

        # Greedily match new ids to nearby recently-lost slots.
        possible: list[tuple[float, int, TrackId]] = []
        for track_id, pose in unmatched.items():
            center = _pose_center(pose)
            for slot in self.slots:
                age = timestamp - slot.last_seen
                if slot.visible or slot.pose is None or not 0.0 <= age <= self.rebind_seconds:
                    continue
                distance = math.dist(center, slot.center)
                if distance <= self.rebind_distance:
                    possible.append((distance, slot.index, track_id))
        claimed_slots: set[int] = set()
        claimed_tracks: set[TrackId] = set()
        for _, slot_index, track_id in sorted(possible, key=lambda item: item[0]):
            if slot_index in claimed_slots or track_id in claimed_tracks:
                continue
            self._bind(self.slots[slot_index], track_id, unmatched[track_id], timestamp)
            claimed_slots.add(slot_index)
            claimed_tracks.add(track_id)
        for track_id in claimed_tracks:
            unmatched.pop(track_id, None)

        # Expired reservations become ordinary free slots.  If all slots are
        # reserved, an actually-departed (not merely flickering) slot is reusable.
        for slot in self.slots:
            if not slot.visible and timestamp - slot.last_seen > self.rebind_seconds:
                slot.track_id = None
                slot.pose = None
                slot.joined_at = None
        for track_id, pose in unmatched.items():
            free = next((slot for slot in self.slots if slot.track_id is None and not slot.visible), None)
            if free is None:
                departed = [
                    slot
                    for slot in self.slots
                    if not slot.visible and timestamp - slot.last_seen > self.leave_after
                ]
                free = min(departed, key=lambda slot: slot.last_seen, default=None)
            if free is not None:
                free.joined_at = timestamp
                self._bind(free, track_id, pose, timestamp)

        for slot in self.slots:
            slot.active = slot.visible or timestamp - slot.last_seen <= self.leave_after
        return self.active_slots


@dataclass
class PlayerScore:
    slot: int
    points: int = 0
    possible_points: int = 0
    judged_moves: int = 0
    combo: int = 0
    max_combo: int = 0
    similarity_total: float = 0.0
    grades: dict[str, int] = field(
        default_factory=lambda: {name: 0 for name, _, _ in GRADE_THRESHOLDS}
    )

    @property
    def accuracy(self) -> float:
        return self.points / self.possible_points if self.possible_points else 0.0

    @property
    def average_similarity(self) -> float:
        return self.similarity_total / self.judged_moves if self.judged_moves else 0.0

    @property
    def stars(self) -> int:
        return stars_for_accuracy(self.accuracy)

    def record(self, similarity: float) -> tuple[str, int]:
        grade = grade_similarity(similarity)
        points = next(points for name, _, points in GRADE_THRESHOLDS if name == grade)
        self.points += points
        self.possible_points += 1_000
        self.judged_moves += 1
        self.similarity_total += similarity
        self.grades[grade] += 1
        if grade == "MISS":
            self.combo = 0
        else:
            self.combo += 1
            self.max_combo = max(self.max_combo, self.combo)
        return grade, points


@dataclass(frozen=True)
class MoveFeedback:
    slot: int
    player_number: int
    move_index: int
    move_name: str
    similarity: float
    grade: str
    points: int
    combo: int
    total_score: int


class GameSession:
    """One song's player lifecycle and once-per-cue scoring state."""

    def __init__(
        self,
        song: Mapping,
        max_players: int = 6,
        *,
        mirror_player_positions: bool = True,
    ) -> None:
        # Dense extracted timelines can be hundreds of MB as Python objects;
        # session code never mutates song data, so keep the nested data shared.
        self.song = dict(song)
        self.moves = sorted(self.song.get("moves", ()), key=lambda move: float(move["time"]))
        self._uses_extracted_timeline = not bool(self.moves)
        if not self.moves:
            # ponytail: extracted video has no semantic moves; one-second pose
            # cues are enough until beat/move segmentation earns its complexity.
            last_cue = -math.inf
            for cue_time, pose in _extracted_keyframes(self.song):
                if cue_time - last_cue >= 1.0:
                    self.moves.append(
                        {"time": cue_time, "name": "FOLLOW", "keypoints": pose}
                    )
                    last_cue = cue_time
        if not self.moves:
            raise ValueError("song has no usable move or extracted-pose timeline")
        self.player_slots = PlayerSlots(max_players=max_players)
        self.scores = {index: PlayerScore(index) for index in range(max_players)}
        self.mirror_player_positions = bool(mirror_player_positions)
        self.dancer_assignments: dict[int, int] = {}
        self._assignment_signature: tuple[int, ...] = ()
        self._last_time: float | None = None
        self._next_move = 0

    @property
    def finished(self) -> bool:
        return self._last_time is not None and self._last_time >= float(self.song["duration"])

    def reset(self) -> None:
        max_players = len(self.player_slots.slots)
        self.player_slots = PlayerSlots(max_players=max_players)
        self.scores = {index: PlayerScore(index) for index in range(max_players)}
        self.dancer_assignments = {}
        self._assignment_signature = ()
        self._last_time = None
        self._next_move = 0

    def current_target(self, time_s: float, player_slot: int | None = None) -> Pose:
        dancer = self.dancer_assignments.get(player_slot) if player_slot is not None else None
        return target_pose(self.song, time_s, dancer)

    def player_targets(self, time_s: float) -> dict[int, Pose]:
        """Return the currently assigned target pose for each active player slot."""

        return {
            slot.index: self.current_target(time_s, slot.index)
            for slot in self.player_slots.active_slots
        }

    def _refresh_dancer_assignments(self, time_s: float) -> None:
        slots = self.player_slots.active_slots
        signature = tuple(slot.index for slot in slots)
        if signature == self._assignment_signature:
            return
        poses = target_poses(self.song, time_s)
        positions = {
            slot.index: 1.0 - slot.center[0] if self.mirror_player_positions else slot.center[0]
            for slot in slots
        }
        self.dancer_assignments = assign_dancers(
            positions, [_pose_center(pose)[0] for pose in poses]
        )
        self._assignment_signature = signature

    def upcoming(self, time_s: float, count: int = 3) -> list[dict]:
        times = [float(move["time"]) for move in self.moves]
        start = bisect_right(times, float(time_s))
        return deepcopy(self.moves[start : start + max(0, int(count))])

    def ui_players(self) -> list[dict]:
        """Return active players in the JSON-like shape consumed by QML."""

        players = []
        for slot in self.player_slots.active_slots:
            score = self.scores[slot.index]
            player = self._score_result(score)
            player.update(
                {
                    "name": f"PLAYER {slot.player_number}",
                    "combo": score.combo,
                    "visible": slot.visible,
                    "dancer_index": self.dancer_assignments.get(slot.index, 0),
                    "keypoints": [list(point) for point in slot.pose] if slot.pose else [],
                }
            )
            players.append(player)
        return players

    def update(self, time_s: float, tracks: Mapping[TrackId, Sequence]) -> list[MoveFeedback]:
        """Update players and judge every move cue crossed since the last frame.

        Only players visible on a cue receive a score and a possible-points entry;
        joining late or leaving therefore never adds phantom misses.
        """

        now = max(0.0, float(time_s))
        previous_joins = {
            slot.index: slot.joined_at for slot in self.player_slots.slots
        }
        self.player_slots.update(tracks, now)
        for slot in self.player_slots.active_slots:
            if slot.joined_at != previous_joins[slot.index]:
                self.scores[slot.index] = PlayerScore(slot.index)
                self._assignment_signature = ()
        self._refresh_dancer_assignments(now)

        if self._last_time is None:
            # A session normally begins at zero.  On seek/start-in-progress, skip
            # expired cues so late joiners are not judged against history.
            if now > 0.25:
                self._next_move = bisect_right([float(move["time"]) for move in self.moves], now)
                self._last_time = now
                return []
            previous = -math.inf
        elif now < self._last_time:
            self._next_move = bisect_right([float(move["time"]) for move in self.moves], now)
            self._last_time = now
            return []
        else:
            previous = self._last_time

        feedback: list[MoveFeedback] = []
        while self._next_move < len(self.moves):
            move = self.moves[self._next_move]
            cue_time = float(move["time"])
            if cue_time > now:
                break
            move_index = self._next_move
            self._next_move += 1
            if cue_time <= previous:
                continue
            for slot in self.player_slots.visible_slots:
                if slot.pose is None:
                    continue
                wanted = (
                    target_pose(
                        self.song,
                        cue_time,
                        self.dancer_assignments.get(slot.index, 0),
                    )
                    if self._uses_extracted_timeline
                    else _move_pose(move)
                )
                similarity = pose_similarity(slot.pose, wanted)
                player_score = self.scores[slot.index]
                grade, points = player_score.record(similarity)
                feedback.append(
                    MoveFeedback(
                        slot=slot.index,
                        player_number=slot.player_number,
                        move_index=move_index,
                        move_name=str(move.get("name", "FOLLOW")),
                        similarity=similarity,
                        grade=grade,
                        points=points,
                        combo=player_score.combo,
                        total_score=player_score.points,
                    )
                )
        self._last_time = now
        return feedback

    @staticmethod
    def _score_result(score: PlayerScore) -> dict:
        return {
            "slot": score.slot,
            "player_number": score.slot + 1,
            "score": score.points,
            "possible_score": score.possible_points,
            "accuracy": score.accuracy,
            "average_similarity": score.average_similarity,
            "stars": score.stars,
            "moves": score.judged_moves,
            "max_combo": score.max_combo,
            "grades": dict(score.grades),
        }

    def results(self) -> dict:
        players = [self._score_result(score) for score in self.scores.values() if score.judged_moves]
        total = sum(player["score"] for player in players)
        possible = sum(player["possible_score"] for player in players)
        accuracy = total / possible if possible else 0.0
        return {
            "song_id": self.song.get("id"),
            "players": players,
            "team_score": total,
            "team_possible_score": possible,
            "team_accuracy": accuracy,
            "team_stars": stars_for_accuracy(accuracy),
        }


# Backwards-friendly spelling for UI code that uses the shorter name.
Session = GameSession
