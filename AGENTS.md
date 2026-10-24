# AGENTS.md – Leitfaden für KI-Agenten und Entwickler

Dieses Dokument richtet sich an Coding-Agenten (Claude Code, Codex, Cursor …) und Menschen, die an VisionGuard
arbeiten. Projektüberblick: [README.md](README.md). Verbindliche Schnittstellen: [CONTRACT.md](CONTRACT.md).

## Grundregeln

1. **CONTRACT.md ist die Quelle der Wahrheit** für REST, WebSocket, MQTT-Topics, Datenmodelle, Medienpfade und
   Stream-Namen. Wer eine Schnittstelle ändert, passt zuerst CONTRACT.md an und dann **alle** betroffenen Dienste
   (Backend, Worker, Frontend inkl. `src/lib/types.ts`, `src/lib/api.ts` und Mock `src/lib/mock.ts`).
2. **Sprache:** Oberfläche, Fehlermeldungen an Nutzer und Dokumentation auf **Deutsch**. Code-Bezeichner Englisch,
   Kommentare dürfen Deutsch sein (wie im bestehenden Code).
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
cp .env.example .env && ./testcam/download-videos.sh
docker compose up -d --build                     # alles
docker compose up -d --build --no-deps backend   # einzelnen Dienst neu bauen
docker compose logs -f worker
```

In einer WSL-Sandbox ohne systemd muss der Docker-Daemon ggf. manuell gestartet werden:
`sudo bash -c 'nohup dockerd > /tmp/dockerd.log 2>&1 &'`.

| Was | Wie |
|---|---|
| Oberfläche | http://localhost:8080 (admin/admin) |
| API-Doku | http://localhost:8080/api/docs |
| Backend direkt | `docker compose exec backend python -c "import httpx; ..."` (Port 8000 nur intern) |
| MQTT mitlesen | `docker compose exec mosquitto mosquitto_sub -t 'vg/internal/#' -v -C 20` |
| Frontend ohne Backend | `cd frontend && corepack pnpm install && corepack pnpm dev:mock` (Port 5173) |
| Frontend-Typecheck/Build | `cd frontend && corepack pnpm build` – muss fehlerfrei sein |
| Worker-Selbsttest | `docker compose run --rm --no-deps worker python -m vg_worker.selftest rtsp://testcam:8554/cam1 --seconds 30` |
| Lasttest 16 Kameras | `... selftest rtsp://testcam:8554/cam1 rtsp://testcam:8554/cam2 --replicate 8 --seconds 60` |
| Simulierte Kameras | `rtsp://testcam:8554/cam1` (Personen/Gesichter), `cam2` (Straße: Autos, Fahrräder) |

Es gibt noch keine automatisierten Testsuiten. Änderungen end-to-end prüfen: Stack hochfahren, Ereignisse unter
`/api/events` bzw. in der Oberfläche kontrollieren, bei UI-Änderungen Screenshot ansehen.

## Wo liegt was

| Thema | Datei(en) |
|---|---|
| Compose, Volumes, GPU | `docker-compose.yml`, `.env.example` |
| Auth (Cookie/JWT) | `backend/app/auth.py`, `backend/app/routers/auth.py` |
| Kameras, go2rtc-Sync | `backend/app/routers/cameras.py`, `backend/app/go2rtc.py`, `backend/app/sync.py` |
| Track → Event, Aktionen | `backend/app/events.py` |
| Regel-Bedingungen, Templates, Webhooks | `backend/app/rules.py` |
| Identitätsabgleich | `backend/app/identity.py` |
| Datenbankmodell | `backend/app/models.py` (`create_all`, keine Migrationen – Spalten nur additiv mit Default ändern) |
| Kamera-Ingest (ffmpeg) | `worker/vg_worker/ingest.py` |
| YOLO-Batching | `worker/vg_worker/detector.py`, `worker/vg_worker/pipeline.py` |
| Tracking, Zonen | `worker/vg_worker/tracking.py` |
| Gesicht/Kennzeichen/Tier | `worker/vg_worker/identify.py` |
| Ringpuffer, Clips, Snapshots | `worker/vg_worker/recorder.py` |
| API-Client, Typen | `frontend/src/lib/api.ts`, `frontend/src/lib/types.ts` |
| Live-Socket/Store | `frontend/src/lib/useLiveSocket.ts`, `frontend/src/lib/store.ts` |
| Live-Player + Overlay | `frontend/src/components/LivePlayer.tsx`, `DetectionOverlay.tsx` |
| Zonen-Editor | `frontend/src/components/ZoneEditor.tsx` |
| Admin-Seiten | `frontend/src/pages/admin/` |
| nginx-Routen | `frontend/nginx.conf` |

Details je Dienst: [backend/README.md](backend/README.md), [worker/README.md](worker/README.md),
[frontend/README.md](frontend/README.md).

## Bekannte Grenzen / offene Punkte

- Ein einzelner Admin-Benutzer, keine Rollen.
- Keine DB-Migrationen (Alembic) – bei Schemaänderungen nachrüsten.
- Kennzeichen- und Tier-Identifikation sind implementiert, aber noch nicht mit echten Szenen validiert.
- Clips werden auf ganze 2-s-Segmente gerundet (etwas länger als eingestellt).
- `buffalo_l` (InsightFace) nur nicht-kommerziell; YOLO11 unter AGPL-3.0.
- Medien unter `/media/*` sind ohne Login abrufbar (zufällige UUID-Pfade) – vor Produktivbetrieb absichern.

## Commits

Kleine, thematisch saubere Commits mit aussagekräftiger Nachricht (Deutsch oder Englisch). Vor dem Commit:
Frontend-Build grün, betroffene Container bauen und starten fehlerfrei.
