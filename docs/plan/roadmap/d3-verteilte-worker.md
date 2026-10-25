# D3 – Weitere Rechner für die KI (verteilte Worker)

Stand: Entwurf 2026-10-24. Zielzustand der Phasen 1–6 laut `CONTRACT.md` (Nachträge A–G) und
`docs/plan/00-gesamtplan.md` vorausgesetzt. Aufwand gesamt: **L** (ca. 6–8 Tage). Erst umsetzen, wenn ein zweiter
Rechner oder mehr Kameras kommen – die **Vorbereitungen in Abschnitt 1** aber jetzt.

## 1. Kleine Vorbereitungen JETZT (damit Phasen 1–6 nichts verbauen)

Alle abwärtskompatibel, je **S**; kein Verhaltenswechsel bei einem einzelnen Worker.

1. **Worker-ID:** Env `VG_WORKER_ID` (Default `"local"`) in `worker/vg_worker/settings.py`. Neues Pflichtfeld
   `worker_id` in **jeder** Worker-Nachricht (`detections`, `track`, `status`, `system`, `cmd_result`,
   `inventory`). Backend ignoriert es vorerst. MQTT-`client_id` = `vg-worker-<worker_id>-<ts>`.
2. **Medienpfade nur relativ und nur über eine Funktion schreiben:** Phase 4 (Inventar-Crops) nutzt
   ausschließlich `util.write_jpeg(rel_path, …)` bzw. eine neue `util.store_media(rel_path, data)`, nie direkte
   `open(MEDIA_DIR/…)`. Dann ist der spätere Upload-Modus eine Änderung an genau einer Stelle (+ Recorder für Clips).
3. **Keine Annahme „Datei existiert sofort“** im Backend: Phase 5 darf `crop_path` speichern, auch wenn die Datei
   noch fehlt (bei `identify` liest das Backend die Datei heute – dort Fallback einplanen, s. 3.4).
4. **`session` in `inventory` (Phase 4) beibehalten** – sie macht Wechsel einer Kamera zwischen Workern bereits
   heute korrekt (neue Session = Abgleich über Label/IoU).
5. **`/api/internal/*` nicht weiter an „nur im Docker-Netz“ koppeln:** neue interne Endpunkte (Phase 4/5) über eine
   gemeinsame Dependency `require_internal` einhängen (heute: no-op). Später prüft sie den Worker-Key.
6. **Retain-Topics je Kamera** (`status`, `inventory`) bleiben kameraweise – gut so; `system` wird später je Worker
   (s. 3.1), daher im Backend `state.set_worker_system(msg)` schon jetzt nach `msg.get("worker_id","local")`
   schlüsseln.

## 2. Zielverhalten

- Beliebig viele Worker (dieser PC, zweiter PC, Jetson) melden sich mit eigenem **Worker-Key** an.
- Das Backend weist jede aktive Kamera genau **einem** Worker zu – nach freier Kapazität, optional fest gepinnt.
- Fällt ein Worker aus (≥ 15 s kein `system`), verschiebt das Backend seine Kameras auf andere Worker
  (Failover ≤ 30 s). Kommt er zurück, wird **nicht** automatisch zurückverschoben (kein Pendeln), außer bei Pin.
- Entfernte Worker brauchen weder das Volume `media` noch Zugang zur Datenbank: Medien gehen per HTTPS-Upload an das
  Backend, Video holen sie bei go2rtc über das Netz.
- Keine Daueraufzeichnung: Ringpuffer bleibt tmpfs auf dem jeweiligen Knoten; Clips werden erst bei Auslösung
  hochgeladen.

## 3. Architektur

### 3.1 MQTT (Backend `mqtt_bus.py`, Worker `service.py`)
- `vg/internal/system/{worker_id}` (retain, alle 5 s) **zusätzlich** zu `vg/internal/system` (Übergang; `local`
  sendet beides). Payload erweitert um `capacity`.
- Neu **Backend → `vg/internal/assign/{worker_id}`** (retain, QoS 1): `{camera_ids:[…], rev:int, ts}`.
  Worker startet/stoppt Pipelines nur noch für diese Kameras; ohne Zuweisung (Altbetrieb, `VG_WORKER_ID=local`
  und Setting `distributed=false`) wie heute alle.
- `vg/internal/config` bleibt Broadcast; jeder Worker filtert `GET /api/internal/cameras?worker_id=` auf seine.
- LWT je Worker: `vg/internal/system/{worker_id}` → `{online:false}` (retain) – beschleunigt Failover.
- Entfernte Worker verbinden sich mit Mosquitto über TLS (8883) mit Benutzer = `worker_id`, Passwort = Worker-Key
  (Mosquitto `password_file` + ACL: nur `vg/internal/+/…` schreiben, nur `assign/<id>`, `config`, `cmd/+` lesen).
  Der anonyme Listener 1883 bleibt nur im Docker-Netz.

### 3.2 Zuweisung und Failover (Backend, neu `backend/app/scheduler.py`)
- Kapazitätsmodell je Worker aus `system.capacity`: `{vram_free_mb, gpu_util, max_cameras, fps_budget,
  features:["detect","face","plate","pet","inventory"]}`; Kosten je Kamera ≈ `detect_fps × (1 + 0.5·inventory)`.
- Greedy: Kamera mit Pin → gepinnter Worker, falls online; sonst Worker mit größter Restkapazität, der alle
  benötigten `features` hat (Jetson evtl. ohne `inventory`).
- Neuberechnung bei: Kamera an/aus/neu, Worker online/offline, alle 60 s (nur Hinzufügen, nie Umverteilen
  laufender Kameras außer bei Überlast > 110 %).
- Beim Umzug: aktive Tracks der Kamera werden wie beim Backend-Start beendet; Ringpuffer-Inhalt geht verloren
  (Clip mit `pre_s` direkt nach Umzug ist kürzer – dokumentieren).

### 3.3 Registrierung / Auth (Backend, neu `backend/app/routers/workers.py`)
- Worker-Keys analog API-Keys (Nachtrag B), aber eigene Tabelle und Präfix **`vgw_`**, Scope implizit `internal`;
  niemals für `/api/*` außerhalb `internal` gültig, API-Keys umgekehrt nie für `internal`.
- Ablauf: Admin legt im UI „Worker“ an → Key einmalig angezeigt (+ fertiger `docker run`-Befehl ohne Key im Log).
- nginx: `/api/internal/` bleibt 403 **außer** mit Header `Authorization: Bearer vgw_…` (eigene `location` mit
  `if ($http_authorization ~ "^Bearer vgw_")` → Proxy; Prüfung macht das Backend). Lokaler Worker darf ohne Key
  (Docker-Netz, Env `VG_INTERNAL_TRUST_LOCAL=1`).

### 3.4 Medien-Upload (Worker `util.py`, `recorder.py`; Backend `routers/internal.py`)
- Env `VG_MEDIA_MODE=volume|upload` (Default `volume`).
- `PUT /api/internal/media/{rel_path}` (Body = Rohdaten, `Content-Type` image/jpeg | video/mp4, max. 200 MB),
  Pfad nur unter `snapshots|crops|clips|inventory/YYYY-MM-DD/`, Traversal → 400, atomar schreiben → 204.
- Worker lädt **vor** `identify`/`track`-Nachricht hoch (Crops klein); Clips asynchron, danach erst `cmd_result`.
  Upload-Warteschlange im RAM max. 50 MB, bei Überlauf verwerfen + `cmd_result ok:false`.
- `identify` bleibt embedding-basiert; das Backend kopiert das Sample aus `crop_path` – fehlt die Datei (Upload
  noch unterwegs), 3 × 500 ms nachsehen, sonst Sample ohne Bild verwerfen (Identität trotzdem zuordnen).

### 3.5 go2rtc über das Netz
- Neues Setting `go2rtc_rtsp_public` (z. B. `rtsp://vg-host:8554`) und optional RTSP-Auth in go2rtc
  (`rtsp: username/password`, Wert nur in `.env`). `/api/internal/cameras?worker_id=` liefert je nach Worker die
  interne oder öffentliche URL, Zugangsdaten **nur** an authentifizierte Worker. Port 8554 nur im LAN/VPN freigeben.
- Bandbreite: Detect-Stream (≤ 720p) ≈ 2–4 Mbit/s je Kamera (vermutet).

### 3.6 Jetson-Image (`worker/Dockerfile.jetson`)
- Basis: `nvcr.io/nvidia/l4t-jetpack:r36.4.0` (JetPack 6.1, CUDA 12.6, cuDNN 9) – **vermutet, prüfen**. Die Grenze
  „CUDA ≤ 12.4“ gilt nur für den Windows/WSL-Host (Treiber 551.x); auf Jetson bringt L4T CUDA selbst mit.
- PyTorch: NVIDIA-Wheels für JetPack 6 (torch 2.5.0a0, vermutet), kein PyPI-`torch`. ultralytics 8.3.253 (gleiche
  Version wie x86, verifiziert im Projekt), onnxruntime-gpu aus Jetson-Zoo/`pypi.jetson-ai-lab` (1.20.x, vermutet).
- ffmpeg mit `nvv4l2decoder` statt `-hwaccel cuda` → `VG_HWACCEL=none` als sicherer Default.
- Unified Memory: Orin NX 16 GB teilt RAM/VRAM; `capacity.vram_free_mb` aus `tegrastats`/`jtop` statt NVML
  (`pynvml` liefert auf Jetson nichts) – Modul `worker/vg_worker/gpuinfo.py` mit Fallback.

## 4. Modelle / VRAM

Keine neuen Modelle. Je Knoten wie heute: ca. **2,7 GB** (verifiziert, RTX A1000 im Betrieb) + YOLOE aus Phase 3/4
(Budget ≤ 4,5 GB Spitze laut Gesamtplan). Ein Knoten ohne Inventar/Gesichter (Feature-Flags) braucht ca. 1–1,5 GB
(vermutet: YOLO11s FP16 + CUDA-Kontext). Backend/Mosquitto: kein VRAM.

## 5. CONTRACT.md-Ergänzungen (additiv)

- Alle Worker-Nachrichten: `worker_id: str`. `system`: `capacity:{vram_free_mb, vram_total_mb, gpu_util,
  max_cameras, fps_budget, features:[…]}`, `cameras:[id]`, `version`, `arch:"x86_64"|"aarch64"`.
- Topics: `vg/internal/system/{worker_id}`, `vg/internal/assign/{worker_id}`.
- DB: Tabelle `workers` (`id` varchar(64) PK, `name`, `prefix`, `token_hash`, `created_at`, `last_seen`,
  `revoked_at`, `max_cameras` int NULL, `features` jsonb DEFAULT '[]'); `cameras.worker_pin` varchar(64) NULL;
  `cameras.assigned_worker` varchar(64) NULL (read-only, vom Scheduler). Beides über `ADDITIVE_COLUMNS`.
- Settings: `distributed: false`, `go2rtc_rtsp_public: null`.
- REST (Cookie-only wie `/tokens`): `GET/POST /api/workers`, `PATCH/DELETE /api/workers/{id}`
  → `Worker {id, name, online, last_seen, cameras:[id], capacity, features, max_cameras, revoked}`;
  `POST /api/workers` → + `token` (einmalig). Camera: `worker_pin`, `assigned_worker`.
- Intern: `GET /api/internal/cameras?worker_id=`, `PUT /api/internal/media/{path}`.
- `GET /system.worker` bleibt (erster/lokaler Worker), neu `workers:[Worker]`. WS: `{"type":"worker","worker":Worker}`.
- MCP: Tool `system_status` (falls vorhanden) um `workers` erweitern; kein neues Tool.

## 6. UI-Skizze

Admin → neue Seite **„Rechenknoten“** (`frontend/src/pages/admin/Workers.tsx`): Tabelle Name · Status-Punkt ·
GPU/Auslastung · Kameras (Chips) · Fähigkeiten · „Key widerrufen“. Button „Rechenknoten hinzufügen“ → Dialog mit
Key (einmalig) und Kopierbefehl. Kamera-Formular: Auswahl „Rechenknoten: automatisch | <Name>“, Anzeige
„läuft auf: …“. System-Seite: eine Karte je Worker.

## 7. Schritte

| # | Schritt | Aufwand |
|---|---|---|
| 0 | Vorbereitungen Abschnitt 1 | S |
| 1 | `workers`-Tabelle, Keys, REST, `require_internal` | M |
| 2 | Worker: `assign` abonnieren, Pipelines je Zuweisung, `capacity` melden | M |
| 3 | `scheduler.py` inkl. Failover und Pin | M |
| 4 | Medien-Upload (Endpunkt + `VG_MEDIA_MODE=upload`, Clip-Queue) | M |
| 5 | Mosquitto TLS/ACL, nginx-Location, go2rtc-RTSP-Auth, `compose.remote-worker.yml` | M |
| 6 | Frontend „Rechenknoten“, Kamera-Pin, Mock/Typen | M |
| 7 | `Dockerfile.jetson` + Machbarkeitstest | L |

## 8. Abnahmekriterien (e2e)

1. Ein Worker, `distributed=false`: Verhalten und Selbsttest unverändert; alle Nachrichten tragen `worker_id`.
2. Zweiter Worker (zweiter Container auf demselben Host mit `VG_MEDIA_MODE=upload`, **ohne** Volume `media`,
   anderes `VG_WORKER_ID`, `cam1` gepinnt): Events, Snapshots, Clips von `cam1` erscheinen unter `/api/events`, Dateien
   abrufbar.
3. `docker compose stop worker2` → innerhalb 30 s übernimmt Worker 1 `cam1`, `assigned_worker` wechselt, UI zeigt
   „offline“.
4. Worker mit widerrufenem Key: MQTT-Connect und `/api/internal/*` abgelehnt (401), Log ohne Key-Klartext.
5. `/api/internal/cameras` liefert an den entfernten Worker keine Kamera-Zugangsdaten, nur go2rtc-URLs.
6. Mit API-Key (`vgt_`) auf `/api/internal/*` → 403; mit `vgw_` auf `/api/events` → 401.

## 9. Machbarkeitstest Jetson

`selftest` aus `Dockerfile.jetson` auf Orin (Nano 8 GB / NX 16 GB): 4 Kameras à 5 fps, Inferenz < 40 ms, YOLOE
optional. Go/No-Go vor Schritt 7-Rest. Ohne Gerät: nur Build per `docker buildx --platform linux/arm64` (lädt
großes Basisimage – eigene Schicht, einmalig).

## 10. Risiken

- Zeitachsen: `detections.ts` ist Worker-Wanduhr → entfernte Knoten brauchen NTP (Abweichung > 200 ms verschiebt
  das Overlay).
- Doppelte Zuweisung bei Netzsplit → `rev` in `assign`; Worker verwirft ältere `rev`, Backend ist einzige Quelle.
- Upload-Ausfall verliert Clips (bewusst: keine lokale Daueraufzeichnung).
- Mosquitto-TLS und go2rtc nach außen erweitern die Angriffsfläche – nur LAN/VPN (z. B. WireGuard) empfehlen.
- Jetson-Stack (Wheels, onnxruntime, InsightFace) ist der unsicherste Teil.

## 11. Abhängigkeiten

- **Phase 2** (API-Keys, `media_sign`, Medien-Router): Worker-Keys bauen auf demselben Hash-/Cache-Muster auf;
  Upload-Endpunkt neben `routers/media.py`.
- **Phase 0** (`ADDITIVE_COLUMNS`) für `cameras.worker_pin/assigned_worker`.
- **Phase 4/5** (Inventar): `session` und `worker_id` in `inventory`; Feature-Flag `inventory` im Scheduler.
- **Phase 6** (MCP): nur Statuserweiterung.
- Roadmap: profitiert von Themen mit höherer Kamerazahl/Last; Medien-Upload ist Voraussetzung für jeden Knoten
  außerhalb dieses Hosts.
