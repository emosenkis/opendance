#!/usr/bin/env python3
"""Refresh stale extraction artifacts from local extracted timelines."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil
import sys

from opendance.extract import (
    MOVE_SCORING_FEATURE,
    MOVE_SCORING_PHASES,
    MOVE_SCORING_SCHEMA_VERSION,
    ROLE_ASSIGNMENT_METHOD,
    _atomic_json,
    _build_move_scoring,
    _record_tracks,
    _role_timeline,
    _stitch_track_fragments,
)


def _manifests(paths: list[Path]) -> list[Path]:
    found = []
    for path in paths or [Path("songs")]:
        if path.is_file():
            found.append(path)
        elif (path / "song.json").is_file():
            found.append(path / "song.json")
        elif path.is_dir():
            found.extend(path.glob("*/song.json"))
        else:
            raise ValueError(f"song path does not exist: {path}")
    return sorted(set(item.resolve() for item in found))


def _current_scoring(scoring: object) -> bool:
    return isinstance(scoring, dict) and (
        scoring.get("schema_version") == MOVE_SCORING_SCHEMA_VERSION
        and scoring.get("feature") == MOVE_SCORING_FEATURE
        and scoring.get("phase_count") == MOVE_SCORING_PHASES
        and bool(scoring.get("definitions"))
        and bool(scoring.get("segments"))
    )


def refresh_manifest(path: Path, *, dry_run: bool = False) -> bool:
    song = json.loads(path.read_text(encoding="utf-8"))
    choreography = song.get("choreography")
    if not isinstance(choreography, dict) or not choreography.get("timeline"):
        return False
    roles_current = choreography.get("role_assignment") == ROLE_ASSIGNMENT_METHOD
    scoring_current = _current_scoring(choreography.get("move_scoring"))
    if roles_current and scoring_current:
        return False

    dancers = choreography.get("dancers") or choreography.get("dancer_track_ids")
    if not isinstance(dancers, list) or not dancers:
        raise ValueError(f"{path}: full video extraction required; no stable dancer roles")
    if dry_run:
        return True

    timeline = choreography["timeline"]
    refreshed_dancers = choreography.get("dancers")
    lead_dancer_index = choreography.get("lead_dancer_index")
    rerole = not roles_current and len(dancers) > 1
    if rerole:
        track_stats = {}
        try:
            timeline = _stitch_track_fragments(timeline)
            for frame in timeline:
                _record_tracks(track_stats, frame.get("people", []))
            seeds = [
                int(dancer["seed_track_id"])
                for dancer in dancers
                if isinstance(dancer, dict)
                and dancer.get("seed_track_id") is not None
                and int(dancer["seed_track_id"]) in track_stats
            ]
            timeline, refreshed_dancers, lead_dancer_index = _role_timeline(
                timeline, len(dancers), seeds, track_stats, 0
            )
        except (KeyError, TypeError, ValueError, RuntimeError) as exc:
            raise ValueError(
                f"{path}: saved role timeline cannot be refreshed: {exc}"
            ) from exc

    scoring = _build_move_scoring(
        timeline,
        len(dancers),
        song.get("bpm"),
        song.get("beat_offset", 0.0),
    )
    if not _current_scoring(scoring):
        raise ValueError(f"{path}: saved timeline produced no usable dance moves")
    if rerole:
        representative_ids = [dancer["track_id"] for dancer in refreshed_dancers]
        choreography.update(
            {
                "timeline": timeline,
                "dancers": refreshed_dancers,
                "dancer_track_ids": representative_ids,
                "lead_dancer_index": lead_dancer_index,
                "lead_track_id": representative_ids[lead_dancer_index],
            }
        )
    choreography["role_assignment"] = ROLE_ASSIGNMENT_METHOD
    choreography["move_scoring"] = scoring
    backup = path.with_name(f"{path.name}.bak")
    if not backup.exists():
        shutil.copy2(path, backup)
    _atomic_json(path, song)
    return True


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Refresh outdated extraction artifacts in local song packages."
    )
    parser.add_argument("packages", nargs="*", type=Path)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    try:
        manifests = _manifests(args.packages)
        changed = failed = 0
        for manifest in manifests:
            try:
                stale = refresh_manifest(manifest, dry_run=args.dry_run)
                action = "would refresh" if args.dry_run else "refreshed"
                print(f"{action}: {manifest}" if stale else f"current: {manifest}")
                changed += int(stale)
            except (OSError, ValueError, json.JSONDecodeError) as exc:
                print(f"error: {exc}", file=sys.stderr)
                failed += 1
        current = len(manifests) - changed - failed
        print(f"{changed} stale, {current} current, {failed} failed")
        return int(bool(failed))
    except (OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
