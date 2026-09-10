# OpenDance checklist

This is the living checklist for the user's requests. Update it whenever scope or
completion changes; `[x]` means implemented and checked, not merely planned.
The full user/assistant transcript from the original Paseo agent was recovered to
the gitignored `prior-agent-transcript.txt`; newer requests below supersede older
ones where they conflict.

## Foundation and targets

- [x] Build a native, polished dance game inspired by the Xbox 360-era *Just
  Dance* / *Dance Central* experience rather than defaulting to a browser.
- [x] Deliberately evaluate the foundation instead of treating Godot, a browser,
  or a pose sidecar as automatically correct; use Qt Quick with latest-frame
  inference in-process because profiling showed that boundary is currently fast
  and simpler to ship.
- [x] Keep camera/model code isolated enough to move to a sidecar later if
  profiling or engine constraints justify it.
- [x] Run tolerably on the current immutable Linux host without installing host
  system libraries; support `uv` plus Podman/Distrobox-style container use.
- [x] Diagnose the previously atrocious setup/game performance, expose the
  actual inference device and timing, use the NVIDIA GPU when available, and
  prevent stale-frame latency.
- [ ] Verify smooth play on the target 10th-gen Core i7 / RTX 3050 Ti Windows
  10/11 laptop with its real camera and NVIDIA driver.
- [ ] Investigate a possible VRAM leak after the user saw NVIDIA
  `NV_ERR_NO_MEMORY` in the middle of a song after roughly five complete songs
  with 3–5 players. Reproduce and measure memory across repeated song starts,
  finishes, and retries; do not assume player/dancer count is the cause merely
  because that was the observed workload. Code review found no repeated model or
  inference-thread creation; local probes held YOLO at 43.3 MiB allocated / 82
  MiB reserved across 100 CUDA frames and returned Qt video decoding from 55 MiB
  to 3 MiB after each of ten source-clear cycles. Real GPU decoder/model memory
  still needs a repeated-play measurement on the target laptop.
  - [x] Stop dense-timeline caches from retaining several previously played
    songs in CPU/system memory; keep only the active song and verify the old
    timeline becomes collectible.

## Pose, cameras, and identity

- [x] Use a state-of-the-art open-weights real-time multi-person pose path:
  Ultralytics YOLO26n-Pose with ByteTrack and GPU acceleration.
- [x] Explain/limit the tracker: it binds otherwise independent frame detections
  to stable player scores and extracted dancer roles; do not add a separate
  service unless measurements require it.
- [x] Discover and select among multiple cameras, preferring recognized USB
  cameras over an integrated laptop camera by default while remembering an
  explicit choice.
- [x] Automatically start the visibly selected camera whenever pre-game setup
  opens; do not require clicking an already-selected row. If cameras arrive
  asynchronously and the user has not made a manual source choice, promote and
  start the newly available highest-ranked camera while preserving explicit
  camera, video-file, and demo selections.
- [x] Accept a video file through the same decoded-frame pose pipeline as a
  simulated webcam, without requiring a virtual-camera utility.
- [x] Hide video-file and simulated-dancer sources unless enabled by a Cyclopts
  CLI flag or environment variable.
- [x] Smooth ordinary live/extracted pose motion and hold implausible one-frame
  position/posture jumps for configurable confirmation frames.
- [x] Reject low-confidence person detections before admission using visible
  keypoint count/coverage and detector confidence, so furniture, pets, and
  partial false positives do not become players.
- [x] Reset extraction smoothing at detected scene cuts and smooth stable dancer
  roles across raw tracker-ID changes.
- [x] Prevent instantaneous multi-dancer role shuffles during extraction,
  including pair swaps and larger cyclic permutations: discard recycled tracker
  bindings at shot cuts and override IDs only when two or more roles would make
  physically implausible one-frame position jumps. Preserve identity through
  gradual on-screen crossings and front/behind changes.
- [x] Show clear on-screen nudges to move forward or back, with a framing
  calibration control.
- [x] Research equally capable hands/feet and monocular 3D models without
  removing the verified 2D path; record quality, speed, depth, and licensing
  constraints in `docs/pose-models.md`.
- [x] Add an optional GPU-capable RTMPose Body/COCO-17 backend for both live play
  and extraction while retaining YOLO26 as the verified default.
- [ ] Run moving-video 1/2/4-person RTMPose latency, crossing, occlusion, and
  identity tests before calling it equivalent to YOLO26/ByteTrack; separately
  add whole-body RTMPose only where checkpoint and dataset terms permit it.
- [ ] Prototype optional RTMW3D for offline extraction and avatar retargeting;
  do not use it for live scoring until it proves both accurate and fast.
- [x] Do not replace pose/spatial tracking with face recognition: faces vanish
  during turns and occlusion, so it does not simplify reliable dance tracking.
- [ ] Add optional sparse, RAM-only face re-identification if real crossing and
  re-entry tests show ByteTrack plus spatial rebind is insufficient.
- [ ] Add consented local player profiles for scores/unlocks, initially with
  manual UUIDs; only add persistent biometric fingerprints after a reviewed,
  revocable privacy-preserving design and false-match evaluation.

## Players and choreography

- [x] Support 1–2 players and the 3–4 player stretch goal.
- [x] Increase the runtime cap from four to six players and create HUD/slot UI
  only for dancers who are actually active rather than showing empty players.
- [x] Detect players dynamically: start with any nonzero player count and allow
  joining, leaving, short-term re-identification, and returning mid-song without
  configuring a count in advance.
- [x] Prevent a newly enrolled dancer from inheriting an expired player's
  score/combo.
- [x] Extract choreography offline through the same pose implementation used at
  runtime and emit a portable song package.
- [x] Segment extracted multi-dancer choreography into bounded moves and emit
  compact phase-sampled motion definitions for runtime scoring and cues.
- [x] Extract more than one dancer with knobs for count and chosen raw track IDs.
- [x] Maintain stable authored dancer roles across raw tracking fragmentation.
- [x] Assign live players to extracted dancers by left/right alignment; cover
  every choreography before balanced duplicates (4→2+2, 3→2+1, 2→1+1), and
  assign distinct location-aligned dances when there are fewer players.
- [x] Record per-frame front-to-back and render back-to-front dancer order.
- [x] Show every extracted dancer in generated coaching and upcoming-pose cues.
- [x] Make simulated players perform their assigned choreography in multi-dancer
  songs rather than cloning only the lead pose.

## Play experience

- [x] Show the original song video when present; only show generated
  choreography avatars when video is absent or fails.
- [x] Resolve `--copy-video` media relative to the extracted package and verify
  copied H.264/AAC playback.
- [x] Support separate song audio behind a silent coach video as well as embedded
  video audio.
- [x] Anchor and smoothly interpolate game/scoring/lyrics/cue time from actual
  media playback so decoder startup and buffering cannot desynchronize play.
- [x] Show a mini camera view with detected characters and poses, correctly
  mapped through aspect-fit video geometry.
- [x] Show synchronized lyrics when available.
- [x] Provide optional heads-up upcoming-move pose cues.
- [x] Provide frequent per-player visual judgements, combos, points, 0–5 stars,
  earned currency, song unlocks, and celebratory effects.
- [x] Replace the extracted-video fallback's periodic single-frame pose-copy
  scoring with the authored dance-move segments: capture each player's motion
  across the complete segment, judge once when that move ends, and keep scoring
  independent of starting location, camera position, and apparent body size.
- [x] Put scoring and judgement feedback in the active-player row across the
  top; allow each song to shrink/inset its video while defaulting video scale
  to 100%.
- [x] Change heads-up cues from a constant N-seconds-ahead live pose to the next
  discrete segmented dance move, using that move's authored representative cue
  pose rather than an arbitrary future video frame.
- [ ] Experiment with clustering repeated occurrences of a move into one move
  definition and identical scoring/cues; try a distinctive still pose or arrows
  on the one to three most important moving body parts instead of animation.
- [x] Make every live-player-to-authored-dancer assignment unmistakable: use one
  stable dancer color on that player's score bar, their next-move cue, and their
  real-time mini-view skeleton, label the player/dancer relationship directly,
  and place the same-color semi-transparent cloud bar below the corresponding
  dancer's feet in source video. Keep each bar keyed to its dancer role and
  heavily smooth its position so foot/bounding-box jitter does not make it hop;
  players assigned duplicate choreography share its color.
- [x] Remove score/judgement sound effects while a song is being played; retain
  the song/video audio and visual feedback. This supersedes the earlier request
  for frequent audible gameplay stingers.
- [x] Stop judgement feedback from flickering/restarting invisibly.
- [x] Use compressed Ogg for every tracked music/effect asset and remove WAV
  assets/references from the source tree.
- [x] Rewrite the pre-publication Git history and garbage-collect it so the old
  uncompressed WAV blobs never reach GitHub.
- [x] Use the application icon for the Qt window, desktop entry, and Windows
  installer.
- [x] Support mouse/touch, keyboard, and game controller menu navigation.
- [x] Support pose menu control: hands-up claims control, directional arm
  gestures navigate, and clap activates the focused control.
- [x] Offer join-gesture-only admission for noisy rooms so passers-by, pets, and
  false detections are not automatically added.
- [x] Keep the camera pose loop available from the initial library for hands-free
  control, while throttling non-game inference.
- [x] Move most or all setup options into a dedicated settings screen; explain
  camera framing plainly or calibrate it automatically when reliable.
- [x] Keep the settings screen populated and navigation-tested at both supported
  window sizes; never route to a background-only/blank page.
- [x] Play an interesting short audio/video preview inside the selected song
  card, with an audio fade-in and fade-out. Use an authored song-relative start
  when provided (otherwise a middle excerpt), honor media trims and hidden video
  intros, support embedded or separate audio, and stop immediately when the
  selection or screen changes.
- [ ] Complete final visual/audio polish review at 1280×720 and the 900×540
  minimum with 1–6 players and reduced-motion mode.

## Song tooling

- [x] Add three original, playable songs with deliberate dances whose move
  landings follow the actual beat grid. Two tracks may use the repository's
  sample-free deterministic synth; at least one must use a real generative-music
  model (prefer Leonardo Music v1), record its provenance honestly, and receive
  choreography timed to the generated audio rather than an assumed tempo.
  Solar Sidewalk and Velvet Voltage use the deterministic synth. Brassline
  Breakaway uses Scenario-hosted Meta MusicGen (`stereo-large`; generation job
  and asset IDs, seed, and exact prompt live in its catalog provenance). Its 45
  dance landings follow the measured 120.0107 BPM grid beginning at the detected
  0.18549-second beat phase. Leonardo Music v1 was attempted first but its API
  balance is separate from the web Essential plan's Fast Tokens and rejected
  the request as insufficient.
- [x] Retempo all six bundled original choreographies to move two to four times
  faster: easy songs land every two beats, medium songs alternate one- and
  two-beat gaps, and high-energy Cosmic Afterburn lands every beat. Keep every
  landing on each recording's exact beat phase, vary the sequences for the
  song's genre and mood, and compare centered/body-scaled joint velocity and
  acceleration with extracted dances so the coaches move at playable human
  pacing rather than holding a pose for each four-beat bar.
- [x] Make original-song coach figures follow human kinematics: keep their
  authored bone lengths fixed in an internal 3D model while allowing natural
  2D foreshortening, rotate each bone over its shortest 3D arc, keep cue landings
  exact, and retain a whole-body beat bounce. Do not constrain camera pose
  estimation or alter poses extracted from real dancers.
- [x] Add a cross-platform local-song reprocessing script that detects the
  current move-scoring signature, rebuilds only stale derived artifacts from
  saved stable-role timelines, preserves all other package data, creates a
  non-overwritten manifest backup, and supports a dry run.
- [x] Backfill authored move segments into the five role-aware local song
  packages that predated move scoring, directly from their saved timelines;
  preserve every other manifest field and keep a recoverable backup while
  validating the migration.
- [x] Re-extract the older local Dynamite package from the root `Dynamite.mp4`
  and validate it in a fresh output directory before replacement. Its saved
  timeline predates stable `dancer_index` roles, so timeline-only move
  segmentation would silently produce no usable moves. The replacement has one
  stable role and 133 authored moves; its H.264/AAC media passed real Qt decode.
- [x] Import optional LRC lyrics and optionally copy the original video into a
  song package.
- [x] Add extractor flags to trim independently from the beginning and end while
  keeping choreography, media timing, lyrics, and duration aligned.
- [x] Add a song option to hide an opening portion of video behind a fun intro
  visualization, retain its audio, and exclude that hidden portion from pose
  capture/scoring.
- [x] Add a standalone extracted-song player for audio, poses, and optional
  original video behind the poses; reuse the game's media/choreography playback
  code rather than duplicating non-trivial logic.

## Distribution and repository

- [x] Add core Linux/Windows CI.
- [x] Add automated CUDA-capable Windows per-user installer builds containing
  Python, Qt, model code, CUDA userspace dependencies, and the pose checkpoint.
- [x] Add a Linux CUDA container release and a local self-contained split-bundle
  build/install script instead of publishing a pointless CPU-only AppImage.
- [x] Ensure local container contexts exclude user videos, songs, environments,
  and build artifacts.
- [x] Bundle the AGPL license and verify frozen packages with a real pose
  inference, not just an import check.
- [x] Make the local Linux bundle's compatibility claim truthful: it inherits
  the build host's glibc floor; use the container for a fixed userspace baseline.
- [x] Create the public `emosenkis/opendance` GitHub repository and push the
  cleaned single-commit history.
- [x] Run and fix public CI to green.
- [x] Tag `v0.1.0`, let release automation build both platforms, publish only
  after both succeed, and anonymously verify the GHCR image can be pulled.

## Assets and final validation

- [x] Use native vector/QML visuals where they fit the established look; do not
  spend limited generated-image quota merely to say it was used.
- [ ] Use Leonardo MCP for a specific missing raster asset only when it materially
  improves the game, inspect the result, and keep the limited quota in mind.
- [ ] Run final unit, QML, media, GPU, extractor, frozen-bundle, packaging, and
  repository-hygiene checks after all remaining changes.
- [ ] Continually update this checklist as requests are added or work is verified.
