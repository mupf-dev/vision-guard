# AGENTS.md – Leitfaden für KI-Agenten und Entwickler

Dieses Dokument richtet sich an Coding-Agenten (Claude Code, Codex, Cursor …) und Menschen, die an VisionGuard
arbeiten. Projektüberblick: [README.md](README.md). Verbindliche Schnittstellen: [CONTRACT.md](CONTRACT.md).

## Grundregeln

1. **CONTRACT.md ist die Quelle der Wahrheit** für REST, WebSocket, MQTT-Topics, Datenmodelle, Medienpfade und
   Stream-Namen. Wer eine Schnittstelle ändert, passt zuerst CONTRACT.md an und dann **alle** betroffenen Dienste
   (Backend, Worker, Frontend inkl. `src/lib/types.ts`, `src/lib/api.ts` und Mock `src/lib/mock.ts`).
2. **Sprache:** Oberfläche, Fehlermeldungen an Nutzer und interne Dokumentation (AGENTS.md, CONTRACT.md, `docs/plan/`)
   auf **Deutsch**. Öffentliche Doku – alle `README.md` und die GitHub-Seite (Zweig `gh-pages`) – auf **Englisch**.
   Code-Bezeichner Englisch, Kommentare dürfen Deutsch sein (wie im bestehenden Code).
3. **Koordinaten** sind überall normiert (0..1, x rechts, y unten). Zeit: ISO-8601 UTC in REST, Unix-Sekunden in MQTT.
4. **Keine Daueraufzeichnung.** Video landet nur auf der Platte, wenn eine Regel einen Clip auslöst
   (RAM-Ringpuffer im Worker, tmpfs `/buffer`).
5. **Keine Zugangsdaten herausgeben:** Kamera-URLs werden in der API maskiert (`backend/app/secrets_mask.py`), der
   Worker kennt nur go2rtc-URLs. Keine Passwörter in Logs, Commits oder Testdaten. `.env` ist nicht im Repo.
6. **Abhängigkeiten pinnen.** Neue Pakete mit fester Version in `requirements.txt` bzw. `package.json` (pnpm).

## Wichtige Umgebungs-Einschränkungen

- **CUDA max. 12.4** (Host-Treiber 551.x). Images mit CUDA ≥ 12.5 starten nicht. Worker-Basis:
  `pytorch/pytorch:2.5.1-cuda12.4-cudnn9-runtime`. onnxruntime-gpu muss ein CUDA-12/cuDNN-9-Build sein (1.20.x).
- **Teure Downloads vermeiden:** Große Pakete (z. B. TensorRT) gehören als eigene Schicht ins Dockerfile, nicht in
  `pip install` bei jedem Testlauf. Modelle liegen im Volume `models` und werden nur einmal geladen.
- TensorRT wurde getestet und bewusst **nicht** genutzt (kein Gewinn bei kleinen Batches, +4,6 GB VRAM).
- Ziel: **16 Kameras** à 5 fps auf einer GPU. Neue Modelle immer lazy laden, Fehler einzelner Modelle dürfen die
  Detektion nicht stoppen, Frames verwerfen statt queuen.

## Stack starten und testen

```bash
cp .env.example .env && ./testcam/download-videos.sh   # SECRET_KEY setzen (openssl rand -hex 32), sonst startet das Backend nicht
docker compose up -d --build                     # alles
docker compose up -d --build --no-deps backend   # einzelnen Dienst neu bauen
docker compose logs -f worker
```

In einer WSL-Sandbox ohne systemd muss der Docker-Daemon ggf. manuell gestartet werden:
`sudo bash -c 'nohup dockerd > /tmp/dockerd.log 2>&1 &'`.

| Was | Wie |
|---|---|
| Oberfläche | http://localhost:8080 (admin/admin) |
| API-Doku | http://localhost:8080/api/docs (nur nach Anmeldung im Browser oder mit API-Key; `VG_PUBLIC_DOCS=1` = offen) |
| Backend direkt | `docker compose exec backend python -c "import httpx; ..."` (Port 8000 nur intern) |
| MQTT mitlesen | `docker compose exec mosquitto mosquitto_sub -t 'vg/internal/#' -v -C 20` |
| Frontend ohne Backend | `cd frontend && corepack pnpm install && corepack pnpm dev:mock` (Port 5173) |
| Frontend-Typecheck/Build | `cd frontend && corepack pnpm build` – muss fehlerfrei sein |
| Worker-Selbsttest | `docker compose run --rm --no-deps worker python -m vg_worker.selftest rtsp://testcam:8554/cam1 --seconds 30` |
| Lasttest 16 Kameras | `... selftest rtsp://testcam:8554/cam1 rtsp://testcam:8554/cam2 --replicate 8 --seconds 60` |
| Simulierte Kameras | `rtsp://testcam:8554/cam1` (Personen/Gesichter), `cam2` (Straße: Autos, Fahrräder) |

Es gibt noch keine automatisierten Testsuiten. Änderungen end-to-end prüfen: Stack hochfahren, Ereignisse unter
`/api/events` bzw. in der Oberfläche kontrollieren, bei UI-Änderungen Screenshot ansehen.

## Angepasste Fremdbibliotheken

- `frontend/public/vendor/video-rtc.js` (go2rtc-Player) ist gepatcht (Kommentar `VisionGuard-Patch`): MSE-Puffer wird
  nur deutlich hinter dem Abspielkopf gekürzt, sonst friert das Bild bei Quellen mit langem Keyframe-Abstand
  (YouTube) ein. Bei einem Update des Players den Patch übernehmen.

## Wo liegt was

| Thema | Datei(en) |
|---|---|
| Compose, Volumes, GPU | `docker-compose.yml`, `.env.example` |
| Auth (Cookie/JWT, API-Keys) | `backend/app/auth.py`, `backend/app/routers/auth.py`, `backend/app/routers/tokens.py` |
| Medien-Auslieferung, signierte Links | `backend/app/routers/media.py`, `backend/app/media_sign.py` |
| YouTube-Quellen (yt-dlp) | `backend/app/youtube.py`, Template `youtube` in `go2rtc/go2rtc.yaml` |
| Kameras, go2rtc-Sync | `backend/app/routers/cameras.py`, `backend/app/go2rtc.py`, `backend/app/sync.py` |
| Track → Event, Aktionen | `backend/app/events.py` |
| Regel-Bedingungen, Templates, Webhooks | `backend/app/rules.py` (Registry `register_trigger`/`register_condition`) |
| Bedingungs-Auswertung (Inventar, Wetter, Herkunft) | `backend/app/rule_conditions.py` |
| Hinweise (Notifications), Push (ntfy/Pushover) | `backend/app/notifications.py`, `backend/app/push.py`, `backend/app/routers/notifications.py` |
| Track-Hooks für Fachmodule | `backend/app/events.py::on_track` |
| Kamera-Gesundheit (Backend) | `backend/app/health.py`, `state.set_camera_health` |
| Identitätsabgleich | `backend/app/identity.py` |
| Gesichts-Abgleich/Galerie, Sammelaktionen, Nachtlauf | `backend/app/face_match.py`, `identity_bulk.py`, `nightly.py`, `routers/nightly.py` |
| Messung Gesichts-Identitäten (nur lesend) | `scripts/face_stats.py` |
| Oberfläche Mehrfachauswahl, Gesichtserkennung/Nachtlauf | `frontend/src/pages/IdentityBulk.tsx`, `frontend/src/pages/admin/FaceSettings.tsx` |
| Datenbankmodell | `backend/app/models.py` (`create_all`, keine Migrationen – Spalten nur additiv mit Default ändern) |
| Kamera-Ingest (ffmpeg) | `worker/vg_worker/ingest.py` |
| YOLO-Batching | `worker/vg_worker/detector.py`, `worker/vg_worker/pipeline.py` |
| Tracking, Zonen | `worker/vg_worker/tracking.py` |
| Gesicht/Kennzeichen/Tier | `worker/vg_worker/identify.py` |
| Gesichtsqualität, Track-Sammlung, Vollbild-Leser | `worker/vg_worker/face_quality.py`, `face_track.py`, `fullres.py` |
| Ringpuffer, Clips, Snapshots | `worker/vg_worker/recorder.py` |
| API-Client, Typen | `frontend/src/lib/api.ts`, `frontend/src/lib/types.ts` |
| Live-Socket/Store | `frontend/src/lib/useLiveSocket.ts`, `frontend/src/lib/store.ts` |
| Live-Player + Overlay | `frontend/src/components/LivePlayer.tsx`, `DetectionOverlay.tsx` |
| Zonen-Editor | `frontend/src/components/ZoneEditor.tsx` |
| Admin-Seiten | `frontend/src/pages/admin/` |
| nginx-Routen | `frontend/nginx.conf` |
| MCP-Server (Tools für KI-Agenten) | `mcp/server.py`, Tools je Thema `mcp/tools/<thema>.py`, Selbsttest `mcp/selftest.py` |
| Frag die Kamera (lokales Bildmodell, GPU-Lease) | Dienst `vlm/` (Profil `vlm`, [vlm/README.md](vlm/README.md)), `backend/app/vlm.py`, `cloud_vlm.py`, `routers/ask.py`, `frontend/src/components/AskCamera.tsx` |
| Virtuelle Sensoren, Vorräte, Einkaufsliste (A3+C4) | `worker/vg_worker/sensors.py`, `backend/app/sensors.py` (Hysterese, `vlm`-Fenster `VG_SENSOR_VLM_GAP_S`), `backend/app/vlm_measure.py` (W4 C4-VLM: Anzahl/leer per Bildmodell, Messwerkzeug `scripts/vlm_supply_bench.py`), `routers/sensors.py`, `frontend/src/pages/Supplies.tsx`, `pages/admin/Sensors.tsx`, `mcp/tools/sensors.py` |
| Zeitmaschine (B1, SigLIP 2, pgvector) | `worker/vg_worker/history.py`, `backend/app/history.py`, `history_query.py` (deutsch→englisch), `routers/history.py`, `frontend/src/pages/History.tsx`, `mcp/tools/history.py` |
| Person über mehrere Kameras / Anwesenheit (C2, OSNet auf der CPU, Galerie nur im RAM) | `worker/vg_worker/reid.py`, `backend/app/reid.py`, `routers/presence.py`, `frontend/src/pages/Presence.tsx`, `pages/admin/ReidSettings.tsx`, `mcp/tools/people.py`, Messung `worker/vg_worker/tests_feasibility/reidtest.py` |
| Embedding-Dienst (geteiltes DINOv2, W2) | Worker `worker/vg_worker/embed.py`, Backend `backend/app/embed_client.py` |
| Rückfragen „Bitte bestätigen“ (W2) | `backend/app/review.py`, `backend/app/routers/review.py`, `frontend/src/pages/Review.tsx` |
| LLM-Client (optional, Standard aus) | `backend/app/llm_client.py`, Test-Server ohne Kosten `scripts/llm_mock.py` |
| Fernseher (DLNA): Livebild per Regel, signierter Strom (ffmpeg) | `backend/app/tv.py`, `backend/app/routers/tv.py`, `frontend/src/pages/admin/TvSettings.tsx`, `mcp/tools/tv.py`, nginx `location /api/tv/` |
| Track-Trigger mit Evaluator (posture/gesture) | `backend/app/rules.py` (`register_trigger(..., evaluate=…)`), `backend/app/events.py` |

Details je Dienst: [backend/README.md](backend/README.md), [worker/README.md](worker/README.md),
[frontend/README.md](frontend/README.md).

## Bekannte Grenzen / offene Punkte

- Ein einzelner Admin-Benutzer, keine Rollen.
- Keine DB-Migrationen (Alembic) – bei Schemaänderungen nachrüsten.
- Kennzeichen- und Tier-Identifikation sind implementiert, aber noch nicht mit echten Szenen validiert.
- Clips werden auf ganze 2-s-Segmente gerundet (etwas länger als eingestellt).
- `buffalo_l` (InsightFace) nur nicht-kommerziell; YOLO11 unter AGPL-3.0.
- **Gesichter-Nachtlauf** (`nightly_mode`): Standard `preview` – berechnet nur einen Bericht. Automatisch
  zusammengeführt werden nur unbenannte Fragmente ohne Lernsperre, nie zwei benannte; `apply` bzw. „Jetzt anwenden“
  nur mit Anmeldung im Browser. Test-Identitäten nie gegen echte Daten anwenden: bei `POST
  /api/nightly/reports/{id}/apply` immer `idx` mit den eigenen Test-Aktionen angeben.
- Das Löschen einer Kamera entfernt ihre Ereignisse und daraus entstandenen Identitäten **nicht** – nach Tests mit
  Testkameras Ereignisse (`DELETE /api/events/{id}`) und Test-Identitäten (`POST /api/identities/bulk-delete`) selbst
  entfernen.
- Neue Gesichtsqualität (ab Paket B) ist strenger als die alte; `face_new_min_quality`/`face_sample_min_quality`
  nach einigen Tagen Betrieb mit `scripts/face_stats.py` prüfen.
- Medien unter `/media/*` liefert das Backend nur mit Cookie, API-Key (Bearer) oder signiertem Link (`?exp=&sig=`)
  aus (`backend/app/routers/media.py`, `media_sign.py`). Signierte Links sind bis zum Ablauf für jeden nutzbar, der
  sie kennt (TTL `media_link_ttl_h`). `VG_MEDIA_PUBLIC=1` schaltet auf das alte, ungeschützte Verhalten zurück.
- `VG_SECRET_KEY` ist zugleich Pepper der API-Key-Hashes und Schlüssel der Medien-Signaturen: ein Wechsel macht
  alle Sitzungen, API-Keys und signierten Links ungültig.
- API-Keys (Admin → „API-Zugänge“) öffnen kein go2rtc-Live-Video und können keine Keys verwalten.
- **PTZ:** nur ONVIF (kein Hersteller-API). Kameras melden ihre Position meist nicht (Reolink E1 Pro: nur
  `ContinuousMove`, Presets; `GetStatus` meldet auch während der Fahrt `IDLE`, `GotoPreset` blockiert bis ≈ 4,5 s).
  Die Grundposition ist ein gespeichertes Preset; „in der Grundposition“ heißt: zuletzt dorthin gefahren und seitdem
  nicht bewegt. Wer die Kamera mit der Hersteller-App bewegt, wird erst über die Kamera-Gesundheit („verschoben“)
  bemerkt. Bewegungen immer mit ONVIF-`Timeout`, damit die Kamera auch ohne `Stop` anhält (WLAN). Verfolgen arbeitet
  mit kurzen Impulsen auf je einer Achse (`FOLLOW_*` in `ptz.py`); gemessen an der E1 Pro: Geschwindigkeit wirkt
  nicht, 0,5 s ≈ ⅓ Bildbreite, Bewegung erst 1,1–1,2 s nach dem Befehl im Detect-Bild, Impulse < 0,25 s teils
  verschluckt. Schrittweite und Richtung lernt der Regler zur Laufzeit nach (Log beim Ende der Verfolgung). Das Standbild
  (`/snapshot.jpg`, go2rtc `frame.jpeg`) ist der letzte Keyframe und läuft nach einer Fahrt einige Sekunden nach.
  Tests ohne PTZ-Kamera: `scripts/fake_onvif.py` simuliert ein ONVIF-Gerät (Presets, Fahrten, Aufruf-Protokoll) und
  läuft im Netz von `testcam`, sodass eine Kamera mit `rtsp://user:pw@testcam:8554/cam1` und ONVIF-Port 8000 echte
  Personen-Detektionen für „Verfolgen“ liefert:
  `docker run -d --rm --name vg-fakeonvif --network container:$(docker compose ps -q testcam) -v "$PWD/scripts:/app:ro" python:3.12-slim python -u /app/fake_onvif.py 8000` Auch mit
  `write` ändern/sehen sie keine Push-Ziele, LLM-Adresse, Standort, Berichtsversand oder `public_base_url`
  (`routers/misc.py::SESSION_ONLY_SETTINGS`).
- **Fernseher (DLNA):** Der Fernseher holt den Strom selbst ab (`tv_stream_base_url`). Unter WSL2 im NAT-Modus ist
  VisionGuard aus dem Heimnetz nicht erreichbar; nötig ist eine Windows-Portweiterleitung, die nur der Fernseher
  nutzen darf (PowerShell als Administrator):
  `netsh interface portproxy add v4tov4 listenaddress=0.0.0.0 listenport=8090 connectaddress=127.0.0.1 connectport=8080`
  und `New-NetFirewallRule -DisplayName "VisionGuard TV" -Direction Inbound -Protocol TCP -LocalPort 8090 -RemoteAddress <TV-IP> -Action Allow`.
  Samsung (Tizen) spielt nur MPEG-TS mit SPS/PPS im Strom **und** bekannter Länge: go2rtcs TS/HLS (Reolink: SPS/PPS
  nur im SDP), fMP4 und chunked-Antworten scheitern mit „Ein unerwarteter Fehler ist aufgetreten“. Deshalb packt das
  Backend mit ffmpeg um (`-c:v copy`) und meldet `Content-Length` 50 GiB. Kein Bild-im-Bild (Tizen); nach `Stop`
  kehrt der Fernseher zur vorherigen Quelle zurück. Das Backend-Image enthält dafür ffmpeg.
- MQTT von außen (Host-Port `MQTT_PORT`) ist **nur lesend** (Listener 1884 mit ACL); schreiben können nur Dienste
  im Docker-Netz (Listener 1883). Testnachrichten daher per `docker compose exec mosquitto mosquitto_pub …`.
- go2rtc loggt das Modul `streams` nur ab `error`, weil dessen Warnungen die Kamera-URL mit Passwort enthalten.

## Commits

Kleine, thematisch saubere Commits mit aussagekräftiger Nachricht (Deutsch oder Englisch). Vor dem Commit:
Frontend-Build grün, betroffene Container bauen und starten fehlerfrei.
