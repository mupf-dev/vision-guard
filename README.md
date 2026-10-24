# VisionGuard

Selbst gehostete, Docker-basierte Videoanalyse für Netzwerkkameras. Kamerastreams werden **nicht dauerhaft
aufgezeichnet**, sondern live per KI ausgewertet: Objekte, Personen, Tiere und Fahrzeuge werden erkannt, verfolgt
und – optional – identifiziert (Gesichter, Kennzeichen, Haustiere). Regeln entscheiden, was passiert: Snapshot,
Videoclip aus dem RAM-Ringpuffer, MQTT-Nachricht oder Webhook.

## Funktionen

- **Live-Kachelansicht** (1–16 Kameras) mit Bounding-Box-Overlays, Zonen und Live-Ereignis-Feed
- **Objekterkennung** mit YOLO11 auf der NVIDIA-GPU, Batching über alle Kameras, ByteTrack-Tracking
- **Identifikation:** Gesichter (InsightFace), Kennzeichen (fast-alpr), Haustiere (DINOv2-Embeddings).
  Unbekannte bekommen automatisch eine ID („Person #12“) und lassen sich später benennen oder zusammenführen.
- **Regel-Engine:** Kamera, Zone, Objektklasse, Konfidenz, Identität (bekannt/unbekannt/bestimmt),
  Auslöser (betritt/verlässt/verweilt), Zeitplan, Cooldown → Snapshot, Clip, MQTT, Webhook
- **Ereignisse** mit Filtern, Snapshots, Clips und Statistik
- **Administration:** Kameras (mit Verbindungstest), Zonen-Editor, Regel-Editor, Einstellungen, Systemstatus/GPU
- **MQTT** nach außen (z. B. Home Assistant): alle Ereignisse, Zähler pro Kamera und Objektklasse

## Architektur

```
 Kameras (RTSP) ──▶ go2rtc ──WebRTC/MSE──▶ Browser (React-UI, nginx)
                      │                         ▲  REST + WebSocket
                      │ RTSP (Sub-Stream)       │
                      ▼                         │
                 worker (GPU) ──MQTT──▶ mosquitto ◀──▶ backend (FastAPI) ──▶ PostgreSQL
                 YOLO11, ByteTrack,                    Regeln, Events,       Medien-Volume
                 Gesicht/Kennzeichen/Tier,             Identitäten, Auth     (Snapshots/Clips)
                 RAM-Ringpuffer für Clips
```

| Dienst | Verzeichnis | Technik |
|---|---|---|
| `frontend` | [`frontend/`](frontend/README.md) | React 19, TypeScript, Vite, nginx (Reverse-Proxy) |
| `backend` | [`backend/`](backend/README.md) | Python 3.12, FastAPI, SQLAlchemy, paho-mqtt |
| `worker` | [`worker/`](worker/README.md) | PyTorch 2.5 / CUDA 12.4, Ultralytics YOLO11, supervision, InsightFace, fast-alpr, timm |
| `go2rtc` | `go2rtc/` | Restreaming der Kameras (eine Verbindung pro Kamera) |
| `mosquitto` | `mosquitto/` | MQTT-Broker (intern und extern) |
| `db` | – | PostgreSQL 17 |
| `testcam` | `testcam/` | Simulierte RTSP-Kameras (Compose-Profil `demo`) |

Alle Schnittstellen zwischen den Diensten sind in **[CONTRACT.md](CONTRACT.md)** festgelegt.

## Voraussetzungen

- Docker mit Compose v2
- NVIDIA-GPU mit [NVIDIA Container Toolkit](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/)
  (`docker run --rm --gpus all nvidia/cuda:12.4.1-base-ubuntu22.04 nvidia-smi` muss funktionieren).
  Das Worker-Image nutzt **CUDA 12.4** – der Treiber muss mindestens 550 sein.
- Ca. 15 GB Platz für Images, ca. 400 MB für Modelle (werden beim ersten Start ins Volume `models` geladen)

## Schnellstart

```bash
cp .env.example .env            # Admin-Passwort und SECRET_KEY anpassen!
./testcam/download-videos.sh    # nur für die Demo-Kameras
docker compose up -d --build
```

Oberfläche: **http://localhost:8080** (Login aus `.env`, Standard `admin` / `admin`).

Beim ersten Start legt das Backend zwei Demo-Kameras und zwei Beispielregeln an (`DEMO_CAMERAS=1`). Ohne Demo:
in `.env` `DEMO_CAMERAS=0` und `COMPOSE_PROFILES=` setzen.

### Eigene Kamera hinzufügen

Administration → Kameras → „Kamera hinzufügen“:

- **URL**: Hauptstream für Live-Ansicht, Snapshots und Clips, z. B. Reolink
  `rtsp://user:pass@IP:554/h264Preview_01_main`
- **Detect-URL** (empfohlen): Sub-Stream mit geringer Auflösung für die KI, z. B. `.../h264Preview_01_sub`.
  Spart bei vielen Kameras viel CPU beim Dekodieren.

## Konfiguration (`.env`)

| Variable | Standard | Bedeutung |
|---|---|---|
| `HTTP_PORT` | `8080` | Port der Oberfläche |
| `ADMIN_USER` / `ADMIN_PASSWORD` | `admin` / `admin` | Login |
| `SECRET_KEY` | `change-me-please` | Signatur der Session-Cookies |
| `POSTGRES_PASSWORD` | `vg` | Datenbank |
| `DEMO_CAMERAS` | `1` | Demo-Kameras beim ersten Start anlegen |
| `COMPOSE_PROFILES` | `demo` | startet den `testcam`-Dienst |
| `MQTT_PORT` | `1883` | externer MQTT-Port |
| `BUFFER_SIZE` | `1g` | RAM (tmpfs) für den Clip-Ringpuffer |
| `TZ` | `Europe/Berlin` | Zeitzone für Zeitpläne |

Weitere Worker-Variablen (Modelle, Batchgröße, NVDEC): siehe [worker/README.md](worker/README.md).

## Leistung (gemessen, RTX 4080 Laptop)

16 Kameras × 5 Bilder/s: 80 Bilder/s gesamt, GPU-Last 20–35 %, 1,9 GB Grafikspeicher, ca. 4,3 GB RAM.
Bei hochauflösenden Kameras ist das Dekodieren auf der CPU der Engpass → Sub-Stream als Detect-URL nutzen.

## Datenschutz und Lizenzen

- Gesichtserkennung verarbeitet **biometrische Daten** (DSGVO Art. 9). Identifikation pro Kamera abschaltbar,
  Aufbewahrungsfristen unter Administration → Einstellungen.
- Die InsightFace-Gewichte `buffalo_l` sind **nur für nicht-kommerzielle Nutzung** freigegeben.
- Ultralytics YOLO11 steht unter **AGPL-3.0**.
- Testvideos: Intel IoT DevKit sample-videos (CC BY 4.0).

## Entwicklung

Hinweise für Entwickler und KI-Agenten: [AGENTS.md](AGENTS.md).
