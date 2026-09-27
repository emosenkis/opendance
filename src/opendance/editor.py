"""Atomic, non-destructive edits for extracted choreography manifests."""

from __future__ import annotations

from copy import deepcopy
import json
import math
import os
from pathlib import Path
import shutil
import tempfile
from typing import Any

from .extract import _move_definition


def _scoring(song: dict[str, Any]) -> dict[str, Any]:
    choreography = song.get("choreography")
    scoring = choreography.get("move_scoring") if isinstance(choreography, dict) else None
    if not isinstance(scoring, dict) or not isinstance(scoring.get("segments"), list):
        raise ValueError("song has no editable extracted dance moves")
    if not isinstance(scoring.get("definitions"), dict):
        raise ValueError("song has no editable move definitions")
    return scoring


def _range(values: dict[str, Any], duration: float) -> tuple[float, float]:
    try:
        start, end = float(values["start"]), float(values["end"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("start and end must be numbers") from exc
    if not math.isfinite(start) or not math.isfinite(end) or not 0 <= start < end <= duration:
        raise ValueError("time range must be inside the song and start before end")
    return start, end


def _segment(scoring: dict[str, Any], value: Any) -> tuple[int, dict[str, Any]]:
    try:
        index = int(value)
        segment = scoring["segments"][index]
    except (IndexError, KeyError, TypeError, ValueError) as exc:
        raise ValueError("invalid move selection") from exc
    if index < 0 or not isinstance(segment, dict):
        raise ValueError("invalid move selection")
    return index, segment


def _next_definition_id(definitions: dict[str, Any]) -> str:
    number = 1
    while f"edit{number:04d}" in definitions:
        number += 1
    return f"edit{number:04d}"


def _definition_for_range(
    song: dict[str, Any], dancer_index: int, start: float, end: float
) -> dict[str, Any]:
    timeline = song["choreography"].get("timeline", [])
    samples = []
    for frame in timeline:
        time_s = float(frame.get("timestamp_ms", 0.0)) / 1_000.0
        for person in frame.get("people", []):
            if person.get("dancer_index") == dancer_index and person.get("keypoints"):
                samples.append((time_s, person["keypoints"]))
                break
    definition = _move_definition(samples, start, end) if samples else None
    if definition is None:
        raise ValueError("selected range does not contain a complete pose track")
    return definition


def _assign_definitions(song: dict[str, Any], segment: dict[str, Any]) -> None:
    scoring = _scoring(song)
    for dancer in segment.get("dancers", []):
        dancer_index = int(dancer["dancer_index"])
        definition_id = _next_definition_id(scoring["definitions"])
        scoring["definitions"][definition_id] = _definition_for_range(
            song, dancer_index, float(segment["start"]), float(segment["end"])
        )
        dancer["definition"] = definition_id


def _split_segments_at(song: dict[str, Any], split_at: float) -> None:
    scoring = _scoring(song)
    for index, segment in enumerate(scoring["segments"]):
        if float(segment["start"]) < split_at < float(segment["end"]):
            left, right = deepcopy(segment), deepcopy(segment)
            left["end"], right["start"] = round(split_at, 3), round(split_at, 3)
            _assign_definitions(song, left)
            _assign_definitions(song, right)
            scoring["segments"][index : index + 1] = [left, right]
            return


def _refresh_cue(definition: dict[str, Any], cue_sample: int) -> None:
    poses = definition.get("poses", [])
    if not poses:
        raise ValueError("move has no cue poses")
    cue_sample = max(0, min(len(poses) - 1, cue_sample))
    definition["cue_sample"] = cue_sample
    arrow_start = max(0, cue_sample - 2)
    arrows = []
    for joint in definition.get("important_joints", [])[:3]:
        try:
            before, after = poses[arrow_start][joint], poses[cue_sample][joint]
            if min(before[2], after[2]) >= 0.2 and math.dist(before[:2], after[:2]) >= 0.025:
                arrows.append(
                    {
                        "joint": joint,
                        "from": [round(before[0], 4), round(before[1], 4)],
                        "to": [round(after[0], 4), round(after[1], 4)],
                    }
                )
        except (IndexError, TypeError):
            continue
    definition["cue_arrows"] = arrows


def apply_edit(song: dict[str, Any], values: dict[str, Any]) -> dict[str, Any]:
    """Return an edited deep copy, or raise without changing the input."""

    edited = deepcopy(song)
    scoring = _scoring(edited)
    duration = float(edited.get("duration", 0.0))
    action = str(values.get("action", ""))

    if action == "swap":
        start, end = _range(values, duration)
        first, second = int(values.get("first", -1)), int(values.get("second", -1))
        dancer_count = len(edited["choreography"].get("dancers", []))
        if first == second or min(first, second) < 0 or max(first, second) >= dancer_count:
            raise ValueError("choose two different valid dancers")
        for frame in edited["choreography"].get("timeline", []):
            time_s = float(frame.get("timestamp_ms", 0.0)) / 1_000.0
            if start <= time_s < end:
                for person in frame.get("people", []):
                    if person.get("dancer_index") == first:
                        person["dancer_index"] = second
                    elif person.get("dancer_index") == second:
                        person["dancer_index"] = first
        _split_segments_at(edited, start)
        _split_segments_at(edited, end)
        for segment in scoring["segments"]:
            if start <= float(segment["start"]) and float(segment["end"]) <= end:
                _assign_definitions(edited, segment)
    elif action == "split":
        index, segment = _segment(scoring, values.get("segment"))
        split_at = float(values.get("time", math.nan))
        start, end = float(segment["start"]), float(segment["end"])
        if not math.isfinite(split_at) or not start + 0.5 <= split_at <= end - 0.5:
            raise ValueError("split must leave at least half a second on each side")
        left = deepcopy(segment)
        right = deepcopy(segment)
        left["end"], right["start"] = round(split_at, 3), round(split_at, 3)
        left.pop("power", None)
        right.pop("power", None)
        _assign_definitions(edited, left)
        _assign_definitions(edited, right)
        scoring["segments"][index : index + 1] = [left, right]
    elif action == "merge":
        index, first = _segment(scoring, values.get("segment"))
        if index + 1 >= len(scoring["segments"]):
            raise ValueError("the last move has no following move to merge")
        second = scoring["segments"][index + 1]
        if abs(float(first["end"]) - float(second["start"])) > 0.05:
            raise ValueError("only adjacent moves can be merged")
        first_lanes = {int(item["dancer_index"]) for item in first.get("dancers", [])}
        second_lanes = {int(item["dancer_index"]) for item in second.get("dancers", [])}
        if first_lanes != second_lanes:
            raise ValueError("moves must contain the same dancers to merge")
        merged = deepcopy(first)
        merged["end"] = second["end"]
        merged["power"] = bool(first.get("power") or second.get("power"))
        _assign_definitions(edited, merged)
        scoring["segments"][index : index + 2] = [merged]
    elif action == "power":
        _, segment = _segment(scoring, values.get("segment"))
        segment["power"] = bool(values.get("enabled"))
    elif action == "cue":
        _, segment = _segment(scoring, values.get("segment"))
        delta = int(values.get("delta", 0))
        if delta not in {-1, 1}:
            raise ValueError("cue adjustment must be one frame earlier or later")
        for dancer in segment.get("dancers", []):
            definition = scoring["definitions"].get(dancer.get("definition"))
            if isinstance(definition, dict):
                _refresh_cue(definition, int(definition.get("cue_sample", 0)) + delta)
    elif action == "joint":
        _, segment = _segment(scoring, values.get("segment"))
        joint = int(values.get("joint", -1))
        if not 5 <= joint <= 16:
            raise ValueError("cue joint must be a COCO body joint from 5 through 16")
        for dancer in segment.get("dancers", []):
            definition = scoring["definitions"].get(dancer.get("definition"))
            if not isinstance(definition, dict):
                continue
            important = list(definition.get("important_joints", []))
            if joint in important:
                important.remove(joint)
            elif len(important) < 3:
                important.append(joint)
            else:
                raise ValueError("a cue can highlight at most three body parts")
            definition["important_joints"] = important
            _refresh_cue(definition, int(definition.get("cue_sample", 0)))
    elif action == "mask":
        start, end = _range(values, duration)
        masks = list(scoring.get("score_masks", [])) + [{"start": start, "end": end}]
        masks.sort(key=lambda item: float(item["start"]))
        merged = []
        for mask in masks:
            mask_start, mask_end = float(mask["start"]), float(mask["end"])
            if merged and mask_start <= merged[-1]["end"]:
                merged[-1]["end"] = max(merged[-1]["end"], mask_end)
            else:
                merged.append({"start": round(mask_start, 3), "end": round(mask_end, 3)})
        scoring["score_masks"] = merged
    elif action == "unmask":
        masks = scoring.get("score_masks", [])
        try:
            del masks[int(values.get("mask"))]
        except (IndexError, TypeError, ValueError) as exc:
            raise ValueError("invalid mask selection") from exc
    else:
        raise ValueError("unknown dance edit")
    return edited


def save_edit(path: Path, values: dict[str, Any]) -> dict[str, Any]:
    song = json.loads(path.read_text(encoding="utf-8"))
    edited = apply_edit(song, values)
    backup = path.with_name(f"{path.name}.editor.bak")
    if not backup.exists():
        shutil.copy2(path, backup)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(edited, stream, indent=2, ensure_ascii=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return edited


def editor_state(song: dict[str, Any], manifest: Path) -> dict[str, Any]:
    scoring = _scoring(song)
    definitions = scoring["definitions"]
    segments = []
    for index, segment in enumerate(scoring["segments"]):
        cues = []
        for dancer in segment.get("dancers", []):
            definition = definitions.get(dancer.get("definition"), {})
            cues.append(int(definition.get("cue_sample", 0)))
        segments.append(
            {
                "index": index,
                "start": float(segment["start"]),
                "end": float(segment["end"]),
                "power": bool(segment.get("power")),
                "cue": round(sum(cues) / len(cues)) if cues else 0,
            }
        )
    video = song.get("video")
    video_path = Path(video) if isinstance(video, str) and video else None
    if video_path is not None and not video_path.is_absolute():
        video_path = manifest.parent / video_path
    return {
        "title": str(song.get("title", "Dance editor")),
        "duration": float(song.get("duration", 0.0)),
        "dancer_count": len(song.get("choreography", {}).get("dancers", [])),
        "video": video_path.resolve().as_uri() if video_path and video_path.is_file() else "",
        "segments": segments,
        "masks": list(scoring.get("score_masks", [])),
    }
