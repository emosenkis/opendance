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
- [x] Keep camera framing automatic rather than user-configurable: tell players
  to move back when their head or feet reach the frame edge, and forward only
  when their detected body is less than 25% of the frame height.
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
- [x] Allow the song/countdown to start before any live dancer has been detected;
  players may step into view and join after playback has begun.
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
- [x] Provide per-move player judgements, combos, points, 0–5 stars, earned
  currency, song unlocks, and celebratory effects without rapid-fire feedback.
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
  real-time mini-view skeleton without numbered relationship labels, and place
  one same-color, borderless translucent rounded rectangle below the
  corresponding dancer's feet in source video, feathered at every edge until it
  fades to transparency. Keep each indicator keyed to its dancer role; settle
  small foot/bounding-box changes almost imperceptibly slowly, but smoothly
  follow meaningful travel without jitter. Players assigned duplicate
  choreography share its color.
- [x] Remove score/judgement sound effects while a song is being played; retain
  the song/video audio and visual feedback. This supersedes the earlier request
  for frequent audible gameplay stingers.
- [x] Stop judgement feedback from flickering/restarting invisibly.
- [x] Show at most one judgement per completed dance move (never several times a
  second), with a minimum multi-second feedback interval for legacy/unsegmented
  choreography, and place feedback so it never obscures dancer identity, score,
  assignment color, or upcoming-move information.
- [x] Size each feathered dancer marker from the horizontal distance between that
  dancer's detected feet, smoothing width with the same dead-zone/slow-settle
  behavior as position and clamping it above the gameplay progress bar. Remove
  all numbered `P1`, `D1`, and `P1 -> D1` identity labels: assignment is color-
  only, while any player names use stable adjective/verb-based nicknames.
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
- [x] Move setup options into a dedicated settings screen while keeping camera
  framing out of settings and deriving its guidance directly from live poses.
- [x] Keep the settings screen populated and navigation-tested at both supported
  window sizes; never route to a background-only/blank page.
- [x] Play an interesting short audio/video preview inside the selected song
  card, with an audio fade-in and fade-out. Use an authored song-relative start
  when provided (otherwise a middle excerpt), honor media trims and hidden video
  intros, support embedded or separate audio, and stop immediately when the
  selection or screen changes.
- [x] Remember fullscreen across launches alongside the existing persistent
  settings. Show the song library as exactly two rows with no clipped edge
  cards; navigate horizontally by complete columns, show every video song's
  preview-start frame as its resting thumbnail, animate/audio-preview only the
  selected song, and reserve the existing art placeholder for songs without
  video.
- [x] Render unselected song thumbnails as cached still images extracted at the
  preview start rather than live video players. In the two-row grid, keep
  left/right on the same row and up/down in the same column, including gamepad
  navigation and incomplete final columns.
- [ ] Complete final visual/audio polish review at 1280×720 and the 900×540
  minimum with 1–6 players and reduced-motion mode.

## Song tooling

- [x] Integrate song importing into Settings: choose a local video or supported
  URL, automatically prefill embedded/file-name title, artist, and duration,
  then review/edit the useful extraction choices before work begins. Expose
  title, artist, start/end trim, hidden opening video, 1–6 authored dancers,
  automatic portable video packaging, and optional LRC lyrics while keeping model, device,
  image-size, smoothing, raw-track, ID, CLI-progress, and overwrite controls out of
  the end-user flow. Run download/metadata/extraction work off the UI thread,
  reuse the loaded pose engine when possible, show analyzed/total frames,
  percentage, processing FPS, ETA, and the final role/move-building phase, then
  refresh and visibly select the new song.
- [x] Ship a catch-all URL-helper config that delegates HTTP(S) downloads to the
  installed `yt-dlp`, letting its current extractor set decide which sites are
  supported and saving into OpenDance's writable import area. Allow domain-
  scoped overrides in `$XDG_CONFIG_HOME/opendance/config.toml`; choose the most-
  specific exact or parent-domain match, execute argv directly without a shell,
  provide `URL`, and accept exactly one existing downloaded-file path on stdout.
  Surface malformed config, unsafe URLs, failures, timeouts, and invalid output
  in the import dialog.
- [x] Make the built-in yt-dlp helper retain its selected MP4 video/audio codec
  constraints while strictly capping both dimensions at 1920x1080, never
  falling back to WebM/4K, and keeping the final-path stdout contract.
- [x] Stream machine-readable yt-dlp download percentage, bytes, speed, and ETA
  into the in-app import progress/status while preserving cancellation, timeout,
  custom helper compatibility, and the final-path stdout contract.
- [x] While in-app pose extraction is running, replace the editable Add Song form
  with the latest processed video frame and its detected pose overlay, refreshing
  every 5–10 seconds without slowing the frame-analysis hot path; retain the
  extraction percentage, frame count, processing FPS, ETA, and cancel-safe UI.
- [x] Remove the in-app “copy video” choice. Always package an intact playable
  video; for URL imports move the helper-downloaded file into the song package
  so exactly one copy remains, while local-file imports preserve the user's
  source and copy it into the package.
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
- [x] Add a cross-platform local-song reprocessing script that detects both the
  current role-assignment method and move-scoring signature. Without repeating
  video inference, reassign stale multi-dancer timelines through the newest
  spatial shuffle guard, refresh dancer/lead metadata, then rebuild the
  dependent scoring segments; leave already-current and legacy one-dancer role
  data alone. Preserve every unrelated package field, create a non-overwritten
  manifest backup, support package-specific paths, and support a dry run over
  every local song.
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
