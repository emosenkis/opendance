"""Safe, user-facing song import helpers."""

from __future__ import annotations

import json
import math
import os
import re
import shutil
import subprocess
import threading
import time
import tomllib
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlsplit

from .extract import extract_song


_DOMAIN = re.compile(
    r"(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)(?:\.(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?))*"
)
URL_HELPER_TIMEOUT = 30 * 60
DEFAULT_CONFIG_PATH = Path(__file__).with_name("content") / "default_config.toml"


@dataclass(frozen=True)
class ImportOptions:
    """The extraction choices useful outside the extractor CLI."""

    title: str | None = None
    artist: str | None = None
    dancer_count: int = 1
    trim_start: float = 0.0
    trim_end: float = 0.0
    hide_video_intro: float = 0.0
    lrc: str | os.PathLike[str] | None = None


def config_path() -> Path:
    """Return the XDG location used for OpenDance's editable TOML config."""

    root = os.environ.get("XDG_CONFIG_HOME")
    return (
        Path(root).expanduser() / "opendance/config.toml"
        if root
        else Path.home() / ".config/opendance/config.toml"
    )


def library_path() -> Path:
    """Return the writable library used by settings-screen imports."""

    if configured := os.environ.get("OPENDANCE_LIBRARY"):
        return Path(configured).expanduser()
    if os.name == "nt" and (local := os.environ.get("LOCALAPPDATA")):
        return Path(local).expanduser() / "OpenDance/songs"
    data = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share"))
    return data.expanduser() / "opendance/songs"


def _domain(value: Any) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise ValueError("URL helper domains must be non-empty strings")
    if value == "*":
        return value
    try:
        domain = value.rstrip(".").casefold().encode("idna").decode("ascii")
    except UnicodeError as exc:
        raise ValueError(f"invalid URL helper domain: {value!r}") from exc
    if not domain or not _DOMAIN.fullmatch(domain):
        raise ValueError(f"invalid URL helper domain: {value!r}")
    return domain


def load_url_helpers(
    path: str | os.PathLike[str] | None = None,
) -> list[tuple[tuple[str, ...], tuple[str, ...]]]:
    """Load user and built-in ``[[url_helpers]]`` domain/argv entries."""

    source = Path(path) if path is not None else config_path()
    helpers = []
    sources = (source,) if source == DEFAULT_CONFIG_PATH else (source, DEFAULT_CONFIG_PATH)
    for current in sources:
        try:
            with current.open("rb") as stream:
                payload = tomllib.load(stream)
        except FileNotFoundError:
            continue
        except (OSError, tomllib.TOMLDecodeError) as exc:
            raise ValueError(f"could not read URL helper config {current}: {exc}") from exc
        rows = payload.get("url_helpers", [])
        if not isinstance(rows, list):
            raise ValueError("config url_helpers must be an array of tables")
        for index, row in enumerate(rows, 1):
            if not isinstance(row, dict):
                raise ValueError(f"url_helpers entry {index} must be a table")
            domains = row.get("domains")
            command = row.get("command")
            if not isinstance(domains, list) or not domains:
                raise ValueError(f"url_helpers entry {index} domains must be a non-empty array")
            if (
                not isinstance(command, list)
                or not command
                or not all(isinstance(part, str) and "\0" not in part for part in command)
                or not command[0]
            ):
                raise ValueError(f"url_helpers entry {index} command must be a non-empty argv array")
            helpers.append((tuple(_domain(item) for item in domains), tuple(command)))
    return helpers


def _url_host(url: str) -> str:
    if not isinstance(url, str) or url != url.strip() or any(
        ord(character) < 32 or ord(character) == 127 for character in url
    ):
        raise ValueError("URL must be a single HTTP or HTTPS URL")
    try:
        parsed = urlsplit(url)
        host = parsed.hostname
        _ = parsed.port
    except ValueError as exc:
        raise ValueError("URL must be a valid HTTP or HTTPS URL") from exc
    if parsed.scheme.casefold() not in {"http", "https"} or not host:
        raise ValueError("URL must be a valid HTTP or HTTPS URL")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("URL credentials are not allowed")
    try:
        return host.rstrip(".").casefold().encode("idna").decode("ascii")
    except UnicodeError as exc:
        raise ValueError("URL contains an invalid host name") from exc


def _helper_command(
    url: str, path: str | os.PathLike[str] | None = None
) -> tuple[str, ...]:
    host = _url_host(url)
    source = Path(path) if path is not None else config_path()
    matches = [
        (0 if domain == "*" else len(domain), command)
        for domains, command in load_url_helpers(source)
        for domain in domains
        if domain == "*" or host == domain or host.endswith(f".{domain}")
    ]
    if not matches:
        raise ValueError(
            f"no download helper is configured for {host}; add one to {source}"
        )
    command = max(matches, key=lambda item: item[0])[1]
    if "{DOWNLOAD_DIR}" in command:
        download_dir = library_path() / ".downloads"
        download_dir.mkdir(parents=True, exist_ok=True)
    else:
        download_dir = Path()
    return tuple(
        url if part == "{URL}" else str(download_dir) if part == "{DOWNLOAD_DIR}" else part
        for part in command
    )


def download_url(
    url: str,
    config: str | os.PathLike[str] | None = None,
    *,
    timeout: float = URL_HELPER_TIMEOUT,
    cancel_event: Any | None = None,
    progress_callback: Callable[[str], None] | None = None,
) -> Path:
    """Run the configured argv with ``URL`` set and return its downloaded file."""

    command = _helper_command(url, config)
    environment = {**os.environ, "URL": url}
    try:
        if cancel_event is not None and cancel_event.is_set():
            raise InterruptedError("song import cancelled")
        if cancel_event is None and progress_callback is None:
            result = subprocess.run(
                command,
                env=environment,
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
        else:
            process = subprocess.Popen(
                command,
                env=environment,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
            )
            stdout: list[str] = []
            stderr: list[str] = []

            def read_stream(stream: Any, output: list[str], progress: bool = False) -> None:
                for line in stream:
                    if progress and line.startswith("opendance-progress:"):
                        if progress_callback:
                            progress_callback(line.removeprefix("opendance-progress:").strip())
                    else:
                        output.append(line)

            readers = (
                threading.Thread(
                    target=read_stream,
                    args=(process.stdout, stdout, True),
                    daemon=True,
                ),
                threading.Thread(
                    target=read_stream,
                    args=(process.stderr, stderr),
                    daemon=True,
                ),
            )
            for reader in readers:
                reader.start()
            deadline = time.monotonic() + timeout
            interrupted = False
            timed_out = False
            while process.poll() is None:
                interrupted = bool(cancel_event is not None and cancel_event.is_set())
                timed_out = time.monotonic() >= deadline
                if interrupted or timed_out:
                    process.terminate()
                    break
                try:
                    process.wait(timeout=max(0.01, min(0.1, deadline - time.monotonic())))
                except subprocess.TimeoutExpired:
                    pass
            if interrupted or timed_out:
                try:
                    process.wait(timeout=1)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
            for reader in readers:
                reader.join()
            process.stdout.close()
            process.stderr.close()
            if interrupted:
                raise InterruptedError("song import cancelled")
            if timed_out:
                raise subprocess.TimeoutExpired(command, timeout)
            result = subprocess.CompletedProcess(
                command, process.returncode, "".join(stdout), "".join(stderr)
            )
    except InterruptedError:
        raise
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"URL download helper timed out after {timeout:g} seconds") from exc
    except OSError as exc:
        raise RuntimeError(f"could not start URL download helper {command[0]!r}: {exc}") from exc
    if result.returncode:
        detail = result.stderr.strip() or result.stdout.strip() or "no error details"
        raise RuntimeError(f"URL download helper failed ({result.returncode}): {detail[:500]}")
    lines = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    if len(lines) != 1:
        raise RuntimeError("URL download helper must print exactly one file path")
    try:
        downloaded = Path(lines[0]).expanduser().resolve(strict=True)
    except (OSError, ValueError) as exc:
        raise RuntimeError(f"URL download helper returned an invalid path: {lines[0]!r}") from exc
    if not downloaded.is_file():
        raise RuntimeError(f"URL download helper did not return a file: {downloaded}")
    return downloaded


def _qt_video_metadata(video: Path) -> dict[str, Any]:
    """Use the bundled Qt media backend when no ffprobe executable exists."""

    try:
        from PySide6.QtCore import QCoreApplication, QEventLoop, QTimer, QUrl
        from PySide6.QtMultimedia import QMediaMetaData, QMediaPlayer
    except ImportError:
        return {}
    if QCoreApplication.instance() is None:
        return {}
    player = QMediaPlayer()
    loop = QEventLoop()
    player.mediaStatusChanged.connect(
        lambda status: loop.quit()
        if status
        in (
            QMediaPlayer.MediaStatus.LoadedMedia,
            QMediaPlayer.MediaStatus.InvalidMedia,
        )
        else None
    )
    QTimer.singleShot(5_000, loop.quit)
    player.setSource(QUrl.fromLocalFile(str(video.resolve())))
    loop.exec()
    metadata = player.metaData()
    result: dict[str, Any] = {}
    title = metadata.stringValue(QMediaMetaData.Key.Title).strip()
    artist = next(
        (
            metadata.stringValue(key).strip()
            for key in (
                QMediaMetaData.Key.ContributingArtist,
                QMediaMetaData.Key.AlbumArtist,
                QMediaMetaData.Key.LeadPerformer,
                QMediaMetaData.Key.Author,
            )
            if metadata.stringValue(key).strip()
        ),
        "",
    )
    if title:
        result["title"] = title
    if artist:
        result["artist"] = artist
    if player.duration() > 0:
        result["duration"] = player.duration() / 1_000.0
    return result


def probe_video_metadata(
    path: str | os.PathLike[str], *, ffprobe: str | None = "ffprobe"
) -> dict[str, Any]:
    """Read embedded title, artist, and duration with ffprobe or bundled Qt."""

    video = Path(path).expanduser()
    if not video.is_file():
        raise ValueError(f"video file does not exist: {video}")
    artist, separator, title = video.stem.partition(" - ")
    metadata: dict[str, Any] = {
        "title": title.strip() if separator and title.strip() else video.stem,
        "artist": artist.strip() if separator and artist.strip() else "Unknown Artist",
        "duration": None,
    }
    try:
        if not ffprobe:
            raise OSError
        result = subprocess.run(
            [
                ffprobe,
                "-v",
                "error",
                "-show_entries",
                "format=duration:format_tags=title,artist,album_artist,author,performer",
                "-of",
                "json",
                str(video.resolve()),
            ],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
        payload = json.loads(result.stdout) if result.returncode == 0 else {}
    except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError):
        return {**metadata, **_qt_video_metadata(video)}
    if result.returncode:
        return {**metadata, **_qt_video_metadata(video)}
    raw_details = payload.get("format", {}) if isinstance(payload, dict) else {}
    details = raw_details if isinstance(raw_details, dict) else {}
    tags = details.get("tags", {}) if isinstance(details, dict) else {}
    normalized = (
        {str(key).casefold(): value for key, value in tags.items()}
        if isinstance(tags, dict)
        else {}
    )
    title = normalized.get("title")
    artist = next(
        (
            normalized.get(key)
            for key in ("artist", "album_artist", "author", "performer")
            if normalized.get(key)
        ),
        None,
    )
    if isinstance(title, str) and title.strip():
        metadata["title"] = title.strip()
    if isinstance(artist, str) and artist.strip():
        metadata["artist"] = artist.strip()
    try:
        duration = float(details.get("duration"))
    except (TypeError, ValueError):
        duration = 0.0
    if math.isfinite(duration) and duration > 0:
        metadata["duration"] = duration
    return metadata


def _slug(value: str) -> str:
    ascii_value = (
        unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode()
    )
    return re.sub(r"[^a-z0-9]+", "-", ascii_value.lower()).strip("-") or "song"


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


def extract_imported_song(
    video: str | os.PathLike[str],
    library: str | os.PathLike[str],
    *,
    engine: Any,
    options: ImportOptions = ImportOptions(),
    metadata: dict[str, Any] | None = None,
    progress_callback: Callable[[int, int | None, float], None] | None = None,
    cancel_event: Any | None = None,
    existing_ids: set[str] | None = None,
) -> Path:
    """Create a non-overwriting song package using only end-user options."""

    details = {
        "title": Path(video).stem,
        "artist": "Unknown Artist",
        **(metadata if metadata is not None else probe_video_metadata(video)),
    }
    title = str(options.title or details.get("title") or Path(video).stem).strip()
    artist = str(options.artist or details.get("artist") or "Unknown Artist").strip()
    if not title or not artist:
        raise ValueError("title and artist must not be empty")
    if (
        isinstance(options.dancer_count, bool)
        or not isinstance(options.dancer_count, int)
        or not 1 <= options.dancer_count <= 6
    ):
        raise ValueError("dancer count must be between 1 and 6")
    if options.dancer_count > engine.max_people:
        raise ValueError(f"dancer count exceeds detector limit of {engine.max_people}")
    trim_start = _seconds(options.trim_start, "trim start")
    trim_end = _seconds(options.trim_end, "trim end")
    hide_intro = _seconds(options.hide_video_intro, "hidden video intro")
    try:
        duration = float(details.get("duration"))
    except (TypeError, ValueError):
        duration = 0.0
    if math.isfinite(duration) and duration > 0:
        if trim_start + trim_end >= duration:
            raise ValueError("trim start and end remove the entire video")
        if hide_intro >= duration - trim_start - trim_end:
            raise ValueError("hidden video intro must end before the trimmed video")
    root = Path(library).expanduser()
    root.mkdir(parents=True, exist_ok=True)
    base = _slug(f"{artist}-{title}")
    suffix = 1
    while True:
        song_id = base if suffix == 1 else f"{base}-{suffix}"
        destination = root / song_id
        if song_id in (existing_ids or ()):
            suffix += 1
            continue
        try:
            destination.mkdir()
            break
        except FileExistsError:
            suffix += 1
    try:
        return extract_song(
            video,
            destination,
            engine=engine,
            lrc=options.lrc,
            title=title,
            artist=artist,
            song_id=song_id,
            dancer_count=options.dancer_count,
            copy_video=True,
            move_video=bool(details.get("_downloaded")),
            trim_start=trim_start,
            trim_end=trim_end,
            hide_video_intro=hide_intro,
            show_progress=False,
            progress_callback=progress_callback,
            cancel_event=cancel_event,
        )
    except Exception:
        if destination.is_dir() and not (destination / "song.json").exists():
            shutil.rmtree(destination, ignore_errors=True)
        raise


__all__ = (
    "DEFAULT_CONFIG_PATH",
    "ImportOptions",
    "URL_HELPER_TIMEOUT",
    "config_path",
    "download_url",
    "extract_imported_song",
    "library_path",
    "load_url_helpers",
    "probe_video_metadata",
)
