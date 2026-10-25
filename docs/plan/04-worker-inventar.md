# Plan: Inventar-Erkennung im Worker (langsame Pipeline auf ausgewählten Kameras)

## Wo der Code heute steht
- `pipeline.py:_infer_loop` (Z. ~150) bündelt die neuesten Frames aller Kameras in einem YOLO-Batch und überspringt Kameras mit `busy`. Wer hier zusätzlich einen 2K-Batch mit Kacheln einschiebt, verzögert alle Kameras.
- `detector.py`: `LABEL_MAP` lässt nur Sicherheitsklassen durch. `classes=self.class_ids` ist fest eingestellt.
- `ingest.py:FrameSource` skaliert auf höchstens 1920 px (`MAX_FRAME_WIDTH`) und dekodiert dauerhaft mit `detect_fps`.
- `recorder.py:RingRecorder` schneidet schon jetzt den **Hauptstream** (`main_rtsp`) per Stream-Copy in 2-s-TS-Segmente in tmpfs. Jedes Segment beginnt mit einem Keyframe.
- `identify.py:ModelWorker` liefert das Muster für die neue Pipeline: Modell lazy laden, eigener Thread, begrenzte Queue, Fehler isoliert.
- `backend/app/db.py:42` macht nur `create_all`. **Neue Spalten in `cameras` werden dabei nicht angelegt.** Dafür braucht es ein `ALTER TABLE … ADD COLUMN IF NOT EXISTS` beim Start (Aufgabe im Backend-Bereich, hier nur als Abhängigkeit).

## Woher das hochaufgelöste Bild kommt
| Variante | Bewertung |
|---|---|
| **A: Keyframe aus dem Ringpuffer** (`ffmpeg -i <letztes fertiges Segment>.ts -frames:v 1 -f rawvideo`) | **Empfohlen.** Volle Auflösung, keine weitere RTSP-Verbindung, kein JPEG-Verlust, etwa 50–100 ms CPU je Abruf. Das Bild ist 2–4 s alt, für ein Inventar unkritisch. Setzt `VG_RECORDER=1` voraus. |
| B: go2rtc `/api/frame.jpeg?src=cam_N` | Ersatzweg. Der Worker bräuchte zusätzlich `VG_GO2RTC_URL`, es gibt JPEG-Verluste und je Abruf wartet go2rtc auf einen Keyframe. |
| C: eigenes ffmpeg `-skip_frame nokey` auf `main_rtsp` | Ersatzweg, wenn der Ringpuffer abgeschaltet ist. Dekodiert nur I-Frames, braucht aber eine weitere dauerhafte Verbindung. |

Umsetzung: neue Klasse `KeyframeGrabber` in `worker/vg_worker/inventory.py`. Standard ist A, bei `recorder is None` wird C genommen.

## Modell
- **Stufe 1 (MVP):** derselbe YOLO11s, dafür ein `classes`-Parameter in `Detector.detect`. Damit stehen die COCO-Gegenstände zur Verfügung (fork, spoon, knife, cup, chair, couch, dining table, laptop, remote, cell phone, book, backpack …). Kein zusätzlicher VRAM, keine neuen Pakete.
- **Stufe 2:** **YOLOE-11** mit offenem Vokabular. Es ist im gepinnten `ultralytics==8.3.253` schon enthalten.
  - Mit Vokabular: `set_classes(vocab)` einmal beim Laden bzw. bei Änderung des Vokabulars. Das Text-Modell (MobileCLIP) wird einmal nach `/models` geladen.
  - Ohne Vokabular: die prompt-freie Variante `yoloe-11*-seg-pf` mit eingebautem Vokabular von mehreren tausend Klassen („so viel wie möglich“). Masken werden nicht ausgewertet.
- Ausgeschlossen werden person und alle Fahrzeuge, die deckt die Sicherheitspipeline ab.
- Neue Env-Variablen: `VG_INVENTORY_MODEL` (Standard `yolo11s.pt`, später `yoloe-11m-seg.pt`), `VG_INVENTORY_IMGSZ=1280`, `VG_INVENTORY_TILES=2x2`, `VG_INVENTORY_OVERLAP=0.15`, `VG_INVENTORY_MAX_BUSY=0.25`.
- **Kachelung:** ein Durchlauf über das ganze Bild mit 1280 px plus 2×2 Kacheln mit 640 px, danach klassenweise NMS und Kantenkorrektur. Bei 2K ergibt das 5 Bilder je Durchlauf. Kleine Dinge wie Schlüssel oder Gabel brauchen die Kacheln.

## Die Sicherheitspipeline nicht ausbremsen
- Eigener Thread `InventoryEngine` nach dem Muster von `ModelWorker`. Je Kamera gibt es höchstens **einen** offenen Auftrag. Ist er noch nicht erledigt, wird der Takt übersprungen, es wird nichts angestaut.
- **GPU-Takt:** Jede Kachel ist ein eigener `predict`-Aufruf. Vor jedem Aufruf wartet der Thread auf ein `Event`, das `_infer_loop` nach jedem Sicherheits-Batch setzt (Z. ~170). So laufen beide abwechselnd statt gleichzeitig.
- **Rechenbudget:** Die Inventar-Rechenzeit darf je Sekunde höchstens `MAX_BUSY` (25 %) betragen, sonst wird das Intervall automatisch gestreckt.
- Lazy laden, sobald die erste Kamera `inventory_enabled` hat. Ein Fehler beim Laden setzt `models.inventory = "error: …"`, die Detektion läuft weiter.
- Inventar-Schlüssel kommen **nicht** in `_STREAM_KEYS`. Eine Änderung startet also kein ffmpeg neu, sie geht nur über `CameraRuntime.update_cfg`.

## Stabilisierung (`InventoryState` je Kamera)
- **Zuordnung:** gleiches Label und IoU ≥ 0,3 bzw. Mittelpunktabstand < 0,05 zu einem bekannten Gegenstand. Sonst entsteht ein Kandidat mit neuer `item_id` (UUID). Die bbox wird per EMA geglättet.
- **„Vorhanden“** ab `confirm_n` Treffern innerhalb von höchstens 2·n Durchläufen (Standard 2).
- **„Verschwunden“** erst nach `miss_m` aufeinanderfolgenden Fehlschlägen (Standard 3).
- Ein Fehlschlag zählt nur, wenn das Bild gültig war und **keine Person** aus `tracker.snapshot_objects()` den Gegenstand verdeckt. Dafür reicht ein bbox-Überlapp > 0,3.
- **Bewegt:** Mittelpunkt verschiebt sich um mehr als 0,1.
- **Crop:** wird beim Übergang zu „vorhanden“ geschrieben und bei besserem Score erneuert, höchstens 1× pro Minute, nach `crops/YYYY-MM-DD/`.
- Bei Änderungen kommt zusätzlich ein Übersichtsbild nach `snapshots/` mit eingezeichneten Gegenständen (`draw_objects`).

## Ergänzungen in CONTRACT.md
**Camera** (REST und `/api/internal/cameras`, nur additiv):
- `inventory_enabled` (bool, Standard false)
- `inventory_interval_s` (float, 3, erlaubt 1–60)
- `inventory_labels` (`[]` = globales Vokabular)
- `inventory_min_score` (0.35)
- `inventory_tiling` (true)

**Settings:** `inventory_vocabulary`, `inventory_confirm_n=2`, `inventory_miss_m=3`. Das Backend löst das Vokabular auf und liefert es je Kamera in `/api/internal/cameras` aus.

**MQTT** `vg/internal/inventory/{camera_id}` (QoS 1, retain, nach jedem Durchlauf):
```json
{"camera_id":1,"ts":0,"frame_ts":0,"session":"<worker-start-uuid>","model":"yoloe-11m","width":2560,"height":1440,
 "snapshot_path":null,
 "items":[{"item_id":"uuid","label":"key","score":0.62,"bbox":[0,0,0,0],"zones":["z1"],
           "state":"candidate|present|missing","first_seen":0,"last_seen":0,"hits":5,"misses":0,"crop_path":"crops/…"}],
 "changes":[{"item_id":"uuid","type":"appeared|moved|disappeared"}]}
```
- Nach einem Neustart des Workers kommt eine neue `session`. Das Backend gleicht dann über Label und IoU ab und meldet nichts als verschwunden, solange die neue Session `miss_m` Durchläufe noch nicht erreicht hat.
- `vg/internal/system.models.inventory` und `perf.inventory {samples_per_min, ms, skipped}`.
- Beim Deaktivieren schickt der Worker einmal `items: []` mit `"stopped": true`.

## Schritte
1. CONTRACT.md ergänzen (Felder, Topic, Settings) – **S**
2. Backend: Spalten und `ADD COLUMN IF NOT EXISTS`, `internal_cameras` erweitern (nur als Abhängigkeit) – **S**
3. `inventory.py` mit `KeyframeGrabber` (A und C), Testlauf mit `selftest --inventory` gegen die 2K-Testkamera – **M**
4. `InventoryEngine`: COCO über den vorhandenen Detector, Kachelung und NMS, Wechsel mit `_infer_loop`, Budget – **M**
5. `InventoryState`: Stabilisierung, Verdeckung, Crops, MQTT-Nachricht – **M**
6. Einbindung in `pipeline.py` (`CameraRuntime`, `apply_config`, `models_status`, `perf`) und in `settings.py` – **S**
7. Lasttest: 16 Kameras mit 5 fps plus Inventar auf einer 2K-Kamera. Erfolg heißt: `perf.fps` bleibt bei 80, `batch_ms` steigt um höchstens 20 % – **S**
8. YOLOE als Modell-Backend: Text-Vokabular, prompt-freie Variante, neu laden bei Vokabular-Änderung, VRAM messen – **M/L**
9. worker/README.md aktualisieren (Tabelle, Env, Messwerte) – **S**

## Risiken und offene Fragen
- **VRAM 6 GB:** YOLO11s plus buffalo_l plus DINOv2 plus ALPR brauchen schon ≈ 1,5–2 GB. YOLOE-m bei 1280 px kostet geschätzt 0,5–1 GB mehr. Das muss auf der A1000 gemessen werden, notfalls YOLOE-s.
- **Qualität bei offenem Vokabular:** Kleine Dinge wie Schlüssel auf dem Teppich sind bei 2K und 2–4 m Abstand grenzwertig. Fehlalarme sind wahrscheinlich, dagegen helfen ein höheres `min_score` je Label und `confirm_n`.
- **Nicht entschieden:** Lizenz von YOLOE (AGPL wie YOLO11, im README ergänzen). Ob das Vokabular deutsch angezeigt wird (Übersetzung im Backend) und englisch an das Modell geht.
- Die Rechenzeit läuft über dieselbe CUDA-Queue. Bei Ruckeln kommen ein eigener `torch.cuda.Stream` mit niedriger Priorität oder ein kleineres `MAX_BUSY` in Frage.
- Variante A hängt am Ringpuffer. Ist der Hauptstream H.265, kostet das Dekodieren auf der CPU etwas mehr.
- Für Fragen wie „Wo ist mein Schlüssel?“ muss das Backend den letzten Stand **dauerhaft** speichern (`last_seen` und Kamera). Der Worker hält nur flüchtigen Zustand.

**Gesamtaufwand im Worker:** M bis L (etwa 3–5 Personentage; Stufe 1 ohne YOLOE etwa 2 Tage).

Grundlage: `/home/miefda/vision-guard/worker/vg_worker/pipeline.py`, `/home/miefda/vision-guard/worker/vg_worker/detector.py`, `/home/miefda/vision-guard/worker/vg_worker/ingest.py`, `/home/miefda/vision-guard/worker/vg_worker/recorder.py`, `/home/miefda/vision-guard/worker/vg_worker/identify.py`, `/home/miefda/vision-guard/backend/app/routers/internal.py`, `/home/miefda/vision-guard/backend/app/db.py`.