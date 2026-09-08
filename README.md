# OpenDance

OpenDance is a local, camera-powered dance game for Windows and Linux. It uses
Qt Quick for the game UI and Ultralytics YOLO26 Pose for multi-person tracking,
with an experimental RTMPose Body backend available as an opt-in.
Inference runs on a latest-frame worker thread in the game process: camera
frames never cross a process or network boundary, and a slow frame cannot build
up input lag.

The repository includes three original Ogg-compressed synth tracks, generated choreography,
lyrics, dynamic join/rejoin, 0–5 star scoring, unlocks, controller navigation,
video coaching, a generated neon coach, and a live pose mini-view.

## Run

Python 3.12 is recommended. [`uv`](https://docs.astral.sh/uv/) gives identical
locked installs on Windows and Linux:

```console
uv sync --extra vision
uv run opendance
```

The first camera session downloads `yolo26n-pose.pt` into OpenDance's per-user
cache. Usage telemetry is disabled before Ultralytics' model code loads, and
camera frames are neither uploaded nor saved. To try the complete game without
a camera or model download:

```console
OPENDANCE_DEMO=1 uv run opendance
```

In PowerShell, use `$env:OPENDANCE_DEMO = "1"` before the launch command.

OpenDance prefers recognized USB cameras over integrated and virtual cameras.
The setup screen lists physical cameras by default; pass
`--enable-alternate-sources` (or set `OPENDANCE_ENABLE_ALTERNATE_SOURCES=1`) to
also show video-file and deterministic demo inputs. A video file feeds decoded
frames directly to the same pose pipeline, so a virtual-camera utility is
unnecessary.

To compare the experimental RTMPose Body/COCO-17 path without installing
Ultralytics or PyTorch, use:

```console
uv sync --extra rtmpose
uv run opendance --pose-backend rtmpose --rtmpose-mode lightweight
```

This extra includes ONNX Runtime's CUDA userspace libraries and headless OpenCV.
The first inference downloads the selected OpenMMLab detector and pose weights.
YOLO26 remains the default because its crossing/occlusion identity tracking and
1/2/4-person performance are the verified game path; RTMPose currently uses
short spatial continuity and should be treated as an experiment.

Controls:

- Mouse/touch: click or tap controls.
- Keyboard: arrows or `WASD`, `Enter`/`Space`, `Escape`, `P`, and `F11`.
- Controller: left stick/D-pad, south/A to accept, east/B to go back, Start to
  pause.
- Pose: hold both hands above your head to claim menu control, point toward a
  side of the mirrored preview to move focus, and clap to activate. The setup
  option “Join gesture only” also requires the hands-up gesture before a dancer
  is admitted; automatic joining remains the default.

## Windows GPU

The locked vision install includes PyTorch. Confirm that the RTX GPU is visible:

```console
uv run python -c "import torch; print(torch.cuda.is_available(), torch.cuda.get_device_name())"
```

If it reports `False`, update the NVIDIA driver and rerun `uv sync --reinstall
--extra vision`; the lock file already selects CUDA PyTorch on Windows and
Linux. Set a different compatible pose checkpoint with `OPENDANCE_MODEL` when
required.

## Immutable Linux

The normal `uv` install is entirely inside `.venv` and does not mutate the OS.
If the host lacks Qt runtime libraries, build the supplied container:

```console
podman build -t opendance .
podman run --rm -it \
  --security-opt=label=disable \
  --device /dev/video1 \
  --device nvidia.com/gpu=all \
  -e DISPLAY -e QT_MEDIA_BACKEND=ffmpeg \
  -e PULSE_SERVER=unix:/tmp/pulse/native \
  -v /tmp/.X11-unix:/tmp/.X11-unix:ro \
  -v "$XDG_RUNTIME_DIR/pulse/native:/tmp/pulse/native" \
  -v opendance-data:/data \
  opendance
```

Change `/dev/video1` to the capture device shown by `v4l2-ctl --list-devices`.
The NVIDIA device line expects an NVIDIA CDI entry; omit it for CPU inference.
GPU/audio passthrough varies by immutable distribution, so a Distrobox based on
the same Containerfile can be the least surprising fallback. Demo mode and
video-file input do not need a webcam device passed through.

## Installable releases

Release builds include Python, Qt, PyTorch, the CUDA 13.0 user-space runtime,
YOLO, and the pose checkpoint. They still require an NVIDIA GPU with a current
driver; no application can safely bundle the kernel-level display/GPU driver.
After a Windows installation, run
`& "$env:LOCALAPPDATA\Programs\OpenDance\opendance-extract.exe" --diagnostics`
in PowerShell to see the compiled CUDA runtime and whether the driver exposes a
GPU. The same command is available as bare `opendance-extract --diagnostics` in
the Linux container and local bundle.

For Windows, download the release's `OpenDance-...-setup.exe` and every
matching `.bin` file into one folder, then run the installer. It installs per
user and does not require administrator access or a system Python/CUDA toolkit.

Linux releases publish the complete CUDA image to
`ghcr.io/emosenkis/opendance:VERSION` and attach the exact image name to the GitHub
release. It contains the userspace dependencies; use the Podman invocation in
the previous section for camera, GPU, display, and audio access.

To build a standalone Linux installation locally, run:

```console
bash packaging/build_linux.sh 0.1.0
chmod +x release/OpenDance-*-install.sh
release/OpenDance-*-install.sh
```

The Linux bundle targets x86-64 systems compatible with the machine that builds
it and installs below `~/.local`. It bundles the application stack but
necessarily uses the host's NVIDIA driver, glibc, and display/audio interfaces;
use the container release when a fixed userspace baseline matters. The local
script emits a split portable archive instead of an
AppImage because the complete CUDA build exceeds GitHub's per-file release
limit. Downloadable Linux releases use GHCR to avoid a fragile multi-gigabyte
PyInstaller build on GitHub's small hosted runners.

Maintainers can produce the same assets locally with `uv` and `curl` installed
(`Inno Setup 6` is additionally required on Windows):

```powershell
packaging\build_windows.ps1 -Version 0.1.0
```

Pushing a version tag matching `v*` builds the CUDA Windows installer and Linux
container in GitHub Actions, then publishes only after both platforms succeed.
The ordinary CI workflow runs the core checks on Linux and Windows without
downloading the multi-gigabyte GPU stack.

## Import choreography

The offline extractor uses the exact pose/tracking implementation used during
play. It defaults to one likely lead dancer; `--dancers` retains up to four
stable choreography roles and records back-to-front rendering order:

```console
uv run opendance-extract dance.mp4 songs/my-song \
  --title "My Song" --artist "My Artist" --dancers 2 --copy-video
```

Add synchronized lyrics with `--lrc lyrics.lrc`. The result is a portable
`songs/my-song/song.json`; OpenDance discovers one-level song packages under
`./songs`, or under the directory named by `OPENDANCE_LIBRARY`.

Useful options:

```console
uv run opendance-extract --help
uv run opendance-extract dance.mp4 songs/my-song --device 0 --imgsz 640 \
  --dancers 2 --track-id 7 --track-id 12 --smooth-frames 3 --force
```

Use `--pose-backend rtmpose --rtmpose-mode lightweight` after syncing the
`rtmpose` extra to extract through the alternate implementation.

An imported package may reference a coach `video`, separate `audio`, LRC-derived
`lyrics`, named `moves`, or a dense `choreography.timeline`. Relative media paths
are resolved from the package directory. Keep media you have rights to use.

## Play an extracted package

The presentation-only player reuses the game's media clock, pose interpolation,
lyrics, and video surface without starting a camera or pose model:

```console
uv run opendance-player songs/my-song
uv run opendance-player songs/my-song/song.json --no-video
```

By default the original video is shown with every extracted dancer overlaid;
`--no-video` plays its audio while showing the generated choreography instead.

## Runtime knobs

- `OPENDANCE_MODEL`: checkpoint/path understood by Ultralytics; defaults to
  `yolo26n-pose.pt`.
- `OPENDANCE_POSE_BACKEND`: `yolo26` or the experimental `rtmpose`.
- `OPENDANCE_RTMPOSE_MODE`: `lightweight`, `balanced`, or `performance`.
- `OPENDANCE_LIBRARY`: additional song-package directory.
- `OPENDANCE_CACHE`: model and generated-audio cache location.
- `OPENDANCE_DEMO=1`: start with simulated dancers.
- `OPENDANCE_JOIN_GESTURE_ONLY=1`: require the hands-up join gesture. The same
  mode is available as `opendance --join-gesture-only` and in camera setup.

Camera choice, latency calibration, join/cue/mini-view options, audio volume,
earned points, and unlock progress are saved per user through the native Qt
settings store. Live and extracted poses use light temporal smoothing and wait
for three consistent frames before accepting an implausible position/posture
jump. See [Pose model choices](docs/pose-models.md) for the evaluated whole-body,
3D, and face re-identification options and why COCO-17 remains the default.

## Checks

On the current Linux host, the complete Qt camera-to-YOLO path was verified with
`/dev/video1`: YOLO26n Pose took 11–12 ms per frame on the GeForce MX450. The
latest-frame queue now accepts up to about 30 pose updates/second without ever
accumulating stale frames. Video-file input and stable multi-person IDs were
exercised with a test clip. The container build also sees that GPU through
NVIDIA CDI.

Run the compact core checks with:

```console
uv run python -m unittest discover -s tests -v
```

## Architecture

- `app.py`: Qt camera/media ownership, newest-frame queue, settings, and QML API.
- `vision.py`: YOLO26 + ByteTrack and experimental RTMPose adapters behind one
  normalized COCO-17 result contract.
- `game.py`: choreography interpolation, identity-aware player slots, scoring,
  stars, and results.
- `extract.py`: offline video/LRC-to-song-package pipeline.
- `player.py`: thin presentation-only launcher over the same playback backend.
- `qml/`: GPU-rendered menus, coach stage, HUD, feedback, and accessibility
  motion option.

Ultralytics software and model weights are offered under AGPL-3.0 and an
Enterprise license. Review that dependency's license before distributing or
commercializing a build. Music generated by OpenDance is deterministic and
contains no bundled third-party recording.
