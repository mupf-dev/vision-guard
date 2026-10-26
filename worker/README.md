# VisionGuard Worker (GPU analysis)

Python service that analyzes all cameras: ingest → batched YOLO11 on the GPU → ByteTrack per camera →
zones/track lifecycle → identification (face / license plate / animal) → MQTT. Plus a cheap
ring buffer (stream copy) for clips and snapshots on request. Interfaces: see `../CONTRACT.md` (German).

## Structure (`vg_worker/`)

| Module | Purpose |
|---|---|
| `service.py` | Entry point (`python -m vg_worker`): MQTT (paho 2.x, sets `worker_id` in every message), loading `GET /api/internal/cameras` (retry/backoff, reload on `vg/internal/config` + every 60 s), status/system every 5 s (retain), commands |
| `ingest.py` | one ffmpeg process per camera (`-rtsp_transport tcp`, `fps=detect_fps`, `scale` to max. 1920 px width, `rawvideo bgr24` → pipe). Keeps only the **newest** frame; watchdog + reconnect with backoff; state `starting/online/offline` |
| `recorder.py` | Ring buffer: one ffmpeg `-c copy -f segment` per camera (2-s MPEG-TS segments, name = Unix start time) in `VG_BUFFER_DIR` (tmpfs), anything older than 30 s is deleted. Clip = concat demuxer `-c copy -movflags +faststart` → mp4 |
| `detector.py` | Ultralytics YOLO11 (`yolo11s.pt`), PyTorch CUDA fp16, one batch across all cameras. COCO → labels (person, bicycle, car, motorcycle, bus, truck, bird, cat, dog, horse, sheep, cow, …) |
| `pipeline.py` | central inference loop (collects the newest frames of all cameras, round-robin, max. 16 per batch, drops instead of queueing), post-processing per camera in its own thread, track expiry, commands `snapshot`/`clip` |
| `tracking.py` | `supervision.ByteTrack` per camera, lifecycle as per the contract, zones (foot point = bbox bottom-center, normalized), best crop (score·area) → `crops/YYYY-MM-DD/<uuid>.jpg` |
| `identify.py` | lazily loaded models in their own threads with bounded queues (jobs are dropped when full); errors of one model do not stop detection |
| `inventory.py` | Inventory (items) on selected cameras: keyframe in full resolution, YOLOE with text prompts, tiling, stabilization, `vg/internal/inventory/{id}` (see below) |
| `frame_metrics.py` | Image metrics on the CPU (320 px grayscale): brightness, contrast, normalized sharpness, flat blocks, saturation; comparison with reference via phase correlation (C5, later A3) |
| `health.py` | Camera health (C5, own thread, CPU only): `dark`/`covered`/`blur`/`frozen`/`moved`, reference images, `vg/internal/health/{id}`, command `health_reference` (see below) |
| `util.py` | Helper functions; **the only place that writes media**: `store_media(rel_path, bytes)` / `write_jpeg` and `media_target(rel_path, suffix)` for ffmpeg outputs (clips), each atomic. Other modules never write directly to `VG_MEDIA_DIR` (preparation for media upload, D3) |
| `pose.py` / `posture.py` / `gestures.py` | Pose stage (C1/D1): YOLO11n-pose on cameras with `pose_enabled`/`gesture_enabled`, posture `standing/sitting/lying`, arm gestures → `vg/internal/gesture/{id}` (see below) |
| `selftest.py` | Standalone test without backend/MQTT (`--inventory` for the item pipeline, `--pose`/`--gestures` for the pose stage) |
| `posecheck.py` | Measurement tool C1/D1: runs a video file offline through detector + tracker + pose (falls vs. still-image segments, gestures) |

### Identification

* **Face** (`face_recognition`): InsightFace `buffalo_l` (SCRFD-10G + ArcFace R100, 512-d) via onnxruntime-gpu 1.20.2 (CUDA 12 / cuDNN 9).
  Runs on the upper part (45 %) of the person box. Since Identities 07 (package B, `face_track.py`) **over the whole
  track duration**: every 0.4 s per track, every 2 s once `face_track_faces` (5) good faces are available; after the first
  3 jobs per track, globally at most 15 jobs/s (the rest is dropped). The best K faces stay in RAM.
  Early `phase:"provisional"` (first face ≥ 0.2 quality, only assignment to existing identities), after 30 s and at track end
  `phase:"final"` with the L2-normalized mean of the best K (outliers < 0.3 cosine removed) and the individual images as
  `samples` (CONTRACT ID.F). The track end waits for it (≤ 6 s).
  Quality (`face_quality.py`) = det_score · min(1, size/112) · cos²(yaw)·cos(pitch) · (0.5 + 0.5·min(1, sharpness/60));
  yaw/pitch from the 5 facial landmarks, sharpness = Laplacian variance of the aligned 112×112 face, plus
  `day_night` (grayscale image = IR). `face_size` in pixels of the image it was cropped from.
  **Full resolution** (`face_fullres`, `fullres.py`): additional ffmpeg reader on the main stream (2 frames/s, only
  while persons are present, 30 s run-on; not if main stream ≤ analysis image) – crop from the simultaneous
  full frame (≤ 0.12 s), otherwise from the analysis image. Cost per active camera ≈ 15 % of a CPU core, ≈ 150 MB RAM
  (measurement: `docs/plan/07-identitaeten-fortschritt.md` (German)). Metrics: `perf.face`, `models.face_fullres`.
* **License plate** (`plate_recognition`, car/truck/bus/motorcycle): `fast-alpr` (open-image-models YOLOv9-t-384 plate detector + fast-plate-ocr `cct-xs-v2-global-model`, ONNX/CUDA).
  identify only once the same text has been read ≥ 2× and is the most frequent reading; `attributes.plate_text` is set.
* **Animal** (`pet_recognition`, dog/cat/horse/bird): DINOv2 ViT-S/14 (timm `vit_small_patch14_dinov2.lvd142m`, 224 px, fp16), L2-normalized 384-d embedding of the best crop, once per track (after 1.5 s).

The identity from the response (`{id, display_name, name, kind}`) flows into all subsequent `detections`/`track` messages (`update` immediately).

## Inventory (items)

Slow second pipeline only for cameras with `inventory_enabled` (interface: CONTRACT.md (German), addendum G).
Inventory fields do **not** trigger an ffmpeg restart (not in `_STREAM_KEYS`), they only go to the engine.

* **Image source** (`KeyframeGrabber`): first frame of the last *finished* ring buffer segment of the main stream
  (`ffmpeg -frames:v 1`, full resolution, no additional RTSP connection, image 2–4 s old). If the ring buffer is off or
  stuck, fallback to go2rtc `/api/frame.jpeg?src=cam_N`, then a one-off `ffmpeg -skip_frame nokey` on `main_rtsp`.
  The same segment is never analyzed twice.
* **Model** (`InventoryModel`): loaded lazily only when a camera enables inventory; unloaded again after 10 min without
  an inventory camera. Default `yoloe-11m-seg.pt` with text prompts: the vocabulary of all inventory cameras (prompts from
  `inventory_vocab`) is set via `set_classes`; the text embeddings are cached under `/models/yoloe/pe_*.pt`,
  the text encoder (MobileCLIP `mobileclip_blt.ts`, located in `/models/yoloe`) is only needed the first time or when the
  vocabulary changes and then always runs on the **CPU** (never goes into VRAM). Downloads go via an
  absolute path to `/models/yoloe` (no `chdir`). After loading or a vocabulary change, a dummy inference warms up
  the model without the GPU lock; the full frame is downscaled to `imgsz` before the lock. A model with fixed class names (e.g. `yolo11s.pt` or YOLOE `-pf`) works too:
  vocabulary entries are then mapped to the class names via `label`/`prompts` (COCO: book, remote, cup …).
  Persons and vehicles are never reported as items.
* **Tiling** (`inventory_tiling`, default **off** – no gain in the feasibility study at 2–3× GPU time):
  full image at `VG_INVENTORY_IMGSZ` plus tiles (`VG_INVENTORY_TILES`, default
  2×2, 15 % overlap) at `VG_INVENTORY_TILE_IMGSZ`. Hits touching an *inner* tile edge are
  dropped (the full image/neighboring tile has them completely); then NMS per label, removal of contained duplicates and, across labels: nearly the same box (IoU ≥ 0.7) with
  several labels (phone as tablet/book/plate) → only the label with the highest score.
* **GPU alternation:** every `predict` (full image, each tile) runs only after a safety batch has finished and
  under the same lock as `detect` – the two alternate. **Compute budget:** effective interval per camera
  = max(`inventory_interval_s`, GPU time × number of inventory cameras / `VG_INVENTORY_MAX_BUSY`). At most one
  run per camera; missed ticks lapse (`perf.inventory.skipped`), nothing piles up. Errors (model, image source)
  stay in the inventory thread; `models.inventory` shows `off|loading|ready|error: …`.
* **Stabilization** (`InventoryState`): matching via label + IoU ≥ 0.3 or center distance < 0.05, bbox/score
  smoothed. `present` from `inventory_confirm_n` hits in 2·n runs, `missing` after `inventory_miss_m`
  consecutive misses (from `GET /api/internal/settings`). A miss does not count if a person (raw
  YOLO detections ±3 s around the capture time) covers > 30 % of the item (`occluded`). Detections lying ≥ 60 %
  inside a person box (carried things) are ignored. Never-confirmed candidates expire silently,
  `missing` is carried along for 10 more runs. `moved` at > 0.1 displacement.
* **Media:** crop per item on appearance/move, otherwise on a better score at most 1×/min →
  `inventory/YYYY-MM-DD/<item_id>_<ts>.jpg`; on changes an overview image (max. 1920 px wide) →
  `inventory/YYYY-MM-DD/cam<id>_<ts>.jpg`.
* **MQTT:** after each run the full state on `vg/internal/inventory/{id}` (QoS 1, retain). When the
  camera/inventory is switched off and when the worker shuts down, once `stopped: true` with `items: []`. `session` is new
  on each worker start.

## Camera health (C5)

* Every `VG_HEALTH_INTERVAL_S` (10 s) per camera with `health_enabled` and state `online`, a check image from the ingest
  (no additional stream), approx. 3–6 ms CPU per check. Thresholds from measurement W0 (`docs/plan/roadmap/messungen.md` (German)).
* Findings: `dark` (mean < 25 and 95th percentile < 60) before `covered` (flat blocks +35 percentage points or contrast
  < 25 % of the reference) before `blur` (sharpness < 0.45 × reference) before `moved` (shift > 2 % or correlation
  < 0.5 × moving baseline); additionally `frozen` (difference to the previous check image < 0.05).
* A finding is reported after two consecutive checks (`since` = the first), it ends after two unremarkable ones.
  Warning/all-clear (60 s) is decided by the backend.
* References `health/cam<N>_<day|night>.jpg` (640 px; night = grayscale/IR). Automatically from the first usable image
  of the mode, manually via `vg/internal/cmd/{id}` `{action:"health_reference", mode:"day|night|auto"}`. At startup
  existing references are loaded. Day/night is determined only from usable images (a covered image is
  colorless and would otherwise count as a night image).
* `vg/internal/health/{id}` (QoS 1, retain) every `VG_HEALTH_PUBLISH_S` (30 s) and on change; when the check is
  switched off, once `enabled: false`. `vg/internal/system.perf.health = {ms, cameras}`.

## Pose, posture and gestures (C1/D1)

* Only on cameras with `pose_enabled` or `gesture_enabled` and only while a confirmed person track is running:
  **full-frame pose** `yolo11n-pose` (640 px, fp16; messungen.md §2: more accurate and cheaper than crops) at 2 Hz
  (posture) or 5 Hz (gestures). Own thread, newest image per camera (older ones dropped), several cameras per
  batch, GPU alternating with detection (`GpuTurn`), budget `VG_POSE_MAX_BUSY` (20 %). Model loads on demand
  (one-off download to `/models/pose`), unloads after 10 min without a pose camera; load error → `models.pose =
  "error: …"`, detection keeps running. ≈ 0.1 GB VRAM, 9–13 ms per image.
* **Posture** per image: `lying` if the box is wider than 1.3 × its height or torso/body is tilted > 55°;
  `upright` (box clearly taller than wide or torso vertical) ends "lying"; otherwise `standing`/`sitting` via
  thigh angle. Smoothed over 2 s → `attributes.posture`, `posture_since`, `motion` (track `update` on
  change).
* **Run-on** (`fall_track_grace_s`, settings): the detector often loses lying persons. A lying track
  ends only after this time; a flat detection or pose person at the same spot is re-attached.
  If a person stands upright at that spot, the run-on ends immediately.
* **Gestures** (`arms_up`, `arm_left`, `arm_right`, `arms_cross` = X above the head) rule-based on the keypoints;
  valid after `gesture_hold_s` in ≥ 80 % of the pose images, then a 5 s lockout per track.

## Virtual sensors (A3/C4)

* Consumer `sensors` on the frame tap (own thread, only the newest image per camera). Sensors come from
  `GET /api/internal/cameras` (`sensors`, kinds `brightness|blink|reference|fill`, plus `vlm` only for the change measure).
* Measurement within the polygon on the detect frame: `brightness` = mean luma 0..1; `blink` = std. dev. of brightness over
  `window_s` (sampling ≈ 5/s while a blink sensor is active); `reference`/`fill` = DINOv2 (shared animal model,
  loaded on demand) against reference images → P(on) or fill level 0..1 (softmax-weighted levels 1/0.5/0;
  for the fill level mixed half-and-half with the color similarity of a 16×16 thumbnail).
  Reference images (`sensors/ref/…`, whole camera image) are scaled to the detect resolution, the region is
  cropped, embeddings are cached per reference + region.
* `occluded`: person boxes cover > 10 % of the region → the measurement does not count. `change`: deviation of a
  32×32 grayscale image from the anchor image; reported once from `change_threshold`, then a new anchor.
* `vg/internal/sensor/{id}` (QoS 0) per measurement, `vg/internal/system.perf.sensors = {per_min, ms}`. CPU ≈ 1 ms per
  measurement, no additional VRAM (DINOv2 is shared with animal recognition; without animal recognition ≈ 0.1 GB).

## Time machine (B1)

* Only on cameras with `history_enabled`: frame tap consumer `history` (own thread, analyzed detect frame,
  at most `VG_MAX_FRAME_WIDTH` wide, no additional ffmpeg) every `history_interval_s` (5–600 s).
* Model **SigLIP 2 ViT-B/16-256** (`open_clip_torch==3.2.0`, own Dockerfile layer; weights once to
  `/models/history`, ≈ 1.4 GB): image tower fp16 on the GPU (≈ 0.25 GB, ≈ 11 ms per image, 5 images ≈ 39 ms),
  text tower fp32 on the CPU (≈ 30 ms per query, ≈ 1.1 GB RAM). Alternative `mobileclip2-s0` (512-d, zero-padded
  to 768). Load error → `models.history = "error: …"`, new attempt after 5 min, detection keeps running.
* Full image + optionally 4 tiles (2×2, 10 % overlap, `history_tiling`). Dedupe against the last stored
  image of the camera (largest change across all tiles < `history_min_change` → only `extend`), but a new image at
  least every 30 min. Preview 256 px wide (`history/YYYY-MM-DD/cam{N}_{unix}.jpg`).
* Text queries via `vg/internal/embed` with `model:"siglip2"`; without an active camera the model loads on the first
  query (≈ 5 s, the backend then retries briefly) and unloads after 10 min idle.
* GPU lease: the image tower moves to main memory (no reload from disk), ticks lapse
  (`perf.history.skipped`), then back to the GPU. `perf.history = {per_min, ms, skipped, stored_per_min, cameras}`.

## Person across several cameras (C2, ReID)

* Only on cameras with `reid_enabled` (default off). Per confirmed person track at most every 3 s and at most
  5 crops (box ≥ 128 px high, taller than wide, not at the edge, not lying, not covered by another
  person) → OSNet x0_25 (MSMT17, ONNX, 512-d) → `POST /api/internal/reid`. Response: `presence_key` as
  track attribute, possibly an identity passed on via ReID (`attributes.identity_source: "reid"`, only without an own one;
  a face recognized later takes precedence).
* Default **CPU** (≈ 7 ms per crop, 0 MB VRAM; messungen.md §11). Weights once to `/models/reid`
  (0.9 MB, SHA-256 checked). Load error → `models.reid = "error: …"`, detection keeps running.
* **Embeddings in RAM only**, neither logged nor stored. `perf.reid = {embeds_per_min, runs_per_min, ms,
  skipped, cameras, sent, linked, new, errors}`.
* Feasibility test: `python -m vg_worker.tests_feasibility.reidtest --market <Market-1501>` (rank-1/mAP,
  household simulation, time, VRAM; `--dino` compares with the animal model).

## Track lifecycle

* `start`: after ≥ 3 hits with `score ≥ min_score` (label = score-weighted majority).
* `update`: immediately on zone change / identity / attribute, on a better crop at most 1×/s.
* `end`: 2 s without hits (waits at most 6 s longer if an identification is still running). `left_zones` contains the last zones at the end.
* `track_id`: monotonic across the process, starts at a value derived from the clock (< 2³¹) → unique across worker/camera restarts.

## Configuration (env)

| Variable | Default | |
|---|---|---|
| `VG_WORKER_ID` | `local` | Worker ID (`[A-Za-z0-9_-]`, ≤ 64 characters): field `worker_id` in all MQTT messages and part of the MQTT `client_id` (preparation for distributed workers, D3) |
| `VG_BACKEND_URL` | `http://backend:8000` | |
| `VG_MQTT_HOST` / `VG_MQTT_PORT` | `mosquitto` / `1883` | |
| `VG_MEDIA_DIR` / `VG_MODELS_DIR` / `VG_BUFFER_DIR` | `/data/media` / `/models` / `/buffer` | all model downloads (Ultralytics, InsightFace, fast-alpr, HF/timm) end up in `/models` |
| `VG_DETECT_MODEL` | `yolo11s.pt` | e.g. `yolo11n.pt`, `yolo11m.pt` or an absolute path |
| `VG_DETECT_IMGSZ` / `VG_DETECT_MAX_BATCH` / `VG_DETECT_HALF` | `640` / `16` / `1` | |
| `VG_MAX_FRAME_WIDTH` | `1920` | Decoding resolution (crops/snapshots) |
| `VG_HWACCEL` | `none` | `cuda`/`auto` = NVDEC (with `auto` fallback to CPU) |
| `VG_BUFFER_SEGMENT_S` / `VG_BUFFER_KEEP_S` / `VG_RECORDER` | `2` / `30` / `1` | |
| `VG_FACE_MODEL` | `buffalo_l` | or `buffalo_s` |
| `VG_FACE_INTERVAL_S` / `VG_FACE_INTERVAL_SLOW_S` | `0.4` / `2` | Face jobs per track (collecting / enough good faces) |
| `VG_FACE_MAX_JOBS_PER_S` / `VG_FACE_FREE_JOBS` / `VG_FACE_MAX_JOBS_TRACK` | `15` / `3` / `120` | global budget, budget-free first jobs, upper limit per track |
| `VG_FACE_PROVISIONAL_MIN_Q` / `VG_FACE_FINAL_AFTER_S` / `VG_FACE_TRACK_OUTLIER` | `0.2` / `30` / `0.3` | provisional from quality / first final assignment / outlier limit |
| `VG_FACE_FULLRES_FPS` / `VG_FACE_FULLRES_IDLE_S` | `2` / `30` | Full-frame reader: frames/s, run-on without a person |
| `VG_FACE_FULLRES_MAX_DT_S` / `VG_FACE_FULLRES_WAIT_S` / `VG_FACE_CROP_MAX_PX` | `0.12` / `1.5` / `960` | Time gap full frame ↔ analysis image, wait time, max. crop |
| `VG_PLATE_DETECTOR` / `VG_PLATE_OCR` | `yolo-v9-t-384-license-plate-end2end` / `cct-xs-v2-global-model` | |
| `VG_PET_MODEL` | `vit_small_patch14_dinov2.lvd142m` | any timm model |
| `VG_TRACK_CONFIRM_HITS` / `VG_TRACK_END_S` | `3` / `2` | |
| `VG_INVENTORY_MODEL` | `yoloe-11m-seg.pt` | Inventory model (`yoloe-11s-seg.pt`, `yoloe-11s-seg-pf.pt` or COCO `yolo11s.pt`) |
| `VG_INVENTORY_IMGSZ` / `VG_INVENTORY_TILE_IMGSZ` | `1280` / `960` | Resolution of full image / per tile |
| `VG_INVENTORY_TILES` / `VG_INVENTORY_OVERLAP` | `2x2` / `0.15` | Tiles (columns×rows, `0` = off) |
| `VG_INVENTORY_MAX_BUSY` | `0.25` | max. GPU time share of the inventory (otherwise the interval is stretched) |
| `VG_POSE_MODEL` | `yolo11n-pose.pt` | Pose model (C1/D1) |
| `VG_POSE_FPS` / `VG_GESTURE_FPS` | `2` / `5` | Pose images per second and camera (posture / gesture cameras) |
| `VG_POSE_MAX_BUSY` | `0.2` | max. GPU time share of the pose stage (above it, images are skipped) |
| `VG_INVENTORY_HALF` / `VG_INVENTORY_MIN_CONF` | `1` / `0.05` | fp16; lower bound for `predict(conf)` |
| `VG_INVENTORY_SOURCE` | `auto` | `auto` (ring buffer → go2rtc → RTSP), `buffer`, `go2rtc`, `rtsp` |
| `VG_INVENTORY_CROP_MIN_S` / `VG_INVENTORY_UNLOAD_S` / `VG_INVENTORY_PERSON_HOLD_S` | `60` / `600` / `3` | Crop interval, unloading without an inventory camera, time window for occlusion |
| `VG_HEALTH` / `VG_HEALTH_INTERVAL_S` / `VG_HEALTH_PUBLISH_S` | `1` / `10` / `30` | Camera health: global switch, check interval per camera, latest send |
| `VG_REID_DEVICE` / `VG_REID_THREADS` | `cpu` / `2` | ReID (C2) on `cpu` or `cuda` |
| `VG_REID_INTERVAL_S` / `VG_REID_MAX_PER_TRACK` / `VG_REID_MIN_HEIGHT` | `3` / `5` / `128` | Throttling and minimum size per track |
| `VG_REID_MODEL_URL` / `VG_REID_MODEL_SHA256` | Hugging Face `kornia/osnet` / checksum | own weights (empty checksum = no check) |
| `VG_GO2RTC_URL` | `http://go2rtc:1984` | only for the keyframe fallback |
| `VG_LOG_LEVEL` | `INFO` | |

## Test

```bash
docker compose build worker
# Standalone (without backend/MQTT; identify is simulated), in the Compose network with GPU:
docker compose run --rm --no-deps worker python -m vg_worker.selftest \
    rtsp://testcam:8554/cam1 rtsp://testcam:8554/cam2 --seconds 40 --clip
# Load test 16 cameras (each URL 8×):
docker compose run --rm --no-deps worker python -m vg_worker.selftest \
    rtsp://testcam:8554/cam1 rtsp://testcam:8554/cam2 --replicate 8 --seconds 60
# Inventory (items) on the living-room camera (2880×1616, side table with lamp/book/remote):
docker compose run --rm --no-deps worker python -m vg_worker.selftest rtsp://go2rtc:8554/cam_3 \
    --no-face --no-plate --no-pet --inventory --inventory-interval 3 --seconds 60
# Also check camera health (otherwise off in the self-test; reference images only in a temporary folder):
docker compose run --rm --no-deps worker python -m vg_worker.selftest rtsp://testcam:8554/cam5 --health --seconds 40
# Pose stage (posture/gestures) on all test cameras, e.g. fall video:
flock ~/vg-gpu.lock docker compose run --rm --no-deps worker python -m vg_worker.selftest rtsp://testcam:8554/cam3 --pose --seconds 120
# Offline measurement: falls (still-image segments of sturz.mp4) or gestures
flock ~/vg-gpu.lock docker compose run --rm --no-deps -v "$PWD/testcam/videos:/videos:ro" worker \
    python -m vg_worker.posecheck /videos/sturz.mp4 --dwell 5
# Investigate hangs: stacks of all threads into the log
docker compose kill --signal=USR1 worker
# In operation:
docker compose exec mosquitto mosquitto_sub -t 'vg/internal/#' -C 20 -v
docker compose exec mosquitto mosquitto_sub -t 'vg/internal/health/#' -C 2 -v
docker compose exec mosquitto mosquitto_sub -t 'vg/internal/inventory/#' -C 2 -v
```

## Measured performance (RTX 4080 Laptop, 768×432 h264 @ 12 fps, detect_fps 5)

| Scenario | Result |
|---|---|
| 2 cameras, all models | 2 × 5.0 fps, GPU memory ≈ 1.0–1.5 GB, worker RAM ≈ 2 GB |
| 16 cameras, all models | **80 fps** total (16 × 5.0 fps), avg. batch 5.6 frames in ≈ 22–26 ms (≈ 4.5 ms/frame), GPU util ≈ 20–35 %, GPU memory 1.9 GB, total host RAM ≈ 4.3 GB |
| YOLO11s fp16 isolated | bs1 ≈ 8 ms, bs16 ≈ 31 ms (≈ 2 ms/frame) |
| Inventory, COCO fallback `yolo11s.pt` on **CPU**, 2880×1616 + 2×2 tiles | ≈ 850–930 ms per run (functional test; GPU/YOLOE still to be measured) |

`inference_ms` in the status is the latency of the batch in which the camera image ran (smoothed). Under CPU load from
ffmpeg decoders, PyTorch's kernel launch latency rises (bs1 8 ms → ≈ 30–40 ms); throughput still scales via batching.
TensorRT was tested (export ≈ 4 min, +4.6 GB activation memory with dynamic batch 16, no benefits for small batches) and is therefore **not** active.

## Notes

* Base image `pytorch/pytorch:2.5.1-cuda12.4-cudnn9-runtime` (host driver supports max. CUDA 12.4). onnxruntime-gpu uses cuDNN/cuBLAS from the Torch wheels (`LD_LIBRARY_PATH`).
* ffmpeg 4.4 (Ubuntu 22.04): the RTSP timeout is called `-stimeout` there (selected automatically).
* First start downloads approx. 400 MB of models to `/models` (yolo11s 19 MB, buffalo_l 280 MB, DINOv2-S 88 MB, ALPR ≈ 10 MB).
* On first activation, inventory downloads YOLOE (`/models/yoloe`, ≈ 30 MB for `-s`) and once the text encoder
  MobileCLIP (`mobileclip_blt.ts`, ≈ 600 MB). YOLOE, like YOLO11, is licensed under AGPL-3.0, MobileCLIP under the Apple license.
* Retained commands on `vg/internal/cmd/+` are ignored. No last will (the backend detects worker failure by the absence of `vg/internal/system`, 15 s).
