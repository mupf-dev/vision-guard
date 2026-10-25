# VisionGuard

Selbst gehostete, Docker-basierte Videoanalyse für Netzwerkkameras. Kamerastreams werden **nicht dauerhaft
aufgezeichnet**, sondern live per KI ausgewertet: Objekte, Personen, Tiere und Fahrzeuge werden erkannt, verfolgt
und – optional – identifiziert (Gesichter, Kennzeichen, Haustiere). Dazu merkt sich VisionGuard, wo Gegenstände
liegen („Wo ist meine Fernbedienung?“). Regeln entscheiden, was passiert: Snapshot, Videoclip aus dem
RAM-Ringpuffer, Push aufs Handy, MQTT-Nachricht oder Webhook.

## Funktionen

- **Live-Kachelansicht** (1–16 Kameras) mit Bounding-Box-Overlays, Zonen und Live-Ereignis-Feed
- **Objekterkennung** mit YOLO11 auf der NVIDIA-GPU, Batching über alle Kameras, ByteTrack-Tracking
- **Identifikation:** Gesichter (InsightFace), Kennzeichen (fast-alpr), Haustiere (DINOv2-Embeddings).
  Unbekannte bekommen automatisch eine ID („Person #12“) und lassen sich später benennen oder zusammenführen.
- **Regel-Engine:** Kamera, Zone, Objektklasse, Konfidenz, Identität (bekannt/unbekannt/bestimmt),
  Auslöser (betritt/verlässt/verweilt), Zeitplan, Cooldown → Snapshot, Clip, MQTT, Webhook
- **Virtuelle Sensoren und Vorräte:** Bildbereiche als Schalter (Licht an/aus per Helligkeit, Blinken,
  Referenzbilder, Gegenstände zählen, Füllstand, Frage ans Bildmodell: Ja/Nein, Anzahl bis 3 oder „leer“) – als
  Regel-Auslöser und -Bedingung,
  per MQTT und MCP; knappe Vorräte landen automatisch auf der Einkaufsliste
- **Gegenstände (Inventar):** offenes Vokabular (YOLOE, 50 Alltagsgegenstände, eigene ergänzbar), Lage
  („vermutlich auf dem Tisch“), Veränderungen seit gestern, eigene Exemplare („mein Schlüsselbund“), Suche auf Deutsch
- **Hinweise mit Push** an ntfy oder Pushover (mit Bild, Antwort-Links), Regeln mit Bedingungen (Gegenstand
  fehlt, Wetter, Sensor), Notfälle mit Wiederholung bis zur Quittierung
- **Bitte bestätigen:** bei unsicheren Erkennungen fragt VisionGuard nach (Oberfläche, Push, MCP) und lernt daraus
- **Haltung, Sturz und Gesten** (Pose-Modell): „liegt am Boden“ als Auslöser, Handzeichen als Schalter
- **Tiere:** Haustier-Tagebuch (Fressen, Trinken, Katzenklo je Zone)
- **Anwesenheit:** wer ist gerade wo, auch ohne Gesicht über mehrere Kameras (Wiedererkennung, nur im Speicher)
- **Tagesbericht** (Satzbausteine, optional mit Sprachmodell über eine OpenAI-kompatible Adresse) per Push,
  MQTT, Webhook oder Mail
- **Frag die Kamera:** Fragen zum aktuellen Bild an ein lokales Bildmodell (Moondream, Dienst `vlm`, optional)
- **Kamera-Gesundheit:** verdeckt, unscharf, zu dunkel, eingefroren oder verschoben – mit Hinweis
- **Schwenk-Kameras (PTZ über ONVIF):** Steuerkreuz im Live-Bild, gespeicherte Positionen, Grundposition,
  Patrouille nach Zeitplan, Verfolgen von Personen/Tieren, Regel-Aktion „Kamera schwenken“; Zonen, Gegenstände und
  Sensoren gelten für die Grundposition und pausieren, solange die Kamera woanders hinschaut
- **Ereignisse** mit Filtern, Snapshots, Clips und Statistik
- **Administration:** Kameras (mit Verbindungstest), Zonen-Editor, Regel-Editor, Einstellungen, Systemstatus/GPU
- **MQTT** nach außen (z. B. Home Assistant): alle Ereignisse, Zähler pro Kamera und Objektklasse, Sensoren;
  von außen nur lesend (eigener Listener, siehe unten)
- **Zeitmaschine:** je Kamera zuschaltbar ein Bildgedächtnis (Bild-Embeddings + 256-px-Vorschau, kein Video);
  Suche mit eigenen Worten („roter Karton im Flur“) liefert Zeiträume mit Filmstreifen – auch per MCP

## Architektur

```
 Kameras (RTSP) ──▶ go2rtc ──WebRTC/MSE──▶ Browser (React-UI, nginx)
                      │                         ▲  REST + WebSocket
                      │ RTSP (Sub-Stream)       │
                      ▼                         │
                 worker (GPU) ──MQTT──▶ mosquitto ◀──▶ backend (FastAPI) ──▶ PostgreSQL (pgvector)
                 YOLO11, ByteTrack,        ▲           Regeln, Events,       Medien-Volume
                 Gesicht/Kennzeichen/Tier, │ GPU-Lease Identitäten, Inventar, (Snapshots/Clips)
                 YOLOE-Inventar, Pose,     │           Hinweise/Push, Auth
                 RAM-Ringpuffer für Clips  │                ▲
                                     vlm (Bildmodell)       │ REST
                                                        mcp (KI-Agenten, /mcp)
```

| Dienst | Verzeichnis | Technik |
|---|---|---|
| `frontend` | [`frontend/`](frontend/README.md) | React 19, TypeScript, Vite, nginx (Reverse-Proxy) |
| `backend` | [`backend/`](backend/README.md) | Python 3.12, FastAPI, SQLAlchemy, paho-mqtt |
| `worker` | [`worker/`](worker/README.md) | PyTorch 2.5 / CUDA 12.4, Ultralytics YOLO11, supervision, InsightFace, fast-alpr, timm |
| `mcp` | [`mcp/`](#mcp-server-für-ki-agenten) | MCP-Server für KI-Agenten (Streamable HTTP unter `/mcp`) |
| `vlm` | `vlm/` | lokales Bildmodell für „Frag die Kamera“ und Bild-Sensoren (Compose-Profil `vlm`, leiht sich die GPU vom Worker) |
| `go2rtc` | `go2rtc/` | Restreaming der Kameras (eine Verbindung pro Kamera) |
| `mosquitto` | `mosquitto/` | MQTT-Broker: intern 1883 (Docker-Netz, voller Zugriff), extern Host-Port `MQTT_PORT` → 1884 (anonym, nur lesen) |
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
cp .env.example .env            # Admin-Passwort und SECRET_KEY anpassen (Pflicht: SECRET_KEY=$(openssl rand -hex 32))!
./testcam/download-videos.sh    # nur für die Demo-Kameras
docker compose up -d --build
```

Oberfläche: **http://localhost:8080** (Login aus `.env`, Standard `admin` / `admin`).

Beim ersten Start legt das Backend zwei Demo-Kameras und zwei Beispielregeln an (`DEMO_CAMERAS=1`). Ohne Demo:
in `.env` `DEMO_CAMERAS=0` und `COMPOSE_PROFILES=` setzen.

### YouTube-Livestreams

Als URL kann auch ein YouTube-Live-Link eingetragen werden (z. B. öffentliche Stadt- oder Verkehrs-Webcams).
Das Backend ermittelt per `yt-dlp` nur die aktuelle Stream-Adresse; das Video wird live analysiert und **nicht
gespeichert** (Clips nur bei Regel-Auslösung, wie bei allen Kameras). Für öffentliche Webcams Gesichts- und
Kennzeichenerkennung in der Kamera abschalten (DSGVO).

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
| `ADMIN_USER` / `ADMIN_PASSWORD` | `admin` / `admin` | Login (schwaches Passwort → Anmeldung nur aus privaten Netzen; max. 5 Fehlversuche/min je IP) |
| `SECRET_KEY` | `change-me-please` | Signatur der Session-Cookies, API-Key-Hashes, Medien-Links. **Pflicht:** ≥ 32 Zeichen, sonst startet das Backend nicht |
| `POSTGRES_PASSWORD` | `vg` | Datenbank |
| `DEMO_CAMERAS` | `1` | Demo-Kameras beim ersten Start anlegen |
| `COMPOSE_PROFILES` | `demo` | startet den `testcam`-Dienst; `demo,vlm` startet zusätzlich das Bildmodell (sonst fehlen „Frag die Kamera“ und Bild-Sensoren nach einem Neustart) |
| `MQTT_PORT` | `1883` | externer MQTT-Port (anonym, **nur lesen**; Schreiben nur im Docker-Netz) |
| `VG_PUBLIC_DOCS` | `0` | `1` = API-Doku `/api/docs` ohne Anmeldung (Standard: nur mit Login oder API-Key) |
| `BUFFER_SIZE` | `1g` | RAM (tmpfs) für den Clip-Ringpuffer |
| `TZ` | `Europe/Berlin` | Zeitzone für Zeitpläne |
| `MCP_ALLOWED_HOSTS` | leer | erlaubte Host-Namen für `/mcp` (kommagetrennt, z. B. `vg.example.org,localhost`); leer = keine Prüfung |

Weitere Worker-Variablen (Modelle, Batchgröße, NVDEC): siehe [worker/README.md](worker/README.md).

## MCP-Server für KI-Agenten

Der Dienst `mcp` stellt VisionGuard als [MCP](https://modelcontextprotocol.io)-Server bereit (Streamable HTTP),
damit KI-Agenten Fragen wie „Wo ist meine Fernbedienung?“ beantworten können.

- **URL:** `http://<host>:8080/mcp` (über nginx; intern `http://mcp:8100/mcp`).
- **API-Key:** unter **Administration → API-Zugänge** erzeugen. Scope `read` genügt für alle lesenden Tools;
  die Tools mit Wirkung (unten mit ✎) brauchen `write`. Der Key wird nur einmal angezeigt und kann dort
  jederzeit widerrufen werden. Ohne gültigen Key antwortet `/mcp` mit 401.
- **Tools** (23, Antworten auf Deutsch):

| Tool | Zweck |
|---|---|
| `find_object(query)` | Gegenstand suchen: „Fernbedienung: aktuell sichtbar in Kamera Wohnzimmer (Raum Wohnzimmer), vermutlich auf dem Tisch, seit 14:02“ |
| `list_visible_objects(camera_id?, room?, camera_name?)` | alle gerade sichtbaren Gegenstände |
| `get_object_history(item_id)` | Verlauf eines Gegenstands |
| `list_cameras()` | Kameras mit Raum und Zustand |
| `get_camera_snapshot(camera_id? \| camera_name?)` | aktuelles Kamerabild (JPEG, max. 1600 px breit) |
| `who_is_where()` | welche Personen/Tiere gerade wo zu sehen sind |
| `search_history(query, since?, until?, camera?, limit?, include_image?)` | Zeitmaschine: „Am Di 14:03–14:21 in Raum Flur, Übereinstimmung 71 %“ mit signierter Vorschau |
| `what_changed(since?, until?, …)` | was ist seit … neu aufgetaucht, verschwunden, verschoben |
| `list_notifications(since?, limit?)` | letzte Hinweise |
| `get_camera_health(camera_id? \| camera_name?)` | Bildzustand der Kameras (verdeckt, dunkel, …) |
| `list_alerts(active_only?)` / `acknowledge_alert(event_id)` ✎ | Notfälle (z. B. Sturz) anzeigen / quittieren |
| `list_open_questions()` / `answer_question(id, answer)` ✎ | Rückfragen „Bitte bestätigen“ |
| `get_pet_diary(pet?, days?)` | Haustier-Tagebuch |
| `get_daily_report(date?)` | gespeicherter Tagesbericht bzw. Vorschau (Vorschau immer ohne Sprachmodell) |
| `get_nightly_report(report_id?)` | Bericht des Gesichter-Nachtlaufs (Zusammenführungen, Rückfragen, Kuratierung; nur lesen) |
| `ask_camera(question, camera_id? \| camera_name?)` | Frage zum aktuellen Bild (lokales Bildmodell; global höchstens 12 Fragen/h über API-Keys) |
| `list_sensors(…)` / `get_sensor(…)` | virtuelle Sensoren und Vorräte |
| `get_shopping_list()` / `add_to_shopping_list(name)` ✎ | Einkaufsliste |
| `ptz_status(camera_id? \| camera_name?)` / `ptz_move_to(preset, camera_id? \| camera_name?, hold_s?)` ✎ | Schwenk-Kameras: Ausrichtung und Positionen / Position anfahren (danach `get_camera_snapshot`) |

Beispiel Claude Code:

```bash
claude mcp add --transport http visionguard http://localhost:8080/mcp \
  --header "Authorization: Bearer $VG_API_KEY"
```

Beispiel für Clients mit JSON-Konfiguration (`mcpServers`):

```json
{
  "mcpServers": {
    "visionguard": {
      "type": "http",
      "url": "https://vg.example.org/mcp",
      "headers": { "Authorization": "Bearer vgt_…" }
    }
  }
}
```

Selbsttest im Container: `docker compose exec -e VG_API_KEY=vgt_… mcp python selftest.py`
(prüft 401 ohne Key, ob alle Tools angeboten werden, und ruft u. a. `list_cameras`, `find_object`,
`list_visible_objects`, `who_is_where`, `what_changed`, `list_alerts` und `search_history` auf).

**Zugriff von außen:** Port 8080 nicht direkt ins Internet freigeben, sondern einen Reverse-Proxy mit TLS
(z. B. Caddy, Traefik, nginx mit Let's Encrypt) oder einen Tunnel (z. B. Cloudflare Tunnel, Tailscale Funnel)
davorsetzen. Dann `MCP_ALLOWED_HOSTS` auf den öffentlichen Host-Namen setzen und unter Einstellungen
`public_base_url` (z. B. `https://vg.example.org`) eintragen, damit Bild-Links in den Antworten absolut sind.
API-Keys nur über HTTPS verwenden, für Agenten eigene Keys mit Scope `read` anlegen und nicht mehr benötigte
widerrufen. Auch ein Key mit `write` kann Push-Ziele, LLM-Adresse, Standort, Berichtsversand und
`public_base_url` nicht ändern (nur mit Anmeldung im Browser) und sieht diese Werte nicht. Hinweis: Kamerabilder und Gegenstands-Crops gehen dabei an den Anbieter des KI-Modells.

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
