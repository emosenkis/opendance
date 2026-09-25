from __future__ import annotations

import base64
import json
import math
import os
import queue
import signal
import sys
import threading
import time
from importlib.resources import files
from pathlib import Path
from typing import Any

from cyclopts import App
from PySide6.QtCore import (
    Property,
    QObject,
    QSettings,
    QTimer,
    QUrl,
    Signal,
    Slot,
)
from PySide6.QtGui import QIcon, QImage
from PySide6.QtMultimedia import (
    QAudioOutput,
    QCamera,
    QMediaCaptureSession,
    QMediaDevices,
    QMediaPlayer,
    QVideoSink,
)
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuick import QQuickWindow
from PySide6.QtWidgets import QApplication

from .audio import ensure_song_wav
from .game import (
    GameSession,
    assign_dancers,
    load_catalog,
    player_nickname,
    target_dancers,
    target_pose,
    target_poses,
)
from .importer import (
    ImportOptions,
    download_url,
    extract_imported_song,
    library_path,
    probe_video_metadata,
)
from .vision import GestureController


MAX_PLAYERS = 6
FEEDBACK_INTERVAL_SECONDS = 2.0


def _enabled(value: Any) -> bool:
    return str(value or "").strip().casefold() in {"1", "true", "yes", "on"}


def _feedback_due(now: float, last_shown: float) -> bool:
    return now - last_shown >= FEEDBACK_INTERVAL_SECONDS


def song_media_path(song: dict[str, Any], kind: str) -> Path | None:
    """Resolve a song's media reference and reject missing files."""

    reference = song.get(kind)
    if not reference:
        return None
    path = Path(str(reference)).expanduser()
    if not path.is_absolute() and song.get("_root"):
        path = Path(str(song["_root"])) / path
    return path.resolve() if path.is_file() else None


def song_manifest_metadata(path: str | os.PathLike[str]) -> dict[str, Any]:
    """Read one extracted manifest without retaining its dense choreography."""

    manifest = Path(path)
    prefix_lines = []
    found_choreography = False
    with manifest.open(encoding="utf-8") as source:
        for line in source:
            if line.startswith('  "choreography"'):
                found_choreography = True
                break
            prefix_lines.append(line)
    text = "".join(prefix_lines)
    song = json.loads(
        text.rstrip().removesuffix(",") + "\n}" if found_choreography else text
    )
    if not isinstance(song, dict) or not {"id", "title", "duration"}.issubset(song):
        raise ValueError("invalid extracted song metadata")
    metadata = {
        key: value
        for key, value in song.items()
        if key not in {"choreography", "extraction", "lyrics", "moves"}
    }
    metadata["_root"] = str(manifest.parent.resolve())
    metadata["_manifest"] = str(manifest.resolve())
    return metadata


def _song_seconds(song: dict[str, Any], key: str) -> float:
    try:
        value = float(song.get(key, 0.0))
    except (TypeError, ValueError):
        return 0.0
    return value if math.isfinite(value) and value >= 0 else 0.0


def song_preview(song: dict[str, Any]) -> dict[str, Any]:
    """Resolve one short, song-relative library preview for QML."""

    video = song_media_path(song, "video")
    audio = song_media_path(song, "audio")
    duration = _song_seconds(song, "duration")
    hidden_until = min(duration, _song_seconds(song, "video_hidden_until"))
    start = (
        _song_seconds(song, "preview_start")
        if "preview_start" in song
        else duration * 0.35
    )
    start = min(duration, max(hidden_until, start))
    length = min(
        _song_seconds(song, "preview_duration") or 8.0,
        max(0.0, duration - start),
    )
    media_start = _song_seconds(song, "media_start")
    return {
        "previewVideoUrl": QUrl.fromLocalFile(str(video)).toString() if video else "",
        "previewAudioUrl": QUrl.fromLocalFile(str(audio)).toString() if audio else "",
        "previewStartMs": round((media_start + start) * 1_000),
        "previewDurationMs": round(length * 1_000),
    }


def framing_nudge(bbox: Any) -> str:
    """Return a camera-distance cue for one normalized ``x, y, w, h`` box."""

    try:
        if len(bbox) != 4:
            return ""
        left, top, width, height = (float(value) for value in bbox)
    except (TypeError, ValueError):
        return ""
    if not all(math.isfinite(value) for value in (left, top, width, height)):
        return ""
    if width <= 0 or height <= 0:
        return ""
    if top <= 0.01 or top + height >= 0.99:
        return "MOVE BACK"
    if height < 0.25:
        return "MOVE FORWARD"
    return ""


def _interpolated_media_time(
    position_s: float, updated_at: float | None, now: float, playing: bool
) -> float:
    return position_s + max(0.0, now - updated_at) if playing and updated_at is not None else position_s


class PoseThread(threading.Thread):
    """Runs inference on only the newest frame so latency cannot accumulate."""

    def __init__(self, bridge: "Backend") -> None:
        super().__init__(name="pose-inference", daemon=True)
        self.bridge = bridge
        self.frames: queue.Queue[tuple[QImage, float]] = queue.Queue(maxsize=1)
        self.stop_event = threading.Event()
        self.reset_event = threading.Event()
        self.engine: Any = None
        self._reported_device = False

    def submit(self, image: QImage, captured_ms: float) -> None:
        while True:
            try:
                self.frames.put_nowait((image, captured_ms))
                return
            except queue.Full:
                try:
                    self.frames.get_nowait()
                except queue.Empty:
                    pass

    def run(self) -> None:
        label = "RTMPose" if self.bridge._pose_backend == "rtmpose" else "YOLO26 Pose"
        self.bridge._vision_status.emit(f"Loading {label}…")
        try:
            from .vision import create_pose_engine

            self.engine = create_pose_engine(
                self.bridge._pose_backend,
                rtmpose_mode=self.bridge._rtmpose_mode,
                max_people=self.bridge._max_players,
            )
            self.engine.load()
            self.bridge._vision_status.emit(f"{label} ready")
        except Exception as exc:
            self.bridge._vision_status.emit(f"Pose unavailable: {exc}")
            return

        while not self.stop_event.is_set():
            try:
                image, captured_ms = self.frames.get(timeout=0.2)
            except queue.Empty:
                continue
            try:
                if self.reset_event.is_set():
                    self.engine.reset_tracking()
                    self.reset_event.clear()
                bgr = image.convertToFormat(QImage.Format.Format_BGR888)
                width, height = bgr.width(), bgr.height()
                stride = bgr.bytesPerLine()
                import numpy as np

                frame = np.frombuffer(bgr.constBits(), dtype=np.uint8, count=stride * height)
                frame = frame.reshape(height, stride)[:, : width * 3].reshape(height, width, 3).copy()
                result = self.engine.infer(frame, captured_ms=captured_ms)
                if not self._reported_device:
                    print(
                        f"OpenDance pose: {result.get('device', 'unknown')} "
                        f"({width}x{height} source, {self.engine.imgsz}px model input)",
                        file=sys.stderr,
                        flush=True,
                    )
                    self._reported_device = True
                self.bridge._vision_result.emit(result)
            except Exception as exc:
                self.bridge._vision_status.emit(f"Pose error: {exc}")

    def close(self) -> None:
        self.stop_event.set()

    def reset(self) -> None:
        self.reset_event.set()
        while not self.frames.empty():
            try:
                self.frames.get_nowait()
            except queue.Empty:
                break


class Gamepad:
    def __init__(self, emit: Any) -> None:
        self.emit = emit
        self.pygame: Any = None
        self.joys: dict[int, Any] = {}
        self.held: dict[str, bool] = {}
        try:
            os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")
            import pygame

            pygame.display.init()
            pygame.joystick.init()
            pygame.event.set_allowed(
                [pygame.JOYDEVICEADDED, pygame.JOYDEVICEREMOVED, pygame.JOYBUTTONDOWN]
            )
            self.pygame = pygame
            self._refresh()
        except Exception:
            self.pygame = None

    def _refresh(self) -> None:
        if not self.pygame:
            return
        self.joys.clear()
        for index in range(self.pygame.joystick.get_count()):
            joy = self.pygame.joystick.Joystick(index)
            joy.init()
            self.joys[joy.get_instance_id()] = joy

    def poll(self) -> None:
        if not self.pygame:
            return
        try:
            for event in self.pygame.event.get():
                if event.type in (self.pygame.JOYDEVICEADDED, self.pygame.JOYDEVICEREMOVED):
                    self._refresh()
                elif event.type == self.pygame.JOYBUTTONDOWN:
                    action = {0: "accept", 1: "back", 6: "back", 7: "pause"}.get(event.button)
                    if action:
                        self.emit(action)

            active: set[str] = set()
            for joy in self.joys.values():
                x = joy.get_axis(0) if joy.get_numaxes() else 0.0
                y = joy.get_axis(1) if joy.get_numaxes() > 1 else 0.0
                hat = joy.get_hat(0) if joy.get_numhats() else (0, 0)
                if x < -0.55 or hat[0] < 0:
                    active.add("left")
                if x > 0.55 or hat[0] > 0:
                    active.add("right")
                if y < -0.55 or hat[1] > 0:
                    active.add("up")
                if y > 0.55 or hat[1] < 0:
                    active.add("down")
            for action in active:
                if not self.held.get(action):
                    self.emit(action)
            self.held = {action: True for action in active}
        except Exception:
            self.pygame = None


class Backend(QObject):
    changed = Signal()
    poseChanged = Signal()
    gameFrameChanged = Signal()
    playersChanged = Signal()
    feedbackChanged = Signal()
    gamepadAction = Signal(str)
    fullscreenRequested = Signal()
    quitRequested = Signal()
    songImportChanged = Signal()
    _vision_result = Signal(object)
    _vision_status = Signal(str)
    _import_prepared = Signal(object)
    _import_completed = Signal(str)
    _import_failed = Signal(str)
    _import_progress = Signal(object)
    _import_preview = Signal(object)

    def __init__(
        self,
        *,
        enable_alternate_sources: bool = False,
        join_gesture_only: bool = False,
        pose_backend: str = "yolo26",
        rtmpose_mode: str = "lightweight",
        presentation_song: dict[str, Any] | None = None,
        presentation_video: bool = True,
    ) -> None:
        super().__init__()
        # Static menu/settings changes refresh everything; hot paths use only
        # their narrow signal so QML does not rebuild the song library at 30 Hz.
        self.changed.connect(self.poseChanged.emit)
        self.changed.connect(self.gameFrameChanged.emit)
        self.changed.connect(self.playersChanged.emit)
        self.settings = QSettings("OpenDance", "OpenDance")
        self._pose_backend = str(
            os.environ.get("OPENDANCE_POSE_BACKEND", pose_backend)
        ).strip().casefold()
        if self._pose_backend not in {"yolo26", "rtmpose"}:
            raise ValueError("pose backend must be yolo26 or rtmpose")
        self._rtmpose_mode = str(
            os.environ.get("OPENDANCE_RTMPOSE_MODE", rtmpose_mode)
        ).strip().casefold()
        if self._rtmpose_mode not in {"lightweight", "balanced", "performance"}:
            raise ValueError(
                "RTMPose mode must be lightweight, balanced, or performance"
            )
        self._presentation_mode = presentation_song is not None
        self._presentation_video = bool(presentation_video)
        self._presentation_finished = False
        self._screen = "game" if self._presentation_mode else "library"
        self._settings_return_screen = "library"
        self._catalog = [dict(presentation_song)] if presentation_song else self._load_songs()
        self._loaded_song_index = 0 if self._presentation_mode else -1
        self._loaded_song = self._catalog[0] if self._presentation_mode else None
        self._song_index = 0
        self._points = int(self.settings.value("profile/points", 0))
        self._cameras: list[dict[str, Any]] = []
        self._camera_devices: dict[str, Any] = {}
        demo_requested = _enabled(os.environ.get("OPENDANCE_DEMO"))
        self._alternate_sources_enabled = (
            bool(enable_alternate_sources)
            or _enabled(os.environ.get("OPENDANCE_ENABLE_ALTERNATE_SOURCES"))
            or demo_requested
        )
        self._selected_source = "" if self._presentation_mode else (
            "demo" if demo_requested else str(self.settings.value("camera/source", ""))
        )
        self._capture_sink: QVideoSink | None = None
        self._coach_sink: QVideoSink | None = None
        self._capture_session = QMediaCaptureSession(self)
        self._camera: QCamera | None = None
        self._source_player = QMediaPlayer(self)
        self._source_audio = QAudioOutput(self)
        self._source_audio.setMuted(True)
        self._source_player.setAudioOutput(self._source_audio)
        self._source_player.setLoops(QMediaPlayer.Loops.Infinite)
        self._coach_player = QMediaPlayer(self)
        self._coach_audio = QAudioOutput(self)
        self._volume = float(self.settings.value("audio/volume", 0.82))
        self._coach_audio.setVolume(self._volume)
        self._coach_player.setAudioOutput(self._coach_audio)
        self._music_player = QMediaPlayer(self)
        self._music_audio = QAudioOutput(self)
        self._music_audio.setVolume(self._volume)
        self._music_player.setAudioOutput(self._music_audio)
        self._separate_audio = False
        self._clock_player = self._coach_player
        self._media_start_s = 0.0
        self._coach_seek_pending = False
        self._music_seek_pending = False
        self._media_position_s = 0.0
        self._media_position_at: float | None = None
        self._has_song_media = False
        self._coach_video_failed = False
        saved_video = str(self.settings.value("camera/video_path", ""))
        self._video_path = saved_video if Path(saved_video).is_file() else ""
        if self._selected_source == "file" and not self._video_path:
            self._selected_source = ""
        stinger_root = files("opendance").joinpath("assets/audio/stingers")
        self._stingers: dict[str, Any] = {}
        self._mixer: Any = None
        if not self._presentation_mode:
            try:
                os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")
                import pygame

                if not pygame.mixer.get_init():
                    pygame.mixer.init(
                        frequency=24_000, size=-16, channels=2, buffer=256
                    )
                self._mixer = pygame.mixer
                for name in ("perfect", "great", "good", "miss", "star", "unlock"):
                    effect = pygame.mixer.Sound(
                        str(stinger_root.joinpath(f"{name}.ogg"))
                    )
                    effect.set_volume(self._volume)
                    self._stingers[name] = effect
            except Exception as exc:
                print(f"OpenDance sound effects unavailable: {exc}", file=sys.stderr)
        self._session: GameSession | None = None
        self._pose_people: list[dict[str, Any]] = []
        self._target_pose: list[list[float]] = []
        self._players: list[dict[str, Any]] = []
        self._feedback: list[dict[str, Any]] = []
        self._last_feedback_at = -math.inf
        self._result: dict[str, Any] = {}
        self._song_time = 0.0
        self._countdown = 3
        self._countdown_started = 0.0
        self._play_started_at = 0.0
        self._paused = False
        self._model_status = "Demo tracking"
        self._inference_fps = 0.0
        self._last_inference_at = 0.0
        self._latency_ms = int(self.settings.value("game/latency_ms", 80))
        self._cues = self.settings.value("game/cues", True, type=bool)
        self._mini = False if self._presentation_mode else self.settings.value(
            "game/mini_view", True, type=bool
        )
        self._reduced_motion = self.settings.value(
            "ui/reduced_motion", False, type=bool
        )
        self._fullscreen = self.settings.value("ui/fullscreen", False, type=bool)
        self._max_players = MAX_PLAYERS
        self._gestures = GestureController(
            join_required=(
                bool(join_gesture_only)
                or _enabled(os.environ.get("OPENDANCE_JOIN_GESTURE_ONLY"))
                or self.settings.value("game/join_gesture_only", False, type=bool)
            )
        )
        self._last_frame_ms = 0.0
        self._pose_thread: PoseThread | None = None
        self._song_import: dict[str, Any] = {}
        self._song_import_busy = False
        self._song_import_status = ""
        self._song_import_progress = -1.0
        self._song_import_preview: dict[str, Any] = {}
        self._song_import_worker: threading.Thread | None = None
        self._song_import_cancel = threading.Event()
        self._import_capture_paused = False

        self._vision_result.connect(self._on_pose_result)
        self._vision_status.connect(self._set_model_status)
        self._import_prepared.connect(self._on_import_prepared)
        self._import_completed.connect(self._on_import_completed)
        self._import_failed.connect(self._on_import_failed)
        self._import_progress.connect(self._on_import_progress)
        self._import_preview.connect(self._on_import_preview)
        self._source_player.errorOccurred.connect(
            lambda _error, message: self._set_model_status(f"Video source error: {message}")
        )
        self._coach_player.mediaStatusChanged.connect(self._coach_status)
        self._music_player.mediaStatusChanged.connect(self._music_status)
        self._coach_player.errorOccurred.connect(self._coach_error)
        self._coach_player.positionChanged.connect(
            lambda position: self._sync_media_position(self._coach_player, position)
        )
        self._music_player.positionChanged.connect(
            lambda position: self._sync_media_position(self._music_player, position)
        )
        self._media_devices: QMediaDevices | None = None
        if not self._presentation_mode:
            self._media_devices = QMediaDevices(self)
            self._media_devices.videoInputsChanged.connect(self.refreshCameras)
            self.refreshCameras()

        self._ticker = QTimer(self)
        self._ticker.setInterval(33)
        self._ticker.timeout.connect(self._tick)
        self._ticker.start()
        self._gamepad = Gamepad(self.gamepadAction.emit)
        if self._presentation_mode:
            QTimer.singleShot(0, self._start_presentation)

    def _load_songs(self) -> list[dict[str, Any]]:
        package_root = files("opendance")
        catalog = load_catalog(package_root.joinpath("content/songs.json"))
        for song in catalog:
            song["_root"] = str(package_root)
        roots = dict.fromkeys((library_path().resolve(), (Path.cwd() / "songs").resolve()))
        for root in roots:
            for manifest in root.glob("*/song.json") if root.is_dir() else ():
                try:
                    song = song_manifest_metadata(manifest)
                    if song.get("id") and all(item.get("id") != song["id"] for item in catalog):
                        catalog.append(song)
                except (OSError, ValueError):
                    continue
        return catalog

    @staticmethod
    def _unlock_cost(song: dict[str, Any]) -> int:
        return int(song.get("unlock_cost", song.get("unlock", 0)))

    def _song_for_ui(self, song: dict[str, Any]) -> dict[str, Any]:
        song_id = str(song.get("id", ""))
        return dict(
            (
                (key, value)
                for key, value in song.items()
                if key not in {"choreography", "lyrics", "moves"}
            ),
            locked=self._points < self._unlock_cost(song),
            unlock_cost=self._unlock_cost(song),
            bestStars=int(self.settings.value(f"profile/best/{song_id}/stars", 0)),
            **song_preview(song),
        )

    @property
    def _selected_song(self) -> dict[str, Any]:
        if not self._catalog:
            return {}
        song = self._catalog[self._song_index]
        manifest = song.get("_manifest")
        if not manifest:
            return song
        if self._loaded_song_index != self._song_index or self._loaded_song is None:
            loaded = json.loads(Path(str(manifest)).read_text(encoding="utf-8"))
            if not isinstance(loaded, dict) or loaded.get("id") != song.get("id"):
                raise ValueError(f"invalid song manifest: {manifest}")
            loaded["_root"] = song["_root"]
            self._loaded_song_index = self._song_index
            self._loaded_song = loaded
        return self._loaded_song

    @property
    def _selected_song_metadata(self) -> dict[str, Any]:
        return self._catalog[self._song_index] if self._catalog else {}

    @Property(str, notify=changed)
    def screen(self) -> str:
        return self._screen

    @Property("QVariantList", notify=changed)
    def songs(self) -> list[dict[str, Any]]:
        return [self._song_for_ui(song) for song in self._catalog]

    @Property("QVariantMap", notify=changed)
    def selectedSong(self) -> dict[str, Any]:
        if not self._catalog:
            return {}
        return self._song_for_ui(self._catalog[self._song_index])

    @Property(int, notify=changed)
    def selectedSongIndex(self) -> int:
        return self._song_index

    @Property(int, notify=changed)
    def points(self) -> int:
        return self._points

    @Property("QVariantList", notify=changed)
    def cameras(self) -> list[dict[str, Any]]:
        return self._cameras

    @Property(str, notify=changed)
    def selectedSource(self) -> str:
        return self._selected_source

    @Property("QVariantMap", notify=songImportChanged)
    def songImport(self) -> dict[str, Any]:
        return dict(self._song_import)

    @Property(bool, notify=songImportChanged)
    def songImportBusy(self) -> bool:
        return self._song_import_busy

    @Property(str, notify=songImportChanged)
    def songImportStatus(self) -> str:
        return self._song_import_status

    @Property(float, notify=songImportChanged)
    def songImportProgress(self) -> float:
        return self._song_import_progress

    @Property("QVariantMap", notify=songImportChanged)
    def songImportPreview(self) -> dict[str, Any]:
        return dict(self._song_import_preview)

    @Property(bool, constant=True)
    def alternateSourcesEnabled(self) -> bool:
        return self._alternate_sources_enabled

    @Property(bool, constant=True)
    def presentationMode(self) -> bool:
        return self._presentation_mode

    @Property(bool, notify=changed)
    def presentationFinished(self) -> bool:
        return self._presentation_finished

    @Property("QVariantList", notify=poseChanged)
    def posePeople(self) -> list[dict[str, Any]]:
        return self._pose_people

    @Property("QVariantList", notify=gameFrameChanged)
    def targetPose(self) -> list[list[float]]:
        return [list(point) for point in self._target_pose]

    @Property("QVariantList", notify=gameFrameChanged)
    def targetDancers(self) -> list[dict[str, Any]]:
        if self._screen not in {"countdown", "game"}:
            return []
        return target_dancers(self._selected_song, self._song_time)

    @Property("QVariantList", notify=gameFrameChanged)
    def cuePose(self) -> list[list[float]]:
        if self._screen != "game":
            return []
        return [
            list(point)
            for point in target_pose(
                self._selected_song, min(self.songDuration, self._song_time + 1.5)
            )
        ]

    @Property("QVariantList", notify=gameFrameChanged)
    def cueDancers(self) -> list[dict[str, Any]]:
        if self._screen != "game":
            return []
        cue = self._session.next_move_cue(self._song_time) if self._session else None
        if cue is not None:
            return cue["dancers"]
        return target_dancers(
            self._selected_song, min(self.songDuration, self._song_time + 1.5)
        )

    @Property("QVariantList", notify=playersChanged)
    def players(self) -> list[dict[str, Any]]:
        return self._players

    @Property(float, notify=gameFrameChanged)
    def songTime(self) -> float:
        return self._song_time

    @Property(float, notify=changed)
    def songDuration(self) -> float:
        return float(self._selected_song_metadata.get("duration", 1.0))

    @Property(float, notify=changed)
    def videoHiddenUntil(self) -> float:
        return _song_seconds(self._selected_song_metadata, "video_hidden_until")

    @Property(float, notify=gameFrameChanged)
    def progress(self) -> float:
        return min(1.0, self._song_time / max(0.001, self.songDuration))

    def _timeline_text(self, key: str, future: bool = False) -> str:
        if self._screen != "game":
            return ""
        rows = self._selected_song.get(key, [])
        found = ""
        for row in rows:
            when = float(row.get("time", 0))
            if future and when > self._song_time:
                return str(row.get("text") or row.get("name") or "")
            if not future and when <= self._song_time:
                found = str(row.get("text") or row.get("name") or "")
            elif not future:
                break
        return found

    @Property(str, notify=gameFrameChanged)
    def currentLyric(self) -> str:
        return self._timeline_text("lyrics")

    @Property(str, notify=gameFrameChanged)
    def nextLyric(self) -> str:
        return self._timeline_text("lyrics", True)

    @Property(str, notify=gameFrameChanged)
    def nextMove(self) -> str:
        cue = (
            self._session.next_move_cue(self._song_time)
            if self._screen == "game" and self._session
            else None
        )
        if cue is not None:
            return cue["name"]
        return self._timeline_text("moves", True)

    @Property(int, notify=gameFrameChanged)
    def countdown(self) -> int:
        return self._countdown

    @Property(str, notify=poseChanged)
    def modelStatus(self) -> str:
        return self._model_status

    @Property(float, notify=poseChanged)
    def inferenceFps(self) -> float:
        return self._inference_fps

    @Property("QVariantList", notify=poseChanged)
    def framingCues(self) -> list[dict[str, Any]]:
        if self._selected_source == "demo":
            return []
        name_by_track = {}
        if self._session:
            name_by_track = {
                slot.track_id: player_nickname(slot.index)
                for slot in self._session.player_slots.active_slots
                if slot.track_id is not None
            }
        people = sorted(
            (
                person
                for person in self._pose_people
                if self._gestures.admits(person.get("track_id"))
            ),
            key=lambda person: -sum(
                float(value) for value in person.get("bbox", (0, 0, 0, 0))[::2]
            ),
        )
        cues = []
        for index, person in enumerate(people):
            label = framing_nudge(person.get("bbox"))
            if label:
                track_id = person.get("track_id")
                cues.append(
                    {
                        "track_id": track_id,
                        "name": name_by_track.get(track_id, player_nickname(index)),
                        "label": label,
                    }
                )
        return cues

    @Property(int, notify=changed)
    def latencyMs(self) -> int:
        return self._latency_ms

    @Property(bool, notify=changed)
    def joinGestureOnly(self) -> bool:
        return self._gestures.join_required

    @Property(str, notify=poseChanged)
    def gestureStatus(self) -> str:
        if self._gestures.controller_id is not None:
            return "GESTURE CONTROL ACTIVE • POINT TO MOVE • CLAP TO SELECT"
        if self._gestures.join_required:
            return "RAISE BOTH HANDS TO JOIN + TAKE CONTROL"
        return "RAISE BOTH HANDS TO TAKE GESTURE CONTROL"

    @Property(bool, notify=changed)
    def paused(self) -> bool:
        return self._paused

    @Property(bool, notify=changed)
    def cuesEnabled(self) -> bool:
        return self._cues

    @Property(bool, notify=changed)
    def miniViewEnabled(self) -> bool:
        return self._mini

    @Property(float, notify=changed)
    def volume(self) -> float:
        return self._volume

    @Property(bool, notify=changed)
    def reducedMotion(self) -> bool:
        return self._reduced_motion

    @Property(str, notify=changed)
    def coachMode(self) -> str:
        return "video" if self._shows_coach_video() else "avatar"

    def _shows_coach_video(self) -> bool:
        show_video = not self._presentation_mode or self._presentation_video
        return bool(
            show_video
            and song_media_path(self._selected_song_metadata, "video")
            and not self._coach_video_failed
        )

    @Property(int, notify=changed)
    def maxPlayers(self) -> int:
        return self._max_players

    @Property("QVariantList", notify=feedbackChanged)
    def feedback(self) -> list[dict[str, Any]]:
        return self._feedback

    @Property("QVariantMap", notify=changed)
    def result(self) -> dict[str, Any]:
        return self._result

    @Slot(int)
    def selectSong(self, index: int) -> None:
        if 0 <= index < len(self._catalog) and index != self._song_index:
            self._song_index = index
            self._loaded_song_index = -1
            self._loaded_song = None
            self.changed.emit()

    @Slot()
    def openSetup(self) -> None:
        if self._points < self._unlock_cost(self._selected_song_metadata):
            return
        self._screen = "setup"
        self._song_time = 0.0
        self.refreshCameras()
        self._apply_source()
        self.changed.emit()

    @Slot()
    def openSettings(self) -> None:
        if self._presentation_mode or self._screen not in {"library", "setup"}:
            return
        self._settings_return_screen = self._screen
        self._screen = "settings"
        self.changed.emit()

    @Slot()
    def closeSettings(self) -> None:
        if self._screen != "settings":
            return
        self._screen = self._settings_return_screen
        self.changed.emit()

    def _begin_song_import_source(self, source: str, *, remote: bool = False) -> None:
        if self._song_import_busy:
            return
        self._song_import = {}
        self._song_import_busy = True
        self._song_import_progress = -1.0
        self._song_import_preview = {}
        self._song_import_cancel.clear()
        self._song_import_status = (
            "Downloading video…" if remote else "Reading video metadata…"
        )
        self.songImportChanged.emit()

        def prepare() -> None:
            try:
                path = (
                    download_url(
                        source,
                        cancel_event=self._song_import_cancel,
                        progress_callback=lambda status: self._import_progress.emit(status),
                    )
                    if remote
                    else Path(source).resolve(strict=True)
                )
                if not path.is_file():
                    raise ValueError(f"video file does not exist: {path}")
                details = probe_video_metadata(path)
                if not self._song_import_cancel.is_set():
                    self._import_prepared.emit(
                        {"source": str(path), "_downloaded": remote, **details}
                    )
            except Exception as exc:
                if not self._song_import_cancel.is_set():
                    self._import_failed.emit(str(exc))

        self._song_import_worker = threading.Thread(
            target=prepare, name="song-import-prepare", daemon=True
        )
        self._song_import_worker.start()

    @Slot()
    def chooseSongImportFile(self) -> None:
        if self._song_import_busy:
            return
        from PySide6.QtWidgets import QFileDialog

        path, _ = QFileDialog.getOpenFileName(
            None,
            "Add dance video",
            "",
            "Videos (*.mp4 *.mkv *.mov *.webm *.avi *.ogv);;All files (*)",
        )
        if path:
            self._begin_song_import_source(path)

    @Slot(str)
    def prepareSongImportUrl(self, url: str) -> None:
        if url.strip():
            self._begin_song_import_source(url.strip(), remote=True)

    @Slot()
    def chooseSongImportLyrics(self) -> None:
        if self._song_import_busy or not self._song_import.get("source"):
            return
        from PySide6.QtWidgets import QFileDialog

        path, _ = QFileDialog.getOpenFileName(
            None, "Add synchronized lyrics", "", "LRC lyrics (*.lrc);;All files (*)"
        )
        if path:
            self._song_import["lyrics"] = str(Path(path).resolve())
            self.songImportChanged.emit()

    @Slot()
    def resetSongImport(self) -> None:
        if self._song_import_busy:
            return
        self._song_import = {}
        self._song_import_status = ""
        self._song_import_progress = -1.0
        self._song_import_preview = {}
        self.songImportChanged.emit()

    @Slot("QVariantMap")
    def startSongImport(self, values: dict[str, Any]) -> None:
        source = self._song_import.get("source")
        if self._song_import_busy or not source:
            return
        try:
            options = ImportOptions(
                title=str(values.get("title", "")).strip(),
                artist=str(values.get("artist", "")).strip(),
                dancer_count=int(values.get("dancer_count", 1)),
                trim_start=float(values.get("trim_start", 0)),
                trim_end=float(values.get("trim_end", 0)),
                hide_video_intro=float(values.get("hide_video_intro", 0)),
                lrc=self._song_import.get("lyrics"),
            )
        except (TypeError, ValueError) as exc:
            self._song_import_status = f"Error: invalid extraction options ({exc})"
            self.songImportChanged.emit()
            return

        self._song_import.update(title=options.title, artist=options.artist)
        pose_thread = self._pose_thread
        self._pose_thread = None
        if pose_thread:
            pose_thread.close()
        self._stop_source()
        self._import_capture_paused = True
        self._song_import_busy = True
        self._song_import_progress = -1.0
        self._song_import_preview = {}
        self._song_import_cancel.clear()
        self._song_import_status = "Extracting choreography… This can take several minutes."
        self.songImportChanged.emit()
        metadata = dict(self._song_import)

        def extract() -> None:
            try:
                if pose_thread:
                    pose_thread.join()
                engine = pose_thread.engine if pose_thread else None
                if engine is None:
                    from .vision import create_pose_engine

                    engine = create_pose_engine(
                        self._pose_backend,
                        rtmpose_mode=self._rtmpose_mode,
                        max_people=self._max_players,
                    )
                manifest = extract_imported_song(
                    source,
                    library_path(),
                    engine=engine,
                    options=options,
                    metadata=metadata,
                    progress_callback=lambda done, total, fps: self._import_progress.emit(
                        (done, total, fps)
                    ),
                    preview_callback=self._encode_import_preview,
                    cancel_event=self._song_import_cancel,
                    existing_ids={str(song.get("id", "")) for song in self._catalog},
                )
                if not self._song_import_cancel.is_set():
                    self._import_completed.emit(str(manifest))
            except Exception as exc:
                if not self._song_import_cancel.is_set():
                    self._import_failed.emit(str(exc))

        self._song_import_worker = threading.Thread(
            target=extract, name="song-import-extract", daemon=True
        )
        self._song_import_worker.start()

    @Slot(object)
    def _on_import_prepared(self, details: dict[str, Any]) -> None:
        self._song_import = dict(details)
        self._song_import_busy = False
        self._song_import_progress = -1.0
        self._song_import_status = "Ready to extract choreography."
        self.songImportChanged.emit()

    @Slot(object)
    def _on_import_progress(self, progress: Any) -> None:
        if isinstance(progress, str):
            progress = " ".join(progress.split())
            percent, _separator, _rest = progress.partition("%")
            try:
                self._song_import_progress = min(1.0, max(0.0, float(percent) / 100))
            except ValueError:
                self._song_import_progress = -1.0
            self._song_import_status = "Downloading video: " + progress
            self.songImportChanged.emit()
            return
        done, total, fps = progress
        if total:
            self._song_import_progress = min(1.0, done / total)
            if done >= total:
                self._song_import_status = "Building stable dancer roles and dance moves…"
            else:
                remaining = max(0.0, (total - done) / fps) if fps > 0 else 0.0
                eta = (
                    f"about {math.ceil(remaining / 60)} min left"
                    if remaining >= 90
                    else f"about {math.ceil(remaining)} sec left"
                    if remaining > 0
                    else "estimating time left"
                )
                self._song_import_status = (
                    f"Analyzing video: {self._song_import_progress:.0%} · "
                    f"{done:,}/{total:,} frames · {fps:.1f} fps · {eta}"
                )
        else:
            self._song_import_progress = -1.0
            self._song_import_status = (
                f"Analyzing video: {done:,} frames"
                + (f" · {fps:.1f} fps" if fps > 0 else "")
            )
        self.songImportChanged.emit()

    def _encode_import_preview(
        self, frame: Any, people: list[dict[str, Any]]
    ) -> None:
        import cv2

        encoded, jpeg = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 75])
        if encoded and not self._song_import_cancel.is_set():
            self._import_preview.emit(
                {
                    "image": "data:image/jpeg;base64,"
                    + base64.b64encode(jpeg.tobytes()).decode("ascii"),
                    "people": people,
                }
            )

    @Slot(object)
    def _on_import_preview(self, preview: dict[str, Any]) -> None:
        self._song_import_preview = dict(preview)
        self.songImportChanged.emit()

    def _resume_after_song_import(self) -> None:
        if self._import_capture_paused:
            self._import_capture_paused = False
            self._apply_source()

    @Slot(str)
    def _on_import_completed(self, manifest: str) -> None:
        destination = Path(manifest).resolve()
        title = str(self._song_import.get("title") or destination.parent.name)
        self._catalog = self._load_songs()
        self._song_index = next(
            (
                index
                for index, song in enumerate(self._catalog)
                if Path(str(song.get("_manifest", ""))).resolve() == destination
            ),
            self._song_index,
        )
        self._loaded_song_index = -1
        self._loaded_song = None
        self._song_import = {}
        self._song_import_busy = False
        self._song_import_progress = -1.0
        self._song_import_preview = {}
        self._song_import_status = f"Added {title} to the song library."
        self._resume_after_song_import()
        self.songImportChanged.emit()
        self.changed.emit()

    @Slot(str)
    def _on_import_failed(self, message: str) -> None:
        self._song_import_busy = False
        self._song_import_progress = -1.0
        self._song_import_preview = {}
        self._song_import_status = f"Error: {message}"
        self._resume_after_song_import()
        self.songImportChanged.emit()

    @Slot(QObject)
    def attachVideoSink(self, sink: QObject) -> None:
        if not isinstance(sink, QVideoSink):
            return
        if self._capture_sink:
            try:
                self._capture_sink.videoFrameChanged.disconnect(self._capture_frame)
            except RuntimeError:
                pass
        self._capture_sink = sink
        sink.videoFrameChanged.connect(self._capture_frame)
        self._apply_source()

    @Slot(QObject)
    def attachCoachSink(self, sink: QObject) -> None:
        if isinstance(sink, QVideoSink):
            self._coach_sink = sink
            self._coach_player.setVideoSink(sink)

    @Slot()
    def refreshCameras(self) -> None:
        previous_source = self._selected_source
        saved = str(self.settings.value("camera/id", ""))
        devices = list(QMediaDevices.videoInputs())
        self._camera_devices = {bytes(device.id()).hex(): device for device in devices}

        def rank(device: Any) -> tuple[int, str]:
            device_id = bytes(device.id()).hex()
            name = device.description().lower()
            if device_id == saved:
                return (0, name)
            built_in = any(word in name for word in ("integrated", "built-in", "internal", "laptop", "facetime", " ir"))
            virtual = any(word in name for word in ("virtual", "obs", "loopback"))
            external = any(word in name for word in ("usb", "logitech", "brio", "c920", "streamcam", "elgato"))
            return (1 if external and not built_in and not virtual else 2 if not built_in and not virtual else 3, name)

        devices.sort(key=rank)
        camera_rows = [
            {"id": bytes(device.id()).hex(), "name": device.description(), "kind": "camera"}
            for device in devices
        ]
        alternate_rows = (
            [
                {"id": "file", "name": "Video file…", "kind": "file"},
                {"id": "demo", "name": "Demo dancers (no camera)", "kind": "demo"},
            ]
            if self._alternate_sources_enabled
            else []
        )
        self._cameras = camera_rows + alternate_rows
        valid = {row["id"] for row in self._cameras}
        if self._selected_source not in valid or (
            self._selected_source in self._camera_devices
            and self._selected_source != saved
        ):
            self._selected_source = camera_rows[0]["id"] if camera_rows else ""
        self.changed.emit()
        if self._capture_sink and (
            self._selected_source != previous_source
            or (
                self._selected_source in self._camera_devices
                and self._camera is None
            )
        ):
            self._apply_source()

    @Slot(str)
    def selectSource(self, source_id: str) -> None:
        if source_id == "file":
            self.chooseVideoFile()
            return
        if source_id not in {row["id"] for row in self._cameras}:
            return
        self._selected_source = source_id
        self.settings.setValue("camera/source", source_id)
        if source_id in self._camera_devices:
            self.settings.setValue("camera/id", source_id)
        self._apply_source()
        self.changed.emit()

    @Slot()
    def chooseVideoFile(self) -> None:
        from PySide6.QtWidgets import QFileDialog

        path, _ = QFileDialog.getOpenFileName(
            None, "Use video as dancer input", "", "Videos (*.mp4 *.mkv *.mov *.webm *.avi *.ogv);;All files (*)"
        )
        if not path:
            return
        self._video_path = path
        self._selected_source = "file"
        self.settings.setValue("camera/source", "file")
        self.settings.setValue("camera/video_path", path)
        self._apply_source()
        self.changed.emit()

    def _apply_source(self) -> None:
        if self._presentation_mode or not self._capture_sink:
            return
        if self._camera:
            self._camera.stop()
            self._camera.deleteLater()
            self._camera = None
        self._source_player.stop()
        self._pose_people = []
        self._players = []
        self._gestures.reset()
        if self._pose_thread:
            self._pose_thread.reset()
        if self._selected_source in self._camera_devices:
            self._camera = QCamera(self._camera_devices[self._selected_source], self)
            self._capture_session.setCamera(self._camera)
            self._capture_session.setVideoSink(self._capture_sink)
            self._ensure_pose_thread()
            self._camera.start()
        elif self._selected_source == "file" and self._video_path:
            self._source_player.setVideoSink(self._capture_sink)
            self._source_player.setSource(QUrl.fromLocalFile(self._video_path))
            self._ensure_pose_thread()
            self._source_player.play()
        elif self._selected_source == "demo":
            self._capture_session.setCamera(None)
            self._model_status = "Demo tracking"
            self._demo_tracks()
            self._players = [
                {"name": player_nickname(index), "score": 0, "stars": 0}
                for index in range(self._max_players)
            ]
        else:
            self._capture_session.setCamera(None)
            self._model_status = "No camera found"

    def _stop_source(self) -> None:
        if self._camera:
            self._camera.stop()
        self._source_player.stop()

    def _ensure_pose_thread(self) -> None:
        if self._pose_thread and self._pose_thread.is_alive():
            return
        self._pose_thread = PoseThread(self)
        self._pose_thread.start()

    @Slot()
    def startGame(self) -> None:
        if self._presentation_mode:
            self._start_presentation()
            return
        self._session = GameSession(
            self._selected_song,
            max_players=self._max_players,
            mirror_player_positions=self._selected_source != "demo",
        )
        self._feedback = []
        self._last_feedback_at = -math.inf
        self.feedbackChanged.emit()
        self._result = {}
        self._song_time = 0.0
        self._paused = False
        self._countdown = 3
        self._screen = "countdown"
        self._prepare_song()
        self._countdown_started = time.monotonic()
        if self._selected_source == "file":
            self._source_player.setPosition(0)
            self._source_player.pause()
        self.changed.emit()

    def _start_presentation(self) -> None:
        self._screen = "game"
        self._song_time = 0.0
        self._target_pose = []
        self._paused = False
        self._presentation_finished = False
        self._prepare_song()
        self._play_started_at = time.monotonic()
        self._start_song_media()
        self.changed.emit()

    def _prepare_song(self) -> None:
        song = self._selected_song
        self._coach_video_failed = False
        video = song_media_path(song, "video")
        audio = song_media_path(song, "audio")
        path = video or audio
        if path is None and song.get("bpm") and song.get("duration"):
            path = ensure_song_wav(song)
        self._coach_player.stop()
        self._coach_player.setSource(QUrl())
        self._music_player.stop()
        self._music_player.setSource(QUrl())
        self._separate_audio = bool(video and audio)
        self._clock_player = self._music_player if self._separate_audio else self._coach_player
        self._media_start_s = _song_seconds(song, "media_start")
        self._media_position_s = 0.0
        self._media_position_at = None
        self._has_song_media = path is not None
        self._coach_audio.setMuted(self._separate_audio)
        self._coach_seek_pending = path is not None
        self._music_seek_pending = self._separate_audio
        if path is not None and path.is_file():
            self._coach_player.setSource(QUrl.fromLocalFile(str(path)))
        if self._separate_audio and audio is not None:
            self._music_player.setSource(QUrl.fromLocalFile(str(audio)))
        start_ms = round(self._media_start_s * 1_000)
        self._coach_player.setPosition(start_ms)
        self._music_player.setPosition(start_ms)

    def _start_song_media(self) -> None:
        start_ms = round(self._media_start_s * 1_000)
        self._coach_player.setPosition(start_ms)
        self._music_player.setPosition(start_ms)
        self._coach_player.play()
        self._music_player.play()

    def _play_stinger(self, name: str) -> None:
        if effect := self._stingers.get(name):
            effect.stop()
            effect.play()

    @Slot()
    def togglePause(self) -> None:
        if self._screen != "game":
            return
        if self._presentation_finished:
            self._start_presentation()
            return
        self._paused = not self._paused
        if not self._paused:
            now = time.monotonic()
            self._play_started_at = now - self._song_time
            if self._has_song_media:
                self._media_position_s = max(
                    0.0,
                    self._clock_player.position() / 1_000.0 - self._media_start_s,
                )
                self._media_position_at = now
        (self._coach_player.pause if self._paused else self._coach_player.play)()
        (self._music_player.pause if self._paused else self._music_player.play)()
        if self._selected_source == "file":
            (self._source_player.pause if self._paused else self._source_player.play)()
        self.changed.emit()

    @Slot()
    def leaveGame(self) -> None:
        self._coach_player.stop()
        self._music_player.stop()
        if self._presentation_mode:
            self.quitRequested.emit()
            return
        self._screen = "library"
        self._paused = False
        self.changed.emit()

    @Slot()
    def retry(self) -> None:
        self._start_presentation() if self._presentation_mode else self.startGame()

    @Slot()
    def goLibrary(self) -> None:
        self._coach_player.stop()
        self._music_player.stop()
        if self._presentation_mode:
            self.quitRequested.emit()
            return
        self._screen = "library"
        self.changed.emit()

    @Slot(str, "QVariant")
    def setOption(self, name: str, value: Any) -> None:
        if name == "cues":
            self._cues = bool(value)
            self.settings.setValue("game/cues", self._cues)
        elif name == "miniView":
            self._mini = bool(value)
            self.settings.setValue("game/mini_view", self._mini)
        elif name == "latencyMs":
            self._latency_ms = max(-300, min(500, int(value)))
            self.settings.setValue("game/latency_ms", self._latency_ms)
        elif name == "joinGestureOnly":
            self._gestures.join_required = bool(value)
            self._gestures.reset()
            self.settings.setValue("game/join_gesture_only", self._gestures.join_required)
        elif name == "volume":
            volume = max(0.0, min(1.0, float(value)))
            self._volume = volume
            self._coach_audio.setVolume(volume)
            self._music_audio.setVolume(volume)
            for effect in self._stingers.values():
                effect.set_volume(volume)
            self.settings.setValue("audio/volume", volume)
        elif name == "reducedMotion":
            self._reduced_motion = bool(value)
            self.settings.setValue("ui/reduced_motion", self._reduced_motion)
        self.changed.emit()

    @Slot()
    def requestFullscreen(self) -> None:
        self.fullscreenRequested.emit()

    @Property(bool, notify=changed)
    def fullscreen(self) -> bool:
        return self._fullscreen

    @Slot(bool)
    def rememberFullscreen(self, fullscreen: bool) -> None:
        self._fullscreen = bool(fullscreen)
        self.settings.setValue("ui/fullscreen", self._fullscreen)

    @Slot()
    def _capture_frame(self, frame: Any) -> None:
        now_ms = time.monotonic() * 1000.0
        # Keep 30 fps sources intact; the size-one queue drops excess 60 fps frames.
        interval_ms = 90.0 if self._screen in ("library", "settings", "results") else 30.0
        if not frame.isValid() or now_ms - self._last_frame_ms < interval_ms:
            return
        self._last_frame_ms = now_ms
        image = frame.toImage()
        if not image.isNull():
            self._ensure_pose_thread()
            assert self._pose_thread
            self._pose_thread.submit(image.copy(), now_ms)

    @Slot(object)
    def _on_pose_result(self, result: dict[str, Any]) -> None:
        now = time.monotonic()
        if self._last_inference_at:
            instant = 1.0 / max(0.001, now - self._last_inference_at)
            self._inference_fps = (
                instant
                if not self._inference_fps
                else self._inference_fps * 0.82 + instant * 0.18
            )
        self._last_inference_at = now
        self._pose_people = list(result.get("people", []))
        for index, person in enumerate(self._pose_people):
            person["name"] = player_nickname(index)
        events = self._gestures.update(self._pose_people, now)
        if self._screen in ("library", "setup", "settings", "results") or (
            self._screen == "game" and self._paused
        ):
            for event in events:
                action = event["action"]
                if action in ("left", "right"):
                    # The preview is mirrored, so raw camera-x is display-opposite.
                    action = "right" if action == "left" else "left"
                if action in ("left", "right", "accept"):
                    self.gamepadAction.emit(action)
        admitted_people = [
            person
            for person in self._pose_people
            if self._gestures.admits(person.get("track_id"))
        ]
        if self._screen in ("setup", "countdown"):
            self._players = [
                {
                    "slot": index,
                    "player_number": index + 1,
                    "name": player_nickname(index),
                    "score": 0,
                    "stars": 0,
                }
                for index, _person in enumerate(admitted_people[: self._max_players])
            ]
        device = str(result.get("device", "unknown")).upper()
        label = str(result.get("backend") or self._pose_backend).upper()
        self._model_status = f"{label} • {device} • {result.get('inference_ms', 0):.0f} ms"
        self.poseChanged.emit()
        if self._screen in ("setup", "countdown"):
            self.playersChanged.emit()

    @Slot(str)
    def _set_model_status(self, status: str) -> None:
        self._model_status = status
        self.poseChanged.emit()

    def _seek_loaded_media(self, player: QMediaPlayer, status: Any) -> None:
        if status not in (
            QMediaPlayer.MediaStatus.LoadedMedia,
            QMediaPlayer.MediaStatus.BufferedMedia,
        ):
            return
        pending_name = (
            "_coach_seek_pending"
            if player is self._coach_player
            else "_music_seek_pending"
        )
        if not getattr(self, pending_name):
            return
        setattr(self, pending_name, False)
        song_time = self._song_time if self._screen == "game" else 0.0
        player.setPosition(round((self._media_start_s + song_time) * 1_000))

    def _coach_status(self, status: Any) -> None:
        self._seek_loaded_media(self._coach_player, status)
        if (
            status == QMediaPlayer.MediaStatus.EndOfMedia
            and self._presentation_mode
            and self._screen == "game"
            and self._clock_player is self._coach_player
        ):
            self._finish_presentation()
            return
        if (
            status == QMediaPlayer.MediaStatus.EndOfMedia
            and self._screen == "game"
            and not self._separate_audio
            and self._song_time >= self.songDuration - 0.25
        ):
            self._finish_game()

    def _music_status(self, status: Any) -> None:
        self._seek_loaded_media(self._music_player, status)
        if (
            status == QMediaPlayer.MediaStatus.EndOfMedia
            and self._presentation_mode
            and self._screen == "game"
            and self._clock_player is self._music_player
        ):
            self._finish_presentation()

    def _sync_media_position(self, player: QMediaPlayer, position_ms: int) -> None:
        if player is not self._clock_player or self._screen != "game":
            return
        reported = max(0.0, float(position_ms) / 1_000.0 - self._media_start_s)
        now = time.monotonic()
        if self._media_position_at is None:
            if reported <= 0.0:
                return
            self._media_position_s = reported
        else:
            estimate = _interpolated_media_time(
                self._media_position_s,
                self._media_position_at,
                now,
                self._clock_player.isPlaying(),
            )
            error = reported - estimate
            self._media_position_s = reported if abs(error) > 0.25 else estimate + error * 0.35
        self._media_position_at = now

    def _coach_error(self, _error: Any, message: str) -> None:
        if self._shows_coach_video():
            self._coach_video_failed = True
            print(f"OpenDance coach video unavailable: {message}", file=sys.stderr, flush=True)
            audio = song_media_path(self._selected_song, "audio")
            if audio and not self._separate_audio:
                self._coach_seek_pending = True
                self._coach_player.setSource(QUrl.fromLocalFile(str(audio)))
                self._coach_player.setPosition(
                    round((self._media_start_s + self._song_time) * 1_000)
                )
                if self._screen == "game" and not self._paused:
                    self._coach_player.play()
            elif self._clock_player is self._coach_player:
                # Keep choreography moving when an embedded-audio video cannot
                # be decoded and there is no separate audio clock to follow.
                self._has_song_media = False
                self._media_position_at = None
                self._play_started_at = time.monotonic() - self._song_time
            self.changed.emit()

    def _demo_tracks(self) -> dict[int, list[list[float]]]:
        poses = target_poses(
            self._selected_song,
            max(0.0, self._song_time - self._latency_ms / 1000.0),
        )
        dancer_centers = [
            sum(point[0] * point[2] for point in pose if point[2] >= 0.2)
            / max(0.001, sum(point[2] for point in pose if point[2] >= 0.2))
            for pose in poses
        ]
        player_centers = {
            index: (index + 1) / (self._max_players + 1)
            for index in range(self._max_players)
        }
        assignments = assign_dancers(player_centers, dancer_centers)
        tracks: dict[int, list[list[float]]] = {}
        for index in range(self._max_players):
            dancer = assignments[index]
            pose = poses[dancer]
            center = dancer_centers[dancer]
            tracks[10_000 + index] = [
                [
                    player_centers[index] + (float(point[0]) - center) * 0.52,
                    float(point[1]),
                    0.98,
                ]
                for point in pose
            ]
        self._pose_people = [
            {"track_id": track_id, "bbox": [0.2, 0.05, 0.6, 0.9], "keypoints": points}
            for track_id, points in tracks.items()
        ]
        return tracks

    def _tick(self) -> None:
        self._gamepad.poll()
        if self._screen == "countdown":
            elapsed = time.monotonic() - self._countdown_started
            self._countdown = max(0, 3 - int(elapsed))
            if elapsed >= 3.0:
                self._screen = "game"
                self._play_started_at = time.monotonic()
                self._start_song_media()
                if self._selected_source == "file":
                    self._source_player.play()
                self.changed.emit()
            else:
                self.gameFrameChanged.emit()
            return
        if self._screen != "game" or self._paused:
            return

        # QMediaPlayer.position() advances in coarse platform-dependent steps;
        # the monotonic playback clock keeps choreography smooth between them.
        now = time.monotonic()
        self._song_time = min(
            self.songDuration,
            max(
                0.0,
                _interpolated_media_time(
                    self._media_position_s,
                    self._media_position_at,
                    now,
                    self._clock_player.isPlaying(),
                )
                if self._has_song_media
                else now - self._play_started_at,
            ),
        )
        self._target_pose = [
            list(point) for point in target_pose(self._selected_song, self._song_time)
        ]
        if self._presentation_mode:
            if self._song_time >= self.songDuration:
                self._finish_presentation()
            else:
                self.gameFrameChanged.emit()
            return
        if not self._session:
            return
        tracks = (
            self._demo_tracks()
            if self._selected_source == "demo"
            else {
                (
                    int(person["track_id"])
                    if person.get("track_id") is not None
                    else -(index + 1)
                ): person.get("keypoints", [])
                for index, person in enumerate(self._pose_people)
                if self._gestures.admits(person.get("track_id"))
            }
        )
        scoring_time = max(0.0, self._song_time - self._latency_ms / 1000.0)
        new_feedback = self._session.update(scoring_time, tracks)
        if new_feedback and _feedback_due(self._song_time, self._last_feedback_at):
            self._feedback = [
                item if isinstance(item, dict) else vars(item) for item in new_feedback
            ][-self._max_players :]
            self._last_feedback_at = self._song_time
            self.feedbackChanged.emit()
        players = self._session.ui_players()
        if players != self._players:
            self._players = players
            self.playersChanged.emit()
        if self._selected_source == "demo":
            self.poseChanged.emit()
        if self._song_time >= self.songDuration:
            self._finish_game()
        else:
            self.gameFrameChanged.emit()

    def _finish_presentation(self) -> None:
        if self._presentation_finished:
            return
        self._song_time = self.songDuration
        self._target_pose = [
            list(point) for point in target_pose(self._selected_song, self._song_time)
        ]
        self._presentation_finished = True
        self._paused = True
        self._coach_player.pause()
        self._music_player.pause()
        self.changed.emit()

    def _finish_game(self) -> None:
        if self._screen == "results" or not self._session:
            return
        self._session.finish()
        result = self._session.results()
        earned = (
            max(
                (int(player.get("score", 0)) for player in result.get("players", [])),
                default=0,
            )
            // 10
        )
        before = self._points
        self._points += earned
        self.settings.setValue("profile/points", self._points)
        newly_unlocked = [
            song["title"]
            for song in self._catalog
            if before < self._unlock_cost(song) <= self._points
        ]
        result["earned_points"] = earned
        result["total_points"] = self._points
        result["newly_unlocked"] = newly_unlocked
        self._players = list(result.get("players", []))
        song_id = str(self._selected_song.get("id", ""))
        best_key = f"profile/best/{song_id}/stars"
        self.settings.setValue(
            best_key,
            max(int(self.settings.value(best_key, 0)), result["team_stars"]),
        )
        self._result = result
        self._play_stinger(
            "unlock" if newly_unlocked else "star" if result["team_stars"] else "miss"
        )
        self._screen = "results"
        self._coach_player.stop()
        self._music_player.stop()
        self.changed.emit()

    def close(self) -> None:
        self._song_import_cancel.set()
        if self._song_import_worker and self._song_import_worker.is_alive():
            self._song_import_worker.join(timeout=2.0)
        self._import_capture_paused = False
        self._ticker.stop()
        self._stop_source()
        self._coach_player.stop()
        self._music_player.stop()
        if self._pose_thread:
            self._pose_thread.close()
            self._pose_thread.join(timeout=1.0)
        if self._gamepad.pygame:
            self._gamepad.pygame.joystick.quit()
            self._gamepad.pygame.display.quit()
        if self._mixer:
            self._mixer.quit()


def _run(
    *,
    enable_alternate_sources: bool = False,
    join_gesture_only: bool = False,
    pose_backend: str = "yolo26",
    rtmpose_mode: str = "lightweight",
    presentation_song: dict[str, Any] | None = None,
    presentation_video: bool = True,
) -> int:
    os.environ.setdefault("QT_QUICK_CONTROLS_STYLE", "Basic")
    app = QApplication([sys.argv[0]])
    for stop_signal in (signal.SIGINT, signal.SIGTERM):
        signal.signal(stop_signal, lambda *_args: app.quit())
    app.setApplicationName("OpenDance Player" if presentation_song else "OpenDance")
    app.setOrganizationName("OpenDance")
    app.setDesktopFileName("opendance")
    app_icon = QIcon(str(files("opendance").joinpath("assets/icon.svg")))
    app.setWindowIcon(app_icon)
    backend = Backend(
        enable_alternate_sources=enable_alternate_sources,
        join_gesture_only=join_gesture_only,
        pose_backend=pose_backend,
        rtmpose_mode=rtmpose_mode,
        presentation_song=presentation_song,
        presentation_video=presentation_video,
    )
    backend.quitRequested.connect(app.quit)
    engine = QQmlApplicationEngine()
    engine.rootContext().setContextProperty("backend", backend)
    qml = files("opendance.qml").joinpath("Main.qml")
    engine.load(QUrl.fromLocalFile(str(qml)))
    if not engine.rootObjects():
        backend.close()
        return 1
    for root in engine.rootObjects():
        if isinstance(root, QQuickWindow):
            root.setIcon(app_icon)
    print(
        f"OpenDance graphics: {QQuickWindow.graphicsApi().name}",
        file=sys.stderr,
        flush=True,
    )
    code = app.exec()
    backend.close()
    del engine
    return code


def main() -> int:
    cli = App(name="opendance", help="Camera-powered local dance game.")

    @cli.default
    def launch(
        enable_alternate_sources: bool = False,
        join_gesture_only: bool = False,
        pose_backend: str = "yolo26",
        rtmpose_mode: str = "lightweight",
    ) -> int:
        """Launch OpenDance.

        Parameters
        ----------
        enable_alternate_sources:
            Show video-file and simulated-dancer inputs in camera setup.
        join_gesture_only:
            Admit dancers only after they hold both hands above their head.
        pose_backend:
            Pose implementation: yolo26 (default) or the optional rtmpose.
        rtmpose_mode:
            RTMPose quality preset: lightweight, balanced, or performance.
        """

        return _run(
            enable_alternate_sources=enable_alternate_sources,
            join_gesture_only=join_gesture_only,
            pose_backend=pose_backend,
            rtmpose_mode=rtmpose_mode,
        )

    return cli(result_action="return_value")
