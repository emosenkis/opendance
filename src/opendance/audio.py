"""Generate a deterministic fallback synth track into the user's cache.

Bundled music and effects use Ogg Vorbis; this stdlib WAV path is only used
when a song manifest has tempo metadata but no playable media file.
"""

from __future__ import annotations

from array import array
import hashlib
import json
import math
import os
from pathlib import Path
import re
import sys
import tempfile
from typing import Callable, Mapping
import wave


DEFAULT_SAMPLE_RATE = 24_000
_CACHE_VERSION = 2
_NOTES = {
    "C": 0,
    "C#": 1,
    "DB": 1,
    "D": 2,
    "D#": 3,
    "EB": 3,
    "E": 4,
    "F": 5,
    "F#": 6,
    "GB": 6,
    "G": 7,
    "G#": 8,
    "AB": 8,
    "A": 9,
    "A#": 10,
    "BB": 10,
    "B": 11,
}


def default_cache_dir() -> Path:
    """Return a conventional per-user cache directory on Windows and Linux."""

    configured = os.environ.get("OPENDANCE_CACHE")
    if configured:
        return Path(configured).expanduser()
    if sys.platform == "win32" and os.environ.get("LOCALAPPDATA"):
        return Path(os.environ["LOCALAPPDATA"]) / "OpenDance" / "Cache"
    if os.environ.get("XDG_CACHE_HOME"):
        return Path(os.environ["XDG_CACHE_HOME"]) / "opendance"
    return Path.home() / ".cache" / "opendance"


def _valid_wav(path: Path) -> bool:
    try:
        if path.stat().st_size <= 44:
            return False
        with wave.open(str(path), "rb") as source:
            return source.getnchannels() in (1, 2) and source.getsampwidth() == 2 and source.getnframes() > 0
    except (OSError, EOFError, wave.Error):
        return False


def _atomic_generate(path: Path, writer: Callable[[Path], None]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.stem}-", suffix=".tmp", dir=path.parent)
    os.close(descriptor)
    temporary = Path(temporary_name)
    try:
        writer(temporary)
        os.replace(temporary, path)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
    return path


def _midi_frequency(note: int) -> float:
    return 440.0 * 2.0 ** ((note - 69) / 12.0)


def _key_details(key: str) -> tuple[int, bool]:
    parts = str(key).strip().split()
    note_name = parts[0].upper() if parts else "C"
    if note_name not in _NOTES:
        raise ValueError(f"unsupported musical key: {key!r}")
    is_minor = len(parts) > 1 and parts[1].lower().startswith("min")
    return 48 + _NOTES[note_name], is_minor


def _pcm_bytes(samples: array) -> bytes:
    if sys.byteorder == "big":
        samples.byteswap()
    return samples.tobytes()


def _write_song(path: Path, song: Mapping, sample_rate: int) -> None:
    duration = float(song["duration"])
    bpm = float(song["bpm"])
    if not 0.05 <= duration <= 900.0:
        raise ValueError("duration must be between 0.05 and 900 seconds")
    if not 30.0 <= bpm <= 300.0:
        raise ValueError("bpm must be between 30 and 300")
    root, minor = _key_details(str(song.get("key", "C minor")))
    identifier = str(song.get("id", "song"))
    seed = int.from_bytes(hashlib.sha256(identifier.encode()).digest()[:4], "little")

    scale = (0, 2, 3, 5, 7, 8, 10, 12) if minor else (0, 2, 4, 5, 7, 9, 11, 12)
    progression = (0, 8, 3, 10) if minor else (0, 7, 9, 5)
    lead_pattern = tuple(scale[(seed // (index + 3) + index * 3) % len(scale)] for index in range(16))
    beat_seconds = 60.0 / bpm
    total_frames = round(duration * sample_rate)
    tau = 2.0 * math.pi
    noise_state = seed or 1

    with wave.open(str(path), "wb") as output:
        output.setnchannels(2)
        output.setsampwidth(2)
        output.setframerate(sample_rate)
        chunk = array("h")
        for frame in range(total_frames):
            time_s = frame / sample_rate
            beat = time_s / beat_seconds
            beat_index = int(beat)
            beat_fraction = beat - beat_index
            beat_local = beat_fraction * beat_seconds
            half_index = int(beat * 2.0)
            half_fraction = beat * 2.0 - half_index
            half_local = half_fraction * beat_seconds * 0.5
            bar = beat_index // 4
            chord_offset = progression[bar % len(progression)]

            # Warm triad bed with slow side-to-side motion.
            third = 3 if minor else 4
            chord_notes = (root + chord_offset, root + chord_offset + third, root + chord_offset + 7)
            pad = sum(math.sin(tau * _midi_frequency(note + 12) * time_s) for note in chord_notes) / 3.0
            pad *= 0.12 * (0.80 + 0.20 * math.sin(tau * time_s / 4.0))

            bass_frequency = _midi_frequency(root - 12 + chord_offset)
            bass_envelope = math.exp(-3.2 * beat_fraction)
            bass = 0.25 * bass_envelope * math.sin(tau * bass_frequency * beat_local)

            lead_note = root + 12 + lead_pattern[half_index % len(lead_pattern)]
            lead_envelope = math.sin(math.pi * min(1.0, half_fraction)) ** 0.45
            lead = 0.18 * lead_envelope * (
                0.76 * math.sin(tau * _midi_frequency(lead_note) * half_local)
                + 0.24 * math.sin(tau * _midi_frequency(lead_note) * 2.0 * half_local)
            )

            # A punchy four-on-the-floor kit made entirely from oscillators/noise.
            kick_envelope = math.exp(-18.0 * beat_local)
            kick_phase = tau * (48.0 * beat_local + 34.0 * (1.0 - math.exp(-16.0 * beat_local)))
            kick = 0.48 * kick_envelope * math.sin(kick_phase)
            noise_state = (1_664_525 * noise_state + 1_013_904_223) & 0xFFFFFFFF
            noise = (noise_state / 0x7FFFFFFF) - 1.0
            snare = 0.0
            if beat_index % 4 in (1, 3):
                snare = 0.20 * math.exp(-22.0 * beat_local) * noise
            hat = 0.07 * math.exp(-48.0 * half_local) * noise

            center = pad + bass + kick + snare + hat
            pan = 0.5 + 0.32 * math.sin(tau * time_s / 7.0)
            left = center + lead * (1.0 - pan)
            right = center + lead * pan
            fade = min(1.0, time_s / 0.35, max(0.0, (duration - time_s) / 1.1))
            left = math.tanh(left * 1.35) * fade
            right = math.tanh(right * 1.35) * fade
            chunk.extend((round(left * 30_000), round(right * 30_000)))
            if len(chunk) >= 16_384:
                output.writeframesraw(_pcm_bytes(chunk))
                chunk = array("h")
        if chunk:
            output.writeframesraw(_pcm_bytes(chunk))


def song_cache_path(song: Mapping, cache_dir: str | Path | None = None, sample_rate: int = DEFAULT_SAMPLE_RATE) -> Path:
    """Return the content-addressed cache path for a generated song."""

    identity = {
        "version": _CACHE_VERSION,
        "id": song.get("id"),
        "duration": float(song["duration"]),
        "bpm": float(song["bpm"]),
        "key": str(song.get("key", "C minor")),
        "sample_rate": int(sample_rate),
    }
    digest = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()[:12]
    slug = re.sub(r"[^a-z0-9_-]+", "-", str(song.get("id", "song")).lower()).strip("-") or "song"
    root = Path(cache_dir) if cache_dir is not None else default_cache_dir()
    return root / "songs" / f"{slug}-{digest}.wav"


def ensure_song_wav(
    song: Mapping,
    cache_dir: str | Path | None = None,
    *,
    sample_rate: int = DEFAULT_SAMPLE_RATE,
) -> Path:
    """Generate a deterministic stereo WAV if absent and return its path."""

    if not 8_000 <= int(sample_rate) <= 96_000:
        raise ValueError("sample_rate must be between 8000 and 96000")
    destination = song_cache_path(song, cache_dir, sample_rate)
    if _valid_wav(destination):
        return destination
    return _atomic_generate(destination, lambda temporary: _write_song(temporary, song, int(sample_rate)))

