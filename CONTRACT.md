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
- `detections.ts` ist die Worker-Wanduhr (Unix-s), zu der das Frame von ffmpeg gelesen wurde – **nicht** der
  Sendezeitpunkt nach der Inferenz. Das Frontend puffert Detektionen je Kamera und zeichnet sie um die gemessene
  Video-Verzögerung (+ manueller Versatz „Overlay-Verzögerung“, je Kamera in `localStorage`) versetzt
  (`frontend/src/lib/overlaySync.ts`).

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
- Ist `detect_url` derselbe YouTube-Link wie `url`, wird `cam_N_detect` in go2rtc nur als Alias auf `cam_N` angelegt
  (kein zweiter Abruf; Analyse und Live-Bild auf derselben Zeitachse).
- Das Backend setzt Streams per `PATCH` (nur im Speicher von go2rtc; `PUT` würde Quellen inkl. Zugangsdaten in
  `go2rtc.yaml` schreiben – die Datei ist zusätzlich read-only eingebunden) und merkt sich die gesetzte Quelle, damit
  der 30-s-Sync laufende Streams nicht neu aufbaut.
- Nicht erreichbare/beendete Streams: `/cameras/test` liefert einen Fehler; beim Sync bleibt der Stream
  unverändert und wird nach 60 s erneut aufgelöst.

## Nachträge: Gesichter-Lernsperre, API-Keys, Gegenstände (Inventar), MCP

Verbindlich wie der Rest. Wo dieser Abschnitt älteren Aussagen widerspricht (insbesondere „`/media/*` ohne Cookie“
im Abschnitt Auth), gilt dieser Abschnitt. Begriff überall **`inventory`** (Anzeige deutsch: „Gegenstände“);
`objects` bleibt den Detektionen (`Obj`) vorbehalten. Nutzung ist privat; REST und MCP dürfen von außen erreichbar
sein und sind dann ausschließlich über API-Keys (oder Cookie) zugänglich.

### A. Datenbank (additiv, `backend/app/models.py` + `db.py::init_db`)

`init_db()` führt nach `create_all` die Liste `ADDITIVE_COLUMNS` als `ALTER TABLE … ADD COLUMN IF NOT EXISTS …`
aus. Neue Spalten werden **nur dort** und im Modell (mit identischem Default) eingetragen.

| Tabelle | Spalte | Typ / Default |
|---|---|---|
| `identities` | `learning_locked` | boolean NOT NULL DEFAULT false |
| `identity_samples` | `manual` | boolean NOT NULL DEFAULT false |
| `cameras` | `inventory_enabled` | boolean NOT NULL DEFAULT false |
| `cameras` | `inventory_interval_s` | double precision NOT NULL DEFAULT 3 |
| `cameras` | `inventory_labels` | jsonb NOT NULL DEFAULT '[]' |
| `cameras` | `inventory_min_score` | double precision NOT NULL DEFAULT 0.35 |
| `cameras` | `inventory_tiling` | boolean NOT NULL DEFAULT false |
| `cameras` | `room` | varchar(100) NULL |

Neue Tabellen (über `create_all`): `api_tokens` (Modell `ApiToken`), `inventory_items` (`InventoryItem`),
`inventory_history` (`InventoryHistory`). Spalten siehe `models.py`; die JSON-Formen unten sind maßgeblich für die API.

### B. Auth: API-Keys (Bearer) und Scopes

- **Key-Format:** `vgt_<prefix>_<secret>`; `<prefix>` = 8 Zeichen `[a-z0-9]`, `<secret>` = 32 Zufallsbytes
  base64url ohne Padding (43 Zeichen). Beispiel-Länge: 56 Zeichen. Gespeichert werden nur
  `prefix` = `"vgt_<prefix>"` (12 Zeichen, eindeutig, zum Nachschlagen/Anzeigen) und
  `token_hash` = `HMAC-SHA256(key=VG_SECRET_KEY, msg=<kompletter Key>)` als Hex (64 Zeichen). Vergleich mit
  `hmac.compare_digest`. Der Klartext wird **genau einmal** in der Antwort auf `POST /api/tokens` geliefert, nie
  geloggt (auch nicht der Header `Authorization`).
- **Übertragung:** nur Header `Authorization: Bearer <key>` (kein Query-Parameter).
- **Scopes:** `read`, `write`. Gespeichert als Liste; `write` impliziert `read` (Backend normalisiert
  `["write"]` → `["read","write"]`). `GET`/`HEAD`/`OPTIONS` verlangen `read`, alle anderen Methoden `write`.
  Fehlender Scope → **403** `{"detail":"Keine Berechtigung (Scope write erforderlich)"}`.
- **Prüfreihenfolge** in `require_user` (Name bleibt, alle Router nutzen ihn weiter): 1. Cookie `vg_session`
  (volle Rechte), 2. Bearer-Key. Unbekannt/widerrufen/abgelaufen/fehlend → **401**
  mit Header `WWW-Authenticate: Bearer`. `last_used_at` wird höchstens 1×/min geschrieben; Key-Cache max. 30 s,
  wird beim Widerruf sofort geleert.
- **Nur mit Session-Cookie** (Bearer → 403 `"Nur mit Anmeldung im Browser"`): `/api/tokens*`, `/api/auth/check`
  (nginx `auth_request`, go2rtc bleibt für Keys gesperrt), `/api/auth/logout`.
- **WebSocket `/api/ws`:** Cookie oder Header `Authorization: Bearer` (Scope `read`).
- `GET /api/auth/me` → `{username, auth:"session"|"token", scopes:["read","write"], token_name:str|null}`
  (bei Token: `username` = Admin-Benutzer, `token_name` = Name des Keys).
- **Absicherung (API/MCP sind von außen erreichbar):** Das Backend startet nicht, wenn `VG_SECRET_KEY` der
  Standardwert oder kürzer als 32 Zeichen ist. `POST /api/auth/login`: höchstens 5 Fehlversuche je IP pro Minute
  (danach **429** mit `Retry-After`, zusätzlich `limit_req` in nginx). Ist das Admin-Passwort schwach (`admin`,
  < 8 Zeichen, = Benutzername), ist die Anmeldung nur aus privaten Netzen möglich (nginx-`X-Real-IP` privat und
  kein öffentlicher Eintrag in `X-Forwarded-For`), sonst **403**; API-Keys sind davon nicht betroffen.
  nginx übernimmt ein vorhandenes `X-Forwarded-Proto` (externer TLS-Proxy) → Cookie mit `Secure`.

```jsonc
// ApiToken (GET /api/tokens → ApiToken[], neueste zuerst; widerrufene bleiben sichtbar)
{ "id": 3, "name": "Claude-MCP", "prefix": "vgt_ab12cd34", "scopes": ["read"],
  "created_at": "...", "last_used_at": "..." | null, "expires_at": "..." | null,
  "revoked_at": "..." | null, "revoked": false, "expired": false }
// POST /api/tokens  {name: str 1..100, scopes: ["read"] | ["read","write"] (Default ["read"]),
//                    expires_days: int 1..3650 | null (Default null = unbegrenzt)}
// → 201 ApiTokenCreated = ApiToken + { "token": "vgt_ab12cd34_<43 Zeichen>" }   // Klartext nur hier
// DELETE /api/tokens/{id} → 204; setzt revoked_at (idempotent), Eintrag bleibt; 404 wenn unbekannt.
```

### C. Medien `/media/*` und signierte URLs

- `/media/<path>` wird vom Backend-Router `routers/media.py` ausgeliefert (kein ungeschützter StaticFiles-Mount,
  Range-Anfragen → 206, Pfad-Traversal → 404). Zugriff, in dieser Reihenfolge: Cookie | Bearer (`read`) |
  Signatur `?exp=&sig=`. Nichts davon → **401**; Signatur falsch oder abgelaufen → **403**.
  Übergangsschalter Env `VG_MEDIA_PUBLIC=1` (Backend) = altes Verhalten ohne Prüfung; Standard `0`.
- **Signatur:** `path` = relativer Medienpfad wie in der DB (ohne führendes `/` und ohne `media/`, z. B.
  `crops/2026-10-24/abc.jpg`), `exp` = Unix-Sekunden (int).
  `sig = base64url_ohne_padding( HMAC-SHA256(key=VG_SECRET_KEY (utf-8), msg="media:" + path + ":" + str(exp))[:16] )`
  (22 Zeichen). URL: `/media/<path>?exp=<exp>&sig=<sig>`; absolut (`<public_base_url>/media/…`), wenn das Setting
  `public_base_url` gesetzt ist. Hilfsfunktionen in `backend/app/media_sign.py`:
  `sign_media_path(path, ttl_s) -> str` und `verify_media_sig(path, exp, sig) -> bool`.
- `POST /api/media/sign` (Scope `read` genügt) `{paths: str[] (1..200, mit oder ohne Präfix "/media/"),
  ttl_s: int 60..604800 = 3600}` → `{urls: {<path wie gesendet>: <signierte URL>}, expires_at: iso}`.
- Query `signed=1` an `GET /events`, `/events/{id}`, `/identities/{id}/events`, `/identities*` (cover_url),
  `/identities/{id}/samples`, `/inventory*`: alle `*_url`-Felder signiert (TTL = `media_link_ttl_h`).
- Externe Payloads (`{prefix}/events`, Webhooks mit `body: null`) enthalten signierte URLs (TTL `media_link_ttl_h`).
  Neue Template-Platzhalter: `{thumb_url}` `{snapshot_url}` `{clip_url}` (signiert, leer wenn nicht vorhanden).
  Umsetzung: auch MQTT-Regelaktionen mit `payload: null` enthalten signierte URLs. `{snapshot_url}`/`{clip_url}`
  sind bereits beim Auslösen gesetzt, wenn die Regel selbst eine `snapshot`-/`clip`-Aktion hat (Pfad steht vorab
  fest, die Datei erscheint wenige Sekunden später).
- `signed=1` wirkt zentral (Middleware `routers/media.py::SignedUrlsMiddleware`) auf **alle** JSON-Antworten unter
  `/api/` (außer `/api/internal/`): jedes Feld `*_url` mit Wert `/media/…` wird signiert. Router brauchen dafür
  keinen eigenen Parameter (dürfen ihn aber für die OpenAPI-Doku deklarieren).
- `/media/*` antwortet mit `Cache-Control: private, max-age=600`; `HEAD` wird unterstützt.
- Neuer Medien-Unterordner `inventory/YYYY-MM-DD/` (Crops und Übersichtsbilder der Gegenstände), Aufbewahrung nach
  `retention_days_inventory`; Dateien, auf die ein bestehendes `inventory_items.crop_path` zeigt, werden nicht gelöscht.

### D. Identitäten: Lernsperre, Merge mit Bildauswahl, Vorschläge

```jsonc
// Identity (zusätzlich)
{ ..., "learning_locked": false }
// nur bei GET /identities?with_suggestion=1 zusätzlich: "suggestion": IdentitySuggestion | null
// Sample (GET /identities/{id}/samples)
{ "id": 101, "image_url": "/media/identities/12/x.jpg", "created_at": "...", "quality": 0.83, "manual": false }
// IdentitySuggestion
{ "identity_id": 5, "display_name": "Anna", "name": "Anna", "cover_url": "/media/…" | null,
  "score": 0.41, "likely": true }
```

- **Lernsperre:** `learning_locked=true` → `/api/internal/identify` erkennt die Identität weiter (Matching
  unverändert, Event bekommt die Identität, `last_seen` wird aktualisiert), speichert aber **kein neues Sample**.
  Antwort von `identify` unverändert. Gilt technisch für alle Arten; UI-Schalter nur bei `face` und `pet`.
- `PATCH /identities/{id}` `{name?: str|null (≤200, ""→null), notes?: str, learning_locked?: bool}` → Identity;
  WS `identity`.
- `GET /identities?kind=&named=&locked=&with_suggestion=1`: `locked` filtert nach `learning_locked`;
  `with_suggestion=1` liefert je Identität `suggestion` = bester Vorschlag mit `likely=true`, sonst `null`
  (nur für unbenannte `face`/`pet`, sonst immer `null`). Antwort bleibt ein Array.
- **Merge** `POST /identities/{src}/merge {into_id: int, sample_ids?: int[] | null}` → Identity des Ziels:
  - Prüfungen: `into_id == src` → 422; unterschiedliche `kind` → 422; unbekannt → 404; `sample_ids` leer → 422
    („Keine Bilder ausgewählt“); ein Sample gehört nicht zur Quelle → 422.
  - **Klassischer Merge** – `sample_ids` fehlt/`null` **oder** enthält *alle* Samples der Quelle:
    alle Samples wandern zum Ziel (bei expliziter Auswahl mit `manual=true`, bei `null` bleibt `manual` wie es ist),
    **alle Events der Quelle wandern zum Ziel**, Name/Notizen/`first_seen`/`last_seen` werden wie bisher
    zusammengeführt, die Quelle wird gelöscht. WS: `{"type":"identity","identity":{"id":src,"kind":…,"deleted":true,"merged_into":dst}}`
    und danach die Ziel-Identity.
  - **Teil-Merge** – `sample_ids` ist eine echte Teilmenge: **nur** diese Samples wandern (Datei nach
    `identities/<dst>/`), bekommen `manual=true`. Die Quelle bleibt mit den restlichen Samples, ihren Events,
    Name und Notizen unverändert bestehen. Keine Events wandern. WS: Ziel-Identity und aktualisierte Quell-Identity.
  - Die Lernsperre des Ziels verhindert keinen Merge (er ist eine bewusste Nutzeraktion).
  - Limit: Hätte das Ziel danach mehr als 20 (`MAX_SAMPLES`) Samples mit `manual=true` → 422
    („max. 20 Samples – erst alte löschen“), nichts wird geändert. `prune_samples` kürzt zuerst Samples mit
    `manual=false` (niedrigste Qualität zuerst), danach `manual=true`.
- **Vorschläge** `GET /identities/{id}/suggestions?limit=3` (1..20) → `IdentitySuggestion[]`, absteigend nach
  `score`. Nur `face`/`pet` (sonst `[]`); Ziele = benannte Identitäten gleicher Art ohne die Quelle;
  `score` = maximale Kosinus-Ähnlichkeit zwischen den Samples der Quelle und denen des Ziels;
  `likely = score >= (face_match_threshold | pet_match_threshold) − 0.10`. Ohne Embeddings → `[]`.
- **Automatisches Lernen und `manual`:** Ein automatisch gelerntes Sample verdrängt nur Samples mit `manual=false`;
  sind alle 20 Samples manuell, wird nichts automatisch hinzugefügt.
- **Aufräumen:** Die tägliche Aufbewahrung löscht unbenannte, nicht gesperrte Identitäten ohne Events, deren
  `last_seen` älter als `retention_days_events` ist (samt Bildern). WS: `{"type":"identity","identity":{"id","kind","deleted":true}}`.

### E. Kamera-Erweiterungen

```jsonc
// Camera (REST, zusätzlich; CameraIn: alle Felder optional mit diesen Defaults)
{ ..., "room": "Wohnzimmer" | null,         // ≤100 Zeichen, ""→null; frei wählbar, gruppiert Gegenstände
  "inventory_enabled": false,
  "inventory_interval_s": 3,                // float, 1..60 (Sekunden zwischen zwei Durchläufen)
  "inventory_labels": [],                   // Vokabular-Schlüssel (label); [] = gesamtes Vokabular aus Settings;
                                            // unbekannter Schlüssel → 422 „Unbekannter Gegenstand: <x>“
  "inventory_min_score": 0.35,              // 0.05..0.95
  "inventory_tiling": false }               // Bild zusätzlich in Kacheln analysieren (kleine Gegenstände);
                                            // Default aus: kostet 2–3× GPU-Zeit (docs/plan/03-machbarkeit-ergebnis.md)
```
Änderungen dieser Felder lösen wie üblich `vg/internal/config` aus; sie dürfen im Worker **kein** ffmpeg neu
starten.
`PUT /api/cameras/{id}`: Die Felder `room` und `inventory_*` werden nur geändert, wenn sie im Body stehen
(nicht gesendet = unverändert); alle übrigen Felder ersetzt PUT wie bisher vollständig.
Stehen alle `inventory_labels` einer Kamera nicht mehr im Vokabular, liefert `inventory_vocab` das ganze Vokabular
(wie bei `[]`) und das Backend loggt eine Warnung.

`GET /api/internal/cameras` liefert je Kamera zusätzlich:
```jsonc
{ ..., "room": "Wohnzimmer" | null, "inventory_enabled": true, "inventory_interval_s": 3,
  "inventory_min_score": 0.35, "inventory_tiling": false,
  "inventory_vocab": [ { "label": "remote", "name_de": "Fernbedienung", "prompts": ["remote control"],
                         "min_score": null } ] }   // aufgelöst: inventory_labels gefiltert (Reihenfolge des
                                                   // Settings-Vokabulars) bzw. ganzes Vokabular; min_score null
                                                   // = inventory_min_score der Kamera
```
`main_rtsp` ist der Hauptstream in voller Auflösung (Quelle des Ringpuffers, z. B. 2880×1616 bei `cam_3`).

### F. Settings-Erweiterungen (`GET/PUT /settings`, `GET /api/internal/settings`)

```jsonc
{ ...,
  "inventory_vocabulary": [ { "label": "keys", "name_de": "Schlüssel", "group": "Persönliches",
                              "prompts": ["keys", "key ring"], "min_score": null }, … ],  // Default: 50 Einträge
  "inventory_synonyms": { "keys": ["Schlüsselbund", "Haustürschlüssel"] },  // zusätzliche deutsche Suchwörter je label; Default {}
  "inventory_confirm_n": 2,          // 1..10  Treffer bis „present“
  "inventory_miss_m": 3,             // 1..20  Fehlschläge in Folge bis „missing“
  "retention_days_inventory": 30,    // 1..3650
  "media_link_ttl_h": 24,            // 1..720 Gültigkeit signierter Links in externen Payloads
  "public_base_url": null }          // z. B. "https://vg.example.org" (ohne / am Ende) oder null;
                                     // PUT mit "" setzt auf null zurück (PUT ignoriert null-Werte)
```
Vokabular-Eintrag: `label` = stabiler englischer Schlüssel `^[a-z0-9_]{1,40}$`, eindeutig; `name_de` 1..60 Zeichen;
`group` (Anzeige-Gruppe, optional, Default `""`); `prompts` 1..8 englische Texte für das Modell (je ≤ 60 Zeichen);
`min_score` 0.05..0.95 oder `null`. Das Default-Vokabular steht in `backend/app/config.py::DEFAULT_INVENTORY_VOCABULARY`
(Quelle `docs/plan/vokabular-50.md`). Personen und Fahrzeuge gehören nie ins Vokabular.

### G. MQTT intern: Inventar

**Worker → `vg/internal/inventory/{camera_id}`** (QoS 1, **retain**, nach jedem Durchlauf, **voller Zustand**, keine Deltas):
```jsonc
{ "camera_id": 3,
  "ts": 1761300000.12,          // Unix-s, Sendezeit
  "frame_ts": 1761299998.0,     // Unix-s, Aufnahmezeit des analysierten Keyframes
  "session": "uuid",            // neu bei jedem Worker-Start
  "interval_s": 3.0,            // effektives Intervall (kann wegen Rechenbudget größer sein als eingestellt)
  "model": "yoloe-11m-seg",     // tatsächlich genutztes Modell (Standard yoloe-11m-seg)
  "width": 2880, "height": 1616,// Pixel des analysierten Bildes
  "snapshot_path": "inventory/2026-10-24/cam3_1761299998.jpg" | null,  // Übersichtsbild, nur bei Änderungen
  "stopped": false,             // true: Inventar für diese Kamera beendet (deaktiviert/entfernt), dann items: []
  "items": [ {
      "item_id": "uuid",        // vom Worker, stabil innerhalb der session
      "label": "remote",        // Vokabular-Schlüssel
      "prompt": "remote control",
      "score": 0.62,            // geglätteter Score
      "bbox": [0.41, 0.55, 0.47, 0.60],   // normiert x1,y1,x2,y2 (EMA-geglättet)
      "zones": ["z1"],
      "state": "candidate|present|missing",
      "occluded": false,        // aktuell von Person verdeckt (Fehlschläge zählen dann nicht)
      "first_seen": 1761290000.0, "last_seen": 1761299998.0,   // Unix-s; last_seen = letzter echter Treffer
      "hits": 5, "misses": 0,
      "crop_path": "inventory/2026-10-24/<item_id>_1761299998.jpg" | null } ],   // letzter Crop (≤ 1×/min neu)
  "changes": [ { "item_id": "uuid", "type": "appeared|moved|disappeared" } ] }   // seit dem letzten Durchlauf
```
- `candidate` = noch nicht bestätigt (Backend speichert nichts); `present` ab `inventory_confirm_n` Treffern;
  `missing` nach `inventory_miss_m` Fehlschlägen in Folge (Verdeckung durch Personen zählt nicht). Der Worker führt
  `missing`-Einträge noch höchstens 10 Durchläufe mit und lässt sie dann weg. `moved` = Mittelpunkt > 0,1 verschoben.
- Zuordnung im Worker: gleiches `label` und IoU ≥ 0,3 oder Mittelpunktabstand < 0,05.
- `vg/internal/system` zusätzlich: `models.inventory` (`"off"|"loading"|"ready"|"error: …"`) und
  `perf.inventory: {runs_per_min, ms, skipped, cameras}`.
  Bedeutung (Worker): `runs_per_min` = Durchläufe der letzten 60 s (alle Kameras), `ms` = mittlere GPU-Zeit je
  Durchlauf (Gesamtbild + Kacheln), `skipped` = in den letzten 60 s verfallene Takte (Budget/laufender Durchlauf),
  `cameras` = Kameras mit aktivem Inventar. Der Worker liest `inventory_confirm_n`/`inventory_miss_m` aus
  `GET /api/internal/settings` (bei jedem Laden der Kameras).

**Backend-Verarbeitung:** übernimmt den `state` des Workers (keine zweite Hysterese). Gleiche `session` →
Abgleich über `item_id`; Einträge eines nicht-`stopped` Durchlaufs, die fehlen, gelten als `missing`. Neue `session`
→ Abgleich mit gespeicherten Gegenständen derselben Kamera über `label` + IoU ≥ 0,3; in den ersten
`inventory_miss_m` Nachrichten einer neuen Session wird nichts wegen Fehlens auf `missing` gesetzt. `stopped: true`
oder keine Nachricht seit > 3 × `interval_s` → sichtbare Gegenstände der Kamera werden `stale`.

### H. Gegenstände: REST und WebSocket (Backend, Präfix `/api`)

```jsonc
// InventoryItem
{ "id": 17, "camera_id": 3, "camera_name": "Wohnzimmer", "room": "Wohnzimmer" | null,
  "label": "remote", "name_de": "Fernbedienung", "score": 0.62,
  "bbox": [0.41, 0.55, 0.47, 0.60], "zones": ["z1"], "zone_names": ["Tischchen"],
  "state": "present|missing|stale", "visible": true,          // visible == (state == "present")
  "relation": { "type": "on|near", "item_id": 9, "label": "table", "name_de": "Tisch",
                "text": "vermutlich auf dem Tisch" } | null,
  "first_seen": "...", "last_seen": "...", "updated_at": "...",
  "crop_url": "/media/inventory/…jpg" | null, "snapshot_url": "/media/inventory/…jpg" | null }
```
- `GET /inventory?camera_id=&room=&label=&state=&visible=&signed=&limit=200&offset=0` → `{items, total}`;
  `label`/`state` kommagetrennt; Sortierung `room`, `name_de`, `camera_id`.
- `GET /inventory/{id}` → InventoryItem; `DELETE /inventory/{id}` → 204 (Fehlerkennung entfernen, inkl. Historie).
- `GET /inventory/search?q=&limit=10&signed=` → `{query, answer, items: [InventoryItem + {"match": {score: 0..100,
  via: "label|name|synonym|prompt|fuzzy"}}]}`. Normalisierung: Kleinschreibung, ä→ae/ö→oe/ü→ue/ß→ss, Füllwörter
  („wo ist/sind“, Artikel, „mein/meine …“, „?“) entfernen, deutsche Pluralendungen. Abgleich gegen `label`,
  `name_de`, `prompts`, eingebaute Synonyme (`backend/app/inventory_names.py`) und `inventory_synonyms`;
  `rapidfuzz` (Schwelle 80) als Fallback. Sortierung: sichtbar zuerst, dann `last_seen` absteigend, dann
  `match.score`. `answer` = deutscher Satz, z. B. „Fernbedienung: aktuell sichtbar in Wohnzimmer (Kamera
  Wohnzimmer), vermutlich auf dem Tisch, seit 14:02.“ bzw. „Fernbedienung: zuletzt gesehen vor 2 h in …“ bzw.
  „Kein Gegenstand „…“ bekannt.“
- `GET /inventory/labels` → `[{label, name_de, group, prompts, synonyms, count_present, count_total}]`
  (Reihenfolge des Vokabulars; Labels in der DB, die nicht mehr im Vokabular stehen, am Ende).
- `GET /inventory/{id}/history?limit=50` (1..500) → `[{ts, state, camera_id, bbox, zones, score, crop_url}]`,
  neueste zuerst. Historie: bei jedem Zustandswechsel und bei `present` höchstens 1 Eintrag je 5 min.
- **Lagebeziehung** (nur für `present`, 2D-Heuristik, Text immer „vermutlich …“): Flächen-Labels
  `table, sofa, bed, chair, rug` derselben Kamera im Zustand `present`. `on`: unterer Mittelpunkt der Item-Box liegt
  in der Flächen-Box und ≥ 60 % der Item-Fläche überlappen, **oder** der untere Mittelpunkt liegt horizontal in der
  Fläche und vertikal zwischen 0,03 über der Oberkante und dem oberen Drittel der Flächen-Box (hohe Dinge wie Lampen,
  die über die Fläche hinausragen) – kleinste passende Fläche gewinnt; sonst `near`: nächste
  Fläche mit Abstand < 0,10 (normiert); sonst `null`. Flächen selbst haben `relation: null`.
- **WS:** `{"type":"inventory","camera_id":3,"action":"appear|update|gone|delete","items":[InventoryItem]}` – nur bei
  Änderung. `appear` = wird `present`; `gone` = `present` → `missing|stale`; `update` = verschoben, neuer Crop,
  Relation geändert (je Gegenstand höchstens 1×/10 s); `delete` nach `DELETE /inventory/{id}`.
- Aufbewahrung: `inventory_history` älter als `retention_days_inventory` wird gelöscht, ebenso Gegenstände mit
  `state != present`, deren `last_seen` älter ist.
- Ergänzungen (Implementierung Backend): `InventoryItem` hat zusätzlich `status_text` (z. B. „aktuell sichtbar“,
  „zuletzt gesehen vor 2 h“, bei `stale` „Stand unklar …“) für Menschen/KI-Agenten. Bei `missing`/`stale` bleibt
  `relation` die zuletzt berechnete Lage (nicht neu berechnet). Historie zusätzlich bei Verschiebung (Mittelpunkt
  > 0,1, höchstens 1×/min). Veraltete Nachrichten (`ts` älter als 3 × `interval_s`, z. B. retained nach
  Worker-Ausfall) werden nicht übernommen, sichtbare Gegenstände werden `stale`. `DELETE /inventory/{id}` merkt sich
  den Worker-Gegenstand (`session`+`item_id`), damit er nicht sofort neu angelegt wird. Wird eine Kamera gelöscht,
  werden ihre Gegenstände samt Historie entfernt. `PUT /cameras/{id}` verwirft stillschweigend bereits gespeicherte
  `inventory_labels`, die nicht mehr im Vokabular stehen (422 nur für neu hinzugefügte unbekannte Schlüssel).

### I. MCP-Dienst `mcp`

- Eigener Dienst `mcp` (Verzeichnis `mcp/`, Python, eigene gepinnte `requirements.txt`, unabhängig von den
  Backend-Versionen), intern `mcp:8100`. Env: `VG_BACKEND_URL` (Default `http://backend:8000`),
  `VG_MCP_PORT` (8100), `VG_MCP_ALLOWED_HOSTS` (kommagetrennt; leer = keine Host-Prüfung).
- Transport **Streamable HTTP** unter Pfad **`/mcp`** (zustandslos genügt). nginx: `location /mcp` →
  `http://mcp:8100/mcp` mit `proxy_buffering off`, `proxy_read_timeout 3600s`, `Authorization` durchreichen,
  **ohne** `auth_request`.
- **Auth:** Header `Authorization: Bearer vgt_…` ist Pflicht; fehlt er → 401 (`WWW-Authenticate: Bearer`), bevor
  MCP verarbeitet wird. Der Dienst prüft den Key per `GET /api/auth/me` am Backend (Ergebnis max. 30 s gecacht,
  Schlüssel = SHA-256 des Keys) und reicht **denselben Header** bei jedem Backend-Aufruf durch. Der Dienst hat
  keinen eigenen Key und loggt keine Keys. Alle Tools lesen nur (Scope `read` genügt).
- Medien gehen als signierte URLs hinaus (Backend-Parameter `signed=1` bzw. `POST /api/media/sign`, TTL 3600 s),
  absolut sofern `public_base_url` gesetzt ist.

| Tool | Parameter | Rückgabe (`structuredContent`; zusätzlich deutscher Text als `TextContent`) |
|---|---|---|
| `find_object` | `query: str`, `limit: int = 5` | `{query, answer, items:[{id, name_de, label, camera_id, camera_name, room, state, visible, relation_text, last_seen, crop_url}]}` aus `GET /inventory/search?signed=1` |
| `list_visible_objects` | `camera_id: int?`, `room: str?` | `{total, items:[wie oben]}` aus `GET /inventory?state=present&signed=1` |
| `get_object_history` | `item_id: int`, `limit: int = 20` | `{item_id, entries:[{ts, state, camera_id, zones, crop_url}]}` |
| `list_cameras` | – | `{cameras:[{id, name, room, state, inventory_enabled}]}` aus `GET /cameras` |
| `get_camera_snapshot` | `camera_id: int?`, `camera_name: str?` (eins von beiden) | `ImageContent` (image/jpeg aus `GET /cameras/{id}/snapshot.jpg`) + Text „Kamera …, Aufnahme <Zeit>“ |
| `who_is_where` | – | `{people:[{display_name, identity_id, kind, label, camera_id, camera_name, room, since, zones, thumb_url}]}` aus `GET /events?active=true&signed=1` (Labels `person`, `dog`, `cat`) |

Fehler (Kamera unbekannt, Backend 4xx/5xx) als Tool-Ergebnis mit `isError: true` und deutscher Meldung;
Backend-401/403 werden als „API-Key ungültig oder ohne Berechtigung“ gemeldet.

Ergänzungen der Umsetzung (MCP, additiv):
- `list_visible_objects` nimmt zusätzlich `camera_name: str?` (wird über `GET /cameras` aufgelöst; Name oder Raum,
  ohne Groß-/Kleinschreibung). `get_camera_snapshot` löst `camera_name` genauso auf.
- `get_camera_snapshot` verkleinert das Bild auf max. 1600 px Breite (JPEG), damit es für Modelle handlich bleibt.
- `GET /health` (ohne Key, nur intern) → `{"status":"ok"}` für den Docker-Healthcheck. Alle anderen Pfade verlangen
  den Bearer-Key; ist das Backend bei der Key-Prüfung nicht erreichbar → 503.
- Server: MCP-SDK `mcp==2.3.0`, `stateless_http=True`, `json_response=True`; versteht auch ältere
  Protokollversionen (z. B. `2025-03-26`). Compose-Variable `MCP_ALLOWED_HOSTS` → `VG_MCP_ALLOWED_HOSTS`; gesetzt
  aktiviert sie die Host-/Origin-Prüfung des SDK (nginx reicht `Host` ohne Port durch).

## Nachträge: Welle 0 – Vorbereitung verteilter Worker (D3)

Abwärtskompatibel; mit einem einzelnen Worker ändert sich kein Verhalten. Hintergrund:
`docs/plan/roadmap/d3-verteilte-worker.md` (Abschnitt 1) und `docs/plan/roadmap/00-wellenplan.md` (W0).

**Worker-ID**
- Env `VG_WORKER_ID` (Worker, Default `"local"`), erlaubt `^[A-Za-z0-9_-]{1,64}$`; ungültig → Worker startet nicht.
- **Jede** Worker-Nachricht unter `vg/internal/…` trägt zusätzlich `worker_id: str` (`detections`, `track`,
  `status`, `system`, `cmd_result`, `inventory`). Gesetzt wird es zentral beim Senden (`Service.publish`). Der
  Body von `POST /api/internal/identify` enthält es ebenfalls (Backend ignoriert es).
- MQTT-`client_id` des Workers: `vg-worker-<worker_id>-<unix_ts>`.
- Backend: Nachrichten ohne `worker_id` gelten als `"local"`. `system` wird **je `worker_id`** gespeichert
  (`state.set_worker_system`; ein späteres Topic `vg/internal/system/{worker_id}` wird bereits verstanden, das
  Feld `worker_id` hat Vorrang vor dem Topic-Segment). `GET /system.worker` zeigt unverändert **einen** Worker:
  bevorzugt einen online-Worker (`local` vor anderen), sonst den mit der jüngsten Meldung (`ts`). Alle übrigen
  Nachrichten wertet das Backend weiterhin nicht nach Worker aus.

**Medien schreiben (Worker)**
- Medien (Crops, Snapshots, Inventar-Bilder, Clips) schreibt der Worker ausschließlich über
  `worker/vg_worker/util.py`: `store_media(rel_path, data)` (Bytes, z. B. JPEG über `write_jpeg`) bzw.
  `media_target(rel_path, suffix)` (Kontextmanager für Dateien, die ffmpeg erzeugt, z. B. Clips). Beide schreiben
  atomar (temporäre Datei + `os.replace`) und nur innerhalb von `VG_MEDIA_DIR`. Der spätere Upload-Modus (D3,
  `PUT /api/internal/media/{path}`) ändert nur diese beiden Funktionen.
- Pfade in Nachrichten bleiben **relativ** zu `/data/media`. Das Backend darf nicht annehmen, dass eine Datei
  zu `crop_path` bereits existiert (`identify` speichert dann ein Sample ohne Bild, wie bisher).

**Interne Endpunkte**
- Alle Router unter `/api/internal/` hängen an der Dependency `auth.require_internal` (heute ohne Wirkung, liefert
  `"local"`). Neue interne Endpunkte müssen sie ebenfalls verwenden. Der Schutz bleibt vorerst allein nginx (403).

**Datenbank**
- `db.py::ADDITIVE_INDEXES` – Liste `(indexname, tabelle, spalten)`; `init_db()` führt sie nach
  `ADDITIVE_COLUMNS` als `CREATE INDEX IF NOT EXISTS` aus (Namen `[a-z_][a-z0-9_]*`, je Index eigene Transaktion,
  Fehler werden geloggt und stoppen den Start nicht). Indizes für **bestehende** Tabellen nur dort eintragen.

**Reservierte Kamera-Slugs (externes MQTT)**
- Themen-Topics stehen direkt hinter dem Präfix. Ergibt der Slug eines Kameranamens eines der reservierten Wörter
  `available`, `events`, `health`, `sensor`, `gesture`, `pets`, `notifications`, `report`, `review`, bekommt er
  das Suffix `_cam` (Kamera „Events“ → `{prefix}/events_cam/{label}`). Liste: `backend/app/util.py::RESERVED_SLUGS`,
  Funktion `camera_slug(name)`. Neue Themen-Topics hinter dem Präfix müssen dort ergänzt werden.

## Nachträge: Welle 1 – Hinweise, Push, Veränderungen, Kamera-Gesundheit

Verbindlich wie der Rest, rein additiv. Grundlage: `docs/plan/roadmap/00-wellenplan.md` (Abschnitt 2, W1) und die
Entwürfe `a1-…`, `b2-…`, `c3-c4-c5-…`. Angelegt im Kickoff K1; Fach-Agenten (A1, B2, C5) ändern diesen Abschnitt,
`db.py`, `models.py` und die Settings-Defaults nicht selbst.

### W1.A Datenbank

| Tabelle | Spalte | Typ / Default |
|---|---|---|
| `rules` | `trigger_options` | jsonb NOT NULL DEFAULT '{}' |
| `rules` | `conditions` | jsonb NOT NULL DEFAULT '[]' |
| `rules` | `condition_delay_s` | double precision NOT NULL DEFAULT 0 |
| `cameras` | `health_enabled` | boolean NOT NULL DEFAULT true |

Neue Tabellen (`create_all`): `notifications` (Modell `Notification`: id, ts, source, level, rule_id, rule_name,
title, message, camera_id, event_id, item_ids jsonb, image_path, data jsonb, delivery jsonb, dedup_key) und
`camera_health_log` (Modell `CameraHealthLog`: id, camera_id, type, start_ts, end_ts NULL, value NULL).
`ADDITIVE_INDEXES`: `ix_inventory_history_camera_ts` auf `inventory_history (camera_id, ts)`.

### W1.B Regel-Engine v2 (Registry)

```jsonc
// Rule (zusätzlich)
{ ..., "trigger": "enter|leave|dwell|time",          // Registry rules.TRIGGERS (W2: posture, gesture; W3: sensor)
  "trigger_options": {},                             // je Trigger; time: { "at": "HH:MM" } (lokal, Pflicht)
  "conditions": [                                    // alle müssen erfüllt sein (UND), max. 20; Registry rules.CONDITIONS
    { "type": "inventory", "labels": ["keys"], "state": "present|absent",
      "camera_ids": [], "room": null, "zone_ids": [] },        // absent = kein passender Gegenstand present
    { "type": "inventory_untouched", "labels": ["medicine"], "since": "06:00", "camera_ids": [], "zone_ids": [] },
    { "type": "weather", "metric": "rain_probability", "min": 60, "hours": 12 },   // min 0..100, hours 1..48
    { "type": "track_came_from", "zone_ids": ["z_flur"] } ],                    // nur bei Track-Triggern
  "condition_delay_s": 0,                            // 0..600; Bedingungen erst nach dieser Zeit prüfen
  "actions": [ ..., { "type": "push", "target": "handy" | null,   // id aus push_targets; null = alle aktiven Ziele
                      "title": "{rule}", "message": "{items} liegt noch da",
                      "priority": "low|default|high", "image": "snapshot|crop|none" } ] }
```
- Validierung über die Registry (`rules.validate_rule_parts`): unbekannter Trigger/Bedingungstyp, fehlende Optionen
  oder `track_came_from` bei `time` → **422**. Gespeichert wird die normalisierte Form (Defaults ergänzt, `at` als
  `HH:MM`). GET/POST/PUT `/rules` liefern immer `trigger_options`, `conditions`, `condition_delay_s`.
- `trigger=time`: läuft ohne Track, erzeugt **kein Event**, sondern eine Notification (`source:"rule"`, Planung
  zustandslos aus der DB, höchstens 1× je Tag und `at`; Tage aus `schedule.days`, `schedule.start/end` ohne Wirkung;
  `camera_ids/labels/identity` ohne Wirkung; `cooldown_s` gilt pro Regel). Umsetzung A1.
- Bedingungen ohne Auswertung oder mit Fehler gelten als **nicht erfüllt**. Wetter unbekannt → nicht erfüllt.
- Neue Platzhalter (A1): `{items}` (deutsche Namen der passenden Gegenstände), `{item_place}` (z. B. „Flur,
  Schlüsselbrett“), `{weather}` („70 % Regen ab 15 Uhr“); ohne passende Bedingung leer.
- Aktion `push`: je Aktion **eine** Notification (`source:"rule"`, `level` = `alarm` bei `priority:"high"`, sonst
  `info`) mit Push an `target`. Bild: `snapshot` = Snapshot dieser Regel (falls Aktion `snapshot` vorhanden),
  sonst Event-Crop; `crop` = Ausschnitt des Gegenstands aus der Bedingung, sonst Event-Crop. Regeln mit
  `conditions`, aber ohne `push`, erzeugen beim Auslösen eine Notification ohne Push (A1).
- Tageslimit `push_max_per_rule_per_day` (0 = aus): weitere Notifications der Regel an diesem (lokalen) Tag werden
  gespeichert und angezeigt, der Push wird übersprungen (`delivery[].status:"skipped"`).

Python-API (`backend/app/rules.py`): `register_trigger(name, *, track_based, options_model, label_de)`,
`register_condition(name, model, *, evaluate=None, track_only=False, label_de)` (erneuter Aufruf überschreibt bzw.
reicht den Evaluator nach), `evaluate_conditions(rule, CondContext) -> CondResult`, `is_track_trigger(name)`.
`CondContext{rule, now (UTC), camera_id, track, event_id}`, `CondResult{ok, ctx (Platzhalter), item_ids,
image_path, reason}`. Schemas der W1-Bedingungen: `schemas.CondInventory`, `CondInventoryUntouched`,
`CondWeather`, `CondTrackCameFrom`, `TimeTriggerOptions`. Evaluatoren registriert A1 in
`backend/app/rule_conditions.py` (wird von `main.py` importiert).

Ergänzungen der Umsetzung (A1, additiv):
- `POST /api/rules/check` (Body wie `POST /rules`, auch ungespeichert) prüft die Bedingungen **jetzt**, ohne Aktionen
  → `{ok, reason, details:[{type, ok: bool|null, reason}], placeholders:{items, item_place, weather}, item_ids,
  preview:{title, message}|null}`. `track_came_from` liefert `ok:null` („erst beim Auslösen prüfbar“).
- Track-Regeln mit `conditions`: Prüfung `condition_delay_s` nach der Auslösung (Thread-Pool), je Regel+Kamera
  höchstens eine offene Prüfung; der Cooldown beginnt erst, wenn die Bedingungen erfüllt sind und die Aktionen laufen.
  Offene Prüfungen gehen bei einem Backend-Neustart verloren.
- `trigger=time`: fällig ab `at` + `condition_delay_s` (lokal), Nachholfenster 15 min (Backend-Neustart), letzter
  Lauf je Regel in `kv.rule_time_runs`. Aktionen `snapshot`/`clip` sind ohne Event wirkungslos; `mqtt`/`webhook` ohne
  `payload`/`body` senden die Notification (signierte URLs). Notification `data.trigger = "time"`, Bild = Übersichts-
  bild (`snapshot`) bzw. Crop (`crop`) des Gegenstands aus der Bedingung.
- `inventory_untouched`: zählt nur Gegenstände, die zu `since` schon `present` waren (liegt `since` in der Zukunft:
  gestern). „Berührt“ = Verschiebung > 0,1 oder `missing`-Phase > 120 s bzw. an anderer Stelle beendet; kürzere
  Phasen am selben Ort gelten als Erkennungs-Flackern, `stale`-Phasen zählen nicht. `zone_ids` bedeutet hier: seit
  `since` war **kein Personen-Track** in diesen Zonen (Bereich vor dem Gegenstand), nicht der Ort des Gegenstands.
- `track_came_from`: erfüllt, wenn der Track eine der Zonen **vor** dem Eintritt in die Auslösezone betreten hat
  (gleichzeitiger Eintritt beim Track-Start = Richtung unklar → nicht erfüllt). `TrackState` hat dafür
  `zones_visited` und `zone_entries`; `CondContext` zusätzlich `trigger_ts`, `trigger_zones`; `CondResult`
  zusätzlich `snapshot_path`, `camera_id`, `details`.
- `weather`: Open-Meteo `hourly=precipitation_probability`, Cache 30 min je Standort, bei Fehlern höchstens 3 h alter
  Cache, danach unbekannt. Basis-URL per Env `VG_WEATHER_URL` (Backend, Default
  `https://api.open-meteo.com/v1/forecast`; für Tests).

**Hook-API** (`backend/app/events.py`): `on_track(callback) -> off()`; `callback(TrackHook)` mit
`TrackHook{phase:"start|update|end", camera_id, track_id, event_id, label, msg, track}` nach Event-Speicherung und
Regelauswertung, auch bei nachträglich aufgelöster Identität (`phase:"update"`, `msg={"identity":…}`) und bei
verwaisten Tracks (`end`). Läuft im Track-Thread unter `processor.lock`: kurz halten, eigene DB-Session; Fehler
werden geloggt.

### W1.C Notifications und Push

```jsonc
// Notification
{ "id": 5, "ts": "...", "source": "rule|health|pet|review|report|sensor", "level": "info|warning|alarm",
  "rule_id": 3 | null, "rule": "Schlüssel vergessen" | null, "title": "...", "message": "...",
  "camera_id": 2 | null, "event_id": "uuid" | null, "item_ids": [17], "image_url": "/media/…" | null,
  "data": { "pushed": true, … },                       // quellenspezifisch, z. B. health: {"type":"covered"}
  "delivery": [ { "target": "handy", "ok": true, "status": "pending|sent|failed|skipped",
                  "error": null | "HTTP 403: forbidden", "ts": "..." | null } ] }
```
- REST: `GET /notifications?source=&level=&rule_id=&camera_id=&since=&until=&limit=50&offset=0&signed=` →
  `{items,total}`, neueste zuerst, `source`/`level` kommagetrennt; `GET /notifications/{id}`;
  `DELETE /notifications/{id}` → 204.
- WS: `{"type":"notification","action":"new|update|delete","notification":Notification}` (`update` = Zustellstatus
  nachgetragen; `delete` nur `{"id"}`).
- MQTT extern: `{prefix}/notifications` (QoS 1, nicht retained), Payload = Notification mit signierten URLs.
- Python-API (`backend/app/notifications.py`): `create(*, source, title, message, level="info", rule_id, rule_name,
  camera_id, event_id, item_ids, image_path, data, targets=None, push_enabled=True, priority="default",
  dedup_key=None, dedup_s=0) -> dict | None`. Ziele: `targets=None` → bei `rule` alle aktiven Ziele, sonst
  `notify_routes[source]`; Liste → genau diese; `[]`/`push_enabled=False` → kein Push. `dedup_key`+`dedup_s`:
  innerhalb des Fensters keine zweite Meldung (Rückgabe `None`). Push läuft im Thread-Pool, nie im Aufrufer.
- Push (`backend/app/push.py`, `send(target, PushMessage) -> {target, ok, status, error, ts}`): ntfy (JSON-Publish
  auf die Server-Wurzel; mit Bild ohne `public_base_url` als Upload `PUT {server}/{topic}` mit RFC-2047-Headern;
  Token als `Authorization: Bearer`), Pushover (`POST {server}/1/messages.json`, Bild als `attachment` ≤ 2,5 MB).
  Timeout 10 s, 1 Wiederholung bei Netzwerkfehler/5xx/429. Ist `public_base_url` gesetzt, gehen Bilder als
  signierter Link (`attach`) und `click` = `<public_base_url>/hinweise` hinaus. Zugangsdaten, Server-URL und Topic
  erscheinen nie in Logs oder Fehlermeldungen.
- `POST /settings/push_test {target?: id, config?: PushTarget, title?, message?}` → `{ok, error, target}`
  (Scope `write`). `config` testet eine ungespeicherte Konfiguration; `"***"` darin wird durch den gespeicherten Wert
  desselben Ziels (gleiche `id`) ersetzt. Unbekanntes `target` → 404.
- Aufbewahrung: Notifications älter als `retention_days_notifications` werden gelöscht (Bilder sind Snapshots/Crops
  und folgen deren Aufbewahrung).

### W1.D Settings (zusätzlich)

```jsonc
{ ...,
  "push_targets": [ { "id": "handy",            // ^[a-z0-9_-]{1,40}$, eindeutig; max. 20 Ziele
      "type": "ntfy|pushover", "name": "Marcs Handy", "enabled": true,
      "server": "https://ntfy.sh" | null,       // ntfy: Default https://ntfy.sh; Pushover: Default https://api.pushover.net
      "topic": "vg-…" | null,                   // ntfy Pflicht (^[A-Za-z0-9_-]{1,64}$), nicht maskiert
      "token": "***" | null,                    // ntfy Access-Token (optional)
      "user_key": "***" | null, "app_token": "***" | null } ],   // Pushover Pflicht
  "notify_routes": { "health": ["handy"], "pet": [], "review": [], "report": [], "sensor": [] },  // andere Schlüssel → 422
  "push_max_per_rule_per_day": 3,               // 0..100, 0 = ohne Limit
  "weather_location": { "lat": 50.11, "lon": 8.68 } | null,     // auf 2 Nachkommastellen gerundet; PUT {} → null
  "retention_days_notifications": 30,           // 1..3650
  "inventory_change_min_s": 120,                // 0..3600 (B2)
  "health_alert_after_s": 60 }                  // 10..3600 (C5)
```
- `token`, `user_key`, `app_token` kommen in **allen** Antworten (`GET/PUT /settings`, `/api/internal/settings`) als
  `"***"`. Ein `PUT` mit `"***"` behält den gespeicherten Wert des Ziels mit derselben `id`.
- Geänderte Defaults (nach Messung W0, A1-Flackertest): `inventory_confirm_n` **3** (vorher 2),
  `inventory_miss_m` **10** (vorher 3). Gilt für neue Installationen; gespeicherte Werte bleiben unverändert.

### W1.E B2 „Was hat sich verändert?“ (Umsetzung B2)

```jsonc
// GET /api/inventory/changes?since=&until=&camera_id=&room=&label=&include_transient=0&signed=&limit=100
// → InventoryChanges
{ "since": "...", "until": "...", "summary": "Seit 07:00: 2 neu (…), 1 weg (…), 1 verschoben (…).",
  "counts": { "appeared": 2, "disappeared": 1, "moved": 1, "uncertain": 0, "transient": 0 },
  "changes": [ { "type": "appeared|disappeared|moved|uncertain|transient", "at": "...",
                 "item": InventoryItem,
                 "before": { "ts", "state", "camera_id", "bbox", "zones", "zone_names", "crop_url" } | null,
                 "after":  { …wie before… } | null,
                 "text": "Fernbedienung: vermutlich vom Tisch zum Sofa verschoben (Wohnzimmer)" } ] }
```
- `since` Pflicht: ISO-8601, relativ `3h`/`90m`/`2d`, `today` (lokal 00:00), `morning` (lokal 06:00),
  `yesterday`; `until` Default jetzt. `since` älter als `retention_days_inventory` → 422 „Zeitraum außerhalb der
  Aufbewahrung“. Route vor `/inventory/{id}` registrieren. Sortierung `disappeared`, `appeared`, `moved`,
  `uncertain`, `transient`, je `at` absteigend.
- Paarung: `disappeared` + `appeared` mit gleichem `label` und gleicher Kamera → ein `moved` (nächster Mittelpunkt
  zuerst); ein Worker-Neustart erzeugt so kein Paar. Rauschfilter: kürzer als `inventory_change_min_s` `present`
  → kein `appeared` (mit `include_transient=1` als `transient`). Modul `backend/app/inventory_diff.py`.
- Ergänzungen der Umsetzung (B2, additiv):
  - `since`/`until` verstehen zusätzlich `now`/`jetzt`, `heute`, `heute morgen`, `gestern` sowie `45s`, `1w`,
    `90 min`, `2 Tage`; ISO ohne Zeitzone = lokale Zeit. `morning` vor 06:00 = gestriger Morgen. `until` in der
    Zukunft wird auf jetzt gekürzt; `since ≥ until` → 422. `limit` 1..1000.
  - Rauschfilter gilt in beide Richtungen: Zustandsabschnitte (sichtbar, weg, `stale`) kürzer als
    `inventory_change_min_s` werden eingeschmolzen; ein noch laufender Abschnitt zählt erst nach der Mindestdauer
    (ein frisch verschwundener Gegenstand erscheint also erst nach 120 s als `disappeared`). Kurze `stale`-Phasen
    (Worker-Neustart) zählen nicht.
  - `transient` = vorher und nachher nicht da, im Zeitraum aber (abgeschlossen) sichtbar – unabhängig von der
    Dauer; Text „zwischendurch da, inzwischen wieder weg“. Ist es nur die alte/neue Zeile eines Gegenstands an
    derselben Stelle (≤ 0,10), entfällt es; ein `appeared` übernimmt dann den früheren Zeitpunkt.
  - Paarung ohne Abstandsgrenze; liegt der neue Fund an derselben Stelle (≤ 0,10, gleiche Zonen), wird gar nichts
    gemeldet. Liefert eine Kamera zu `until` Gegenstände (`present`), gelten ihre übrigen `stale`-Zeilen als
    Altlast (nicht `uncertain`, sondern wie `missing`).
  - `before`/`after`: `crop_url` nur bei `state == "present"`, sonst `null`. `disappeared.before` = letzter
    sichtbarer Stand, `disappeared.after` = Zustand `missing`; `appeared.before` = `null`.

### W1.F C5 Kamera-Gesundheit (Umsetzung C5)

- MQTT intern `vg/internal/health/{camera_id}` (Worker, QoS 1, **retain**, alle 30 s und bei Änderung, mit
  `worker_id`): `{camera_id, ts, problems:[{type:"dark|blur|covered|frozen|moved", since, value}],
  metrics:{brightness, sharpness, sharpness_ref, diff, inliers, shift, rotation}, reference:{day:path|null,
  night:path|null}}`. Referenzbilder unter `health/cam<N>_<day|night>.jpg` (640 px, über `util.store_media`).
  Prüfung nur bei `health_enabled` (Feld zusätzlich in `GET /api/internal/cameras`) und `state == online`.
- `vg/internal/cmd/{camera_id}`: neue Aktion `{action:"health_reference", request_id, mode:"day|night|auto"}` →
  `cmd_result` wie gehabt (`path` = neues Referenzbild).
- Backend: `mqtt_bus` leitet das Topic an `backend/app/health.py::handle(camera_id, msg)`; Status über
  `state.set_camera_health(camera_id, health|None)`. Camera `status.health` = `{state:"ok|warn|unknown",
  problems:[{type, since, text}]} | null` (read-only; `text` deutsch, z. B. „Verdeckt seit 14:02“), auch in
  `/system.cameras[].status` und WS `camera_status`. Warnung erst nach `health_alert_after_s` Dauer, Entwarnung nach
  60 s Normalzustand.
- Camera (REST) zusätzlich `"health_enabled": true` (bei `PUT` nur geändert, wenn im Body).
- REST: `GET /cameras/{id}/health?hours=168` → `{camera_id, enabled, current:{state, problems, metrics, ts}|null,
  reference_urls:{day, night}, log:[{type, start_ts, end_ts, value}]}`; `POST /cameras/{id}/health/reference
  {mode:"day|night|auto"}` → 202 `{request_id}` (Scope `write`).
- Tabelle `camera_health_log` (eine Zeile je Problem-Episode); abgeschlossene Einträge älter als
  `retention_days_events` werden gelöscht.
- Meldung: Notification `source:"health"`, `level:"warning"`, Ziele aus `notify_routes.health`,
  `dedup_key:"health:<camera_id>:<type>"`. MQTT extern `{prefix}/health/{camera_slug}` (retain):
  `{"state":"ok|warn","problems":[…]}`.
- Ergänzungen der Umsetzung (C5, additiv): Worker-Nachricht zusätzlich `enabled` (false = Prüfung abgeschaltet, einmal
  gesendet), `checked_ts`, bei `moved` je Problem `shift_x`/`shift_y` (Richtung, normiert); `metrics` zusätzlich
  `shift_x, shift_y, shift_resp, base_resp, ncc, contrast, p95, saturation, flat_share, mode ("day"|"night")`.
  `metrics.inliers` = Korrelationsantwort relativ zur gleitenden Basis (Phasenkorrelation statt ORB, Messung W0),
  `metrics.rotation` = `null` (Drehung zeigt sich als eingebrochene Antwort → `moved`). Der Worker meldet einen
  Befund erst nach zwei Prüfungen in Folge (10-s-Takt). `vg/internal/system.perf.health = {ms, cameras}`.
  `GET /cameras/{id}/health`: `current.pending` = Befunde unter der Warnschwelle; `reference_urls` mit `?v=<mtime>`
  (bei `signed=1` signiert). `POST …/health/reference` wartet bis 8 s und liefert zusätzlich `ok` (`null` = noch
  keine Antwort), `error`, `path`; deaktivierte Kamera → 409. Ohne Worker-Meldung seit 120 s ist `state` `unknown`
  (laufende Warnungen bleiben stehen). Bei Entwarnung zusätzlich Notification `level:"info"` („wieder in
  Ordnung“), nur wenn zuvor gewarnt wurde; je Kamera/Art höchstens eine Warnung pro Stunde. Warnungen tragen ein
  aktuelles Kamerabild (`snapshots/…/health_cam<N>_<ts>.jpg`). Externes Topic zusätzlich `camera_id, camera, ts`;
  bei abgeschalteter Prüfung wird es geleert.

### W1.G MCP

- Aufteilung: `mcp/server.py` (Server, Auth, App) und `mcp/tools/<thema>.py` mit `register(mcp)`; Helfer in
  `mcp/tools/common.py` (`api`, `ok`, `fail`, `cameras`, `find_camera`, `compact_item`, `fmt_time`, `fmt_ago`,
  `READ_ONLY`, `BackendError`). Neue Module in `tools/__init__.py::MODULES` eintragen. Bestehende Tools:
  `inventory.py`, `cameras.py`, `people.py`.
- Neue Tools (alle lesend, Scope `read`, URLs signiert):

| Tool | Modul | Parameter | Rückgabe (`structuredContent`) |
|---|---|---|---|
| `list_notifications` | `notifications.py` (A1) | `since: str?` (wie B2-`since`), `limit: int = 10`, `source: str?` | `{total, items:[{id, ts, source, level, rule, title, message, camera_id, image_url}]}` aus `GET /notifications?signed=1` |
| `what_changed` | `changes.py` (B2) | `since: str = "today"`, `until: str?`, `camera_id: int?`, `camera_name: str?`, `room: str?`, `limit: int = 30` | InventoryChanges (Items kompakt wie `find_object`) aus `GET /inventory/changes?signed=1`; Text = `summary` + 1 Zeile je Änderung |
| `get_camera_health` | `health.py` (C5) | `camera_id: int?`, `camera_name: str?` | `{cameras:[{id, name, room, enabled, state, problems:[{type, since, text}]}]}` |

### W1.H Oberfläche

- Neue Seite **„Hinweise“** unter `/hinweise` (`pages/Notifications.tsx`, Hauptnavigation nach „Gegenstände“), WS-Toast
  bei `notification/new` (Schalter wie Ereignis-Toasts).
- Einstellungen → Karte „Benachrichtigungen“ (`pages/admin/NotificationSettings.tsx`): Push-Ziele inkl. Test,
  Weiterleitung je Quelle, Tageslimit, Wetter-Standort, Aufbewahrung, `health_alert_after_s`.
- Regeln: Aktion „Push“ (Ziel, Priorität, Bild, Titel/Text). Trigger „Zu einer Uhrzeit“ und Block „Nur wenn …“: A1.
- Gegenstände: Reiter „Veränderungen“ (B2). Kameraverwaltung: Reiter „Zustand“, Ampel auf der Systemseite (C5).
- Typen in `frontend/src/lib/types.ts`: `Trigger`, `TriggerOptions`, `RuleCondition`, `PushPriority`,
  `VGNotification`, `NotificationMsg`, `NotificationSource`, `NotificationLevel`, `PushDelivery`, `PushTarget`,
  `NotifyRoutes`, `WeatherLocation`, `PushTestInput/Result`, `InventoryChanges`, `InventoryChange(Snap|Type)`,
  `CameraHealth`, `CameraHealthStatus`, `CameraHealthLogEntry`, `HealthProblemType`; API in `lib/api.ts`:
  `notifications`, `notification`, `deleteNotification`, `pushTest`, `inventoryChanges`, `cameraHealth`,
  `setHealthReference`; Mock-Routen in `lib/mock.ts`.

## Nachträge: Welle 2 – Lernen & Personen (Exemplare, Rückfragen, Pose/Gesten, Tiere, Tagesbericht)

Verbindlich wie der Rest, rein additiv. Grundlage: `docs/plan/roadmap/00-wellenplan.md` (Abschnitt 2, W2), die
Entwürfe `b4-d2-…`, `c1-d1-c2-…`, `c3-c4-c5-…` (C3), `b2-b3-…` (B3) und `messungen.md`. Angelegt im Kickoff K2;
Fach-Agenten (B4+D2, C1+D1, C3, B3) ändern diesen Abschnitt, `db.py`, `models.py` und die Settings-Defaults nicht
selbst. Wo dieser Abschnitt einem Entwurf widerspricht, gilt dieser Abschnitt.

### W2.A Datenbank

| Tabelle | Spalte | Typ / Default | Thema |
|---|---|---|---|
| `rules` | `repeat_s` | double precision NOT NULL DEFAULT 0 | C1 |
| `rules` | `repeat_max` | integer NOT NULL DEFAULT 0 | C1 |
| `events` | `acknowledged_at` | timestamptz NULL | C1 |
| `cameras` | `pose_enabled` | boolean NOT NULL DEFAULT false | C1 |
| `cameras` | `gesture_enabled` | boolean NOT NULL DEFAULT false | D1 |
| `cameras` | `inventory_label_min_scores` | jsonb NOT NULL DEFAULT '{}' | D2 |
| `cameras` | `inventory_suppress` | jsonb NOT NULL DEFAULT '[]' | D2 |
| `identities` | `item_label` | varchar(40) NULL | B4 |
| `identity_samples` | `negative` | boolean NOT NULL DEFAULT false | B4/D2 |
| `identity_samples` | `source` | varchar(16) NOT NULL DEFAULT 'auto' (`auto\|merge\|upload\|review`) | B4/D2 |
| `inventory_items` | `identity_id` | integer NULL | B4 |
| `inventory_items` | `identity_score` | double precision NULL | B4 |

Neue Tabellen (`create_all`, Spalten siehe `models.py`):
- `review_requests` (Modell `ReviewRequest`): id, kind, status, dedup_key, priority, question, camera_id, label,
  inventory_item_id, identity_id, source_identity_id, score, threshold, bbox jsonb, crop_path, snapshot_path,
  data jsonb, correct_label, created_at, answered_at, answer_source, expires_at.
- `inventory_examples` (`InventoryExample`): id, label, camera_id, crop_path, bbox jsonb, positive, embedding
  real[] NULL, review_id NULL, created_at – Beispielsammlung aus D2-Antworten (später Few-shot/visuelle Prompts).
- `pet_visits` (`PetVisit`): id, identity_id NULL, label, camera_id, zone_id, purpose, event_id NULL, start_ts,
  end_ts NULL, duration_s NULL.
- `reports` (`Report`): id, kind (`daily|manual`), period_key (`"daily:YYYY-MM-DD"`, eindeutig; `manual` → NULL),
  period_start, period_end, created_at, facts jsonb, text, html, generator, delivery jsonb.

`ADDITIVE_INDEXES`: `ix_inventory_items_identity_id` auf `inventory_items (identity_id)`.
Identität `kind` erlaubt zusätzlich `"item"` (Spalte bleibt varchar(8)). `display_name` unbenannt: „Gegenstand #N“.
Zonen (JSON in `cameras.zones`) haben zusätzlich `"purpose": "food"|"water"|"litter"|null` (C3, keine Spalte).

### W2.B Settings (zusätzlich)

```jsonc
{ ...,
  // B4/D2 – Exemplare und Rückfragen (Werte aus messungen.md §3)
  "item_match_threshold": 0.55,      // 0.2..0.95  Kosinus; Band [thr-0.15, thr) → Rückfrage statt Zuordnung
  "item_min_px": 90,                 // 0..1000    kürzere Crop-Seite (px); kleiner → keine automatische Zuordnung
  "review_enabled": true,
  "review_max_per_day": 10,          // 0..100     neue Fragen je lokalem Tag (0 = keine neuen Fragen)
  "review_max_open": 20,             // 1..200
  "review_expire_h": 48,             // 1..720
  "review_band": 0.10,               // 0.01..0.5  Score-Band unter der Schwelle für „unsicher“
  "review_auto_tune": true,          // Schwellen je Kamera+Label nachführen
  // C1/D1 – Pose und Gesten
  "gesture_hold_s": 1.0,             // 0.5..5
  "fall_default_dwell_s": 60,        // 5..3600   Vorschlag für min_dwell_s im Regel-Editor (posture)
  "fall_track_grace_s": 30,          // 0..300    Nachlauf: liegender Track reißt ab → Liegedauer wird vererbt
  // C3 – Haustier-Tagebuch
  "pet_diary_labels": ["dog", "cat"],
  "pet_diary_min_visit_s": { "food": 5, "water": 3, "litter": 10 },   // je 0..600; andere Schlüssel → 422
  "pet_diary_notify": true,
  "pet_diary_single_pet_fallback": true,
  // B3 – Tagesbericht
  "report_enabled": false,
  "report_time": "21:00",            // HH:MM lokal
  "report_days": [0, 1, 2, 3, 4, 5, 6],
  "report_generator": "template",    // "template" | "llm" (llm nur mit llm_provider ≠ "none", sonst template)
  "report_channels": { "mqtt": true, "webhook_url": null, "mail_to": [] },  // Push über notify_routes.report
  // Gemeinsamer LLM-Client (B3, später A2-Cloud) – standardmäßig AUS
  "llm_provider": "none",            // "none" | "openai_compat" | "anthropic"
  "llm_base_url": null,              // openai_compat: z. B. "http://llm-bridge:8000/v1"; PUT "" → null
  "llm_model": null,                 // Modell-ID (Pflicht für llm-Aufrufe)
  "llm_timeout_s": 60 }              // 5..600
```
- Schlüssel kommen **nur** aus der Backend-Env: `VG_LLM_API_KEY` (openai_compat, optional) bzw.
  `VG_ANTHROPIC_API_KEY`; nie in Settings, API-Antworten oder Logs. `VG_REPORT_LLM_API_KEY` entfällt.
- `GET /settings` liefert zusätzlich read-only `llm_key_configured: {"openai_compat": bool, "anthropic": bool}`
  (PUT ignoriert das Feld).

### W2.C Regeln: Pose, Gesten, Wiederholung, Quittieren (C1/D1)

```jsonc
// Rule (zusätzlich)
{ ..., "trigger": "enter|leave|dwell|time|posture|gesture",
  "trigger_options": { "posture": "lying|sitting|standing", "max_motion": 0.02 | null, "exclude_zone_ids": [] }  // posture
                   | { "gestures": ["arms_up","arm_left","arm_right","arms_cross"] },                           // gesture (min. 1)
  "min_dwell_s": 60,            // posture: so lange muss die Haltung bestehen
  "repeat_s": 120,              // 0 = aus, sonst 10..3600: erneut auslösen, solange das Event nicht quittiert/beendet ist
  "repeat_max": 3 }             // 0..20 (0 = unbegrenzt, solange repeat_s > 0)
```
- Neue Platzhalter: `{posture}` (deutsch, z. B. „liegt“), `{gesture}` (z. B. „beide Arme hoch“), `{repeat}`
  (0 = erste Auslösung).
- Registry (`rules.register_trigger`): `posture` und `gesture` sind `track_based=True` und haben einen
  **Track-Evaluator** (`TriggerSpec.evaluate`, siehe W2.I). `exclude_zone_ids`: in diesen Zonen wird nicht ausgelöst
  (Sofa, Bett). `gesture` wird durch `vg/internal/gesture/{cid}` ausgelöst (Phase `"gesture"`).
- Event (REST/WS) zusätzlich `"acknowledged_at": "..." | null`; `POST /events/{id}/ack` (Scope `write`) → Event,
  setzt `acknowledged_at` (idempotent) und sendet WS `event/update`. Quittierte oder beendete Events werden nicht
  wiederholt. `attributes` zusätzlich `posture?`, `posture_since?` (Unix-s), `motion?`.
- Kamera (REST + `GET /api/internal/cameras`) zusätzlich `pose_enabled`, `gesture_enabled` (bei `PUT` nur geändert,
  wenn im Body).

**MQTT intern (Worker):**
- `Obj.attributes` zusätzlich `posture?: "standing|sitting|lying|unknown"`, `motion?: float`; Track-`update`
  zusätzlich bei Haltungswechsel (`attributes.posture`, `attributes.posture_since`).
- Worker → `vg/internal/gesture/{camera_id}` (QoS 1, nicht retained, mit `worker_id`):
  `{camera_id, track_id, gesture, score, held_s, ts, zones:[…], identity:{…}|null}`.
- `vg/internal/system.models.pose` (`"off"|"loading"|"ready (…)"|"error: …"`), `perf.pose = {ms, crops_per_s}`.

**Backend:** WS `{"type":"gesture", …wie MQTT…, "camera_name"}`; MQTT extern `{prefix}/gesture/{camera_slug}`
(nicht retained) `{gesture, camera, camera_id, identity, ts}`.

Ergänzungen der Umsetzung (C1/D1, additiv):
- **Notfall-Kennzeichnung:** Löst eine Regel mit `trigger: "posture"` oder `repeat_s > 0` aus, bekommt das Event
  `attributes.alert = {rule, rule_id, trigger, repeat, fired_at, first_fired_at, posture?, posture_since?}`
  (ISO-Zeiten; `repeat` = Nummer der letzten Auslösung). `GET /events` hat dafür die Filter `alert` (bool, hat
  `attributes.alert`) und `acknowledged` (bool). MCP `list_alerts` liest `GET /events?alert=true[&active=true&
  acknowledged=false]&signed=1`; `active_only` = laufend **und** unquittiert.
- **Wiederholung:** je (Regel, Event) im Backend-Speicher; endet bei Quittierung, Event-Ende, Regeländerung,
  `repeat_max` oder – bei `posture` – wenn die Haltung nicht mehr besteht. Wiederholungen umgehen Cooldown und
  Bedingungen (die waren bei der ersten Auslösung erfüllt); ein Backend-Neustart beendet sie.
- **posture-Auswertung:** einmal je Track und Regel; nach ≥ 10 s anderer Haltung wieder scharf. Die Dauer zählt ab
  `posture_since`, aber erst ab dem letzten Zeitpunkt, an dem die Person in einer `exclude_zone_ids`-Zone, außerhalb
  von `zone_ids` oder über `max_motion` war. `motion` = Median der Keypoint-Verschiebung / Box-Diagonale je Sekunde,
  geglättet (0 = regungslos, Gehen ≈ 0,1–0,5); Track-`update` bei Änderung > 0,02 höchstens alle 3 s.
- **Nachlauf (`fall_track_grace_s`) im Worker:** Ein Personen-Track mit Haltung `lying` auf einer Kamera mit
  `pose_enabled` endet erst nach `fall_track_grace_s` ohne Treffer (statt 2 s). Eine neue Detektion bzw. eine
  Pose-Person an derselben Stelle (IoU ≥ 0,2 oder Mittelpunkt < 0,1) wird an diesen Track angehängt – Event, Liegedauer
  und Wiederholungen laufen weiter.
- **Gesten:** `arm_left`/`arm_right` = linker/rechter Arm **der Person**; `arms_cross` = Unterarme über
  Schulterhöhe gekreuzt („X über dem Kopf“), nicht vor der Brust verschränkt (Alltagshaltung). Gilt nach
  `gesture_hold_s` in ≥ 80 % der Pose-Bilder (mind. 3), danach 5 s Sperre je Track. Pose-Rate: 2 Hz (`pose_enabled`)
  bzw. 5 Hz (`gesture_enabled`), nur bei bestätigtem Personen-Track.
- `vg/internal/system.perf.pose` zusätzlich `runs_per_min`, `skipped`, `cameras`, `gestures`. Worker-Env:
  `VG_POSE_MODEL` (`yolo11n-pose.pt`, wird einmalig nach `/models/pose` geladen), `VG_POSE_FPS`, `VG_GESTURE_FPS`,
  `VG_POSE_MAX_BUSY` (0,2), `VG_POSE_MAX_BATCH`, `VG_POSE_IMGSZ`, `VG_POSE_UNLOAD_S`.
- Platzhalter `{posture}`/`{gesture}`/`{repeat}` sind bei `trigger=time` leer.

### W2.D Exemplare (B4) und Rückfragen (D2)

```jsonc
// Identity: kind += "item"; zusätzlich "item_label": "backpack" | null (nur bei item)
// Sample zusätzlich: "negative": false, "source": "auto|merge|upload|review"
// InventoryItem zusätzlich:
{ ..., "identity": { "id": 31, "kind": "item", "display_name": "Annas Rucksack", "name": "Annas Rucksack" } | null,
  "identity_score": 0.78 | null }
// Camera zusätzlich (REST, PUT nur wenn im Body):
{ ..., "inventory_label_min_scores": { "remote": 0.30 },              // 0.05..0.95, nur Vokabular-Schlüssel
       "inventory_suppress": [ { "label": "remote", "bbox": [x1,y1,x2,y2], "review_id": 41 | null } ] }   // max. 200

// ReviewRequest
{ "id": 41, "kind": "inventory_label|inventory_identity|identity_match",   // erweiterbar über review.register_kind
  "status": "open|yes|no|skipped|expired", "question": "Ist das eine Fernbedienung?",
  "camera_id": 3 | null, "camera_name": "Wohnzimmer" | null, "label": "remote" | null, "name_de": "Fernbedienung" | null,
  "inventory_item_id": 17 | null, "identity_id": 5 | null, "source_identity_id": 12 | null,
  "score": 0.31 | null, "threshold": 0.35 | null, "bbox": [..] | null,
  "crop_url": "/media/…" | null, "snapshot_url": "/media/…" | null, "data": {},
  "created_at": "...", "answered_at": null, "answer_source": "ui|push|mcp|api" | null, "expires_at": "...",
  "correct_label": null }
```
- **REST (Exemplare, B4):** `POST /identities {kind:"item", item_label, name}` (nur `item`, sonst 422; `item_label`
  muss im Vokabular sein) → 201 Identity. `POST /identities/{id}/samples` multipart `file` (jpeg/png/webp ≤ 10 MB),
  `bbox?="x1,y1,x2,y2"` (normiert) → 201 Sample (`source:"upload"`, `manual:true`); Worker antwortet nicht binnen
  15 s → **503** „Analyse-Dienst nicht erreichbar“ (keine halbe Identität/kein Sample).
  `POST /identities/{id}/samples/from-inventory {inventory_item_id}` → 201 Sample. Suche: `match.via` += `"identity"`,
  `answer` nennt den Exemplar-Namen. `GET /identities/{id}/suggestions` lässt Ziele weg, die in einer Rückfrage mit
  „Nein“ beantwortet wurden. `GET /identities?kind=` ohne `kind` liefert weiterhin alle Arten (auch `item`).
- **REST (Rückfragen, D2):**
  `GET /review?status=open&kind=&camera_id=&limit=50&offset=0&signed=` → `{items, total, open_count, asked_today, max_per_day}`
  (offene nach `priority` absteigend, sonst neueste zuerst); `GET /review/{id}`;
  `POST /review/{id}/answer {answer:"yes|no|skip", correct_label?}` (Scope `write`) → ReviewRequest; nicht offen → 409.
  `GET /review/thresholds?camera_id=` → `[{camera_id, label, base, current, auto, yes, no}]`.
  Signierte Antwort-Links ohne Login (D2): `GET /review/a/{id}?a=yes|no&exp=&sig=` → kleine HTML-Seite mit Knopf (kein
  Beantworten beim Prefetch), `POST` derselben URL beantwortet (`answer_source:"push"`); `sig` wie `media_sign`, aber
  `msg = "review:{id}:{a}:{exp}"`; falsch/abgelaufen → 403. `nginx` reicht `/api/review/a/` unverändert durch.
- **WS:** `{"type":"review","action":"new|answered|expired","review":ReviewRequest}`; `inventory` wie bisher, Items
  mit `identity`.
- **Dedup/Limits (Kern in `backend/app/review.py`, K2):** Ein `dedup_key` wird nicht erneut gefragt, wenn er offen
  ist, in den letzten 30 Tagen mit „Ja“ oder jemals mit „Nein“ beantwortet wurde. Höchstens `review_max_per_day` neue
  Fragen je lokalem Tag und `review_max_open` offene; offene verfallen nach `review_expire_h` (`status:"expired"`).
  Schlüssel-Schema (D2): `lbl:{cam}:{label}:{zelle10x10}`, `inst:{inventory_item_id}:{identity_id}`, `idm:{src}:{dst}`.
- **Push:** Notification `source:"review"` über `notify_routes.review` (D2), mit signierten Antwort-Links
  (`public_base_url` nötig). MQTT extern `{prefix}/review` (nicht retained) `{review, answer_urls:{yes,no}}`.

**Intern:**
- `GET /api/internal/cameras` je Kamera zusätzlich `inventory_suppress` (Liste wie oben), `inventory_review: bool`
  (= `review_enabled`); `inventory_vocab[]` zusätzlich `instances: int` (Zahl der `item`-Identitäten mit diesem
  `item_label`), und `min_score` ist bereits mit `inventory_label_min_scores` aufgelöst (Kamera-Wert vor
  Vokabular-Wert, `null` = `inventory_min_score`).
- `POST /api/internal/identify` mit `kind:"item"`, zusätzlich `label` (Vokabular-Schlüssel) und `item_id`
  (Worker-Item-ID, statt `track_id`): Abgleich nur gegen Identitäten mit `item_label == label`; schlägt ein
  negatives Sample das beste positive, ist es kein Treffer; legt **nie** eine neue Identität an
  (`identity_id: null`); lernt automatisch nur bei Score ≥ Schwelle + 0,10, ohne Lernsperre, Crop ≥ `item_min_px`.
  Umsetzung: `backend/app/item_identity.py::identify_item` (B4).
- `vg/internal/inventory/{cid}`: Items zusätzlich `identity`, `identity_score`; Nachricht zusätzlich
  `uncertain: [{label, score, bbox, crop_path}]` (≤ 3 je Durchlauf, Score in [min − band, min), je Kamera+Label
  höchstens 1 Crop/10 min). Exemplar-Abgleich bei neuem Gegenstand, `moved`, sonst höchstens alle 10 min, nur für
  Labels mit `instances > 0`. Treffer in einer Unterdrückungsfläche (gleiches Label, IoU ≥ 0,5) verwirft der Worker.

**Ergänzungen der Umsetzung (B4/D2, additiv):**
- `POST /api/internal/identify` (`kind:"item"`) nimmt zusätzlich `crop_px: int | null` (kürzere Seite des Gegenstands im
  analysierten Bild, px). Unter `item_min_px` nie Zuordnung und nie Lernen, aber Rückfrage im Band. Ohne Treffer liefert
  die Antwort `identity_id: null` mit dem besten `score`. Automatisches Lernen zusätzlich nur, wenn das Bild keinem
  gespeicherten fast gleicht (Score < 0,95) und höchstens 1 Sample je Exemplar und 10 min.
- Worker-Items enthalten `identity`/`identity_score` erst nach einem Abgleich; fehlen die Schlüssel, lässt das Backend
  die Zuordnung unverändert. Je Kamera bekommt höchstens ein Gegenstand ein Exemplar (bester Score). Nach jeder
  `vg/internal/config` gleicht der Worker die sichtbaren Gegenstände neu ab (Exemplar/Samples geändert → Backend sendet
  `config`). `perf.inventory.items = {embeds, identify, matched, errors}`.
- `GET /review/thresholds` liefert zusätzlich `name_de`. Welche Kamera-Schwellen automatisch gesetzt wurden, steht in
  `kv["review_tune:{camera_id}"]`; weicht der Kamera-Wert davon ab, gilt er als von Hand gesetzt (`auto:false`).
- Rückfrage-Gründe in `data.reason`: `uncertain` | `low_score` | `new_label`; `inventory_identity` zusätzlich
  `data.embedding` (für das Sample bei der Antwort), `data.crop_px`, `data.small`; `identity_match` zusätzlich
  `data.source_name`, `data.target_name` (Bilder: `crop_url` = Quelle, `snapshot_url` = Ziel).
- `inventory_label` „Nein“ mit `correct_label`: Unterdrückungsfläche für das falsche Label, die Zeile wird umetikettiert
  und von der Worker-ID gelöst (danach „zuletzt gesehen“), Beispiele für beide Labels.
- Notification `source:"review"` entsteht nur, wenn `notify_routes.review` Ziele hat; ntfy bekommt die Aktionen Ja/Nein
  (HTTP-POST auf die signierten Antwort-Links, nur mit `public_base_url`). `notifications.create(…, actions=, click_path=)`
  und `push.PushMessage.actions` sind dafür additiv ergänzt.
- `POST /review/a/{id}` mit `Accept: text/html` antwortet mit einer kleinen Danke-Seite, sonst mit dem ReviewRequest.

### W2.E Embedding-Dienst (gemeinsam, K2) – `vg/internal/embed`

Ein Topic-Paar für alle Bild-/Text-Embeddings (ersetzt B4 `embed` und B1 `history/encode`). Der Worker nutzt das
**geteilte DINOv2** der Tiererkennung (`identify.PetModel`, 384-d, L2-normiert; 0 MB zusätzlich, wenn das
Tiermodell geladen ist; lädt es sonst bei Bedarf). Modul `worker/vg_worker/embed.py` (`EmbedService`): eigener
Thread, Warteschlange max. 32 (voll → Anfrage wird mit `ok:false, error:"busy"` beantwortet), Batch ≤ 8, läuft mit
niedriger Priorität (wartet nicht auf die Detektion, blockiert sie aber auch nicht: kurze Batches).

```jsonc
// Backend → Worker: vg/internal/embed (QoS 1, nicht retained; retained Nachrichten ignoriert der Worker)
{ "request_id": "hex", "kind": "item|image|text",
  "path": "identities/31/upload_1.jpg" | null,    // relativ zu /data/media (oder image_b64)
  "image_b64": "<JPEG/PNG base64>" | null,        // ≤ 4 MB, für Bilder außerhalb des Medienordners
  "bbox": [x1,y1,x2,y2] | null,                   // normiert; Ausschnitt vor dem Embedding
  "augment": false,                               // item: + Spiegelung und verkleinerte/verrauschte Fassungen
  "text": null }                                  // kind text (W3, B1) – bis dahin ok:false, "kind text nicht unterstützt"
// Worker → Backend: vg/internal/embed_result (QoS 1, mit worker_id)
{ "request_id": "hex", "ok": true, "kind": "item", "model": "vit_small_patch14_dinov2.lvd142m", "dim": 384,
  "embedding": [384 float] | null,                // bei augment: L2-normierter Mittelwert aller Fassungen
  "variants": [[384 float], …] | null,            // nur bei augment: einzelne Fassungen (Original zuerst)
  "width": 812, "height": 640,                    // Pixel des (ausgeschnittenen) Bildes
  "ms": 6.1, "error": null }
```
- `kind:"item"` und `"image"` rechnen identisch (quadratisch auffüllen, 224 px, fp16); `item` mit `augment:true`
  erzeugt: Original, gespiegelt, auf 120 px und auf 64 px verkleinert (Unschärfe, JPEG q=40) – Empfehlung aus
  messungen.md §3.
- Backend-API: `backend/app/embed_client.py::request(kind, *, path=None, image_bytes=None, bbox=None,
  augment=False, text=None, timeout=15.0) -> EmbedResult` (blockierend, thread-sicher; `EmbedResult{embedding,
  variants, dim, model, width, height, ms}`). Keine Antwort binnen `timeout` → `EmbedUnavailable`
  („Analyse-Dienst nicht erreichbar“, Router → 503); `ok:false` → `EmbedError(<error>)` (Router → 422).
- `vg/internal/system.perf.embed = {requests_per_min, ms, dropped}`; `models.embed` = Status des geteilten Modells.
- Gemessen (K2, `cam_3` läuft parallel): Rechenzeit im Worker Median 8,7 ms (max. 12,6), Rundlauf Backend → Worker →
  Backend Median 9,9 ms (max. 13,6); `augment` (4 Fassungen in einem Batch) 12 ms. Voraussetzung: `TCP_NODELAY` auf
  den MQTT-Sockets von Worker und Backend (paho setzt es nicht; sonst +40 ms durch Delayed ACK) und
  `set_tcp_nodelay true` in `mosquitto.conf`.

### W2.F Haustier-Tagebuch (C3)

- Zone `purpose` (siehe W2.A). Besuch beginnt mit `entered_zones` einer Zweck-Zone, endet mit `left_zones`/`end`;
  gezählt ab `pet_diary_min_visit_s[purpose]`; Besuche desselben Tiers innerhalb von 60 s werden zusammengefasst.
  Erfassung über `events.on_track` in `backend/app/pet_diary.py` (nur `pet_diary_labels`).
- REST: `GET /pets/visits?identity_id=&purpose=&camera_id=&since=&until=&limit=100&offset=0&signed=` →
  `{items:[PetVisit], total}`; `GET /pets/summary?days=14` → `[{identity:{id,kind,display_name,name}|null, label,
  purposes:{food|water|litter:{today, normal:[lo,hi]|null, last_ts, daily:[{day, count}]}}}]`.
  `PetVisit = {id, identity:{…}|null, label, camera_id, camera_name, zone_id, zone_name, purpose, event_id,
  start_ts, end_ts, duration_s, thumb_url}`.
- Hinweise: **keine** eigene Tabelle – Notification `source:"pet"`, `level:"warning"`, Ziele `notify_routes.pet`,
  `data = {type:"too_few|too_many|long_gap", identity_id, purpose, day, value, expected:[lo,hi]}`,
  `dedup_key = "pet:{identity_id|label}:{purpose}:{day}:{type}"`.
- WS `{"type":"pet_visit","action":"new|update","visit":PetVisit}`. MQTT extern `{prefix}/pets/{pet_slug}/{purpose}`
  (retain) `{today, last_ts}`.
- Aufbewahrung: `pet_visits` wie Events (`retention_days_events`).
- Ergänzungen der Umsetzung (C3, additiv): `/pets/visits` zusätzlich `label=`, `identity_id=0` = Besuche ohne
  Identität, `purpose` kommagetrennt (unbekannter Zweck → 422). `/pets/summary` je Eintrag zusätzlich `name`, je Zweck
  `median`, `expected_now:[lo,hi]` (Normalbereich bis zur aktuellen Uhrzeit), `status:"ok|low|high|gap|unknown"`,
  `gap_s`, `usual_gap_s` (P95 der Pausen). Normalwert = P10/P90 der Tageszahlen der 14 Tage vor heute (mind. 7 Tage
  Daten). Hinweise: stündlich `too_few` (heute < P10 bis jetzt − 1), `too_many` (heute > P90 + 2), `long_gap`
  (Pause > 1,5 × P95, mind. 10 Pausen); nach Mitternacht Abschluss des Vortags mit Ganztageswerten. Besuche laufen
  schon ab der Mindestdauer (`end_ts: null`, WS `new`), das Ende kommt per WS `update`. Fallback-Zuordnung
  (`pet_diary_single_pet_fallback`) wird durch eine später erkannte Identität ersetzt; Merge von Identitäten nimmt
  Besuche mit, Löschen setzt sie auf `identity: null`. Test-Seed: `python -m app.pet_diary_seed` (Zeilen mit
  `event_id:"seed"`, `--clear`).

### W2.G Tagesbericht (B3) und LLM-Client (K2)

```jsonc
// Report
{ "id": 4, "kind": "daily|manual", "period_start": "...", "period_end": "...", "created_at": "...",
  "text": "…", "html": "…", "generator": "template|llm:<modell>|template (Fallback)",
  "facts": { … } ,                              // nur in GET /reports/{id} und preview
  "delivery": { "mqtt": {"ok": true, "ts": "..."}, "push": {...}, "webhook": {...}, "mail": {"ok": false, "error": "SMTP nicht konfiguriert"} } }
```
- REST: `GET /reports?limit=30&offset=0` → `{items: Report ohne facts/html, total}`; `GET /reports/{id}?signed=`;
  `DELETE /reports/{id}` → 204; `GET /reports/preview?since=&until=` (Scope `read`, Default heute 00:00 bis jetzt,
  speichert nichts); `POST /reports/generate {since?, until?, deliver: false}` (Scope `write`) → Report.
- Genau ein `daily` je lokalem Tag (`period_key` eindeutig); verpasster Lauf wird bis 6 h nach `report_time`
  nachgeholt; Scheduler-Hook `report.tick()` (vom Backend-Ticker jede Sekunde aufgerufen, selbst gedrosselt).
- Versand: MQTT extern `{prefix}/report` (retain) `{date, text, report_id, counts}`; Push als Notification
  `source:"report"` über `notify_routes.report`; Webhook `report_channels.webhook_url`; Mail über Env
  `VG_SMTP_HOST/PORT/USER/PASSWORD/FROM/STARTTLS`. Fehler je Kanal in `delivery`, nie Abbruch.
- WS `{"type":"report","report":Report ohne facts}`. Aufbewahrung wie Events.
- LLM nur mit `report_generator:"llm"` und `llm_provider ≠ "none"`; Fakten-JSON ohne Bilder/URLs;
  Plausibilitätsprüfung (Uhrzeiten, Zahlen inkl. Zahlwörter, Namen müssen in den Fakten stehen), sonst
  `template (Fallback)`.
- **LLM-Client** `backend/app/llm_client.py` (K2): `configured() -> bool`, `complete(messages, *, system=None,
  max_tokens=400, temperature=0.2, timeout=None) -> LLMResult{text, model, provider, ms, usage}`; Fehler →
  `LLMError` (deutsche Meldung, ohne Schlüssel/URL-Geheimnisse). `openai_compat`: `POST {llm_base_url}/chat/completions`
  (Bearer `VG_LLM_API_KEY`, falls gesetzt); `anthropic`: Messages-API `https://api.anthropic.com/v1/messages`
  (Header `x-api-key` aus `VG_ANTHROPIC_API_KEY`, `anthropic-version: 2023-06-01`). Kein neues Paket (httpx).
  Test-Server ohne Kosten: `scripts/llm_mock.py` (OpenAI-kompatibel, nur Standardbibliothek).
- Ergänzungen der Umsetzung (B3, additiv): Zeitraum eines `daily` = Berichtszeitpunkt des Vortags bis
  `report_time` (am Umstellungstag 23/25 h). `facts` = `{version, period{start,end,date,weekday,hours}, events[],
  people{events,named[],unknown_faces,new_faces,without_face}, vehicles{total,by_label[],plates[]}, animals{total,
  by_label[],named[],visits[]}, rules[{name,count,first,last,snapshot_url}], inventory (B2-Änderungen kompakt)|null,
  notifications{by_source,items[]}, system{cameras[],offline_cameras[]}, counts, sections[{key,title,text}],
  generation?{requested,ok,model,ms,error,problems[],rejected_text}}` (Typ `ReportFacts` in `types.ts`).
  Vorschau: `id: null`. `delivery` je Kanal zusätzlich Detailfelder (`topic`, `notification_id`, `targets`,
  `status`, `recipients`, `note`); nicht eingerichtete Kanäle fehlen. Endet ein Zeitraum genau um Mitternacht,
  gilt der Vortag als Berichtstag (Überschrift, MCP `get_daily_report`). `preview`/`generate`: Zeitangaben wie
  B2 (`parse_when`), höchstens 31 Tage, sonst 422.

### W2.H MCP

- **Schreibwerkzeuge (Regel ab W2):** Tools mit Wirkung sind erlaubt. Sie rufen zuerst
  `await common.require_write(ctx)` auf (prüft `scopes` aus `GET /auth/me`, 30 s gecacht) und liefern ohne `write`
  `isError` „API-Key ohne Schreibrecht.“; Backend-Aufrufe mit Wirkung über `common.api_send(ctx, method, path,
  json=…)`; Annotation `common.WRITE` (`read_only_hint=False`, `destructive_hint=False`). Lesende Tools bleiben
  `READ_ONLY`.

| Tool | Modul | Parameter | Rückgabe (`structuredContent`) |
|---|---|---|---|
| `list_alerts` | `alerts.py` (C1) | `active_only: bool = True`, `limit: int = 10` | `{total, items:[{event_id, camera_id, camera_name, label, posture, since, acknowledged_at, repeat, snapshot_url}]}` |
| `acknowledge_alert` (write) | `alerts.py` (C1) | `event_id: str` | `{event_id, acknowledged_at}` |
| `list_open_questions` | `review.py` (D2) | `limit: int = 10` | `{open_count, items:[{id, kind, question, camera_name, crop_url, created_at, expires_at}]}` |
| `answer_question` (write) | `review.py` (D2) | `id: int`, `answer: "yes"\|"no"\|"skip"`, `correct_label: str?` | ReviewRequest (kompakt) |
| `get_pet_diary` | `pets.py` (C3) | `pet: str?`, `days: int = 1` | `{pets:[{name, label, purposes:{…}}], visits:[…]}` aus `/pets/summary` + `/pets/visits?signed=1` |
| `get_daily_report` | `reports.py` (B3) | `date: str?` (`YYYY-MM-DD`, `today`, `yesterday`) | Report (Text + Zustellstatus); ohne gespeicherten Bericht → Vorschau |

- `find_object` liefert in `items[]` zusätzlich `identity_name` (B4, `null` ohne Exemplar).

### W2.I Gemeinsame Bausteine (K2) – Python-APIs

- **Track-Trigger-Evaluator** (`backend/app/rules.py`): `register_trigger(name, *, track_based, options_model,
  label_de, evaluate=None)`. `evaluate(TriggerContext) -> list[str] | None` (Zonen, in denen ausgelöst wird; `None` =
  nicht auslösen). `TriggerContext{rule, track: TrackState, msg, now (lokale Unix-s), phase:"start|update|end|tick|gesture",
  state: dict}` – `state` ist je Regel+Track persistent (`TrackState.trigger_state[rule_id]`), z. B. für
  Haltedauer/Wiederholungen. `events.Processor._evaluate` ruft Evaluatoren für alle Nicht-Standard-Trigger auf; `tick`
  ruft sie jede Sekunde mit Phase `"tick"` für aktive Tracks auf (posture: Liegedauer). Cooldown, `static_match`
  (Kamera/Label/Score/Identität/Zeitplan) und Bedingungen prüft weiterhin `events`.
  `processor.dispatch_track_phase(camera_id, track_id, phase, msg)` wertet nur Evaluator-Trigger für einen laufenden
  Track aus (für `vg/internal/gesture`). Wiederholung (`repeat_s/repeat_max`, Platzhalter `{repeat}`) setzt C1 in
  `events.py` um.
- **MQTT-Handler-Registry** (`backend/app/mqtt_bus.py`): `register_handler(kind, fn, *, direct=False)` für
  `vg/internal/<kind>/…`; `fn(camera_id|None, msg)`; `direct=True` läuft im MQTT-Thread (nur für sehr kurze
  Arbeit, z. B. `embed_result`), sonst im Track-Thread. Vorbelegt: `gesture` → `pose_events.handle_gesture`,
  `embed_result` → `embed_client.handle_result`.
- **Rückfragen-Kern** (`backend/app/review.py`): `register_kind(kind, *, label_de, on_answer)` mit
  `on_answer(session, ReviewRequest, answer, correct_label)` (läuft vor dem Commit, Ausnahme → Antwort wird nicht
  gespeichert, 422); `create(*, kind, dedup_key, question, priority=0.0, camera_id=None, label=None,
  inventory_item_id=None, identity_id=None, source_identity_id=None, score=None, threshold=None, bbox=None,
  crop_path=None, snapshot_path=None, data=None) -> dict | None` (None = abgeschaltet, Dublette oder Limit);
  `answer(session, review_id, answer, *, source, correct_label=None) -> dict`; `review_json(r, signed=False)`;
  `counts(session) -> {open_count, asked_today, max_per_day}`; `on_created(callback)` (D2: Push/MQTT);
  `tick()` (Verfall). Dedup und Limits sind dort fertig, die Erzeuger und Antwort-Wirkungen baut D2.
- **Platzhalter-Module mit fertiger Verdrahtung** (vom Fach-Agenten zu füllen, nicht umbenennen):
  `backend/app/pose_events.py` (C1), `item_identity.py` (B4), `pet_diary.py` (C3), `report/` (B3, `tick()`),
  Router `routers/review.py` (D2 ergänzt), `routers/pets.py` (C3), `routers/reports.py` (B3);
  Worker `worker/vg_worker/embed.py` (fertig); MCP `mcp/tools/{alerts,review,pets,reports}.py`.
- **Oberfläche:** Seiten `/bestaetigen` (`pages/Review.tsx`, „Bitte bestätigen“, Zähler in der Kopfzeile),
  `/tiere` (`pages/Pets.tsx`), `/berichte` (`pages/Reports.tsx`); Einstellungs-Karten
  `admin/ReviewSettings.tsx` (D2), `admin/PersonSettings.tsx` (C1/D1), `admin/PetSettings.tsx` (C3),
  `admin/ReportSettings.tsx` (B3), `admin/AiSettings.tsx` (K2, LLM-Client). Typen in `types.ts`:
  `ReviewRequest`, `ReviewKind`, `ReviewStatus`, `ReviewList`, `ReviewAnswerInput`, `ReviewThreshold`, `ReviewMsg`,
  `InventorySuppress`, `Posture`, `GestureName`, `GestureMsg`, `PostureTriggerOptions`, `GestureTriggerOptions`,
  `ZonePurpose`, `PetVisit`, `PetSummary`, `PetPurposeStats`, `PetVisitMsg`, `Report`, `ReportList`,
  `ReportChannels`, `ReportMsg`, `ReportGenerateInput`, `LlmProvider`, `ItemIdentityInput`; API in `lib/api.ts`:
  `reviews`, `review`, `answerReview`, `reviewThresholds`, `ackEvent`, `createItemIdentity`, `uploadSample`,
  `sampleFromInventory`, `petVisits`, `petSummary`, `reports`, `report`, `deleteReport`, `reportPreview`,
  `generateReport`; Mock-Routen in `lib/mock.ts`.

## Nachträge: Welle 3 – Bildverständnis & Gedächtnis (Frag die Kamera, Sensoren/Vorräte, Zeitmaschine)

Verbindlich wie der Rest, rein additiv. Grundlage: `docs/plan/roadmap/00-wellenplan.md` (Abschnitt 2, W3), die
Entwürfe `a2-a3-…`, `c3-c4-c5-…` (C4), `b1-…` und `messungen.md` (§5 B1, §6 A2). Angelegt im Kickoff K3;
Fach-Agenten (A2, A3+C4, B1) ändern diesen Abschnitt, `db.py`, `models.py` und die Settings-Defaults nicht selbst.
Wo dieser Abschnitt einem Entwurf widerspricht, gilt dieser Abschnitt (insbesondere: C4 `supply_items`,
`vg/internal/supplies`, `rules.sensor`, B1 `history/encode` und das einseitige `vg/internal/gpu` entfallen).

### W3.A Datenbank (pgvector)

- Dienst `db` läuft ab K3 auf **`pgvector/pgvector:0.8.2-pg17-bookworm`** mit dem neuen Volume `db-data-pgv`
  (Umzug per Dump/Restore: `scripts/db-migrate-pgvector.sh`; Rückfall auf das alte Volume `db-data` mit
  `postgres:17-alpine`: `scripts/db-rollback-pgvector.sh`, schreibt `docker-compose.override.yml`). Backups unter
  `~/vision-guard-backups/` (außerhalb des Repos, 600).
- `init_db()` führt `CREATE EXTENSION IF NOT EXISTS vector` aus. Klappt das nicht (alte DB nach Rückfall), bleibt
  `db.vector_available()` `False`: Tabellen mit Vektorspalten werden **nicht** angelegt, B1 meldet sich ab
  (`/system.db.vector = false`), alles andere läuft. Tabellen mit Vektorspalten tragen im Modell
  `__table_args__ = {"info": {"vector": True}}` und werden nur dann angelegt (eigener Schritt nach `create_all`,
  inkl. ihrer Indizes aus `db.VECTOR_INDEXES`).
- Vektortyp: `backend/app/vector.py::HalfVec(dim)` (SQLAlchemy-Typ für `halfvec(dim)`, ohne Zusatzpaket; Werte
  als `list[float]`/`numpy`), Vergleich `HistoryFrame.embedding.cosine_distance(vec)` (Operator `<=>`).

| Tabelle | Spalte | Typ / Default | Thema |
|---|---|---|---|
| `cameras` | `vlm_cloud_allowed` | boolean NOT NULL DEFAULT false | A2 |
| `cameras` | `history_enabled` | boolean NOT NULL DEFAULT false | B1 |
| `cameras` | `history_interval_s` | double precision NOT NULL DEFAULT 30 (5..600) | B1 |
| `cameras` | `history_tiling` | boolean NOT NULL DEFAULT false | B1 |

Neue Tabellen (`create_all`, Spalten siehe `models.py`):
- `ask_log` (`AskLog`, A2): id, ts, camera_id, question, region jsonb NULL, answer, yes_no, engine, model, ms,
  snapshot_path, source (`ui|api|mcp|sensor`), token_name NULL, error NULL.
- `virtual_sensors` (`VirtualSensor`, A3): id, name, camera_id, enabled, kind, region jsonb (Polygon), params jsonb,
  state_set, supply, verify_vlm, interval_s, vlm_max_age_s, change_threshold, confirm_n, state, value NULL,
  since NULL, last_eval NULL, last_engine NULL, last_vlm_ts NULL, snapshot_path NULL, created_at, updated_at.
- `sensor_history` (`SensorHistory`, A3): id, sensor_id (FK, CASCADE), ts, state, value NULL, engine NULL,
  snapshot_path NULL; Index `ix_sensor_history_sensor_ts (sensor_id, ts)`.
- `sensor_references` (`SensorReference`, A3): id, sensor_id (FK, CASCADE), state (`on|off|full|half|empty`),
  image_path, embedding real[] NULL, created_at.
- `shopping_list` (`ShoppingItem`, C4): id, name, note, source (`auto|manual|mcp`), sensor_id NULL, created_at,
  done_at NULL.
- `history_frames` (`HistoryFrame`, B1, **nur mit pgvector**): id bigserial, camera_id, ts, last_ts, tile smallint
  (0 = ganzes Bild, 1..4 Kacheln), bbox jsonb NULL, embedding `halfvec(768)`, thumb_path NULL, model varchar(60).
  Indizes (`db.VECTOR_INDEXES`): `ix_history_frames_camera_ts (camera_id, ts)`, `ix_history_frames_ts (ts)`,
  `ix_history_frames_embedding` = HNSW `embedding halfvec_cosine_ops`. Suche mit
  `SET LOCAL hnsw.iterative_scan = relaxed_order` bei Filtern.

### W3.B Settings (zusätzlich)

```jsonc
{ ...,
  // A2 – Frag die Kamera (Werte aus messungen.md §6)
  "vlm_enabled": true,                 // lokales VLM (Dienst vlm, Compose-Profil vlm) nutzen, wenn erreichbar
  "vlm_model": "moondream2-int4",      // moondream2-int4 | smolvlm-500m | qwen25vl-3b-nf4 (nur nicht-kommerziell)
  "vlm_idle_unload_s": 120,            // 30..3600  danach entlädt der Dienst das Modell und gibt die GPU frei
  "vlm_need_mb": 3000,                 // 500..6000 Lease-Bedarf (torch + eigener CUDA-Kontext)
  "vlm_cloud_model": null,             // Modell-ID für den Cloud-Fallback über llm_client (llm_provider);
                                       // null = Cloud aus. Zusätzlich nötig: cameras.vlm_cloud_allowed
  "retention_days_ask": 7,             // 1..365  ask_log + Bilder unter ask/
  // A3+C4 – Sensoren und Vorräte
  "retention_days_sensors": 30,        // 1..3650 sensor_history
  "shopping_list_auto": true,          // Vorrats-Sensoren (supply) setzen bei low/out Einträge, bei ok erledigt
  // B1 – Zeitmaschine (messungen.md §5)
  "retention_days_history": 14,        // 1..365
  "history_min_change": 0.05,          // 0..0.5  1−cos unter diesem Wert → nur last_ts verlängern
  "history_model": "siglip2-b16-256" } // siglip2-b16-256 | mobileclip2-s0 (Wechsel macht alte Vektoren unsuchbar)
```
`PUT /settings`: `vlm_cloud_model: ""` setzt auf `null` zurück. `notify_routes.sensor` existiert seit W1.

### W3.C GPU-Lease (K3, Worker) – `vg/internal/gpu/*`

Ein Dienst mit großem Modell (A2: `vlm`) leiht sich die GPU vom Worker. Der Worker entlädt dafür alle
registrierten entladbaren Modelle (heute YOLOE des Inventars, ab B1 der SigLIP-Bildturm), ruft
`torch.cuda.empty_cache()` und meldet den freien Speicher per **NVML** (nicht `cudaMemGetInfo`, WSL2).
Detektion, Gesicht/Kennzeichen/Tier, DINOv2-Embedding und Pose bleiben geladen.

```jsonc
// Dienst → Worker: vg/internal/gpu/request (QoS 1, nicht retained)
{ "request_id": "hex", "holder": "vlm", "need_mb": 3000, "max_s": 300 }   // max_s 10..1800 (Default 300)
// Worker → Dienst: vg/internal/gpu/granted (QoS 1, mit worker_id)
{ "request_id": "hex", "holder": "vlm", "ok": true, "free_mb": 3410, "need_mb": 3000,
  "unloaded": ["inventory"], "until": 1761300300.0, "ms": 420.0, "error": null }
// Dienst → Worker: vg/internal/gpu/release (QoS 1)
{ "request_id": "hex", "holder": "vlm" }
// Worker → Dienst: vg/internal/gpu/released (QoS 1, mit worker_id)
{ "request_id": "hex", "holder": "vlm", "reason": "release|expired" }
```
- `unloaded` = alle registrierten entladbaren Modelle, die jetzt **nicht** im VRAM sind (auch solche, die gar nicht
  geladen waren). `ok:false` mit `error`: `"busy: GPU-Lease von <holder>"` (anderer Halter aktiv; nichts entladen) oder
  `"zu wenig VRAM: <free_mb> < <need_mb>"` (entladen, Lease **läuft trotzdem** – der Dienst gibt sie mit `release`
  zurück oder lädt ein kleineres Modell). Der Dienst wartet höchstens **10 s** auf `granted`, sonst → 503
  „Bildmodell nicht verfügbar“.
- Erneutes `request` mit derselben `request_id` verlängert die Lease (`until` = jetzt + `max_s`) und wird sofort
  wieder mit `granted` beantwortet. Läuft `until` ab, gibt der Worker die GPU selbst frei (`released`,
  `reason:"expired"`). Nach dem Freigeben lädt das Inventar YOLOE beim nächsten Takt neu (≈ 1–2 s).
- Während der Lease: `models.inventory = "paused: GPU-Lease vlm"`, Inventar-Takte verfallen (`perf.inventory.skipped`),
  Inventar-Nachrichten pausieren (das Backend setzt nach 3 × Intervall auf `stale`, wie bei einem Worker-Ausfall).
- `vg/internal/system` zusätzlich `gpu_lease: {request_id, holder, since, until, need_mb, free_mb} | null`;
  `GET /system.worker` gibt `gpu_lease` und `perf` (seit K3) mit aus.
- Worker-API (`worker/vg_worker/gpu_lease.py`): `GpuLeaseManager` (an `Pipeline.lease`) mit
  `register(name, pause: Callable[[str], bool], resume: Callable[[], None])` – `pause(holder)` entlädt
  blockierend (≤ 8 s) und liefert `True`, wenn das Modell aus dem VRAM ist; `resume()` erlaubt das Neuladen.
  `active() -> bool`, `status() -> dict | None`. Referenz-Client und Prüfwerkzeug:
  `python -m vg_worker.leasetest --need-mb 3000 --hold 5` (im Worker-Container, ohne GPU-Zugriff).

### W3.D Frame-Tap (K3, Worker) – Abnehmer pro Kamera

`worker/vg_worker/frametap.py`: `FrameTapHub` an `Pipeline.taps`. Nach jedem analysierten Detect-Frame (in
`Pipeline._post`, nach Tracking) bekommt jeder registrierte Abnehmer – gedrosselt je Kamera – das Bild.
- `taps.register(name, callback, *, cameras: Callable[[int], bool], interval_s: float | Callable[[int], float] = 1.0)`
  → `FrameTap`. `callback(TapFrame)` läuft im **eigenen Thread** des Abnehmers; je Kamera liegt höchstens ein
  Frame bereit (neuer ersetzt älteren = verwerfen statt queuen). Ausnahmen werden geloggt (höchstens 1×/min) und
  stoppen weder Detektion noch andere Abnehmer.
- `TapFrame{camera_id, ts (Worker-Wanduhr wie detections.ts), image (BGR uint8, Detect-Auflösung, NUR lesen),
  width, height, objects: [Obj wie in vg/internal/detections], persons: [[x1,y1,x2,y2]] (rohe Personen-Boxen ≥ 0,3,
  normiert), runtime}` – `runtime` = `CameraRuntime` (für `KeyframeGrabber`, z. B. B1/Vollauflösung).
- `taps.unregister(name)`; `vg/internal/system.perf.taps = {name: {frames_per_min, ms, dropped}}`.
- Abnehmer in W3: `sensors` (A3, CPU-Messungen), `history` (B1, Bildquelle wenn kein Keyframe).

### W3.E Frag die Kamera (A2)

- Dienst **`vlm`** (`vlm/`, Compose-Profil `vlm`, intern `vlm:8200`, kein Port nach außen; Basis
  `pytorch/pytorch:2.5.1-cuda12.4-cudnn9-runtime`, Pins wie `vlm/Dockerfile.bench`). Backend-Env `VG_VLM_URL`
  (Default `http://vlm:8200`).
  - `POST /infer {image_b64, question, region?: [x1,y1,x2,y2], mode: "query"|"yesno", max_tokens?: int}` →
    `{answer, yes_no: "yes"|"no"|"unknown", ms, model}`; 429 beschäftigt (Warteschlange 1), 503 nicht verfügbar.
  - `GET /health` → `{state: "off|loading|ready|error: …", model, vram_mb, last_used}`.
  - Vor dem Laden: GPU-Lease (W3.C, `need_mb` = Setting `vlm_need_mb`, das Backend reicht Settings im Request-Header
    nicht durch – der Dienst liest `GET /api/internal/settings`). Nach `vlm_idle_unload_s`: entladen,
    `empty_cache`, `release`.
- Backend: `backend/app/vlm.py` (Bild über go2rtc `frame.jpeg?src=cam_N`, Ausschnitt, an `vlm` oder Cloud),
  `backend/app/cloud_vlm.py` (über `llm_client.complete(..., images=[jpeg])`, Modell `vlm_cloud_model`),
  Router `backend/app/routers/ask.py`. `/system.worker.models.vlm` ergänzt das Backend aus `vlm /health`.
- REST:
  - `POST /cameras/{id}/ask {question: 1..300, region?: [x1,y1,x2,y2], engine?: "auto"|"local"|"cloud"}` → `AskResult`.
    **Scope `read` genügt**, Rate-Limit **10/min je API-Key** (Sitzungen: 10/min je Benutzer) über
    `auth.require_read_limited("ask", 10)` → 429 „Zu viele Anfragen (max. 10/min)“ mit `Retry-After`.
    Fehler: 503 „Bildmodell nicht verfügbar“, 429 „Bildmodell beschäftigt“, 403 „Cloud für diese Kamera nicht
    freigegeben“, 409 „Kamera ist deaktiviert“, 502 „Kamerabild nicht verfügbar“.
  - `GET /ask?camera_id=&limit=20&offset=0&signed=` → `{items: AskResult[], total}`; `DELETE /ask/{id}` → 204.
```jsonc
// AskResult
{ "id": 7, "camera_id": 3, "camera_name": "Wohnzimmer", "question": "Is the light on?", "region": null,
  "answer": "Yes, the ceiling light is on.", "yes_no": "yes|no|unknown", "engine": "local|cloud",
  "model": "moondream2-int4", "ms": 1012, "snapshot_url": "/media/ask/…jpg", "ts": "...", "source": "ui|api|mcp|sensor" }
```
- Medien `ask/YYYY-MM-DD/cam{N}_{unix}.jpg` (beantwortetes Standbild), Aufbewahrung `retention_days_ask`.
- Prompts intern englisch; Antworten werden **nicht** übersetzt (Vorlagen in der UI; MCP-Beschreibung bittet um
  englische Fragen). `answer` ist Modelltext, `yes_no` heuristisch (`unknown` erlaubt).
- Ergänzungen der Umsetzung (A2, additiv):
  - `vlm /infer` liefert zusätzlich `model_ms` (reine Modellzeit); `/health` zusätzlich `enabled, lease, mqtt,
    download, last_error, stats`; `POST /unload` entlädt sofort. Das Modell läuft in einem Kindprozess (Entladen
    gibt auch den CUDA-Kontext frei); reicht der Speicher laut `granted.free_mb` nicht, weicht der Dienst auf
    `smolvlm-500m` aus. Settings liest der Dienst alle 30 s.
  - Backend: Fragen, die mit is/are/do/can/… bzw. ist/sind/gibt/… beginnen, gehen mit `mode:"yesno"` an das Modell.
    Bild über go2rtc `frame.jpeg?src=cam_N&cache=3s`. Fehlgeschlagene Fragen stehen mit `error` in `ask_log` (ohne
    Bild) und erscheinen nicht in `GET /ask`. `auto` weicht auf die Cloud aus, wenn lokal 503/429 und die Cloud für
    die Kamera erlaubt und eingerichtet ist.
  - `vlm.ask(camera_id, question, *, region, engine, source, token_name, image=None, record=True)`: für A3
    (Sensor-Art `vlm`) `image` = eigenes JPEG statt go2rtc-Bild, `record=False` = kein Standbild/kein `ask_log`.
    Helfer `vlm.is_yes_no_question`, `vlm.parse_yes_no`, `vlm.crop_jpeg`, `cloud_vlm.configured()`.

### W3.F Virtuelle Sensoren und Vorräte (A3+C4)

```jsonc
// VirtualSensor
{ "id": 4, "name": "Bad-Fenster", "camera_id": 3, "camera_name": "Wohnzimmer", "room": "Wohnzimmer" | null,
  "enabled": true,
  "kind": "brightness|blink|reference|inventory_count|fill|vlm",
  "region": [[0.62,0.10],[0.80,0.10],[0.80,0.45],[0.62,0.45]],   // Polygon wie Zonen (≥ 3 Punkte); Rechteck = 4 Punkte
  "params": { "threshold": 0.55 },   // je Art, siehe unten
  "state_set": "binary|level",       // binary: on|off|unknown; level: ok|low|out|unknown
  "supply": false,                   // Vorrat (nur level): low/out → Einkaufsliste (shopping_list_auto)
  "verify_vlm": false,               // billige Art liefert Kandidaten, VLM bestätigt den Wechsel (A2 nötig)
  "interval_s": 10,                  // 2..3600 (vlm: ≥ 60)
  "vlm_max_age_s": 900,              // 60..86400 VLM höchstens so oft bei unverändertem Bild
  "change_threshold": 0.08,          // 0..1  Änderungsmaß, ab dem das Bild als verändert gilt
  "confirm_n": 2,                    // 1..10 gleiche Messungen in Folge für einen Zustandswechsel (Hysterese)
  "state": "on|off|ok|low|out|unknown", "value": 0.71 | null, "since": "...", "last_eval": "...",
  "last_engine": "cheap|local|cloud" | null, "snapshot_url": "/media/…" | null,
  "state_text": "an seit 14:02" }
```
| `kind` | `state_set` | `params` (Defaults) | Auswertung |
|---|---|---|---|
| `brightness` | binary | `{threshold: 0.5, invert: false}` | Worker: mittlere Helligkeit 0..1 im Bereich; on wenn ≥ threshold (invert: ≤) |
| `blink` | binary | `{min_std: 0.05, window_s: 3}` | Worker: Std.-Abw. der Helligkeit über `window_s`; on wenn ≥ min_std |
| `reference` | binary | `{}` | Worker: DINOv2-Ähnlichkeit zu Referenzen `on`/`off` (je ≤ 5); value = P(on) |
| `inventory_count` | binary oder level | `{labels: [..] (≥ 1), min_count: 1}` | Backend: Gegenstände `present` mit Mittelpunkt im Bereich; binary on ⇔ count ≥ min_count; level ok ≥ min_count, low 1..min_count−1, out 0 |
| `fill` | level | `{low_below: 0.4, out_below: 0.15}` | Worker: DINOv2-Ähnlichkeit zu Referenzen `full`/`half`/`empty` → Füllstand 0..1 |
| `vlm` | binary | `{question: str 1..300}` | Backend über A2 (`source:"sensor"`); yes → on, no → off, sonst unknown |

- Personen im Bereich (`persons` des Frame-Taps, Überlappung > 10 %) → `occluded:true`, Messung zählt nicht.
- REST (Präfix `/api`): `GET /sensors?camera_id=&room=&kind=&supply=&signed=` → `VirtualSensor[]`;
  `POST /sensors` (SensorIn = VirtualSensor ohne Zustandsfelder) → 201; `GET/PUT/DELETE /sensors/{id}`;
  `POST /sensors/{id}/evaluate` (write) → VirtualSensor; `GET /sensors/{id}/history?limit=50` (1..500) →
  `[{ts, state, value, engine, snapshot_url}]` neueste zuerst; `GET /sensors/{id}/references` →
  `[{id, state, image_url, created_at}]`; `POST /sensors/{id}/references {state}` (write; aktuelles Bild, Ausschnitt
  des Bereichs; max. 5 je Zustand → 422) → SensorReference; `DELETE /sensors/{id}/references/{rid}` → 204.
- Einkaufsliste: `GET /shopping-list?done=false|true|all` (Default `false`) → `{items: ShoppingItem[], open_count}`;
  `POST /shopping-list {name: 1..100, note?: ≤ 300}` (write; offener Eintrag gleichen Namens → derselbe, 200 statt
  201); `PATCH /shopping-list/{id} {done?, name?, note?}`; `DELETE /shopping-list/{id}` → 204.
  `ShoppingItem = {id, name, note, source: "auto|manual|mcp", sensor_id, created_at, done_at, done}`.
- WS: `{"type":"sensor","sensor":VirtualSensor}` (Anlegen/Ändern/Zustandswechsel; Löschen:
  `{"type":"sensor","sensor":{"id","deleted":true}}`), `{"type":"shopping_list","action":"new|update|delete","item":ShoppingItem}`.
- MQTT intern: Worker → `vg/internal/sensor/{camera_id}` (QoS 0, je Messung, mit `worker_id`):
  `{camera_id, ts, frame_ts, values: [{sensor_id, value, change, occluded, scores: {on,off}|{full,half,empty}|null}]}`
  – nur Arten `brightness|blink|reference|fill`. `GET /api/internal/cameras` je Kamera zusätzlich
  `sensors: [{id, kind, region, params, interval_s, change_threshold, references: [{id, state, path}]}]` (nur
  aktive Sensoren dieser Arten). `vg/internal/system.perf.sensors = {per_min, ms}`.
- MQTT extern: `{prefix}/sensor/{sensor_slug}` → `on|off|ok|low|out|unknown` (retain),
  `{prefix}/sensor/{sensor_slug}/attributes` → VirtualSensor (signierte URLs, retain);
  `{prefix}/shopping_list` → `{count, items: [name]}` (retain). `sensor_slug` = `util.slugify(name)`, bei
  Gleichstand mit Suffix `_{id}`. `shopping_list` ist reservierter Slug.
- Hinweise: Vorrats-Sensoren auf `low`/`out` → Notification `source:"sensor"`, `level:"info"`, Ziele
  `notify_routes.sensor`, `dedup_key = "sensor:{id}:{state}:{YYYY-MM-DD}"`.
- **Regeln:** Trigger `sensor` (ohne Track → **kein Event**, Notification + Aktionen wie bei `time`)
  mit `trigger_options = {sensor_id: int, state: "on|off|ok|low|out|any"}`; Bedingung
  `{type: "sensor", sensor_id: int, state: "on|off|ok|low|out|unknown"}` (für alle Trigger). Neue Platzhalter
  `{sensor}` (Name), `{sensor_state}` (deutsch: an/aus/ok/knapp/leer/unklar). `snapshot`/`clip` in Sensor-Regeln:
  Snapshot über `cmd` ohne Event (A3), Clip übersprungen.
- Aufbewahrung: `sensor_history` nach `retention_days_sensors`; erledigte Einkaufseinträge nach 30 Tagen.
- Ergänzungen der Umsetzung (A3+C4, additiv):
  - `GET /api/internal/cameras.sensors` enthält zusätzlich aktive `vlm`-Sensoren; der Worker meldet für sie nur
    `change`/`occluded` (`value: null`). `change` = mittlere Absolutdifferenz (0..1, 32×32-Graubild des Bereichs)
    zu einem Ankerbild; ab `change_threshold` meldet der Worker sie einmal und setzt den Anker neu. Ein
    `vlm`-Sensor fragt das Bildmodell nur, wenn sich der Bereich seit der letzten Frage verändert hat, ein Wechsel
    noch bestätigt werden muss oder `vlm_max_age_s` abgelaufen ist (sonst höchstens alle `interval_s` prüfen).
  - `verify_vlm` (nur `brightness|blink|reference`) braucht `params.question`; widerspricht das Bildmodell,
    bleibt der Zustand, bis sich das Bild ändert oder `vlm_max_age_s` vergeht. Ist das Bildmodell nicht
    verfügbar, gilt der billige Befund (`last_engine: "cheap"`).
  - Referenzbilder (`POST /sensors/{id}/references`) speichern das ganze aktuelle Kamerabild
    (`sensors/ref/sensor{id}_{state}_{ms}.jpg`); der Worker skaliert es auf die Detect-Auflösung und schneidet den
    Bereich selbst aus. `sensor_references.embedding` bleibt `NULL`.
  - `fill`: Ähnlichkeit je Stufe = 0,5 × DINOv2-Kosinus + 0,5 × Farbähnlichkeit einer 16×16-Miniatur (DINOv2 allein
    trennte gleichförmige Flächen nicht: „halb“ lag näher an „voll“); `value` = Softmax-gewichtetes Mittel (T 0,02)
    der Stufen 1/0,5/0, mindestens zwei Stufen nötig. `reference`: P(an) = Sigmoid((cos_on − cos_off)/0,02).
  - `POST /sensors/{id}/evaluate` bewertet sofort **ohne** Hysterese (letzte Worker-Messung, neu zählen bzw.
    Bildmodell fragen).
  - `unknown` (deaktiviert, Kamera aus, keine Messwerte seit `max(5 × interval_s, 120 s)`, Referenzen fehlen,
    Gegenstandserkennung ruht) startet keine Regeln; Regeln laufen nur bei Wechseln zwischen bekannten Zuständen.
  - Standbild beim bestätigten Wechsel über go2rtc (`sensors/YYYY-MM-DD/sensor{id}_{unix}.jpg`, höchstens 1×/min
    je Sensor); es ist der Snapshot von Sensor-Regeln (Aktion `snapshot`).
  - Sensoren gelöschter Kameras entfernt das Backend selbst (Prüfung jede Minute).
- Ergänzungen der Integration (Welle 3, additiv):
  - Periodische Fragen von `vlm`-Sensoren laufen nur in einem gemeinsamen Fenster von 60 s, das sich höchstens alle
    `VG_SENSOR_VLM_GAP_S` Sekunden öffnet (Backend-Env, Standard 900, min. 60). Grund: Jede Frage leiht die GPU
    (Inventar und Zeitmaschine pausieren) und verlängert die Lease; ein einzelner Sensor mit kurzem `vlm_max_age_s`
    hielt das Bildmodell im Test sonst ~70 % der Zeit geladen. „Jetzt prüfen“ (`evaluate`) und `verify_vlm` sind
    ausgenommen.
  - `GET /api/system` liefert zusätzlich `sensors: {vlm_calls, vlm_skipped, vlm_gated, commits, sensors}` (Zähler
    seit Backend-Start).

### W3.G Zeitmaschine (B1)

- Kamerafelder `history_enabled`, `history_interval_s`, `history_tiling` (REST und `GET /api/internal/cameras`).
- Modell `siglip2-b16-256` = open_clip `ViT-B-16-SigLIP2-256`/`webli` (`open_clip_torch==3.2.0`, eigene
  Dockerfile-Schicht, Gewichte nach `/models/history/`): Bildturm fp16 auf der GPU (entladbar per Lease),
  Textturm auf der CPU. 768-d, L2-normiert.
- MQTT intern: Worker → `vg/internal/history/{camera_id}` (QoS 1, mit `worker_id`):
  `{camera_id, ts, model, dim, thumb_path|null, extend: bool, vectors: [{tile, bbox:[x1,y1,x2,y2], vec_b64}]}`
  (`vec_b64` = float16 little-endian, L2-normiert; `extend:true` → nur `last_ts` der letzten Zeile verlängern,
  `vectors` leer). `vg/internal/system`: `models.history`, `perf.history = {per_min, ms, skipped}`.
- **Textkodierung über `vg/internal/embed`** (ersetzt `history/encode`): Request zusätzlich
  `model: "dinov2"|"siglip2"` (Default `dinov2` für item/image, `siglip2` für text);
  `{kind:"text", text:"roter Karton", model:"siglip2"}` → `embed_result {embedding:[768], dim:768,
  model:"ViT-B-16-SigLIP2-256"}`. `kind:"image"` mit `model:"siglip2"` = Bild-Suche (optional). Backend:
  `embed_client.request("text", text=…, model="siglip2", timeout=5)` – Timeout → 503 „Textsuche derzeit nicht verfügbar“.
- REST: `GET /history/search?q=&camera_id=&room=&since=&until=&limit=10&signed=` →
  `{query, model, items: [HistoryEpisode]}`; `HistoryEpisode = {camera_id, camera_name, room, start_ts, end_ts,
  best_ts, score (0..100), tile_bbox, thumb_url, frames}` (Treffer derselben Kamera mit Abstand ≤ 2 × Intervall);
  `GET /history/frames?camera_id=&around=&span_min=10` → `{items: [{ts, last_ts, thumb_url}]}`;
  `GET /history/stats` → `{enabled: bool, frames, vectors, thumb_bytes, oldest, by_camera: {id: n}}`;
  `DELETE /history?camera_id=&before=` (write) → `{deleted}`. Ohne pgvector: Suche/Frames 503
  „Zeitmaschine nicht verfügbar (Datenbank ohne pgvector)“, Stats `enabled:false`.
- Medien `history/YYYY-MM-DD/cam{N}_{unix}.jpg` (256×144), Aufbewahrung `retention_days_history`.
- Ergänzungen der Umsetzung (B1, additiv):
  - Worker: Bildquelle ist der Frame-Tap (Detect-Frame, ≤ `VG_MAX_FRAME_WIDTH`), Kacheln 1..4 = 2×2 mit 10 %
    Überlappung. Dedupe über die größte Änderung aller Kacheln; spätestens alle 30 min wird trotzdem ein neues Bild
    gespeichert. `model` in `vg/internal/history` und `history_frames.model` = Setting-Schlüssel
    (`siglip2-b16-256` | `mobileclip2-s0`); gesucht wird nur in Zeilen des aktuellen `history_model`.
    `mobileclip2-s0` (512-d) wird mit Nullen auf 768 aufgefüllt. Nachricht zusätzlich `change` (größte Änderung).
    `perf.history` zusätzlich `stored_per_min`, `cameras`. `embed` mit `model:"siglip2"` lädt das Modell bei Bedarf
    (≈ 5 s); bis dahin `ok:false, error:"Modell lädt noch …"` – das Backend wiederholt bis 12 s.
  - Suche: Anfrage + englische Fassung (`backend/app/history_query.py`, Wörterbuch + Inventar-Vokabular/Synonyme),
    je Bild die bessere Ähnlichkeit. Zeiträume nur aus Bildern einer Kamera mit Kosinus ≥ bester Kosinus der Kamera
    − 0,03; Abstand ≤ 2,5 × Intervall (2 × Intervall + Taktschwankung). `score` = Kosinus linear [0; 0,20] → 0..100.
  - Antwort `/history/search` zusätzlich `queries: [str]` (kodierte Texte), `HistoryEpisode.similarity` (roher
    Kosinus); `/history/stats` zusätzlich `model`. `/history/frames` nur Kachel 0, `span_min` 1..240, max. 500.
  - `DELETE /history`: `before` bezieht sich auf `last_ts`; `deleted` = Anzahl Bilder (Kachel 0). Kamera löschen
    entfernt ihre Zeitmaschinen-Bilder.

### W3.H MCP

| Tool | Modul | Parameter | Rückgabe (`structuredContent`) |
|---|---|---|---|
| `ask_camera` | `ask.py` (A2) | `question: str`, `camera_id: int?`, `camera_name: str?`, `region: [4 float]?`, `allow_cloud: bool = False` | `{answer, yes_no, engine, model, ms, camera_id, camera_name, snapshot_url}` + `ImageContent` (Standbild) |
| `list_sensors` | `sensors.py` (A3) | `camera_id: int?`, `room: str?`, `supply: bool?` | `{total, items:[{id, name, kind, camera_name, room, state, state_text, value, since}]}` |
| `get_sensor` | `sensors.py` (A3) | `sensor_id: int?`, `name: str?` | VirtualSensor (kompakt) + `history:[…]` (≤ 20) |
| `get_shopping_list` | `sensors.py` (C4) | `include_done: bool = False` | `{open_count, items:[{id, name, note, source, created_at, done}]}` |
| `add_to_shopping_list` (write) | `sensors.py` (C4) | `name: str`, `note: str?` | ShoppingItem |
| `search_history` | `history.py` (B1) | `query: str`, `since: str?`, `until: str?`, `camera: str?`, `limit: int = 5`, `include_image: bool = False` | `{query, items:[{camera_name, room, start_ts, end_ts, best_ts, score, thumb_url}]}` (+ Bild des besten Treffers) |

`ask_camera` ist ein lesendes Tool (Scope `read`, wie REST), unterliegt aber dem Rate-Limit des Keys.

### W3.I Gemeinsame Bausteine (K3) – Python-APIs

- **Regeln ohne Event** (`backend/app/events.py`): `processor.run_eventless_rule(rule_id, *, trigger, ctx=None,
  camera_id=None, image_path=None, snapshot_path=None, data=None, force=False) -> dict | None` (Bedingungen,
  Cooldown, Notification/Push, MQTT/Webhook – wie bisher `run_time_rule`, das jetzt darauf aufsetzt) und
  `processor.sensor_changed(sensor: dict, old_state: str | None, new_state: str)` – startet alle Regeln mit
  `trigger:"sensor"`, deren `sensor_id`/`state` passt (asynchron im Bedingungs-Pool). A3 ruft es bei jedem
  bestätigten Zustandswechsel auf. Bedingung `sensor` ist in `rule_conditions.py` ausgewertet (liest
  `virtual_sensors.state`).
- **Rate-Limit mit Scope `read`** (`backend/app/auth.py`): `require_read_limited(bucket: str, per_min: int)` →
  Dependency (Cookie oder Key mit `read`, Zähler je Key bzw. Sitzung, gleitendes 60-s-Fenster); Liste der
  POST-Ausnahmen mit `read`: `auth.READ_POST_EXCEPTIONS` (`/api/media/sign`, `/api/cameras/{id}/ask`).
- **LLM-Client mit Bildern** (`backend/app/llm_client.py`): `complete(messages, *, images: list[bytes] | None = None,
  model: str | None = None, …)` – Bilder (JPEG/PNG) hängen an der letzten `user`-Nachricht
  (`openai_compat`: `image_url` als data-URL, `anthropic`: `image`-Block base64); `model` überschreibt `llm_model`.
- **pgvector** (`backend/app/db.py`, `vector.py`): `vector_available()`, `vector_version()`, `HalfVec(dim)`.
- **MQTT-Handler** (`mqtt_bus.register_handler`): vorbelegt `sensor` → `sensors.handle_worker_values` (A3),
  `history` → `history.handle_worker_frame` (B1); `gpu` wird vom Backend nicht ausgewertet.
- **Platzhalter-Module mit fertiger Verdrahtung** (vom Fach-Agenten zu füllen, nicht umbenennen):
  Backend `vlm.py`, `cloud_vlm.py`, `routers/ask.py` (A2); `sensors.py`, `shopping.py`, `routers/sensors.py`
  (A3+C4, inkl. `/shopping-list`); `history.py`, `routers/history.py` (B1); Ticks `sensors.tick()`,
  `history.tick()` (Backend-Ticker jede Sekunde, selbst gedrosselt), Aufbewahrung `retention.py` ruft
  `vlm.retention(now)`, `sensors.retention(now)`, `history.retention(now)`. Worker `worker/vg_worker/sensors.py`
  (`SensorEngine`, Frame-Tap-Abnehmer), `worker/vg_worker/history.py` (`HistoryEngine`, entladbar per Lease); MCP
  `mcp/tools/{ask,sensors,history}.py`.
- **Oberfläche:** Seiten `/zeitmaschine` (`pages/History.tsx`, B1), `/vorraete` (`pages/Supplies.tsx`, Vorräte +
  Einkaufsliste, C4); Admin `/admin/sensoren` (`admin/Sensors.tsx`, A3); Einstellungs-Karten `admin/VlmSettings.tsx`
  (A2), `admin/HistorySettings.tsx` (B1), `admin/SensorSettings.tsx` (A3+C4); Komponente
  `components/AskCamera.tsx` („Frag die Kamera“-Dialog in der Live-Ansicht, A2). Kamera-Formular: Schalter
  `vlm_cloud_allowed` und Abschnitt „Zeitmaschine“ legt K3 an. Typen in `types.ts`: `AskEngine`, `AskYesNo`,
  `AskInput`, `AskResult`, `AskList`, `SensorKind`, `SensorStateSet`, `SensorState`, `VirtualSensor`, `SensorInput`,
  `SensorHistoryEntry`, `SensorReference`, `SensorMsg`, `ShoppingItem`, `ShoppingList`, `ShoppingListMsg`,
  `HistoryEpisode`, `HistorySearch`, `HistoryFrameThumb`, `HistoryStats`, `GpuLease`, `VlmModel`; API in
  `lib/api.ts`: `askCamera`, `askLog`, `deleteAsk`, `sensors`, `sensor`, `createSensor`, `updateSensor`,
  `deleteSensor`, `evaluateSensor`, `sensorHistory`, `sensorReferences`, `addSensorReference`,
  `deleteSensorReference`, `shoppingList`, `addShoppingItem`, `updateShoppingItem`, `deleteShoppingItem`,
  `historySearch`, `historyFrames`, `historyStats`, `deleteHistory`; Mock-Routen in `lib/mock.ts`.

## Nachträge: Welle 4 – C2 Person über mehrere Kameras (ReID, Anwesenheit)

Verbindlich wie der Rest, rein additiv. Grundlage: `docs/plan/roadmap/c1-d1-c2-pose-gesten-reid.md` (C2),
`00-wellenplan.md` (W4) und die Messung `reidtest` (`messungen.md` §11). Für Welle 4 gab es keinen Kickoff; diese
Ergänzungen hat der Fach-Agent C2 minimal angelegt.

### W4.A Datenbank und Settings

| Tabelle | Spalte | Typ / Default |
|---|---|---|
| `cameras` | `reid_enabled` | boolean NOT NULL DEFAULT false |

Keine neue Tabelle: **Embeddings und Galerie existieren nur im RAM** (Worker: bis zum HTTP-Aufruf, Backend:
`app/reid.py`). Nichts davon geht in die DB, auf die Platte, ins Log oder über MQTT.
Settings (`GET/PUT /settings`): `reid_ttl_min` 60 (5..720), `reid_match_threshold` 0.70 (0.3..0.95),
`reid_assign_identity` true (Identität über ReID an Events weitergeben, ab Schwelle + 0,1).
Camera/`CameraIn`/`GET /api/internal/cameras`: zusätzlich `reid_enabled` (PUT ohne das Feld lässt es unverändert).

### W4.B Worker → Backend

- Nur Kameras mit `reid_enabled`. Je bestätigtem Personen-Track höchstens alle 3 s und höchstens 5 Embeddings;
  nur wenn die Box im Detect-Bild ≥ 128 px hoch, höher als breit, nicht am Rand abgeschnitten, nicht `lying` und
  nicht zu > 25 % von einer anderen Person überdeckt ist. Modell OSNet x0_25 (MSMT17, ONNX, 512-d, L2-normiert),
  Standard auf der **CPU** (`VG_REID_DEVICE=cuda` optional). `models.reid` in `vg/internal/system`
  (`off|loading|ready (…)|error: …`), `perf.reid = {embeds_per_min, runs_per_min, ms, skipped, cameras, sent,
  linked, new, errors}`. Bei einer GPU-Lease rechnet ReID (falls `cuda`) auf der CPU weiter.
- `POST /api/internal/reid {camera_id, track_id, embedding:[512 float], quality, ts}` →
  `{presence_key, identity_id|null, score, is_new, identity:{id,display_name,name,kind}|null,
  identity_source:"reid"|"face"|null}`. `identity` ist nur gesetzt, wenn die Identität über ReID weitergegeben wird
  (`score ≥ reid_match_threshold + 0,1`, `reid_assign_identity`, Eintrag mit Gesichts-Identität, Track ohne eigene).
- Der Worker setzt daraufhin am Track `attributes.presence_key` und – nur ohne eigene Identität – `identity` mit
  `attributes.identity_source:"reid"`. Erkennt der Worker später selbst ein Gesicht, gilt dieses
  (`identity_source` wird `"face"`). Track-`update` wie bei jeder Attributänderung; Events übernehmen
  `attributes.presence_key`/`identity_source` und die Identität wie gewohnt.

### W4.C Galerie und Abgleich (Backend, `app/reid.py`)

- Einträge `presence_key` (`"p-" + 10 Hex`, zufällig) mit ≤ 10 Embeddings, verknüpften Tracks, Identität
  (nur aus Gesicht/Tier, nie aus ReID), letzter Kamera. Track-Schritte kommen über `events.on_track` für die Labels
  `person`, `dog`, `cat` (auch ohne ReID: Eintrag je Identität bzw. je Track).
- Abgleich: Kosinus, Maximum über die Embeddings eines Eintrags. Kandidaten nur ohne **gleichzeitig aktiven anderen
  Track** und ohne widersprechende Identität; verknüpft ab `reid_match_threshold`. Verknüpft wird nur, solange der
  Track allein in seinem Eintrag steht (danach kommen weitere Embeddings nur hinzu).
- Gesicht hat Vorrang: Tracks mit eigener Identität (Gesicht/Tier) werden nie per ReID verknüpft, sie gehören zum
  Eintrag ihrer Identität. Bekommt ein per ReID verknüpfter Track nachträglich eine eigene Identität, wird er samt
  Embeddings in den Eintrag dieser Identität verschoben (der bisherige Eintrag wird **nicht** benannt) – außer die
  Verknüpfung lag bei ≥ `reid_match_threshold + 0,1`, dann bekommt der Eintrag die Identität. Gründet ein
  Track mit eigener Identität einen Eintrag, trägt der Eintrag diese Identität; dieselbe Identität in zwei Einträgen
  → zusammenführen; widersprechende Identität → Track wird herausgelöst.
- Umgekehrte Richtung: Ein Track mit eigener Identität übernimmt einen **anonymen** Eintrag ohne aktiven Track, wenn
  sein Embedding ab `reid_match_threshold + 0,1` passt (z. B. Person zuerst von hinten gesehen, dann mit Gesicht).
- Verfall: Einträge ohne aktiven Track `reid_ttl_min` nach dem letzten Sehen (samt Embeddings). Embeddings von
  Kameras, deren `reid_enabled` abgeschaltet wurde, werden binnen 5 s verworfen. Backend-Neustart = leere Galerie.
  `presence_key` in alten Events ist danach nur noch eine Kennung ohne Bezug.

### W4.D REST, WebSocket, MCP

```jsonc
// Presence (GET /api/presence?minutes=60&label=person,dog,cat → Presence[], aktive zuerst, dann last_seen absteigend)
{ "presence_key": "p-1a2b3c4d5e", "label": "person",
  "identity": { "id": 12, "kind": "face", "display_name": "Anna", "name": "Anna" } | null,
  "source": "face|pet|reid|none",      // face/pet = eigene Erkennung des aktuellen Tracks, reid = über Kleidung
  "confidence": 0.83,                  // reid: Score der Verknüpfung; face/pet: 1
  "camera_id": 3, "camera_name": "Wohnzimmer", "room": "Wohnzimmer", "zones": ["z1"], "zone_names": ["Sofa"],
  "active": true, "since": "...", "last_seen": "...", "event_id": "uuid" | null,
  "thumb_url": "/media/crops/…jpg" | null,    // signiert mit ?signed=1
  "trail": [ { "camera_id", "camera_name", "since", "last_seen", "active", "event_id", "score": 0.81 | null,
               "via": "face|reid|track" } ] }
```
- `GET /api/presence/status` → `{entries, active, embeddings, requests, linked, new, adopted, identity_given,
  merged, expired}` (nur Zähler). `DELETE /api/presence` → 204, leert die Galerie sofort (Scope `write`).
- WS: `{"type":"presence","presence":Presence}` bei neuem Eintrag, Kamera-/Zonen-/Identitätswechsel, Track-Ende
  (je Eintrag höchstens alle 2 s, Start/Ende sofort); `{"type":"presence","presence":{"presence_key","deleted":true
  [,"merged_into"]}}` bei Verfall/Zusammenführung.
- MCP `who_is_where(minutes=60)` liest `/presence?signed=1`: `{people:[{display_name, identity_id, kind, label,
  presence_key, source, confidence, active, camera_id, camera_name, room, zones, since, last_seen, thumb_url}]}`,
  Text mit „gerade sichtbar“ und „zuletzt gesehen vor … in …“.
- Oberfläche: Seite `/anwesenheit` (`pages/Presence.tsx`), Kamera-Dialog „Personenanalyse“ → Schalter
  „Wiedererkennung über Kameras“, Einstellungs-Karte `admin/ReidSettings.tsx`.

## Nachträge: Welle 4 – C4-VLM-Modus (Vorräte per Bildmodell)

Verbindlich wie der Rest, rein additiv (keine neuen Spalten, Tabellen oder Settings). Grundlage:
`docs/plan/roadmap/00-wellenplan.md` (W4 „VLM-Modus für C4 als S-Auftrag“), Messung `messungen.md` §12.

- **Sensor-Art `vlm`, `params.measure`** (`"yesno"` Default | `"count"` | `"fill"`). Fehlt `measure`, gilt `yesno`
  (bisheriges Verhalten, binär).
  - `count`: `params = {measure, item?: str ≤ 80 (englisch, z. B. "bottles"), question?: str ≤ 300,
    min_count: 1..3}` – `item` oder `question` ist Pflicht. Frage = `question` oder
    „How many {item} are there? Answer with a number only.“; `value` = gelesene Anzahl (Ziffern, Zahlwörter
    englisch/deutsch, „no …“ = 0). `state_set` `binary` (on ⇔ Anzahl ≥ `min_count`) oder `level`
    (ok ≥ `min_count`, low 1..`min_count`−1, out 0) wie `inventory_count`. `min_count` > 3 → 422 (Modell zählt
    größere Mengen zu niedrig).
  - `fill`: `params = {measure, item?, question?}`; Frage = `question` oder „How full is the {item}? Answer with one
    word: full, half, low or empty.“; `value` 0..1 (full 1, almost full 0,8, half 0,5, quarter 0,25, low 0,2, almost
    empty 0,15, empty 0, Prozentangaben direkt). `state_set` immer `level`, Zustand **nur `ok` oder `out`**
    (`out` ⇔ `value` < 0,15) – „knapp“ meldet diese Messart nie (eingeschränkte Variante, §12).
  - Für `count`/`fill` gilt `vlm_max_age_s` ≥ 1800 (sonst 422); `supply: true` erlaubt (Einkaufsliste, Hinweise
    wie bei anderen Vorräten). Periodische Fragen laufen wie alle `vlm`-Sensoren im gemeinsamen Fenster
    (`VG_SENSOR_VLM_GAP_S`), sichtbare Änderung (`change`) oder Verdeckung durch Personen wie bisher.
  - Antwort nicht lesbar → keine Messung, Zustand bleibt; `state_text` zeigt „(letzte Prüfung: Antwort unklar …)“.
- **`verify_vlm` auch für `inventory_count`** (bisher nur `brightness|blink|reference`): `params.item` (Default aus
  dem Vokabular: erster Prompt des ersten Labels + „s“) bzw. `params.question`; nur mit `min_count` ≤ 3 (sonst 422).
  Ablauf: Kandidat nach der Hysterese → Zählfrage an das Bildmodell (höchstens 1× je 5 min und Sensor) →
  gleicher Zustand: übernehmen; bisheriger Zustand: Wechsel verneint (Sperre bis `vlm_max_age_s`); dritter Zustand
  (z. B. Kandidat `low`, Modell zählt 0): Zustand des Bildmodells übernehmen. Modell nicht verfügbar → billiger
  Befund (`last_engine: "cheap"`). `verify_vlm` für `fill` bleibt 422.
- `POST /sensors/{id}/evaluate` bei `vlm` und wirksamem `verify_vlm`: Zustand des Bildmodells wird direkt (ohne
  Hysterese) übernommen; bei `inventory_count` ist `value` dann die Zahl des Bildmodells.
- **VirtualSensor zusätzlich** `last_answer: str | null` – letzte Antwort des Bildmodells (≤ 300 Zeichen, nur im
  Speicher des Backends, nach Neustart `null`).
- **MCP** `list_sensors`/`get_sensor`: je Sensor zusätzlich `measure` (`yesno|count|fill`, bei `inventory_count`
  `count`, bei `fill` `fill`, sonst `null`) und `last_answer`; Text mit Stückzahl bzw. letzter Antwort.
- Umsetzung: `backend/app/vlm_measure.py` (Fragen, Antworten lesen; ohne Abhängigkeiten), `sensors.py`
  (`vlm_measure_of`, `vlm_question`, `verify_enabled`, `_ask_vlm`), `routers/sensors.py` (Prüfungen), Oberfläche
  `admin/Sensors.tsx` (Messart Ja/Nein · Anzahl · leer/nicht leer, Nachzählen), Typ `VlmMeasure` in `types.ts`.

## Nachträge: Refinement 1 – Robustheit, Sicherheit, Bedienung

Verbindlich wie der Rest; wo es älteren Aussagen widerspricht, gilt dieser Abschnitt.

**MQTT-Broker (`mosquitto`)**
- Zwei Listener (`per_listener_settings true`): **1883** nur im Docker-Netz (anonym, voller Zugriff – Backend,
  Worker, vlm, Selbsttests per `docker compose exec/run`) und **1884** für außen (Host-Port `MQTT_PORT` → 1884,
  anonym, ACL `mosquitto/acl-extern.conf`: alles **lesen**, nichts schreiben). Damit sind steuernde Topics
  (`vg/internal/gpu/request`, `cmd`, `config`) und gefälschte Inventar-/Sensor-Nachrichten von außen nicht möglich.
  Image per Digest gepinnt.
- Beim Löschen einer Kamera leert das Backend die retained Topics der Kamera: sofort `{prefix}/{slug}/{label}` (alle
  Labels der Kamera), nach 10 s (nach dem einmaligen `offline` des Workers) `vg/internal/status|health|inventory/{id}`.
  Retained Nachrichten auf diesen drei internen Topics für unbekannte Kamera-IDs löscht das Backend beim Verbinden.

**GPU-Lease (Ergänzung zu W3.C)**
- Der Worker sendet beim ersten MQTT-Connect nach dem Start `vg/internal/gpu/released`
  `{request_id:"*", holder:"*", reason:"restart"}`. Jeder Lease-Client verwirft daraufhin eine gehaltene Lease
  (vlm entlädt sein Modell) – auch nach Absturz/`docker kill` des alten Workers.

**REST / Auth**
- `/api/docs` und `/api/openapi.json` verlangen Anmeldung (Cookie oder API-Key, Scope `read`); Env
  `VG_PUBLIC_DOCS=1` (Backend) schaltet auf das alte, offene Verhalten zurück.
- `PUT /settings` mit API-Key: die Felder `push_targets`, `notify_routes`, `llm_provider`, `llm_base_url`,
  `weather_location`, `report_channels`, `public_base_url` → **403** „Nur mit Anmeldung im Browser änderbar: …“.
  `GET/PUT /settings` mit API-Key liefert `push_targets`, `weather_location`, `llm_base_url` und `report_channels`
  nicht mit.
- Push-Ziele: `topic` gehört zu den maskierten Feldern (`token`, `topic`, `user_key`, `app_token` → `"***"`).
  `"***"` wird nur durch den gespeicherten Wert ersetzt, wenn `id`, `type` **und** `server` (ohne `/` am Ende, ohne
  Groß-/Kleinschreibung) unverändert sind; sonst **422** „Server oder Art des Push-Ziels geändert – Zugangsdaten …
  bitte neu eingeben“ (gilt für `PUT /settings` und `POST /settings/push_test`).
- `POST /cameras/{id}/ask`: zusätzlich global höchstens 20 Fragen/min (alle Aufrufer) und über API-Keys/MCP global
  höchstens 12 Fragen je Stunde (429 mit `Retry-After`) – jede lokale Frage pausiert das Inventar bis zu
  `vlm_idle_unload_s` + 60 s.
- `GET /reports/preview?template=1`: nur Satzbausteine, nie ein Sprachmodell (MCP `get_daily_report` nutzt das für
  die Vorschau; vorher Zeitüberschreitung nach 20 s und ggf. bezahlte Wiederholungen).

**Events / Regeln über Neustarts**
- Beim Start werden aktive Events wie bisher beendet, aber gemerkt: meldet der Worker in den ersten 120 s ein
  `update` für denselben (Kamera, Track), wird **dasselbe Event** fortgesetzt (`active` wieder `true`, `end_ts`
  null); Regeln aus `event.rules` gelten als bereits ausgelöst (kein zweiter Alarm). Offene Wiederholungen (C1)
  stehen in `kv.rule_repeats` und laufen weiter (Quittierung wirkt wie gewohnt). Geplante verzögerte
  Bedingungsprüfungen (`condition_delay_s`) stehen in `kv.rule_cond_delayed`; fallen sie in einen Neustart, werden
  sie nicht nachgeholt, sondern als Warnung geloggt und als Hinweis (ohne Push) angezeigt.
- Tagesbericht: schlägt die Erzeugung fehl, wird sie nach 5, 15 und 60 min erneut versucht, danach nicht mehr
  automatisch (vorher alle 20 s bis zum Ende des Nachhol-Fensters).
- Rückfragen der Art `inventory_label`: je Label höchstens eine pro 24 h (DB-geprüft, übersteht Neustarts).

**Inventar / Aufbewahrung**
- Worker: Übersichtsbild (`snapshot_path`) höchstens alle 5 min je Kamera, außer ein Gegenstand erscheint, der in
  dieser Session noch auf keinem Übersichtsbild war (dann höchstens 1/min); Breite höchstens 1280 px (vorher 1920).
- Aufbewahrung: Übersichtsbilder `inventory/*/cam*.jpg`, auf die nichts zeigt (Gegenstand, Historie, offene
  Rückfrage, Beispiel, Hinweis), werden nach 2 Tagen gelöscht (übrige Inventar-Dateien wie bisher nach
  `retention_days_inventory`). Abgeschlossene Rückfragen (`yes|no|skipped|expired`) älter als
  `retention_days_inventory` werden gelöscht; `inventory_examples` je (Label, Kamera) auf die neuesten 200 begrenzt.
- `cleanup_orphans` löscht nie Identitäten der Art `item` (eigene Exemplare).

**go2rtc**
- `log.streams: error` – die Warnungen des Moduls `streams` enthalten die Quell-URL samt Zugangsdaten.
- `DELETE /api/streams` antwortet wegen der read-only eingebundenen `go2rtc.yaml` mit 400 „read-only file
  system“, obwohl der Stream gelöscht ist; das Backend wertet das als Erfolg.

## Nachträge: PTZ – Schwenken, Neigen, Zoomen (ONVIF)

Verbindlich wie der Rest. Steuerung herstellerneutral über **ONVIF Profile S (PTZ-Dienst)**; getestet mit Reolink
E1 Pro (ONVIF-Port 8000, nur `ContinuousMove`, Presets, `GetStatus` ohne Position). Nur das Backend spricht mit der
Kamera (es kennt die Zugangsdaten); Worker und Frontend erfahren nur den Zustand.

### PTZ.A Datenbank und Kamera-Felder

| Tabelle | Spalte | Typ / Default |
|---|---|---|
| `cameras` | `ptz_enabled` | boolean NOT NULL DEFAULT false |
| `cameras` | `ptz` | jsonb NOT NULL DEFAULT '{}' (PtzConfig) |

```jsonc
// Camera (REST, zusätzlich; beide Felder bei PUT optional wie room/inventory_*)
{ ..., "ptz_enabled": false,
  "ptz": {                                  // PtzConfig – fehlende Schlüssel = Default
    "onvif_port": null,                     // null = automatisch (80, 8000, 2020, 8899, 8080), sonst 1..65535
    "onvif_user": null,                     // null = Benutzer/Passwort aus der Kamera-URL
    "onvif_password": null,                 // in Antworten "***" wenn gesetzt; "***" zurückgeschickt = unverändert
    "profile_token": null,                  // null = erstes Medienprofil mit PTZ-Konfiguration
    "home_preset": null,                    // Preset-Token der Grundposition (null = keine)
    "return_s": 60,                         // 0..3600; Rückkehr zur Grundposition nach Handsteuerung (0 = nie)
    "home_on_start": true,                  // beim Backend-Start/Einschalten einmal zur Grundposition fahren
    "speed": 0.5,                           // 0.1..1 Geschwindigkeit der Handsteuerung
    "invert_pan": false, "invert_tilt": false,
    "patrol": { "enabled": false,
                "steps": [ { "preset": "1", "dwell_s": 20 } ],   // 1..32 Schritte, dwell_s 3..3600
                "schedule": null,           // wie Rule.schedule; null = immer
                "pause_s": 0 },             // 0..86400 Pause in der Grundposition zwischen zwei Runden
    "follow": { "enabled": false,
                "labels": ["person"],       // aus den Detektions-Labels der Kamera
                "schedule": null,
                "lost_s": 8,                // 2..120 ohne Ziel → zurück zur Grundposition
                "max_s": 300,               // 10..3600 längste Verfolgung, danach 60 s Ruhe
                "speed": 0.5,               // 0.1..1 (viele Kameras ignorieren den Wert, z. B. Reolink E1 Pro)
                "latency_s": 1.4,           // 0.3..5 Bildverzögerung: nach einem Impuls erst Bilder ab Stopp + latency_s auswerten
                "tilt": true } },           // auch neigen (false = nur schwenken)
  "ptz_state": PtzState | null }            // null wenn ptz_enabled false
```
Prüfungen (422, deutsch): `home_preset`/Patrouille-Presets müssen nicht vorab existieren (Kamera evtl. offline),
werden aber beim Ausführen geprüft („Position … gibt es auf der Kamera nicht“).

### PTZ.B Betriebsarten und Sicht

```jsonc
// PtzState
{ "mode": "idle|manual|rule|follow|patrol|error",
  "view": "home|away|unknown",   // home = steht ruhig in der Grundposition (dann gelten Zonen, Gegenstände, Sensoren)
  "moving": false,
  "preset": "1" | null, "preset_name": "Fenster" | null,    // zuletzt angefahrene Position (null nach Handsteuerung)
  "until": "...iso" | null,      // Ende von manual/rule bzw. Verweilen im Patrouillen-Schritt
  "patrol_step": 0 | null, "target": { "track_id": 4711, "label": "person" } | null,
  "paused_until": "...iso" | null,   // Automatik (Patrouille/Verfolgung) pausiert
  "error": "Kamera nicht erreichbar …" | null, "updated_at": "..." }
```
- Vorrang: `manual` > `rule` > `follow` > `patrol` > `idle`. Eine höhere Betriebsart unterbricht eine niedrigere;
  `follow` startet nur aus `idle`/`patrol` (bestätigtes Objekt eines `follow.labels` ≥ 1 s im Bild).
- `manual`: jede Handsteuerung verlängert `until` = jetzt + `return_s` (`return_s` 0 = bleibt stehen, bis
  `resume`); danach Grundposition. Während `manual` laufen Patrouille und Verfolgung nicht.
- `rule`: Regel-Aktion `ptz` (PTZ.D), `until` = jetzt + `hold_s`, danach Grundposition.
- `follow`: Ziel mittig halten: je Schritt **eine** Achse, Impuls 0,25–0,6 s (`ContinuousMove` mit `Timeout` +
  `Stop`), der ≈ 60 % der Abweichung ausgleicht; Totzone 0,12 (waagrecht) / 0,15 (senkrecht); erst wieder
  nachsteuern, wenn eine Detektion mit `ts ≥ Stopp + latency_s` vorliegt. Schrittweite (Bildanteil je s) und
  Richtung je Achse lernt das Backend aus der Verschiebung des Ziels nach (nur im Speicher; zweimal deutlich
  falsche Richtung → Achse umgekehrt, Warnung im Log). Ziel weg > `lost_s` oder Dauer > `max_s` → Grundposition.
- `patrol`: Schritte der Reihe nach (anfahren, ruhig werden, `dwell_s` verweilen), danach Grundposition und
  `pause_s` warten; nur wenn `schedule` aktiv.
- `view`: `home` nur nach einer abgeschlossenen Fahrt zu `home_preset` (Kamera meldet `IDLE` bzw. 6 s ohne
  Statusabfrage, danach 1,5 s Beruhigung) und ohne Handbewegung seitdem; jede Bewegung → `away`. Ohne
  `home_preset` bleibt `view` bis zur ersten Bewegung `home`, danach `away`.
- Kamera nicht erreichbar → `mode: "error"` mit deutscher Meldung, neuer Versuch alle 60 s; die Detektion läuft
  davon unberührt weiter.

### PTZ.C REST (Präfix `/api/cameras/{id}/ptz`; GET Scope `read`, sonst `write`)

- `GET` → `PtzInfo { camera_id, enabled, connected, error, device: {manufacturer, model, firmware} | null,
  capabilities: {continuous, zoom, presets, presets_max, status}, presets: [{token, name}], state: PtzState | null }`
  (Presets max. 30 s gecacht).
- `POST /probe {onvif_port?, onvif_user?, onvif_password?}` → `{ok, port, device, capabilities, presets, error}` –
  auch bei `ptz_enabled=false` (Einrichtung); fehlende Werte aus der gespeicherten Konfiguration bzw. Kamera-URL.
- `POST /move {pan, tilt, zoom (je -1..1, Default 0), duration_ms 50..3000 = 500}` → PtzState. Bewegt mit
  `speed`·Wert, stoppt nach `duration_ms` selbst (Halten in der Oberfläche = wiederholte Aufrufe). Modus `manual`.
- `POST /stop` → PtzState. `POST /goto {preset: str | null (null = Grundposition), hold_s?: 5..3600}` → PtzState
  (`manual`, `until` = jetzt + (`hold_s` oder `return_s`)).
- `POST /presets {name: 1..40}` → `{token, name}` (aktuelle Position speichern); `DELETE /presets/{token}` → 204,
  409 wenn Grundposition oder in der Patrouille benutzt.
- `POST /resume` → beendet `manual`/`rule`/`follow`, fährt zur Grundposition, Automatik läuft wieder.
- `POST /pause {minutes: 1..1440}` → Patrouille/Verfolgung pausieren (`paused_until`); `minutes: 0` hebt auf.
- Fehler: 409 „PTZ ist für diese Kamera nicht eingeschaltet“, 502 mit deutscher Meldung bei Kamerafehlern.

### PTZ.D Regeln, WebSocket, MQTT

- Regel-Aktion `{ "type": "ptz", "camera_id": null | int (null = Kamera des Ereignisses), "preset": null | str
  (null = Grundposition), "hold_s": 30 (5..3600) }`. Wirkt bei Track- und Bedingungs-Regeln; wird ignoriert,
  solange `manual` aktiv ist.
- WS: `{"type":"ptz","camera_id":3,"state":PtzState}` bei jeder Zustandsänderung.
- Intern (Backend → Worker, retain): `vg/internal/ptz/{camera_id}` `{camera_id, view, moving, mode, ts}`; leer
  (gelöscht), wenn PTZ aus ist. Der Worker wertet bei `view != "home"` oder `moving`: **keine Zonen-Wechsel**
  (bestehende Tracks behalten ihre Zonen, neue haben keine), **kein Inventar-Durchlauf** (stattdessen derselbe
  Zustand mit neuem `ts` und `"paused": "ptz"`, nichts wird `missing`), **keine Sensor-Messung**, **keine
  Gesundheitsprüfung**. Detektion, Tracking, Identifikation, Clips und Zeitmaschine laufen weiter.
- Extern: `{prefix}/{camera_slug}/ptz` (retain) `{mode, view, preset_name}`.
- Kamera-Gesundheit meldet bei PTZ-Kameras in der Grundposition „verschoben“ → das Backend fährt (höchstens alle
  10 min) erneut die Grundposition an.

### PTZ.E MCP (Tools mit Schreibwirkung brauchen Scope `write`)

| Tool | Parameter | Rückgabe |
|---|---|---|
| `ptz_status` | `camera_id?`, `camera_name?` | `{cameras:[{id, name, state, presets:[{token,name}], home_preset}]}` (nur PTZ-Kameras) |
| `ptz_move_to` | `camera_id?`/`camera_name?`, `preset: str` (Name oder Token; „home“/„Grundposition“), `hold_s?` | PtzState; danach Bild per `get_camera_snapshot` |
