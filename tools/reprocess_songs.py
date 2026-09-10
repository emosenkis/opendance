#!/usr/bin/env python3
"""Refresh stale move-scoring artifacts from local extracted timelines."""

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
    _atomic_json,
    _build_move_scoring,
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


def _current(scoring: object) -> bool:
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
    if _current(choreography.get("move_scoring")):
        return False

    dancers = choreography.get("dancers") or choreography.get("dancer_track_ids")
    if not isinstance(dancers, list) or not dancers:
        raise ValueError(f"{path}: full video extraction required; no stable dancer roles")
    if dry_run:
        return True

    scoring = _build_move_scoring(choreography["timeline"], len(dancers))
    if not _current(scoring):
        raise ValueError(f"{path}: saved timeline produced no usable dance moves")
    choreography["move_scoring"] = scoring
    backup = path.with_name(f"{path.name}.bak")
    if not backup.exists():
        shutil.copy2(path, backup)
    _atomic_json(path, song)
    return True


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Refresh outdated move scoring in local extracted song packages."
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
