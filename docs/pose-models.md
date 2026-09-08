# Pose model choices

OpenDance keeps YOLO26 Pose/COCO-17 as its default because it is the only tested
path that combines good multi-person quality, tracking, redistribution terms,
and real-time performance on the target RTX 3050 Ti class hardware.

An opt-in RTMPose Body/COCO-17 path is implemented through rtmlib and ONNX
Runtime CUDA. It uses a separate YOLOX person detector, not YOLO26, and feeds the
same OpenDance pose contract used by live play and extraction. On the current
MX450 host its lightweight preset processed a four-person 1080p frame in about
39 ms after warm-up. Its short spatial-continuity IDs can swap when people
cross, so it remains experimental until moving-video 1/2/4-person latency,
occlusion, and identity tests pass. This option is not a whole-body model and
does not change the game's 17 scoring points.

The strongest practical whole-body experiment is a top-down RTMW-L 256×192
pass over the existing YOLO person boxes. It provides 133 COCO-WholeBody points
(body, feet, face, and hands); DWPose-M is the faster alternative. Neither is
bundled because the official COCO-WholeBody terms say research/non-commercial
use and the checkpoint redistribution position is unresolved. Finger gestures
also remain unreliable when full-body webcam framing leaves only a few pixels
per finger.

RTMW3D-L is a reasonable offline experiment, not a live default. Its depth is
root-relative per person rather than global scene depth, so it cannot replace
OpenDance's 2D overlap and bounding-box ordering. Live scoring stays 2D until a
dance-specific benchmark shows better accuracy without unacceptable latency.

Face matching would not replace the tracker: dancers turn away, occlude one
another, and leave the face too small or blurred. YuNet plus SFace could later
run sparsely as RAM-only re-identification, but persistent face templates are
biometric data even when the source image is discarded. OpenDance currently
saves no frames or face embeddings.

Primary references:

- [rtmlib RTMPose API and model presets](https://github.com/Tau-J/rtmlib/blob/main/README.md)
- [ONNX Runtime CUDA provider compatibility](https://onnxruntime.ai/docs/execution-providers/CUDA-ExecutionProvider.html)
- [COCO-WholeBody dataset and terms](https://github.com/jin-s13/COCO-WholeBody)
- [RTMW paper and benchmarks](https://arxiv.org/html/2407.08634)
- [DWPose official model table](https://github.com/IDEA-Research/DWPose#-results-and-models)
- [RTMPose3D official model table](https://github.com/open-mmlab/mmpose/blob/main/projects/rtmpose3d/README.md)
- [OpenCV YuNet/SFace documentation](https://docs.opencv.org/4.13.0/d0/dd4/tutorial_dnn_face.html)
