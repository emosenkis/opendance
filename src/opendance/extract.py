"""Extract a portable, timestamped dance timeline from a video."""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import shutil
import sys
import tempfile
import time
import unicodedata
from bisect import bisect_right
from itertools import combinations, permutations
from pathlib import Path
from statistics import median
from typing import Any, Callable, Sequence

from .game import (
    _coerce_pose,
    _normalized,
    _pose_anchor_scale,
    assign_dancers,
    interpolate_pose,
    pose_similarity,
)
from .vision import COCO17_KEYPOINTS, PoseEngine, TemporalPoseFilter, create_pose_engine


_TIME_TAG = re.compile(r"\[(\d{1,3}):(\d{2}(?:\.\d{1,3})?)\]")
_META_TAG = re.compile(r"^\[([A-Za-z][A-Za-z0-9]*):([^]]*)\]\s*$")
_WORD_TIME_TAG = re.compile(r"<\d{1,3}:\d{2}(?:\.\d{1,3})?>")
_SONG_ID = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
_SCENE_CUT_DELTA = 0.12
_ROLE_DISCONTINUITY = 0.72
_ROLE_JUMP_MARGIN = 0.08
_ROLE_MAX_SPEED = 0.8
ROLE_ASSIGNMENT_METHOD = "bidirectional_track_stitch_with_spatial_shuffle_guard"
MOVE_SCORING_SCHEMA_VERSION = 1
MOVE_SCORING_FEATURE = "coco17-motion-v1"
MOVE_SCORING_PHASES = 12
_MOVE_MIN_SECONDS = 2.0
_MOVE_MAX_SECONDS = 4.0
_MOVE_CONFIDENCE = 0.20
_MOVE_BODY_JOINTS = range(5, len(COCO17_KEYPOINTS))


def _parse_lrc_text(text: str) -> tuple[list[dict[str, Any]], dict[str, str]]:
    lyrics: list[dict[str, Any]] = []
    metadata: dict[str, str] = {}
    offset_ms = 0

    for raw_line in text.splitlines():
        line = raw_line.strip()
        matches = list(_TIME_TAG.finditer(line))
        if matches:
            lyric = _WORD_TIME_TAG.sub("", _TIME_TAG.sub("", line)).strip()
            for match in matches:
                seconds = int(match.group(1)) * 60 + float(match.group(2))
                lyrics.append({"time": seconds, "text": lyric})
            continue

        meta = _META_TAG.fullmatch(line)
        if meta:
            key, value = meta.group(1).lower(), meta.group(2).strip()
            metadata[key] = value
            if key == "offset":
                try:
                    offset_ms = int(value)
                except ValueError as exc:
                    raise ValueError(f"invalid LRC offset: {value!r}") from exc

    if offset_ms:
        for lyric in lyrics:
            lyric["time"] = max(0.0, lyric["time"] + offset_ms / 1000.0)
    lyrics.sort(key=lambda item: item["time"])
    return lyrics, metadata


def parse_lrc(path: str | os.PathLike[str]) -> list[dict[str, Any]]:
    """Parse standard LRC timestamps into ``[{time: seconds, text: ...}]``."""

    lrc_path = Path(path)
    if not lrc_path.is_file():
        raise ValueError(f"LRC file does not exist: {lrc_path}")
    try:
        text = lrc_path.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeError) as exc:
        raise ValueError(f"could not read LRC file {lrc_path}: {exc}") from exc
    return _parse_lrc_text(text)[0]


def _read_lrc(path: Path | None) -> tuple[list[dict[str, Any]], dict[str, str]]:
    if path is None:
        return [], {}
    if not path.is_file():
        raise ValueError(f"LRC file does not exist: {path}")
    try:
        return _parse_lrc_text(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError) as exc:
        raise ValueError(f"could not read LRC file {path}: {exc}") from exc


def _seconds(value: Any, name: str) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{name} must be a finite non-negative number")
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be a finite non-negative number") from exc
    if not math.isfinite(result) or result < 0:
        raise ValueError(f"{name} must be a finite non-negative number")
    return result


def _retime_lyrics(
    lyrics: Sequence[dict[str, Any]], start: float, duration: float
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    previous: dict[str, Any] | None = None
    end = start + duration
    for lyric in lyrics:
        when = float(lyric["time"])
        if when < start:
            previous = lyric
        elif when < end:
            result.append(dict(lyric, time=when - start))
    if previous is not None and (not result or result[0]["time"] > 0):
        result.insert(0, dict(previous, time=0.0))
    return result


def _video_timestamp(
    capture: Any,
    cv2: Any,
    frame_index: int,
    fps: float,
    previous_ms: float | None,
) -> float:
    timestamp_ms = float(capture.get(cv2.CAP_PROP_POS_MSEC))
    valid = math.isfinite(timestamp_ms) and timestamp_ms >= 0
    if valid and (previous_ms is None or timestamp_ms > previous_ms):
        return timestamp_ms
    if frame_index == 0 and valid:
        return timestamp_ms
    if fps <= 0:
        raise RuntimeError(
            f"video has no usable timestamp or frame rate at frame {frame_index}"
        )
    fallback = frame_index * 1000.0 / fps
    if previous_ms is not None:
        fallback = max(fallback, previous_ms + 1000.0 / fps)
    return fallback


def _record_tracks(
    stats: dict[int, dict[str, Any]], people: list[dict[str, Any]]
) -> None:
    for person in people:
        track_id = person.get("track_id")
        if track_id is None:
            continue
        bbox = person["bbox"]
        keypoints = person["keypoints"]
        item = stats.setdefault(
            int(track_id),
            {
                "frames": 0.0,
                "area": 0.0,
                "center": 0.0,
                "x": 0.0,
                "visible": 0.0,
                "motion": 0.0,
                "motion_frames": 0.0,
                "last_keypoints": None,
            },
        )
        item["frames"] += 1
        item["area"] += bbox[2] * bbox[3]
        item["center"] += abs(bbox[0] + bbox[2] / 2 - 0.5)
        item["x"] += bbox[0] + bbox[2] / 2
        item["visible"] += sum(point[2] >= 0.25 for point in keypoints)
        previous = item.get("last_keypoints")
        if previous is not None:
            try:
                item["motion"] += 1.0 - pose_similarity(
                    keypoints, previous, allow_mirror=False
                )
                item["motion_frames"] += 1
            except (TypeError, ValueError):
                pass
        item["last_keypoints"] = keypoints


def _rank_tracks(stats: dict[int, dict[str, Any]]) -> list[int]:
    def rank(item: tuple[int, dict[str, Any]]) -> tuple[float, float, float, float, float, int]:
        track_id, values = item
        frames = values["frames"]
        activity = values.get("motion", 0.0) / max(1.0, values.get("motion_frames", 0.0))
        return (
            frames * (0.35 + min(1.0, activity * 8.0)),
            frames,
            values["visible"] / frames,
            -values["center"] / frames,
            values["area"] / frames,
            -track_id,
        )

    return [track_id for track_id, _ in sorted(stats.items(), key=rank, reverse=True)]


def _choose_lead(stats: dict[int, dict[str, Any]]) -> int | None:
    ranked = _rank_tracks(stats)
    return ranked[0] if ranked else None


def _validate_dancer_request(
    stats: dict[int, dict[str, Any]],
    dancer_count: int | None,
    requested_track_ids: Sequence[int] | None,
) -> tuple[int, list[int]]:
    requested = [int(track_id) for track_id in requested_track_ids or ()]
    if len(requested) != len(set(requested)):
        raise ValueError("dancer track ids must be unique")
    count = dancer_count if dancer_count is not None else max(1, len(requested))
    if isinstance(count, bool) or not isinstance(count, int) or count <= 0:
        raise ValueError("dancer count must be a positive integer")
    if len(requested) > count:
        raise ValueError("more dancer track ids were given than the dancer count")
    missing = [track_id for track_id in requested if track_id not in stats]
    if missing:
        available = ", ".join(str(track_id) for track_id in sorted(stats)) or "none"
        raise ValueError(
            f"dancer track id(s) {', '.join(map(str, missing))} were not detected; "
            f"available: {available}"
        )
    if len(stats) < count:
        raise RuntimeError(
            f"requested {count} dancers but only {len(stats)} tracks were detected"
        )
    return count, requested


def _person_quality(person: dict[str, Any]) -> float:
    bbox = person["bbox"]
    visible = sum(point[2] >= 0.25 for point in person["keypoints"]) / len(
        COCO17_KEYPOINTS
    )
    return (
        float(person.get("confidence", 0.0))
        + visible
        + min(1.0, bbox[2] * bbox[3] * 4.0)
    ) / 3.0


def _role_cost(
    person: dict[str, Any],
    role: int,
    dancer_count: int,
    previous: dict[str, Any] | None,
    spatial_only: bool = False,
) -> float:
    bbox = person["bbox"]
    center = (bbox[0] + bbox[2] / 2, bbox[1] + bbox[3] / 2)
    quality_penalty = (1.0 - _person_quality(person)) * 0.12
    if previous is None:
        canonical_x = (role + 0.5) / dancer_count
        return abs(center[0] - canonical_x) + quality_penalty

    old_bbox = previous["bbox"]
    old_center = (
        old_bbox[0] + old_bbox[2] / 2,
        old_bbox[1] + old_bbox[3] / 2,
    )
    if spatial_only:
        return (
            abs(center[0] - old_center[0])
            + 0.25 * abs(center[1] - old_center[1])
            + quality_penalty
        )
    area = max(1e-6, bbox[2] * bbox[3])
    old_area = max(1e-6, old_bbox[2] * old_bbox[3])
    try:
        shape_cost = 1.0 - pose_similarity(
            person["keypoints"], previous["keypoints"], allow_mirror=False
        )
    except (KeyError, TypeError, ValueError):
        shape_cost = 1.0
    return (
        1.4 * abs(center[0] - old_center[0])
        + 0.35 * abs(center[1] - old_center[1])
        + 0.12 * min(2.0, abs(math.log(area / old_area)))
        + 0.55 * shape_cost
        + quality_penalty
    )


def _best_role_assignment(
    candidates: Sequence[dict[str, Any]],
    roles: Sequence[int],
    dancer_count: int,
    previous: dict[int, dict[str, Any]],
    *,
    spatial_only: bool = False,
) -> tuple[dict[int, dict[str, Any]], float]:
    count = min(len(candidates), len(roles))
    if count == 0:
        return {}, 0.0
    best: tuple[float, tuple[tuple[int, int], ...], dict[int, dict[str, Any]]] | None = None
    for chosen in combinations(range(len(candidates)), count):
        for ordered_roles in permutations(roles, count):
            pairs = tuple(zip(ordered_roles, chosen))
            score = sum(
                _role_cost(
                    candidates[candidate_index],
                    role,
                    dancer_count,
                    previous.get(role),
                    spatial_only,
                )
                for role, candidate_index in pairs
            )
            assignment = {
                role: candidates[candidate_index]
                for role, candidate_index in pairs
            }
            option = (score, pairs, assignment)
            if best is None or option[:2] < best[:2]:
                best = option
    assert best is not None
    return best[2], best[0] / count


def _stitch_track_fragments(
    frames: Sequence[dict[str, Any]], protected: Sequence[int] = ()
) -> list[dict[str, Any]]:
    """Join non-overlapping tracker fragments using evidence from both ends."""

    fragments: dict[int, dict[str, Any]] = {}
    for frame_index, frame in enumerate(frames):
        timestamp = float(frame.get("timestamp_ms", 0.0)) / 1000.0
        for person in frame.get("people", ()):
            if person.get("track_id") is None:
                continue
            track_id = int(person["track_id"])
            fragment = fragments.setdefault(
                track_id,
                {
                    "first_index": frame_index,
                    "last_index": frame_index,
                    "first_time": timestamp,
                    "last_time": timestamp,
                    "first": person,
                    "last": person,
                },
            )
            fragment.update(
                last_index=frame_index,
                last_time=timestamp,
                last=person,
            )

    protected_ids = {int(track_id) for track_id in protected}
    cut_prefix = [0]
    for frame in frames:
        cut_prefix.append(cut_prefix[-1] + int(bool(frame.get("scene_cut"))))
    candidates = []
    for earlier_id, earlier in fragments.items():
        for later_id, later in fragments.items():
            gap = later["first_time"] - earlier["last_time"]
            if (
                earlier_id == later_id
                or earlier_id in protected_ids
                or later_id in protected_ids
                or not 0.0 < gap <= 3.0
                or earlier["last_index"] >= later["first_index"]
                or cut_prefix[later["first_index"] + 1]
                > cut_prefix[earlier["last_index"] + 1]
            ):
                continue
            cost = _role_cost(later["first"], 0, 1, earlier["last"])
            if cost <= 0.58:
                candidates.append((cost + gap * 0.03, earlier_id, later_id))

    parent = {track_id: track_id for track_id in fragments}

    def root(track_id: int) -> int:
        while parent[track_id] != track_id:
            parent[track_id] = parent[parent[track_id]]
            track_id = parent[track_id]
        return track_id

    claimed_later: set[int] = set()
    claimed_earlier: set[int] = set()
    for _, earlier_id, later_id in sorted(candidates):
        earlier_root, later_root = root(earlier_id), root(later_id)
        if (
            earlier_root == later_root
            or earlier_id in claimed_earlier
            or later_root in claimed_later
        ):
            continue
        parent[later_root] = earlier_root
        claimed_later.add(later_root)
        claimed_earlier.add(earlier_id)

    result = []
    for source_frame in frames:
        frame = dict(source_frame)
        people = []
        for source_person in source_frame.get("people", ()):
            person = dict(source_person)
            if person.get("track_id") is not None:
                person["track_id"] = root(int(person["track_id"]))
            people.append(person)
        frame["people"] = people
        result.append(frame)
    return result


def _role_timeline(
    frames: Sequence[dict[str, Any]],
    dancer_count: int,
    seed_track_ids: Sequence[int],
    track_stats: dict[int, dict[str, Any]],
    smooth_frames: int = 3,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], int]:
    canonical = [(index + 0.5) / dancer_count for index in range(dancer_count)]
    seed_roles = assign_dancers(
        {
            track_id: track_stats[track_id]["x"] / track_stats[track_id]["frames"]
            for track_id in seed_track_ids
        },
        canonical,
    )
    track_roles = dict(seed_roles)
    previous: dict[int, dict[str, Any]] = {}
    previous_time: dict[int, float] = {}
    role_stats = {
        index: {
            "frames": 0.0,
            "area": 0.0,
            "center": 0.0,
            "x": 0.0,
            "visible": 0.0,
            "tracks": {},
        }
        for index in range(dancer_count)
    }
    result: list[dict[str, Any]] = []
    role_filter = (
        TemporalPoseFilter(alpha=1.0, confirm_frames=smooth_frames)
        if smooth_frames
        else None
    )
    for source_frame in frames:
        frame_time = float(source_frame.get("timestamp_ms", 0.0)) / 1000.0
        source_people = [dict(person) for person in source_frame.get("people", ())]
        known_ids = {
            int(person["track_id"])
            for person in source_people
            if person.get("track_id") is not None
            and int(person["track_id"]) in track_roles
        }
        scene_cut = bool(source_frame.get("scene_cut"))
        if not scene_cut and previous and not known_ids:
            _, continuity_cost = _best_role_assignment(
                source_people,
                list(range(dancer_count)),
                dancer_count,
                {
                    role: person
                    for role, person in previous.items()
                    if frame_time - previous_time.get(role, -math.inf) <= 1.0
                },
            )
            scene_cut = continuity_cost >= _ROLE_DISCONTINUITY
        if scene_cut:
            track_roles.clear()
        if role_filter is not None and scene_cut:
            role_filter.reset()

        assigned: dict[int, dict[str, Any]] = {}
        used_people: set[int] = set()
        known_people = []
        for person_index, person in enumerate(source_people):
            track_id = person.get("track_id")
            if track_id is None:
                continue
            role = track_roles.get(int(track_id))
            if role is None:
                continue
            prior = previous.get(role)
            known_people.append(
                (
                    role,
                    track_id != (prior or {}).get("track_id"),
                    _role_cost(person, role, dancer_count, prior, True),
                    person_index,
                    person,
                )
            )
        for role, _, _, person_index, person in sorted(known_people):
            if role in assigned:
                continue
            assigned[role] = person
            used_people.add(person_index)

        candidates = [
            person for index, person in enumerate(source_people) if index not in used_people
        ]
        roles = [role for role in range(dancer_count) if role not in assigned]
        recent = {
            role: person
            for role, person in previous.items()
            if frame_time - previous_time.get(role, -math.inf) <= 1.0
        }
        matched, _ = _best_role_assignment(
            candidates,
            roles,
            dancer_count,
            recent,
            spatial_only=scene_cut,
        )
        assigned.update(matched)

        if not scene_cut and len(recent) >= 2 and len(assigned) >= 2:
            spatial, _ = _best_role_assignment(
                source_people,
                list(range(dancer_count)),
                dancer_count,
                recent,
                spatial_only=True,
            )
            changed = [
                role
                for role in assigned.keys() & spatial.keys() & recent.keys()
                if assigned[role] is not spatial[role]
            ]
            if (
                len(changed) >= 2
                and assigned.keys() == spatial.keys()
                and {id(person) for person in assigned.values()}
                == {id(person) for person in spatial.values()}
            ):
                def center_x(person: dict[str, Any]) -> float:
                    bbox = person["bbox"]
                    return bbox[0] + bbox[2] / 2

                assigned_travel = sum(
                    abs(center_x(assigned[role]) - center_x(recent[role]))
                    for role in changed
                )
                spatial_travel = sum(
                    abs(center_x(spatial[role]) - center_x(recent[role]))
                    for role in changed
                )
                abrupt = sum(
                    abs(center_x(assigned[role]) - center_x(recent[role]))
                    > _ROLE_JUMP_MARGIN
                    + _ROLE_MAX_SPEED
                    * max(0.0, frame_time - previous_time.get(role, frame_time))
                    for role in changed
                )
                if (
                    abrupt >= 2
                    and assigned_travel - spatial_travel > _ROLE_JUMP_MARGIN
                ):
                    assigned = spatial

        people = []
        for role, source_person in sorted(assigned.items()):
            person = dict(source_person)
            person["dancer_index"] = role
            people.append(person)
            track_id = person.get("track_id")
            if track_id is not None:
                track_id = int(track_id)
                track_roles[track_id] = role
                tracks = role_stats[role]["tracks"]
                tracks[track_id] = tracks.get(track_id, 0) + 1
            bbox = person["bbox"]
            stats = role_stats[role]
            stats["frames"] += 1
            stats["area"] += bbox[2] * bbox[3]
            stats["center"] += abs(bbox[0] + bbox[2] / 2 - 0.5)
            stats["x"] += bbox[0] + bbox[2] / 2
            stats["visible"] += sum(
                point[2] >= 0.25 for point in person["keypoints"]
            )
            previous[role] = source_person
            previous_time[role] = frame_time

        if role_filter is not None:
            people = role_filter.update(people, identity_key="dancer_index")

        front_to_back = sorted(
            people,
            key=lambda person: (
                person["bbox"][2] * person["bbox"][3],
                person["bbox"][1] + person["bbox"][3],
            ),
            reverse=True,
        )
        frame = dict(source_frame)
        frame["scene_cut"] = scene_cut
        frame["people"] = people
        frame["depth_order"] = [person["dancer_index"] for person in front_to_back]
        frame["render_order"] = list(reversed(frame["depth_order"]))
        result.append(frame)

    populated = {
        role: stats for role, stats in role_stats.items() if stats["frames"]
    }
    if len(populated) < dancer_count:
        raise RuntimeError(
            f"requested {dancer_count} dancers but only {len(populated)} "
            "role lanes could be populated"
        )
    dancers = []
    for role, stats in role_stats.items():
        provenance = sorted(
            stats["tracks"], key=lambda track_id: (-stats["tracks"][track_id], track_id)
        )
        dancers.append(
            {
                "index": role,
                "track_id": provenance[0] if provenance else None,
                "seed_track_id": next(
                    (track_id for track_id, seed_role in seed_roles.items() if seed_role == role),
                    None,
                ),
                "track_ids": provenance,
                "average_x": stats["x"] / stats["frames"],
            }
        )
    lead_role = _choose_lead(populated)
    assert lead_role is not None
    return result, dancers, lead_role


def _scoring_frames(
    timeline: Sequence[dict[str, Any]], dancer_count: int
) -> list[dict[str, Any]]:
    raw: list[dict[str, Any]] = []
    pending_cut = False
    for frame in timeline:
        pending_cut = pending_cut or bool(frame.get("scene_cut"))
        try:
            time_s = float(frame["timestamp_ms"]) / 1_000.0
        except (KeyError, TypeError, ValueError):
            continue
        if not math.isfinite(time_s) or (raw and time_s <= raw[-1]["time"]):
            continue
        poses = {}
        for person in frame.get("people", ()):
            try:
                dancer_index = int(person["dancer_index"])
                pose = _coerce_pose(person["keypoints"])
            except (KeyError, TypeError, ValueError):
                continue
            if (
                _pose_anchor_scale(pose, _MOVE_CONFIDENCE)[2] > 1e-9
                and 0 <= dancer_index < dancer_count
            ):
                poses[dancer_index] = pose
        if poses:
            raw.append({"time": time_s, "poses": poses, "scene_cut": pending_cut})
            pending_cut = False

    if len(raw) < 2:
        return raw
    sampled = [raw[0]]
    for frame in raw[1:-1]:
        if frame["scene_cut"] or frame["time"] - sampled[-1]["time"] >= 1 / 15:
            sampled.append(frame)
    if raw[-1]["time"] > sampled[-1]["time"]:
        sampled.append(raw[-1])
    return sampled


def _motion_delta(first: Sequence, second: Sequence) -> list[float | None]:
    first_root = _pose_anchor_scale(first, _MOVE_CONFIDENCE)
    second_root = _pose_anchor_scale(second, _MOVE_CONFIDENCE)
    first_pose = _normalized(first, _MOVE_CONFIDENCE)
    second_pose = _normalized(second, _MOVE_CONFIDENCE)
    scale = (first_root[2] + second_root[2]) / 2
    if first_pose is None or second_pose is None or scale <= 1e-9:
        return [None] * len(COCO17_KEYPOINTS)
    root_dx = (second_root[0] - first_root[0]) / scale
    root_dy = (second_root[1] - first_root[1]) / scale
    return [
        math.dist(before[:2], (after[0] + root_dx, after[1] + root_dy))
        if min(before[2], after[2]) >= _MOVE_CONFIDENCE
        else None
        for before, after in zip(first_pose, second_pose)
    ]


def _motion_speed(first: Sequence, second: Sequence, seconds: float) -> float | None:
    distance = weight = 0.0
    delta = _motion_delta(first, second)
    for index in _MOVE_BODY_JOINTS:
        confidence = min(first[index][2], second[index][2])
        if delta[index] is None:
            continue
        distance += delta[index] * confidence
        weight += confidence
    return distance / weight / seconds if weight and seconds > 0 else None


def _move_boundaries(frames: Sequence[dict[str, Any]]) -> list[tuple[int, int]]:
    if len(frames) < 2 or frames[-1]["time"] - frames[0]["time"] < _MOVE_MIN_SECONDS:
        return []

    energy = [0.0]
    for before, after in zip(frames, frames[1:]):
        speeds = [
            speed
            for dancer_index in before["poses"].keys() & after["poses"].keys()
            if (
                speed := _motion_speed(
                    before["poses"][dancer_index],
                    after["poses"][dancer_index],
                    after["time"] - before["time"],
                )
            )
            is not None
        ]
        energy.append(0.0 if after["scene_cut"] else median(speeds) if speeds else 0.0)

    scores = [0.0] * len(frames)
    for index in range(1, len(frames) - 1):
        before, after = energy[index], energy[index + 1]
        local = median(energy[max(1, index - 2) : min(len(energy), index + 3)])
        scores[index] = (
            abs(after - before)
            + max(0.0, local - min(before, after))
            + 0.1 * max(0.0, before - after)
        )
    typical = median(scores[1:-1]) if len(scores) > 2 else 0.0
    deviation = median(abs(score - typical) for score in scores[1:-1]) if len(scores) > 2 else 0.0
    threshold = max(0.08, typical + 2.0 * deviation)

    boundaries = {0, len(frames) - 1}

    def can_add(index: int) -> bool:
        ordered = sorted(boundaries)
        position = bisect_right(ordered, index)
        left = ordered[position - 1]
        right = ordered[position]
        return (
            frames[index]["time"] - frames[left]["time"] >= _MOVE_MIN_SECONDS
            and frames[right]["time"] - frames[index]["time"] >= _MOVE_MIN_SECONDS
        )

    for index in range(1, len(frames) - 1):
        if frames[index]["scene_cut"] and can_add(index):
            boundaries.add(index)

    candidates = [
        index
        for index in range(1, len(frames) - 1)
        if scores[index] >= threshold
        and scores[index] >= scores[index - 1]
        and scores[index] >= scores[index + 1]
    ]
    for index in sorted(candidates, key=lambda item: (-scores[item], item)):
        if can_add(index):
            boundaries.add(index)

    while True:
        oversized = next(
            (
                (left, right)
                for left, right in zip(sorted(boundaries), sorted(boundaries)[1:])
                if frames[right]["time"] - frames[left]["time"] > _MOVE_MAX_SECONDS
            ),
            None,
        )
        if oversized is None:
            break
        left, right = oversized
        pieces = math.ceil(
            (frames[right]["time"] - frames[left]["time"]) / _MOVE_MAX_SECONDS
        )
        target = frames[left]["time"] + (
            frames[right]["time"] - frames[left]["time"]
        ) / pieces
        feasible = [
            index
            for index in range(left + 1, right)
            if frames[index]["time"] - frames[left]["time"] >= _MOVE_MIN_SECONDS
            and frames[right]["time"] - frames[index]["time"] >= _MOVE_MIN_SECONDS
        ]
        if not feasible:
            break
        near = [
            index
            for index in feasible
            if abs(frames[index]["time"] - target) <= _MOVE_MIN_SECONDS / 2
        ]
        choice = max(
            near or feasible,
            key=lambda index: (
                scores[index],
                -abs(frames[index]["time"] - target),
                -index,
            ),
        )
        boundaries.add(choice)

    ordered = sorted(boundaries)
    return list(zip(ordered, ordered[1:]))


def _pose_at(samples: Sequence[tuple[float, Sequence]], time_s: float):
    times = [sample[0] for sample in samples]
    right = bisect_right(times, time_s)
    if right == 0:
        return samples[0][1] if times[0] - time_s <= 0.25 else None
    if right == len(samples):
        return samples[-1][1] if time_s - times[-1] <= 0.25 else None
    before, after = samples[right - 1], samples[right]
    if after[0] - before[0] > 0.5:
        return None
    amount = (time_s - before[0]) / max(1e-9, after[0] - before[0])
    return interpolate_pose(before[1], after[1], amount)


def _move_definition(
    samples: Sequence[tuple[float, Sequence]], start: float, end: float
) -> dict[str, Any] | None:
    poses = []
    for phase in range(MOVE_SCORING_PHASES):
        pose = _pose_at(
            samples,
            start + (end - start) * phase / (MOVE_SCORING_PHASES - 1),
        )
        if pose is None:
            return None
        poses.append(pose)

    motion = [0.0] * len(COCO17_KEYPOINTS)
    for first, second in zip(poses, poses[1:]):
        delta = _motion_delta(first, second)
        for index in _MOVE_BODY_JOINTS:
            confidence = min(first[index][2], second[index][2])
            if delta[index] is not None:
                motion[index] += delta[index] * confidence
    peak = max(motion, default=0.0)
    weights = [round(value / peak, 4) if peak > 1e-9 else 0.0 for value in motion]
    important = [
        index
        for index in sorted(_MOVE_BODY_JOINTS, key=lambda item: (-motion[item], item))[:3]
        if motion[index] >= max(0.01, peak * 0.2)
    ]
    cue_delta = [_motion_delta(poses[0], pose) for pose in poses]
    cue_sample = (
        max(
            range(MOVE_SCORING_PHASES),
            key=lambda phase: sum(
                (cue_delta[phase][index] or 0.0)
                for index in important or _MOVE_BODY_JOINTS
            ),
        )
        if peak > 1e-9
        else MOVE_SCORING_PHASES // 2
    )
    return {
        "poses": [
            [
                [round(x, 4), round(y, 4), round(confidence, 3)]
                for x, y, confidence in pose
            ]
            for pose in poses
        ],
        "weights": weights,
        "cue_sample": cue_sample,
        "important_joints": important,
    }


def _build_move_scoring(
    timeline: Sequence[dict[str, Any]], dancer_count: int
) -> dict[str, Any]:
    artifact: dict[str, Any] = {
        "schema_version": MOVE_SCORING_SCHEMA_VERSION,
        "feature": MOVE_SCORING_FEATURE,
        "pose_coordinate_space": "normalized_image",
        "phase_count": MOVE_SCORING_PHASES,
        "definitions": {},
        "segments": [],
    }
    frames = _scoring_frames(timeline, dancer_count)
    lanes = {
        dancer_index: [
            (frame["time"], frame["poses"][dancer_index])
            for frame in frames
            if dancer_index in frame["poses"]
        ]
        for dancer_index in range(dancer_count)
    }
    for start_index, end_index in _move_boundaries(frames):
        start, end = frames[start_index]["time"], frames[end_index]["time"]
        dancers = []
        for dancer_index, samples in lanes.items():
            definition = _move_definition(samples, start, end) if samples else None
            if definition is None:
                continue
            definition_id = f"m{len(artifact['definitions']):04d}"
            artifact["definitions"][definition_id] = definition
            dancers.append(
                {
                    "dancer_index": dancer_index,
                    "definition": definition_id,
                    "mirrored": False,
                }
            )
        if dancers:
            artifact["segments"].append(
                {"start": round(start, 3), "end": round(end, 3), "dancers": dancers}
            )
    return artifact


def analyze_video(
    video: str | os.PathLike[str],
    engine: PoseEngine,
    *,
    skip_before_s: float = 0.0,
    trim_end_s: float = 0.0,
    show_progress: bool = True,
    progress_callback: Callable[[int, int | None, float], None] | None = None,
    preview_callback: Callable[[Any, list[dict[str, Any]]], None] | None = None,
    cancel_event: Any | None = None,
) -> dict[str, Any]:
    """Run ``engine`` on every decoded frame and retain the video's timestamps."""

    video_path = Path(video)
    skip_before_ms = _seconds(skip_before_s, "skip-before time") * 1_000.0
    trim_end_ms = _seconds(trim_end_s, "end trim") * 1_000.0
    if not video_path.is_file():
        raise ValueError(f"video file does not exist: {video_path}")
    try:
        import cv2
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "Video extraction requires OpenCV; install the vision/ML dependencies"
        ) from exc

    capture = cv2.VideoCapture(str(video_path))
    if not capture.isOpened():
        capture.release()
        raise RuntimeError(f"could not open video: {video_path}")

    raw_fps = float(capture.get(cv2.CAP_PROP_FPS))
    fps = raw_fps if math.isfinite(raw_fps) and raw_fps > 0 else 0.0
    raw_count = float(capture.get(cv2.CAP_PROP_FRAME_COUNT))
    reported_frames = (
        int(raw_count) if math.isfinite(raw_count) and raw_count > 0 else None
    )
    stop_before_ms = (
        reported_frames * 1_000.0 / fps - trim_end_ms
        if reported_frames and fps and trim_end_ms
        else None
    )
    expected_frames = (
        min(reported_frames, max(1, math.ceil(stop_before_ms * fps / 1_000.0)))
        if reported_frames and fps and stop_before_ms is not None
        else reported_frames
    )
    if stop_before_ms is not None and stop_before_ms <= skip_before_ms:
        capture.release()
        raise ValueError("requested trim and hidden intro leave no choreography frames")
    timeline: list[dict[str, Any]] = []
    previous_ms: float | None = None
    last_timestamp_ms: float | None = None
    previous_thumbnail: Any | None = None
    decoded_frames = 0
    source_size = {"width": 0, "height": 0}
    started = time.perf_counter()
    last_progress = started
    last_preview = -math.inf
    if progress_callback:
        progress_callback(0, expected_frames, 0.0)
    try:
        engine.reset_tracking()
        while True:
            if cancel_event is not None and cancel_event.is_set():
                raise InterruptedError("song extraction cancelled")
            ok, frame = capture.read()
            if not ok:
                break
            source_frame_index = decoded_frames
            decoded_frames += 1
            timestamp_ms = _video_timestamp(
                capture, cv2, source_frame_index, fps, previous_ms
            )
            previous_ms = timestamp_ms
            last_timestamp_ms = timestamp_ms
            if stop_before_ms is not None and timestamp_ms >= stop_before_ms:
                break
            if timestamp_ms < skip_before_ms:
                continue
            thumbnail = cv2.resize(frame, (32, 18), interpolation=cv2.INTER_AREA)
            scene_delta = (
                float(cv2.absdiff(thumbnail, previous_thumbnail).mean()) / 255.0
                if previous_thumbnail is not None
                else 0.0
            )
            previous_thumbnail = thumbnail
            if scene_delta >= _SCENE_CUT_DELTA:
                reset_smoothing = getattr(engine, "reset_smoothing", None)
                if callable(reset_smoothing):
                    reset_smoothing()
            event = engine.process(frame, timestamp_ms=timestamp_ms)
            source_size = event["source"]
            people = event["people"]
            timeline.append(
                {
                    "frame": source_frame_index,
                    "timestamp_ms": timestamp_ms,
                    # ponytail: thumbnail delta is intentionally cheap; add
                    # optical-flow/manual cut markers only if real imports show
                    # role swaps that pose/position continuity cannot resolve.
                    "scene_cut": scene_delta >= _SCENE_CUT_DELTA,
                    "people": people,
                    "timing": event["timing"],
                }
            )

            now = time.perf_counter()
            if preview_callback and now - last_preview >= 5.0:
                preview_callback(frame, people)
                last_preview = now
            interval = 0.25 if progress_callback else 0.5 if sys.stderr.isatty() else 5.0
            if (show_progress or progress_callback) and now - last_progress >= interval:
                elapsed = max(now - started, 1e-9)
                if progress_callback:
                    progress_callback(
                        min(decoded_frames, expected_frames or decoded_frames),
                        expected_frames,
                        decoded_frames / elapsed,
                    )
                if show_progress:
                    progress = f"{len(timeline)} frames ({len(timeline) / elapsed:.1f} fps)"
                    if expected_frames:
                        progress += f" / {expected_frames} ({decoded_frames / expected_frames:.1%})"
                    print(
                        progress,
                        file=sys.stderr,
                        end="\r" if sys.stderr.isatty() else "\n",
                        flush=True,
                    )
                last_progress = now
    finally:
        capture.release()

    if show_progress and sys.stderr.isatty():
        print(file=sys.stderr)
    if not decoded_frames:
        raise RuntimeError(f"video contains no decodable frames: {video_path}")
    if not timeline:
        raise RuntimeError(
            f"video contains no frames at or after {skip_before_ms / 1_000.0:g} seconds"
        )
    if not trim_end_ms and reported_frames and decoded_frames + 1 < reported_frames:
        print(
            f"warning: decoder returned {decoded_frames} of {reported_frames} reported frames",
            file=sys.stderr,
        )

    frame_duration_ms = 1000.0 / fps if fps else 0.0
    assert last_timestamp_ms is not None
    duration_ms = last_timestamp_ms + frame_duration_ms
    if reported_frames and fps:
        duration_ms = max(duration_ms, reported_frames * 1000.0 / fps)
    track_stats: dict[int, dict[str, Any]] = {}
    for frame in timeline:
        _record_tracks(track_stats, frame["people"])
    elapsed = time.perf_counter() - started
    if progress_callback:
        progress_callback(
            expected_frames or decoded_frames,
            expected_frames,
            decoded_frames / max(elapsed, 1e-9),
        )
    return {
        "source": {
            "path": str(video_path.resolve()),
            **source_size,
            "fps": fps or None,
            "reported_frame_count": reported_frames,
            "decoded_frame_count": decoded_frames,
            "analyzed_frame_count": len(timeline),
            "duration_ms": duration_ms,
        },
        "lead_track_id": _choose_lead(track_stats),
        "track_ids": sorted(track_stats),
        "_track_stats": track_stats,
        "frames": timeline,
        "processing": {
            "elapsed_seconds": elapsed,
            "average_fps": len(timeline) / elapsed if elapsed else None,
        },
    }


def _slug(value: str) -> str:
    ascii_value = (
        unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode()
    )
    return re.sub(r"[^a-z0-9]+", "-", ascii_value.lower()).strip("-") or "song"


def _atomic_json(path: Path, value: dict[str, Any]) -> None:
    descriptor, temporary = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as output:
            json.dump(value, output, ensure_ascii=False, indent=2)
            output.write("\n")
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _atomic_copy(source: Path, destination: Path) -> None:
    descriptor, temporary = tempfile.mkstemp(
        prefix=f".{destination.name}.", suffix=".tmp", dir=destination.parent
    )
    os.close(descriptor)
    try:
        shutil.copy2(source, temporary)
        os.replace(temporary, destination)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def extract_song(
    video: str | os.PathLike[str],
    output_dir: str | os.PathLike[str],
    *,
    engine: PoseEngine,
    lrc: str | os.PathLike[str] | None = None,
    title: str | None = None,
    artist: str | None = None,
    song_id: str | None = None,
    dancer_count: int | None = None,
    dancer_track_ids: Sequence[int] | None = None,
    copy_video: bool = False,
    move_video: bool = False,
    trim_start: float = 0.0,
    trim_end: float = 0.0,
    hide_video_intro: float = 0.0,
    force: bool = False,
    show_progress: bool = True,
    progress_callback: Callable[[int, int | None, float], None] | None = None,
    preview_callback: Callable[[Any, list[dict[str, Any]]], None] | None = None,
    cancel_event: Any | None = None,
) -> Path:
    """Analyze a video and atomically create ``OUTPUT_DIR/song.json``."""

    video_path = Path(video)
    if not video_path.is_file():
        raise ValueError(f"video file does not exist: {video_path}")
    lrc_path = Path(lrc) if lrc is not None else None
    lyrics, lrc_metadata = _read_lrc(lrc_path)
    resolved_title = (title or lrc_metadata.get("ti") or video_path.stem).strip()
    resolved_artist = (artist or lrc_metadata.get("ar") or "Unknown Artist").strip()
    if not resolved_title:
        raise ValueError("title must not be empty")
    if not resolved_artist:
        raise ValueError("artist must not be empty")
    resolved_id = song_id or _slug(f"{resolved_artist}-{resolved_title}")
    if not _SONG_ID.fullmatch(resolved_id):
        raise ValueError(
            "song id must start with a lowercase letter or digit and contain only "
            "lowercase letters, digits, underscores, or hyphens"
        )
    requested_count = dancer_count if dancer_count is not None else max(
        1, len(dancer_track_ids or ())
    )
    if (
        isinstance(requested_count, bool)
        or not isinstance(requested_count, int)
        or requested_count <= 0
    ):
        raise ValueError("dancer count must be a positive integer")
    if requested_count > engine.max_people:
        raise ValueError(
            f"dancer count {requested_count} exceeds detector --max-people {engine.max_people}"
        )
    trim_start = _seconds(trim_start, "trim start")
    trim_end = _seconds(trim_end, "trim end")
    hide_video_intro = _seconds(hide_video_intro, "hidden video intro")
    if move_video and not copy_video:
        raise ValueError("moving video into the package requires copy_video")

    destination = Path(output_dir)
    if destination.exists() and not destination.is_dir():
        raise ValueError(f"output is not a directory: {destination}")
    destination.mkdir(parents=True, exist_ok=True)
    song_path = destination / "song.json"
    copied_video = destination / video_path.name
    if song_path.exists() and not force:
        raise FileExistsError(f"output already exists (use --force): {song_path}")
    same_video = False
    if copy_video:
        try:
            same_video = copied_video.resolve() == video_path.resolve()
        except OSError:
            pass
        if copied_video.exists() and not same_video and not force:
            raise FileExistsError(
                f"copied video already exists (use --force): {copied_video}"
            )

    analysis = analyze_video(
        video_path,
        engine,
        skip_before_s=trim_start + hide_video_intro,
        trim_end_s=trim_end,
        show_progress=show_progress,
        progress_callback=progress_callback,
        preview_callback=preview_callback,
        cancel_event=cancel_event,
    )
    source_duration = float(analysis["source"]["duration_ms"]) / 1_000.0
    duration = source_duration - trim_start - trim_end
    if duration <= 0:
        raise ValueError("trim start and end remove the entire video")
    if hide_video_intro >= duration:
        raise ValueError("hidden video intro must end before the trimmed video")
    source_end = source_duration - trim_end
    frames = []
    for source_frame in analysis["frames"]:
        timestamp = float(source_frame["timestamp_ms"]) / 1_000.0
        if timestamp >= source_end:
            continue
        frame = dict(source_frame)
        frame["frame"] = len(frames)
        frame["timestamp_ms"] = (timestamp - trim_start) * 1_000.0
        frames.append(frame)
    if not frames:
        raise RuntimeError("trimmed video contains no frames available for choreography")
    frames = _stitch_track_fragments(frames, dancer_track_ids or ())
    analysis["frames"] = frames
    source = dict(analysis["source"])
    source.update(
        {
            "original_duration_ms": source["duration_ms"],
            "duration_ms": duration * 1_000.0,
            "trim_start_ms": trim_start * 1_000.0,
            "trim_end_ms": trim_end * 1_000.0,
            "pose_start_ms": hide_video_intro * 1_000.0,
            "analyzed_frame_count": len(frames),
        }
    )
    analysis["source"] = source
    track_stats: dict[int, dict[str, Any]] = {}
    for frame in frames:
        _record_tracks(track_stats, frame["people"])
    analysis["_track_stats"] = track_stats
    analysis["track_ids"] = sorted(track_stats)
    analysis["lead_track_id"] = _choose_lead(track_stats)
    if not any(frame["people"] for frame in frames):
        raise RuntimeError("no people were detected in the video")
    selected_count, seed_track_ids = _validate_dancer_request(
        track_stats, dancer_count, dancer_track_ids
    )
    selected_timeline, dancers, lead_dancer_index = _role_timeline(
        analysis["frames"],
        selected_count,
        seed_track_ids,
        track_stats,
        getattr(engine, "smooth_frames", 3),
    )
    representative_track_ids = [dancer["track_id"] for dancer in dancers]
    lead_track_id = representative_track_ids[lead_dancer_index]
    moved_video = False
    if copy_video and not same_video:
        if move_video:
            shutil.move(video_path, copied_video)
            moved_video = True
        else:
            _atomic_copy(video_path, copied_video)
    video_reference = video_path.name if copy_video else str(video_path.resolve())
    song = {
        "schema_version": 1,
        "id": resolved_id,
        "title": resolved_title,
        "artist": resolved_artist,
        "duration": duration,
        "bpm": None,
        "key": "",
        "unlock_cost": 0,
        "palette": [],
        "video": video_reference,
        "media_start": trim_start,
        "video_hidden_until": hide_video_intro,
        "lyrics": _retime_lyrics(lyrics, trim_start, duration),
        "moves": [],
        "choreography": {
            "schema_version": 1,
            "format": "coco17",
            "coordinate_space": "normalized",
            "keypoints": list(COCO17_KEYPOINTS),
            "model": {
                "backend": getattr(engine, "backend_name", "YOLO26"),
                "name": engine.model_name,
                "imgsz": engine.imgsz,
                "device": engine.device,
                "max_people": engine.max_people,
                "smooth_frames": getattr(engine, "smooth_frames", 3),
                "tracker": getattr(engine, "tracker_name", "unknown"),
            },
            "lead_track_id": lead_track_id,
            "lead_dancer_index": lead_dancer_index,
            "dancer_track_ids": representative_track_ids,
            "dancers": dancers,
            "role_assignment": ROLE_ASSIGNMENT_METHOD,
            "depth_order_convention": "front_to_back",
            "depth_estimation": "bounding_box_area",
            "render_order_convention": "back_to_front",
            "track_ids": analysis["track_ids"],
            "source": analysis["source"],
            "timeline": selected_timeline,
            "move_scoring": _build_move_scoring(selected_timeline, selected_count),
        },
        "extraction": analysis["processing"],
    }
    try:
        _atomic_json(song_path, song)
    except Exception:
        if moved_video and copied_video.exists() and not video_path.exists():
            shutil.move(copied_video, video_path)
        raise
    return song_path


def _positive_int(value: str) -> int:
    try:
        number = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be an integer") from exc
    if number <= 0:
        raise argparse.ArgumentTypeError("must be positive")
    return number


def _nonnegative_int(value: str) -> int:
    try:
        number = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be an integer") from exc
    if number < 0:
        raise argparse.ArgumentTypeError("must be non-negative")
    return number


def _nonnegative_float(value: str) -> float:
    try:
        return _seconds(value, "value")
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be a finite non-negative number") from exc


def _device(value: str) -> str | int:
    return int(value) if re.fullmatch(r"\d+", value) else value


def diagnostics() -> int:
    """Verify that a distribution contains CUDA PyTorch, weights, and inference."""

    import numpy as np
    import torch

    model = Path(os.environ.get("OPENDANCE_MODEL", ""))
    available = torch.cuda.is_available()
    print(f"PyTorch: {torch.__version__}")
    print(f"CUDA runtime: {torch.version.cuda or 'none'}")
    print(f"CUDA available: {available}")
    if available:
        print(f"GPU: {torch.cuda.get_device_name(0)}")
    print(f"Bundled model: {model.is_file()}")
    if not torch.version.cuda or not model.is_file():
        return 1
    result = PoseEngine(
        model, device="cpu", imgsz=320, max_people=1, smooth_frames=0
    ).process(np.zeros((320, 320, 3), dtype=np.uint8))
    print(f"Bundled pose inference: {result['inference_ms']:.0f} ms")
    return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="opendance-extract",
        description="Extract multi-person COCO-17 choreography from every video frame.",
    )
    parser.add_argument("video", type=Path, nargs="?", help="source video")
    parser.add_argument("output", type=Path, nargs="?", help="song package directory")
    parser.add_argument(
        "--diagnostics",
        action="store_true",
        help="verify bundled CUDA runtime, pose weights, and inference, then exit",
    )
    parser.add_argument("--lrc", type=Path, help="optional synchronized lyrics")
    parser.add_argument("--title", help="song title (defaults to LRC metadata/file name)")
    parser.add_argument("--artist", help="artist (defaults to LRC metadata)")
    parser.add_argument("--id", dest="song_id", help="stable lowercase song id")
    parser.add_argument(
        "--model",
        help="Ultralytics model (default: OPENDANCE_MODEL or yolo26n-pose.pt)",
    )
    parser.add_argument(
        "--pose-backend",
        choices=("yolo26", "rtmpose"),
        default=os.environ.get("OPENDANCE_POSE_BACKEND", "yolo26"),
        help="pose implementation (default: yolo26)",
    )
    parser.add_argument(
        "--rtmpose-mode",
        choices=("lightweight", "balanced", "performance"),
        default=os.environ.get("OPENDANCE_RTMPOSE_MODE", "lightweight"),
        help="RTMPose speed/quality preset",
    )
    parser.add_argument("--imgsz", type=_positive_int, default=640)
    parser.add_argument("--device", type=_device)
    parser.add_argument("--max-people", type=_positive_int, default=4)
    parser.add_argument(
        "--smooth-frames",
        type=_nonnegative_int,
        default=3,
        help="consistent frames required before accepting a pose jump; 0 disables",
    )
    parser.add_argument(
        "--dancers",
        type=_positive_int,
        help="number of choreography dancers to retain (default: 1, or --track-id count)",
    )
    parser.add_argument(
        "--track-id",
        type=int,
        action="append",
        default=[],
        help="specific detected dancer track to retain; repeat for more than one",
    )
    parser.add_argument("--copy-video", action="store_true")
    parser.add_argument(
        "--trim-start",
        type=_nonnegative_float,
        default=0.0,
        metavar="SECONDS",
        help="omit this much media and choreography from the beginning",
    )
    parser.add_argument(
        "--trim-end",
        type=_nonnegative_float,
        default=0.0,
        metavar="SECONDS",
        help="omit this much media and choreography from the end",
    )
    parser.add_argument(
        "--hide-video-intro",
        type=_nonnegative_float,
        default=0.0,
        metavar="SECONDS",
        help="keep intro audio but hide video and skip pose extraction",
    )
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--no-progress", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    if args.diagnostics:
        try:
            return diagnostics()
        except Exception as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
    if args.video is None or args.output is None:
        parser.error("video and output are required unless --diagnostics is used")
    try:
        engine = create_pose_engine(
            args.pose_backend,
            model=args.model,
            imgsz=args.imgsz,
            device=args.device,
            max_people=args.max_people,
            smooth_frames=args.smooth_frames,
            rtmpose_mode=args.rtmpose_mode,
        )
        result = extract_song(
            args.video,
            args.output,
            engine=engine,
            lrc=args.lrc,
            title=args.title,
            artist=args.artist,
            song_id=args.song_id,
            dancer_count=args.dancers,
            dancer_track_ids=args.track_id,
            copy_video=args.copy_video,
            trim_start=args.trim_start,
            trim_end=args.trim_end,
            hide_video_intro=args.hide_video_intro,
            force=args.force,
            show_progress=not args.no_progress,
        )
    except KeyboardInterrupt:
        print("error: interrupted; no song.json was written", file=sys.stderr)
        return 130
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["analyze_video", "diagnostics", "extract_song", "main", "parse_lrc"]
