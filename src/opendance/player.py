"""Presentation-only player for extracted OpenDance song packages."""

from __future__ import annotations

from pathlib import Path
import sys

from cyclopts import App

from .app import _run, song_media_path
from .game import load_catalog, target_poses


def load_player_song(path: str | Path) -> dict:
    manifest = Path(path).expanduser()
    if manifest.is_dir():
        manifest /= "song.json"
    if not manifest.is_file():
        raise ValueError(f"song manifest does not exist: {manifest}")
    songs = load_catalog(manifest)
    if len(songs) != 1:
        raise ValueError("the player accepts exactly one extracted song")
    song = songs[0]
    song["_root"] = str(manifest.resolve().parent)
    target_poses(song, 0.0)
    if not (
        song_media_path(song, "video")
        or song_media_path(song, "audio")
        or (song.get("bpm") and song.get("duration"))
    ):
        raise ValueError("song has no playable audio or video media")
    return song


def main() -> int:
    cli = App(name="opendance-player", help="Play one extracted OpenDance song.")

    @cli.default
    def launch(song: Path, video: bool = True) -> int:
        """Play a song manifest or package directory.

        Parameters
        ----------
        song:
            Extracted song.json or the directory containing it.
        video:
            Show the original video behind the extracted poses when available.
        """

        return _run(
            presentation_song=load_player_song(song),
            presentation_video=video,
        )

    try:
        return cli(result_action="return_value")
    except (OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["load_player_song", "main"]
