# VisionGuard – Schnittstellenvertrag

Verbindliche Schnittstellen zwischen den Diensten. Änderungen hier zuerst abstimmen.

## Dienste & Netz (docker-compose.yml)

| Dienst | intern | Aufgabe |
|---|---|---|
| `db` | postgres:5432 (db `visionguard`, user/pw `vg`/`vg`) | Persistenz (nur Backend greift zu) |
| `mosquitto` | mosquitto:1883 (anonym) | interner Bus **und** externes MQTT |
| `go2rtc` | go2rtc:1984 (HTTP-API/WS), go2rtc:8554 (RTSP), 8555 (WebRTC) | Restream der Kameras |
| `backend` | backend:8000 | FastAPI, alle Pfade unter `/api` |
| `worker` | – | GPU-Analyse |
| `frontend` | nginx:80 → Host 8080 | SPA + Reverse-Proxy |
| `testcam` | testcam:8554 (`rtsp://testcam:8554/cam1`, `/cam2`) | simulierte Kameras (Profil `demo`) |

Gemeinsames Volume `media` unter `/data/media` (backend + worker). Unterordner:
`snapshots/YYYY-MM-DD/`, `crops/YYYY-MM-DD/`, `clips/YYYY-MM-DD/`, `identities/`.
Pfade in Nachrichten/DB sind **relativ** zu `/data/media`; ausgeliefert vom Backend unter `/media/<pfad>`.

Env (siehe compose): `VG_DATABASE_URL`, `VG_MQTT_HOST`, `VG_MQTT_PORT`, `VG_GO2RTC_URL`, `VG_GO2RTC_RTSP`,
`VG_MEDIA_DIR`, Backend zusätzlich `VG_ADMIN_USER`, `VG_ADMIN_PASSWORD`, `VG_SECRET_KEY`, `VG_DEMO_CAMERAS`
(=1: beim ersten Start zwei Demo-Kameras `rtsp://testcam:8554/cam1|cam2` anlegen). Worker: `VG_MODELS_DIR`, `VG_BUFFER_DIR` (tmpfs).

## nginx (frontend)

- `/` → SPA (index.html-Fallback)
- `/api/` → `http://backend:8000/api/` (inkl. WebSocket-Upgrade für `/api/ws`)
- `/api/internal/` → **403** (nur intern erreichbar)
- `/media/` → `http://backend:8000/media/`
- `/go2rtc/` → `http://go2rtc:1984/` (WebSocket-Upgrade!), geschützt per `auth_request /api/auth/check`

## Auth

Ein Admin aus Env. `POST /api/auth/login {username,password}` setzt httpOnly-Cookie `vg_session` (JWT, 7 Tage)
und liefert `{username}`. `POST /api/auth/logout`, `GET /api/auth/me` → `{username}` oder 401,
`GET /api/auth/check` → 204/401 (für nginx auth_request). Alle `/api/*` außer `auth/login`, `auth/check`, `internal/*`
und `/media/*` verlangen das Cookie. Frontend nutzt `credentials: "include"`.

## Stream-Namen in go2rtc

Kamera mit `id=N` → go2rtc-Stream `cam_N` (Hauptstream, `url`), optional `cam_N_detect` (wenn `detect_url` gesetzt).
Backend synchronisiert Streams beim Start und bei Änderungen per go2rtc-API
(`PUT /api/streams?name=cam_N&src=<url>`, `DELETE /api/streams?src=cam_N`).
Worker liest `rtsp://go2rtc:8554/cam_N_detect` falls vorhanden, sonst `rtsp://go2rtc:8554/cam_N`.
Frontend-Live: `<video-stream>` (go2rtc `video-stream.js`) mit `src="/go2rtc/api/ws?src=cam_N"`, Modus `webrtc,mse,mjpeg`.
Standbild: `GET /api/cameras/{id}/snapshot.jpg` (Backend proxy auf `go2rtc /api/frame.jpeg?src=cam_N`).

## Datenmodelle (JSON, REST)

Koordinaten sind immer **normiert 0..1** (x nach rechts, y nach unten). Zeitstempel: ISO-8601 UTC in REST, Unix-Sekunden (float) in MQTT.

```jsonc
// Camera
{ "id": 1, "name": "Einfahrt", "url": "rtsp://...", "detect_url": null, "enabled": true,
  "detect_fps": 5, "labels": ["person","car","truck","bicycle","motorcycle","bus","dog","cat","bird","horse"],
  "min_score": 0.5,
  "face_recognition": true, "plate_recognition": true, "pet_recognition": true,
  "zones": [ { "id": "z1", "name": "Tor", "color": "#ff9800", "polygon": [[0.1,0.2],[0.5,0.2],[0.5,0.9]] } ],
  "status": { "state": "online|offline|starting|disabled", "fps": 4.9, "width": 1920, "height": 1080,
              "inference_ms": 12.3, "last_seen": "..." } }   // status read-only, vom Worker

// Rule
{ "id": 1, "name": "Person an Einfahrt nachts", "enabled": true,
  "camera_ids": [],            // leer = alle
  "zone_ids": [],              // leer = ganzes Bild; sonst Objekt muss in einer dieser Zonen sein
  "labels": ["person"],        // leer = alle
  "min_score": 0.6,
  "identity": { "mode": "any|known|unknown|specific", "identity_ids": [] },
  "trigger": "enter|leave|dwell",   // enter = Track startet / betritt Zone; leave = Track endet / verlässt Zone; dwell = min_dwell_s im Bereich
  "min_dwell_s": 0,
  "schedule": null,            // oder { "days": [0..6] (0=Mo), "start": "22:00", "end": "06:00" }
  "cooldown_s": 30,            // pro Regel+Kamera
  "actions": [
    { "type": "snapshot" },
    { "type": "clip", "pre_s": 5, "post_s": 10 },
    { "type": "mqtt", "topic": "visionguard/alarm/{camera}", "payload": null, "retain": false },   // payload null = Event-JSON
    { "type": "webhook", "url": "https://...", "method": "POST", "headers": {}, "body": null }      // body null = Event-JSON
  ] }
// Templates in topic/payload/body/url: {camera} {camera_id} {label} {score} {identity} {zone} {event_id} {rule} {time}

// Identity – unbenannte bekommen automatisch eine laufende Nummer
{ "id": 12, "kind": "face|plate|pet", "name": null, "display_name": "Person #12",  // "Kennzeichen B-AB 123", "Tier #3"
  "plate_text": null, "notes": "", "sample_count": 7, "cover_url": "/media/identities/12/xyz.jpg",
  "first_seen": "...", "last_seen": "...", "event_count": 23 }

// Event (ein Event = ein Objekt-Track)
{ "id": "uuid", "camera_id": 1, "camera_name": "Einfahrt", "track_id": 4711, "label": "person",
  "score": 0.91, "start_ts": "...", "end_ts": null, "zones": ["z1"],
  "identity": { "id": 12, "kind": "face", "display_name": "Person #12", "name": null } | null,
  "attributes": { "plate_text": "B-AB 123" },
  "thumb_url": "/media/crops/...jpg", "snapshot_url": "/media/snapshots/...jpg" | null,
  "clip_url": "/media/clips/...mp4" | null, "rules": ["Person an Einfahrt nachts"], "active": true }
```

## REST-Endpunkte (Backend, Präfix /api)

- Auth: siehe oben
- `GET/POST /cameras`, `GET/PUT/DELETE /cameras/{id}`, `GET /cameras/{id}/snapshot.jpg`,
  `POST /cameras/test {url}` → `{ok, width, height, codec, error}` (über go2rtc-Probe oder ffprobe)
- `GET/POST /rules`, `GET/PUT/DELETE /rules/{id}`
- `GET /events?camera_id=&label=&identity_id=&since=&until=&active=&has_snapshot=&has_clip=&limit=50&offset=0` → `{items,total}`;
  `GET /events/{id}`, `DELETE /events/{id}`
- `GET /identities?kind=&named=`, `GET/PATCH/DELETE /identities/{id}` (PATCH: `name`, `notes`),
  `POST /identities/{id}/merge {into_id}` (Samples+Events auf Ziel umhängen, Quelle löschen),
  `GET /identities/{id}/samples` → `[{id, image_url, created_at}]`, `DELETE /identities/{id}/samples/{sid}`,
  `GET /identities/{id}/events` (wie /events)
- `GET /stats?hours=24` → `{by_label:{person:12}, by_camera:{1:5}, timeline:[{ts,label,count}], identities_new: 3}`
- `GET /system` → `{cameras:[{id,name,status}], worker:{online, gpu:{name,util,mem_used,mem_total,temp}, models:{...}}, mqtt:{connected}, version}`
- `GET/PUT /settings` → `{ retention_days_events: 30, retention_days_media: 7, face_match_threshold: 0.45,
  pet_match_threshold: 0.80, plate_max_distance: 1, mqtt_prefix: "visionguard", publish_all_events: true }`
- `GET /ws` (WebSocket, Cookie-Auth): Server → Client Nachrichten:
  - `{"type":"detections","camera_id":1,"ts":..., "width":W,"height":H,"objects":[Obj...]}`
  - `{"type":"event","action":"new|update|end","event":Event}`
  - `{"type":"camera_status","camera_id":1,"status":{...}}`
  - `{"type":"identity","identity":Identity}` (neu/geändert)

### Intern (Worker → Backend, von nginx gesperrt)
- `GET /api/internal/cameras` → `[{id,name,enabled,detect_url_rtsp,main_rtsp,detect_fps,labels,min_score,face_recognition,plate_recognition,pet_recognition,zones}]` (nur enabled)
- `POST /api/internal/identify` `{kind:"face|pet|plate", camera_id, track_id, embedding:[float]|null, plate_text:str|null, crop_path, quality:float}`
  → `{identity_id, display_name, name, score, is_new}`.
  Backend: Face/Pet per Kosinus-Ähnlichkeit gegen alle Samples (Schwelle aus Settings), Plate per normalisiertem Text
  (Levenshtein ≤ plate_max_distance). Kein Treffer → neue Identität. Speichert Sample (max. 20 je Identität, beste Qualität behalten).
- `GET /api/internal/settings` → Settings

## MQTT

### Intern (`vg/internal/...`, JSON)
- Worker → `vg/internal/detections/{camera_id}` (jedes analysierte Frame, QoS 0):
  `{camera_id, ts, width, height, objects:[Obj]}`
  `Obj = {track_id, label, score, bbox:[x1,y1,x2,y2], zones:[zone_id], identity:{id,display_name,name,kind}|null, attributes:{plate_text?}}`
- Worker → `vg/internal/track/{camera_id}` (QoS 1):
  `{type:"start|update|end", camera_id, track_id, label, score, ts, first_seen, last_seen, zones:[...],
    entered_zones:[...], left_zones:[...], identity:{...}|null, attributes:{}, crop_path, best_score}`
  start: nach ≥3 Treffern bestätigt; update: bei Zonenwechsel, Identität aufgelöst, bessere Qualität (≤1/s); end: 2 s ohne Treffer.
- Worker → `vg/internal/status/{camera_id}` (alle 5 s, retain): `{state, fps, width, height, inference_ms, ts}`
- Worker → `vg/internal/system` (alle 5 s, retain): `{gpu:{...}, models:{detector,face,plate,pet}, ts}`
- Backend → `vg/internal/config` (bei jeder Kamera-/Settings-Änderung): `{}` → Worker lädt `/api/internal/cameras` neu
- Backend → `vg/internal/cmd/{camera_id}`: `{action:"snapshot", request_id, path}` bzw.
  `{action:"clip", request_id, path, pre_s, post_s}` (Zeitbezug: jetzt)
- Worker → `vg/internal/cmd_result`: `{request_id, action, ok, path, error}`

### Extern (Präfix aus Settings, Standard `visionguard`)
- `visionguard/events` – jedes Event new/update/end (wenn `publish_all_events`)
- `visionguard/{camera_name_slug}/{label}` – Zähler aktuell sichtbarer Objekte (retain)
- `visionguard/available` – `online`/`offline` (LWT)
- plus frei konfigurierte Topics aus Regel-Aktionen

## Nachträge (Stand der Implementierung)

Ergänzungen, die bei der Umsetzung hinzukamen. Sie sind abwärtskompatibel und gelten verbindlich wie der Rest.

**Backend / REST**
- Kamera-URLs (`url`, `detect_url`) werden in allen Antworten mit maskiertem Passwort ausgeliefert
  (`rtsp://user:***@host/...`). Schickt der Client eine maskierte URL zurück (PUT, `/cameras/test`), ersetzt das
  Backend sie durch die gespeicherte. Der Worker erhält nur go2rtc-URLs und sieht nie Zugangsdaten.
- `PUT /settings` akzeptiert Teil-Updates und liefert immer die vollständigen Settings.
- `GET /events`: `label` darf eine kommagetrennte Liste sein; Filter `has_snapshot`, `has_clip`.
- Zusatzfelder: Samples haben `quality`; `/stats` liefert `hours` und `bucket` (stündlich bis 72 h, sonst täglich);
  `/system.worker` hat `last_seen`.
- Zusätzliche Endpunkte: `GET /api/health` (ohne Login, für den Compose-Healthcheck), Swagger unter `/api/docs`.
- Kamerastatus `offline`, wenn der Worker 15 s nichts gemeldet hat; `disabled` bei abgeschalteter Kamera.
- go2rtc wird zusätzlich alle 30 s synchronisiert (per API angelegte Streams gehen bei go2rtc-Neustart verloren).
- Beim Start werden noch aktive Events beendet; Tracks ohne Lebenszeichen seit 60 s werden automatisch beendet.

**WebSocket**
- Ohne gültiges Cookie wird der Handshake abgelehnt (HTTP 403) – Client behandelt das als „abgemeldet“.
- `{"type":"event","action":"delete","event":...}` beim Löschen eines Events.
- `{"type":"identity","identity":{"id","kind","deleted":true[,"merged_into"]}}` beim Löschen/Zusammenführen.

**MQTT**
- Extern `{prefix}/events`: Payload `{"action":"new|update|end","event":{...}}`.
- Zähler-Topics nutzen einen ASCII-Slug des Kameranamens („Demo Straße“ → `demo_strasse`).
- `track_id` ist prozessweit eindeutig und zeitbasiert initialisiert (große Zahl < 2³¹), damit IDs über Worker-
  und Kamera-Neustarts eindeutig bleiben.
- `vg/internal/system` enthält zusätzlich `perf: {fps, batch_ms, avg_batch}` und `online: true`.
- Beim Entfernen/Deaktivieren einer Kamera sendet der Worker einmal `status` mit `state: "offline"` (retain).
- `end` kommt bis zu 6 s später als 2 s, wenn noch eine Identifikation läuft; `left_zones` im `end` enthält die
  zuletzt belegten Zonen.
- Embeddings: `face` 512-d (InsightFace), `pet` 384-d (DINOv2 ViT-S/14), jeweils L2-normiert.
- Retained Nachrichten auf `vg/internal/cmd/+` ignoriert der Worker. Kein Last-Will für den Worker; das Backend
  erkennt den Ausfall am Ausbleiben von `vg/internal/system`.

**YouTube-Livestreams als Kameraquelle**
- `url`/`detect_url` dürfen YouTube-Links sein (`youtube.com/watch?v=…`, `youtube.com/live/…`, `youtu.be/…`).
  Das Backend (`app/youtube.py`) ermittelt per `yt-dlp -g` nur die aktuelle HLS-Adresse (nur echte Livestreams,
  Hauptstream ≤ 1080p, Detect ≤ 720p, H.264) und registriert sie in go2rtc als
  `ffmpeg:<hls>#input=youtube#video=copy`. Die Adresse wird gecacht und vor Ablauf (≈ 30 min vorher) erneuert.
- Es wird **nichts heruntergeladen oder gespeichert**: ffmpeg liest den Stream live in den Speicher (go2rtc-Template
  `youtube` mit `-re`), Clips nur wie bei allen Kameras aus dem RAM-Ringpuffer bei Regel-Auslösung.
- Nicht erreichbare/beendete Streams: `/cameras/test` liefert einen Fehler; beim Sync bleibt der Stream
  unverändert und wird nach 60 s erneut aufgelöst.
