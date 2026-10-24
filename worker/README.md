# VisionGuard Worker (GPU-Analyse)

Python-Dienst, der alle Kameras analysiert: Ingest → Batched YOLO11 auf der GPU → ByteTrack je Kamera →
Zonen/Track-Lebenszyklus → Identifikation (Gesicht / Kennzeichen / Tier) → MQTT. Dazu ein billiger
Ringpuffer (Stream-Copy) für Clips und Snapshots auf Anforderung. Schnittstellen: siehe `../CONTRACT.md`.

## Aufbau (`vg_worker/`)

| Modul | Aufgabe |
|---|---|
| `service.py` | Einstieg (`python -m vg_worker`): MQTT (paho 2.x), Laden von `GET /api/internal/cameras` (Retry/Backoff, Reload bei `vg/internal/config` + alle 60 s), Status/System alle 5 s (retain), Kommandos |
| `ingest.py` | je Kamera ein ffmpeg-Prozess (`-rtsp_transport tcp`, `fps=detect_fps`, `scale` auf max. 1920 px Breite, `rawvideo bgr24` → Pipe). Hält nur das **neueste** Frame; Watchdog + Reconnect mit Backoff; Zustand `starting/online/offline` |
| `recorder.py` | Ringpuffer: je Kamera ein ffmpeg `-c copy -f segment` (2-s-MPEG-TS-Segmente, Name = Unix-Startzeit) in `VG_BUFFER_DIR` (tmpfs), älter als 30 s wird gelöscht. Clip = Concat-Demuxer `-c copy -movflags +faststart` → mp4 |
| `detector.py` | Ultralytics YOLO11 (`yolo11s.pt`), PyTorch CUDA fp16, ein Batch über alle Kameras. COCO → Labels (person, bicycle, car, motorcycle, bus, truck, bird, cat, dog, horse, sheep, cow, …) |
| `pipeline.py` | zentrale Inferenzschleife (sammelt neueste Frames aller Kameras, Round-Robin, max. 16 pro Batch, verwirft statt zu queuen), Nachverarbeitung je Kamera in eigenem Thread, Ablauf von Tracks, Kommandos `snapshot`/`clip` |
| `tracking.py` | `supervision.ByteTrack` je Kamera, Lebenszyklus laut Contract, Zonen (Fußpunkt = bbox unten-mitte, normiert), bester Crop (score·Fläche) → `crops/YYYY-MM-DD/<uuid>.jpg` |
| `identify.py` | lazy geladene Modelle in eigenen Threads mit begrenzten Queues (Jobs werden verworfen, wenn voll); Fehler eines Modells stoppen die Detektion nicht |
| `selftest.py` | Standalone-Test ohne Backend/MQTT |

### Identifikation

* **Gesicht** (`face_recognition`): InsightFace `buffalo_l` (SCRFD-10G + ArcFace R100, 512-d) via onnxruntime-gpu 1.20.2 (CUDA 12 / cuDNN 9).
  Läuft auf dem oberen Teil (45 %) der Personen-Box, alle 0,4 s pro Track. Qualität = det_score · Größe · Frontalität.
  Erster brauchbarer Treffer (≥ 36 px) → `POST /api/internal/identify` (kind=face, normiertes Embedding, Gesichts-Crop unter `crops/`),
  danach nur noch bei deutlich besserer Qualität (×1,3), max. 3 Aufrufe pro Track.
* **Kennzeichen** (`plate_recognition`, car/truck/bus/motorcycle): `fast-alpr` (open-image-models YOLOv9-t-384 Plate-Detektor + fast-plate-ocr `cct-xs-v2-global-model`, ONNX/CUDA).
  identify erst, wenn derselbe Text ≥ 2× gelesen wurde und die häufigste Lesung ist; `attributes.plate_text` wird gesetzt.
* **Tier** (`pet_recognition`, dog/cat/horse/bird): DINOv2 ViT-S/14 (timm `vit_small_patch14_dinov2.lvd142m`, 224 px, fp16), L2-normiertes 384-d Embedding des besten Crops, einmal pro Track (nach 1,5 s).

Die Identität aus der Antwort (`{id, display_name, name, kind}`) fließt in alle folgenden `detections`/`track`-Nachrichten (`update` sofort).

## Track-Lebenszyklus

* `start`: nach ≥ 3 Treffern mit `score ≥ min_score` (Label = score-gewichtete Mehrheit).
* `update`: sofort bei Zonenwechsel / Identität / Attribut, bei besserem Crop höchstens 1×/s.
* `end`: 2 s ohne Treffer (wartet max. 6 s länger, falls noch eine Identifikation läuft). `left_zones` enthält beim Ende die letzten Zonen.
* `track_id`: prozessweit monoton, startet bei einem aus der Uhrzeit abgeleiteten Wert (< 2³¹) → eindeutig über Worker-/Kamera-Neustarts hinweg.

## Konfiguration (Env)

| Variable | Standard | |
|---|---|---|
| `VG_BACKEND_URL` | `http://backend:8000` | |
| `VG_MQTT_HOST` / `VG_MQTT_PORT` | `mosquitto` / `1883` | |
| `VG_MEDIA_DIR` / `VG_MODELS_DIR` / `VG_BUFFER_DIR` | `/data/media` / `/models` / `/buffer` | alle Modell-Downloads (Ultralytics, InsightFace, fast-alpr, HF/timm) landen in `/models` |
| `VG_DETECT_MODEL` | `yolo11s.pt` | z. B. `yolo11n.pt`, `yolo11m.pt` oder absoluter Pfad |
| `VG_DETECT_IMGSZ` / `VG_DETECT_MAX_BATCH` / `VG_DETECT_HALF` | `640` / `16` / `1` | |
| `VG_MAX_FRAME_WIDTH` | `1920` | Decodierauflösung (Crops/Snapshots) |
| `VG_HWACCEL` | `none` | `cuda`/`auto` = NVDEC (bei `auto` Fallback auf CPU) |
| `VG_BUFFER_SEGMENT_S` / `VG_BUFFER_KEEP_S` / `VG_RECORDER` | `2` / `30` / `1` | |
| `VG_FACE_MODEL` | `buffalo_l` | oder `buffalo_s` |
| `VG_PLATE_DETECTOR` / `VG_PLATE_OCR` | `yolo-v9-t-384-license-plate-end2end` / `cct-xs-v2-global-model` | |
| `VG_PET_MODEL` | `vit_small_patch14_dinov2.lvd142m` | beliebiges timm-Modell |
| `VG_TRACK_CONFIRM_HITS` / `VG_TRACK_END_S` | `3` / `2` | |
| `VG_LOG_LEVEL` | `INFO` | |

## Test

```bash
docker compose build worker
# Standalone (ohne Backend/MQTT; Identify wird simuliert), im Compose-Netz mit GPU:
docker compose run --rm --no-deps worker python -m vg_worker.selftest \
    rtsp://testcam:8554/cam1 rtsp://testcam:8554/cam2 --seconds 40 --clip
# Lasttest 16 Kameras (jede URL 8×):
docker compose run --rm --no-deps worker python -m vg_worker.selftest \
    rtsp://testcam:8554/cam1 rtsp://testcam:8554/cam2 --replicate 8 --seconds 60
# Im Betrieb:
docker compose exec mosquitto mosquitto_sub -t 'vg/internal/#' -C 20 -v
```

## Gemessene Leistung (RTX 4080 Laptop, 768×432 h264 @ 12 fps, detect_fps 5)

| Szenario | Ergebnis |
|---|---|
| 2 Kameras, alle Modelle | 2 × 5,0 fps, GPU-Speicher ≈ 1,0–1,5 GB, Worker-RAM ≈ 2 GB |
| 16 Kameras, alle Modelle | **80 fps** gesamt (16 × 5,0 fps), Ø Batch 5,6 Frames in ≈ 22–26 ms (≈ 4,5 ms/Frame), GPU-Util ≈ 20–35 %, GPU-Speicher 1,9 GB, Host-RAM gesamt ≈ 4,3 GB |
| YOLO11s fp16 isoliert | bs1 ≈ 8 ms, bs16 ≈ 31 ms (≈ 2 ms/Frame) |

`inference_ms` im Status ist die Latenz des Batches, in dem das Kamerabild lief (geglättet). Bei CPU-Last durch
ffmpeg-Decoder steigt die Kernel-Launch-Latenz von PyTorch (bs1 8 ms → ≈ 30–40 ms); der Durchsatz skaliert über das Batching trotzdem.
TensorRT wurde getestet (Export ≈ 4 min, +4,6 GB Aktivierungs-Speicher bei dynamischem Batch 16, keine Vorteile bei kleinen Batches) und ist deshalb **nicht** aktiv.

## Hinweise

* Basis-Image `pytorch/pytorch:2.5.1-cuda12.4-cudnn9-runtime` (Host-Treiber unterstützt max. CUDA 12.4). onnxruntime-gpu nutzt cuDNN/cuBLAS aus den Torch-Wheels (`LD_LIBRARY_PATH`).
* ffmpeg 4.4 (Ubuntu 22.04): RTSP-Timeout heißt dort `-stimeout` (wird automatisch gewählt).
* Erster Start lädt ca. 400 MB Modelle nach `/models` (yolo11s 19 MB, buffalo_l 280 MB, DINOv2-S 88 MB, ALPR ≈ 10 MB).
* Retained Kommandos auf `vg/internal/cmd/+` werden ignoriert. Kein Last-Will (Backend erkennt Worker-Ausfall über das Ausbleiben von `vg/internal/system`, 15 s).
