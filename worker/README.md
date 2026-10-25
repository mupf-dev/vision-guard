# VisionGuard Worker (GPU-Analyse)

Python-Dienst, der alle Kameras analysiert: Ingest → Batched YOLO11 auf der GPU → ByteTrack je Kamera →
Zonen/Track-Lebenszyklus → Identifikation (Gesicht / Kennzeichen / Tier) → MQTT. Dazu ein billiger
Ringpuffer (Stream-Copy) für Clips und Snapshots auf Anforderung. Schnittstellen: siehe `../CONTRACT.md`.

## Aufbau (`vg_worker/`)

| Modul | Aufgabe |
|---|---|
| `service.py` | Einstieg (`python -m vg_worker`): MQTT (paho 2.x, setzt `worker_id` in jede Nachricht), Laden von `GET /api/internal/cameras` (Retry/Backoff, Reload bei `vg/internal/config` + alle 60 s), Status/System alle 5 s (retain), Kommandos |
| `ingest.py` | je Kamera ein ffmpeg-Prozess (`-rtsp_transport tcp`, `fps=detect_fps`, `scale` auf max. 1920 px Breite, `rawvideo bgr24` → Pipe). Hält nur das **neueste** Frame; Watchdog + Reconnect mit Backoff; Zustand `starting/online/offline` |
| `recorder.py` | Ringpuffer: je Kamera ein ffmpeg `-c copy -f segment` (2-s-MPEG-TS-Segmente, Name = Unix-Startzeit) in `VG_BUFFER_DIR` (tmpfs), älter als 30 s wird gelöscht. Clip = Concat-Demuxer `-c copy -movflags +faststart` → mp4 |
| `detector.py` | Ultralytics YOLO11 (`yolo11s.pt`), PyTorch CUDA fp16, ein Batch über alle Kameras. COCO → Labels (person, bicycle, car, motorcycle, bus, truck, bird, cat, dog, horse, sheep, cow, …) |
| `pipeline.py` | zentrale Inferenzschleife (sammelt neueste Frames aller Kameras, Round-Robin, max. 16 pro Batch, verwirft statt zu queuen), Nachverarbeitung je Kamera in eigenem Thread, Ablauf von Tracks, Kommandos `snapshot`/`clip` |
| `tracking.py` | `supervision.ByteTrack` je Kamera, Lebenszyklus laut Contract, Zonen (Fußpunkt = bbox unten-mitte, normiert), bester Crop (score·Fläche) → `crops/YYYY-MM-DD/<uuid>.jpg` |
| `identify.py` | lazy geladene Modelle in eigenen Threads mit begrenzten Queues (Jobs werden verworfen, wenn voll); Fehler eines Modells stoppen die Detektion nicht |
| `inventory.py` | Inventar (Gegenstände) auf ausgewählten Kameras: Keyframe in voller Auflösung, YOLOE mit Text-Prompts, Kachelung, Stabilisierung, `vg/internal/inventory/{id}` (siehe unten) |
| `frame_metrics.py` | Bildkennzahlen auf der CPU (320 px Graustufen): Helligkeit, Kontrast, normierte Schärfe, flache Blöcke, Sättigung; Vergleich mit Referenz per Phasenkorrelation (C5, später A3) |
| `health.py` | Kamera-Gesundheit (C5, eigener Thread, nur CPU): `dark`/`covered`/`blur`/`frozen`/`moved`, Referenzbilder, `vg/internal/health/{id}`, Kommando `health_reference` (siehe unten) |
| `util.py` | Hilfsfunktionen; **einzige Schreibstelle für Medien**: `store_media(rel_path, bytes)` / `write_jpeg` und `media_target(rel_path, suffix)` für ffmpeg-Ausgaben (Clips), jeweils atomar. Andere Module schreiben nie direkt nach `VG_MEDIA_DIR` (Vorbereitung Medien-Upload, D3) |
| `pose.py` / `posture.py` / `gestures.py` | Pose-Stufe (C1/D1): YOLO11n-pose auf Kameras mit `pose_enabled`/`gesture_enabled`, Haltung `standing/sitting/lying`, Armgesten → `vg/internal/gesture/{id}` (siehe unten) |
| `selftest.py` | Standalone-Test ohne Backend/MQTT (`--inventory` für die Gegenstands-Pipeline, `--pose`/`--gestures` für die Pose-Stufe) |
| `posecheck.py` | Messwerkzeug C1/D1: Videodatei offline durch Detektor + Tracker + Pose (Stürze gegen Standbild-Abschnitte, Gesten) |

### Identifikation

* **Gesicht** (`face_recognition`): InsightFace `buffalo_l` (SCRFD-10G + ArcFace R100, 512-d) via onnxruntime-gpu 1.20.2 (CUDA 12 / cuDNN 9).
  Läuft auf dem oberen Teil (45 %) der Personen-Box, alle 0,4 s pro Track. Qualität = det_score · Größe · Frontalität.
  Erster brauchbarer Treffer (≥ 36 px) → `POST /api/internal/identify` (kind=face, normiertes Embedding, Gesichts-Crop unter `crops/`),
  danach nur noch bei deutlich besserer Qualität (×1,3), max. 3 Aufrufe pro Track.
* **Kennzeichen** (`plate_recognition`, car/truck/bus/motorcycle): `fast-alpr` (open-image-models YOLOv9-t-384 Plate-Detektor + fast-plate-ocr `cct-xs-v2-global-model`, ONNX/CUDA).
  identify erst, wenn derselbe Text ≥ 2× gelesen wurde und die häufigste Lesung ist; `attributes.plate_text` wird gesetzt.
* **Tier** (`pet_recognition`, dog/cat/horse/bird): DINOv2 ViT-S/14 (timm `vit_small_patch14_dinov2.lvd142m`, 224 px, fp16), L2-normiertes 384-d Embedding des besten Crops, einmal pro Track (nach 1,5 s).

Die Identität aus der Antwort (`{id, display_name, name, kind}`) fließt in alle folgenden `detections`/`track`-Nachrichten (`update` sofort).

## Inventar (Gegenstände)

Langsame zweite Pipeline nur für Kameras mit `inventory_enabled` (Schnittstelle: CONTRACT.md, Nachtrag G).
Inventar-Felder lösen **keinen** ffmpeg-Neustart aus (nicht in `_STREAM_KEYS`), sie gehen nur an die Engine.

* **Bildquelle** (`KeyframeGrabber`): erstes Bild des letzten *fertigen* Ringpuffer-Segments des Hauptstreams
  (`ffmpeg -frames:v 1`, volle Auflösung, keine weitere RTSP-Verbindung, Bild 2–4 s alt). Ist der Ringpuffer aus oder
  hängt, Fallback auf go2rtc `/api/frame.jpeg?src=cam_N`, danach ein einmaliges `ffmpeg -skip_frame nokey` auf `main_rtsp`.
  Dasselbe Segment wird nie zweimal analysiert.
* **Modell** (`InventoryModel`): lazy erst, wenn eine Kamera Inventar aktiviert; nach 10 min ohne Inventar-Kamera
  wieder entladen. Standard `yoloe-11m-seg.pt` mit Text-Prompts: das Vokabular aller Inventar-Kameras (Prompts aus
  `inventory_vocab`) wird per `set_classes` gesetzt; die Text-Embeddings werden unter `/models/yoloe/pe_*.pt` gecacht,
  der Textencoder (MobileCLIP `mobileclip_blt.ts`, liegt in `/models/yoloe`) wird nur beim ersten Mal bzw. bei
  geändertem Vokabular gebraucht und läuft dann immer auf der **CPU** (kommt nie in den VRAM). Downloads gehen per
  absolutem Pfad nach `/models/yoloe` (kein `chdir`). Nach dem Laden bzw. einem Vokabularwechsel wärmt eine
  Dummy-Inferenz das Modell ohne GPU-Lock auf; das Vollbild wird vor dem Lock auf `imgsz` verkleinert. Ein Modell mit festen Klassennamen (z. B. `yolo11s.pt` oder YOLOE `-pf`) geht auch:
  Vokabular-Einträge werden dann über `label`/`prompts` den Klassennamen zugeordnet (COCO: book, remote, cup …).
  Personen und Fahrzeuge werden nie als Gegenstand gemeldet.
* **Kachelung** (`inventory_tiling`, Standard **aus** – in der Machbarkeitsprüfung kein Gewinn bei 2–3× GPU-Zeit):
  Gesamtbild mit `VG_INVENTORY_IMGSZ` plus Kacheln (`VG_INVENTORY_TILES`, Standard
  2×2, 15 % Überlappung) mit `VG_INVENTORY_TILE_IMGSZ`. Treffer, die eine *innere* Kachelkante berühren, werden
  verworfen (das Gesamtbild/die Nachbarkachel hat sie ganz); danach NMS je Label, Entfernen enthaltener Duplikate und labelübergreifend: fast dieselbe Box (IoU ≥ 0,7) mit
  mehreren Labels (Handy als tablet/book/plate) → nur das Label mit dem höchsten Score.
* **GPU-Wechsel:** jeder `predict` (Gesamtbild, jede Kachel) läuft erst nach dem Ende eines Sicherheits-Batches und
  unter demselben Lock wie `detect` – beide wechseln sich ab. **Rechenbudget:** effektives Intervall je Kamera
  = max(`inventory_interval_s`, GPU-Zeit × Anzahl Inventar-Kameras / `VG_INVENTORY_MAX_BUSY`). Je Kamera höchstens ein
  Durchlauf; verpasste Takte verfallen (`perf.inventory.skipped`), nichts wird angestaut. Fehler (Modell, Bildquelle)
  bleiben im Inventar-Thread; `models.inventory` zeigt `off|loading|ready|error: …`.
* **Stabilisierung** (`InventoryState`): Zuordnung über Label + IoU ≥ 0,3 bzw. Mittelpunktabstand < 0,05, bbox/Score
  geglättet. `present` ab `inventory_confirm_n` Treffern in 2·n Durchläufen, `missing` nach `inventory_miss_m`
  Fehlschlägen in Folge (aus `GET /api/internal/settings`). Ein Fehlschlag zählt nicht, wenn eine Person (rohe
  YOLO-Detektionen ±3 s um die Aufnahmezeit) > 30 % des Gegenstands überdeckt (`occluded`). Detektionen, die zu ≥ 60 %
  in einer Personenbox liegen (getragene Dinge), werden ignoriert. Nie bestätigte Kandidaten verfallen still,
  `missing` wird noch 10 Durchläufe mitgeführt. `moved` bei > 0,1 Verschiebung.
* **Medien:** Crop je Gegenstand beim Erscheinen/Verschieben, sonst bei besserem Score höchstens 1×/min →
  `inventory/YYYY-MM-DD/<item_id>_<ts>.jpg`; bei Änderungen ein Übersichtsbild (max. 1920 px breit) →
  `inventory/YYYY-MM-DD/cam<id>_<ts>.jpg`.
* **MQTT:** nach jedem Durchlauf voller Zustand auf `vg/internal/inventory/{id}` (QoS 1, retain). Beim Abschalten der
  Kamera/des Inventars und beim Beenden des Workers einmal `stopped: true` mit `items: []`. `session` ist je
  Worker-Start neu.

## Kamera-Gesundheit (C5)

* Alle `VG_HEALTH_INTERVAL_S` (10 s) je Kamera mit `health_enabled` und Zustand `online` ein Prüfbild aus dem Ingest
  (kein zusätzlicher Stream), ca. 3–6 ms CPU je Prüfung. Schwellen aus der Messung W0 (`docs/plan/roadmap/messungen.md`).
* Befunde: `dark` (Mittelwert < 25 und 95-Perzentil < 60) vor `covered` (flache Blöcke +35 %-Punkte oder Kontrast
  < 25 % der Referenz) vor `blur` (Schärfe < 0,45 × Referenz) vor `moved` (Verschiebung > 2 % oder Korrelation
  < 0,5 × gleitende Basis); zusätzlich `frozen` (Differenz zum vorigen Prüfbild < 0,05).
* Gemeldet wird ein Befund nach zwei Prüfungen in Folge (`since` = erste), er endet nach zwei unauffälligen.
  Warnung/Entwarnung (60 s) entscheidet das Backend.
* Referenzen `health/cam<N>_<day|night>.jpg` (640 px; Nacht = Graustufen/IR). Automatisch beim ersten brauchbaren Bild
  des Modus, manuell über `vg/internal/cmd/{id}` `{action:"health_reference", mode:"day|night|auto"}`. Beim Start
  werden vorhandene Referenzen geladen. Tag/Nacht wird nur aus brauchbaren Bildern bestimmt (ein abgedecktes Bild ist
  farblos und gälte sonst als Nachtbild).
* `vg/internal/health/{id}` (QoS 1, retain) alle `VG_HEALTH_PUBLISH_S` (30 s) und bei Änderung; bei abgeschalteter
  Prüfung einmal `enabled: false`. `vg/internal/system.perf.health = {ms, cameras}`.

## Pose, Haltung und Gesten (C1/D1)

* Nur auf Kameras mit `pose_enabled` oder `gesture_enabled` und nur, solange ein bestätigter Personen-Track läuft:
  **Ganzbild-Pose** `yolo11n-pose` (640 px, fp16; messungen.md §2: genauer und billiger als Ausschnitte) mit 2 Hz
  (Haltung) bzw. 5 Hz (Gesten). Eigener Thread, neuestes Bild je Kamera (ältere verworfen), mehrere Kameras je
  Batch, GPU abwechselnd mit der Detektion (`GpuTurn`), Budget `VG_POSE_MAX_BUSY` (20 %). Modell lädt bei Bedarf
  (einmaliger Download nach `/models/pose`), entlädt nach 10 min ohne Pose-Kamera; Ladefehler → `models.pose =
  "error: …"`, Detektion läuft weiter. ≈ 0,1 GB VRAM, 9–13 ms je Bild.
* **Haltung** je Bild: `lying`, wenn die Box breiter als 1,3 × hoch ist oder Rumpf/Körper > 55° geneigt ist;
  `upright` (Box deutlich höher als breit bzw. Rumpf senkrecht) beendet „liegt“; sonst `standing`/`sitting` über
  Oberschenkelwinkel. Geglättet über 2 s → `attributes.posture`, `posture_since`, `motion` (Track-`update` bei
  Wechsel).
* **Nachlauf** (`fall_track_grace_s`, Settings): Der Detektor verliert liegende Personen oft. Ein liegender Track
  endet erst nach dieser Zeit; eine flache Detektion oder Pose-Person an derselben Stelle wird wieder angehängt.
  Steht an der Stelle eine Person aufrecht, endet der Nachlauf sofort.
* **Gesten** (`arms_up`, `arm_left`, `arm_right`, `arms_cross` = X über dem Kopf) regelbasiert auf den Keypoints;
  gelten nach `gesture_hold_s` in ≥ 80 % der Pose-Bilder, danach 5 s Sperre je Track.

## Virtuelle Sensoren (A3/C4)

* Abnehmer `sensors` am Frame-Tap (eigener Thread, je Kamera nur das neueste Bild). Sensoren kommen aus
  `GET /api/internal/cameras` (`sensors`, Arten `brightness|blink|reference|fill`, dazu `vlm` nur fürs Änderungsmaß).
* Messung im Polygon auf dem Detect-Frame: `brightness` = mittlere Luma 0..1; `blink` = Std.-Abw. der Helligkeit über
  `window_s` (Abtastung ≈ 5/s, solange ein Blink-Sensor aktiv ist); `reference`/`fill` = DINOv2 (geteiltes Tiermodell,
  wird bei Bedarf geladen) gegen Referenzbilder → P(an) bzw. Füllstand 0..1 (Softmax-gewichtete Stufen 1/0,5/0;
  beim Füllstand je zur Hälfte mit der Farbähnlichkeit einer 16×16-Miniatur gemischt).
  Referenzbilder (`sensors/ref/…`, ganzes Kamerabild) werden auf die Detect-Auflösung skaliert, der Bereich wird
  ausgeschnitten, Embeddings je Referenz + Bereich gecacht.
* `occluded`: Personen-Boxen bedecken > 10 % des Bereichs → die Messung zählt nicht. `change`: Abweichung eines
  32×32-Graubilds zum Ankerbild; ab `change_threshold` einmal melden, dann neuer Anker.
* `vg/internal/sensor/{id}` (QoS 0) je Messung, `vg/internal/system.perf.sensors = {per_min, ms}`. CPU ≈ 1 ms je
  Messung, kein zusätzliches VRAM (DINOv2 ist mit der Tiererkennung geteilt; ohne Tiererkennung ≈ 0,1 GB).

## Track-Lebenszyklus

* `start`: nach ≥ 3 Treffern mit `score ≥ min_score` (Label = score-gewichtete Mehrheit).
* `update`: sofort bei Zonenwechsel / Identität / Attribut, bei besserem Crop höchstens 1×/s.
* `end`: 2 s ohne Treffer (wartet max. 6 s länger, falls noch eine Identifikation läuft). `left_zones` enthält beim Ende die letzten Zonen.
* `track_id`: prozessweit monoton, startet bei einem aus der Uhrzeit abgeleiteten Wert (< 2³¹) → eindeutig über Worker-/Kamera-Neustarts hinweg.

## Konfiguration (Env)

| Variable | Standard | |
|---|---|---|
| `VG_WORKER_ID` | `local` | Kennung des Workers (`[A-Za-z0-9_-]`, ≤ 64 Zeichen): Feld `worker_id` in allen MQTT-Nachrichten und Teil der MQTT-`client_id` (Vorbereitung verteilter Worker, D3) |
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
| `VG_INVENTORY_MODEL` | `yoloe-11m-seg.pt` | Inventar-Modell (`yoloe-11s-seg.pt`, `yoloe-11s-seg-pf.pt` oder COCO `yolo11s.pt`) |
| `VG_INVENTORY_IMGSZ` / `VG_INVENTORY_TILE_IMGSZ` | `1280` / `960` | Auflösung Gesamtbild / je Kachel |
| `VG_INVENTORY_TILES` / `VG_INVENTORY_OVERLAP` | `2x2` / `0.15` | Kacheln (Spalten×Zeilen, `0` = aus) |
| `VG_INVENTORY_MAX_BUSY` | `0.25` | max. GPU-Zeitanteil des Inventars (sonst wird das Intervall gestreckt) |
| `VG_POSE_MODEL` | `yolo11n-pose.pt` | Pose-Modell (C1/D1) |
| `VG_POSE_FPS` / `VG_GESTURE_FPS` | `2` / `5` | Pose-Bilder je Sekunde und Kamera (Haltung / Gesten-Kameras) |
| `VG_POSE_MAX_BUSY` | `0.2` | max. GPU-Zeitanteil der Pose-Stufe (darüber werden Bilder übersprungen) |
| `VG_INVENTORY_HALF` / `VG_INVENTORY_MIN_CONF` | `1` / `0.05` | fp16; Untergrenze für `predict(conf)` |
| `VG_INVENTORY_SOURCE` | `auto` | `auto` (Ringpuffer → go2rtc → RTSP), `buffer`, `go2rtc`, `rtsp` |
| `VG_INVENTORY_CROP_MIN_S` / `VG_INVENTORY_UNLOAD_S` / `VG_INVENTORY_PERSON_HOLD_S` | `60` / `600` / `3` | Crop-Abstand, Entladen ohne Inventar-Kamera, Zeitfenster für Verdeckung |
| `VG_HEALTH` / `VG_HEALTH_INTERVAL_S` / `VG_HEALTH_PUBLISH_S` | `1` / `10` / `30` | Kamera-Gesundheit: globaler Schalter, Prüfabstand je Kamera, spätestes Senden |
| `VG_GO2RTC_URL` | `http://go2rtc:1984` | nur für den Keyframe-Fallback |
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
# Inventar (Gegenstände) auf der Wohnzimmer-Kamera (2880×1616, Tischchen mit Lampe/Buch/Fernbedienung):
docker compose run --rm --no-deps worker python -m vg_worker.selftest rtsp://go2rtc:8554/cam_3 \
    --no-face --no-plate --no-pet --inventory --inventory-interval 3 --seconds 60
# Kamera-Gesundheit mitprüfen (sonst im Selbsttest aus; Referenzbilder nur in einem temporären Ordner):
docker compose run --rm --no-deps worker python -m vg_worker.selftest rtsp://testcam:8554/cam5 --health --seconds 40
# Pose-Stufe (Haltung/Gesten) auf allen Testkameras, z. B. Sturz-Video:
flock ~/vg-gpu.lock docker compose run --rm --no-deps worker python -m vg_worker.selftest rtsp://testcam:8554/cam3 --pose --seconds 120
# Offline-Messung: Stürze (Standbild-Abschnitte von sturz.mp4) bzw. Gesten
flock ~/vg-gpu.lock docker compose run --rm --no-deps -v "$PWD/testcam/videos:/videos:ro" worker \
    python -m vg_worker.posecheck /videos/sturz.mp4 --dwell 5
# Hänger untersuchen: Stacks aller Threads ins Log
docker compose kill --signal=USR1 worker
# Im Betrieb:
docker compose exec mosquitto mosquitto_sub -t 'vg/internal/#' -C 20 -v
docker compose exec mosquitto mosquitto_sub -t 'vg/internal/health/#' -C 2 -v
docker compose exec mosquitto mosquitto_sub -t 'vg/internal/inventory/#' -C 2 -v
```

## Gemessene Leistung (RTX 4080 Laptop, 768×432 h264 @ 12 fps, detect_fps 5)

| Szenario | Ergebnis |
|---|---|
| 2 Kameras, alle Modelle | 2 × 5,0 fps, GPU-Speicher ≈ 1,0–1,5 GB, Worker-RAM ≈ 2 GB |
| 16 Kameras, alle Modelle | **80 fps** gesamt (16 × 5,0 fps), Ø Batch 5,6 Frames in ≈ 22–26 ms (≈ 4,5 ms/Frame), GPU-Util ≈ 20–35 %, GPU-Speicher 1,9 GB, Host-RAM gesamt ≈ 4,3 GB |
| YOLO11s fp16 isoliert | bs1 ≈ 8 ms, bs16 ≈ 31 ms (≈ 2 ms/Frame) |
| Inventar, COCO-Fallback `yolo11s.pt` auf **CPU**, 2880×1616 + 2×2 Kacheln | ≈ 850–930 ms je Durchlauf (Funktionstest; GPU/YOLOE noch zu messen) |

`inference_ms` im Status ist die Latenz des Batches, in dem das Kamerabild lief (geglättet). Bei CPU-Last durch
ffmpeg-Decoder steigt die Kernel-Launch-Latenz von PyTorch (bs1 8 ms → ≈ 30–40 ms); der Durchsatz skaliert über das Batching trotzdem.
TensorRT wurde getestet (Export ≈ 4 min, +4,6 GB Aktivierungs-Speicher bei dynamischem Batch 16, keine Vorteile bei kleinen Batches) und ist deshalb **nicht** aktiv.

## Hinweise

* Basis-Image `pytorch/pytorch:2.5.1-cuda12.4-cudnn9-runtime` (Host-Treiber unterstützt max. CUDA 12.4). onnxruntime-gpu nutzt cuDNN/cuBLAS aus den Torch-Wheels (`LD_LIBRARY_PATH`).
* ffmpeg 4.4 (Ubuntu 22.04): RTSP-Timeout heißt dort `-stimeout` (wird automatisch gewählt).
* Erster Start lädt ca. 400 MB Modelle nach `/models` (yolo11s 19 MB, buffalo_l 280 MB, DINOv2-S 88 MB, ALPR ≈ 10 MB).
* Inventar lädt beim ersten Aktivieren YOLOE (`/models/yoloe`, ≈ 30 MB für `-s`) und einmalig den Textencoder
  MobileCLIP (`mobileclip_blt.ts`, ≈ 600 MB). YOLOE steht wie YOLO11 unter AGPL-3.0, MobileCLIP unter Apple-Lizenz.
* Retained Kommandos auf `vg/internal/cmd/+` werden ignoriert. Kein Last-Will (Backend erkennt Worker-Ausfall über das Ausbleiben von `vg/internal/system`, 15 s).
