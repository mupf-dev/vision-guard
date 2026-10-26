# VisionGuard

**Website & screenshots: <https://mupf-dev.github.io/vision-guard/>**

Self-hosted, Docker-based video analytics for network cameras. Camera streams are **not recorded continuously** but
analysed live by AI: objects, people, animals and vehicles are detected, tracked and – optionally – identified (faces,
licence plates, pets). VisionGuard also remembers where things are ("Where is my remote?"). Rules decide what happens:
snapshot, video clip from the RAM ring buffer, push notification to your phone, MQTT message or webhook.

![Live view with detections](https://mupf-dev.github.io/vision-guard/img/live.webp)

> The user interface is currently in German; an English interface with support for further languages is in progress.

## Features

- **Live tile view** (1–16 cameras) with bounding-box overlays, zones and a live event feed
- **Object detection** with YOLO11 on an NVIDIA GPU, batched across all cameras, ByteTrack tracking
- **Identification:** faces (InsightFace), licence plates (fast-alpr), pets (DINOv2 embeddings). Unknown subjects
  automatically get an ID ("Person #12") and can be named or merged later. Faces are collected per track and rated
  by quality (size, head pose, sharpness); for cameras above 1920 px they are cropped from the full resolution.
  Multi-select to merge/delete with history and undo; a **nightly run** bundles fragments, curates the image gallery
  and adapts per-person thresholds (default: dry run with preview only, applied after approval in Settings → Nightly
  run)
- **Rule engine:** camera, zone, object class, confidence, identity (known/unknown/specific), trigger
  (enter/leave/dwell/posture/gesture/time/sensor), schedule, cooldown → snapshot, clip, MQTT, webhook, push, PTZ, TV
- **Virtual sensors and supplies:** image regions as switches (light on/off by brightness, blinking, reference
  images, counting items, fill level, asking the vision model: yes/no, count up to 3 or "empty") – usable as rule
  trigger and condition, via MQTT and MCP; low supplies are added to the shopping list automatically
- **Items (inventory):** open vocabulary (YOLOE, 50 everyday objects, extendable), location ("probably on the
  table"), changes since yesterday, your own specific items ("my key ring"), search in German
- **Notifications with push** to ntfy or Pushover (with image and answer links), rules with conditions (item
  missing, weather, sensor), emergencies repeated until acknowledged
- **Please confirm:** for uncertain detections VisionGuard asks (UI, push, MCP) and learns from the answer
- **Posture, falls and gestures** (pose model): "lying on the floor" as a trigger, hand signals as switches
- **Pets:** pet diary (eating, drinking, litter box per zone)
- **Presence:** who is where right now, across cameras even without a face (re-identification, kept in memory only)
- **Daily report** (template-based, optionally written by a language model via an OpenAI-compatible endpoint) via
  push, MQTT, webhook or e-mail
- **Ask the camera:** questions about the current image to a local vision model (Moondream, service `vlm`, optional)
- **Camera health:** covered, blurred, too dark, frozen or moved – with notification
- **Pan/tilt cameras (PTZ via ONVIF):** control pad in the live view, save positions and the home position right
  there, scheduled patrols, following people/animals, rule action "pan camera"; zones, items and sensors apply to
  the home position and pause while the camera looks elsewhere
- **Live view on your TV (DLNA):** rule action "show on TV" (e.g. person at the door → camera full screen for 60 s,
  then back to the programme), test button in the settings, MCP `show_on_tv`; tested with Samsung (Tizen). The TV
  must be able to reach VisionGuard on the home network (under WSL2: port forwarding, see AGENTS.md)
- **Events** with filters, snapshots, clips and statistics
- **Administration:** cameras (with connection test), zone editor, rule editor, settings, system status/GPU
- **MQTT** to the outside (e.g. Home Assistant): all events, counters per camera and object class, sensors;
  read-only from outside (separate listener, see below)
- **Time machine:** per camera an optional image memory (image embeddings + 256 px preview, no video); searching in
  your own words ("red box in the hallway") returns time ranges with a filmstrip – also via MCP

## Architecture

```
 Cameras (RTSP) ──▶ go2rtc ──WebRTC/MSE──▶ Browser (React UI, nginx)
                      │                         ▲  REST + WebSocket
                      │ RTSP (sub stream)       │
                      ▼                         │
                 worker (GPU) ──MQTT──▶ mosquitto ◀──▶ backend (FastAPI) ──▶ PostgreSQL (pgvector)
                 YOLO11, ByteTrack,        ▲           rules, events,        media volume
                 face/plate/pet,           │ GPU lease identities, items,    (snapshots/clips)
                 YOLOE items, pose,        │           notifications, auth
                 RAM ring buffer for clips │                ▲
                                     vlm (vision model)     │ REST
                                                        mcp (AI agents, /mcp)
```

| Service | Directory | Technology |
|---|---|---|
| `frontend` | [`frontend/`](frontend/README.md) | React 19, TypeScript, Vite, nginx (reverse proxy) |
| `backend` | [`backend/`](backend/README.md) | Python 3.12, FastAPI, SQLAlchemy, paho-mqtt |
| `worker` | [`worker/`](worker/README.md) | PyTorch 2.5 / CUDA 12.4, Ultralytics YOLO11, supervision, InsightFace, fast-alpr, timm |
| `mcp` | [`mcp/`](#mcp-server-for-ai-agents) | MCP server for AI agents (Streamable HTTP at `/mcp`) |
| `vlm` | [`vlm/`](vlm/README.md) | local vision model for "Ask the camera" and image sensors (Compose profile `vlm`, borrows the GPU from the worker) |
| `go2rtc` | `go2rtc/` | camera restreaming (one connection per camera) |
| `mosquitto` | `mosquitto/` | MQTT broker: internal 1883 (Docker network, full access), external host port `MQTT_PORT` → 1884 (anonymous, read-only) |
| `db` | – | PostgreSQL 17 with pgvector |
| `testcam` | `testcam/` | simulated RTSP cameras (Compose profile `demo`) |

All interfaces between the services are defined in **[CONTRACT.md](CONTRACT.md)** (German).

## Requirements

- Docker with Compose v2
- NVIDIA GPU with the [NVIDIA Container Toolkit](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/)
  (`docker run --rm --gpus all nvidia/cuda:12.4.1-base-ubuntu22.04 nvidia-smi` must work).
  The worker image uses **CUDA 12.4** – the driver must be at least 550.
- About 15 GB of disk space for images and about 400 MB for models (downloaded into the `models` volume on first start)

## Quick start

```bash
cp .env.example .env            # set admin password and SECRET_KEY (required: SECRET_KEY=$(openssl rand -hex 32))!
./testcam/download-videos.sh    # only needed for the demo cameras
docker compose up -d --build
```

User interface: **http://localhost:8080** (login from `.env`, default `admin` / `admin`).

On first start the backend creates two demo cameras and two example rules (`DEMO_CAMERAS=1`). Without the demo:
set `DEMO_CAMERAS=0` and `COMPOSE_PROFILES=` in `.env`.

### YouTube live streams

A YouTube live link can be used as camera URL as well (e.g. public city or traffic webcams). The backend only
resolves the current stream address with `yt-dlp`; the video is analysed live and **not stored** (clips only when a
rule fires, as for every camera). For public webcams, switch off face and licence plate recognition for that camera
(GDPR).

### Adding your own camera

Administration → Cameras → "Kamera hinzufügen" (Add camera):

- **URL**: main stream for live view, snapshots and clips, e.g. Reolink
  `rtsp://user:pass@IP:554/h264Preview_01_main`
- **Detect URL** (recommended): low-resolution sub stream for the AI, e.g. `.../h264Preview_01_sub`.
  Saves a lot of CPU for decoding when you run many cameras.

## Configuration (`.env`)

| Variable | Default | Meaning |
|---|---|---|
| `HTTP_PORT` | `8080` | port of the user interface |
| `ADMIN_USER` / `ADMIN_PASSWORD` | `admin` / `admin` | login (weak password → login only from private networks; max. 5 failed attempts/min per IP) |
| `SECRET_KEY` | `change-me-please` | signs session cookies, API key hashes and media links. **Required:** ≥ 32 characters, otherwise the backend does not start |
| `POSTGRES_PASSWORD` | `vg` | database |
| `DEMO_CAMERAS` | `1` | create demo cameras on first start |
| `COMPOSE_PROFILES` | `demo` | starts the `testcam` service; `demo,vlm` also starts the vision model (otherwise "Ask the camera" and image sensors are missing after a restart) |
| `MQTT_PORT` | `1883` | external MQTT port (anonymous, **read-only**; writing only inside the Docker network) |
| `VG_PUBLIC_DOCS` | `0` | `1` = API docs at `/api/docs` without login (default: only with login or API key) |
| `BUFFER_SIZE` | `1g` | RAM (tmpfs) for the clip ring buffer |
| `TZ` | `Europe/Berlin` | time zone for schedules |
| `MCP_ALLOWED_HOSTS` | empty | allowed host names for `/mcp` (comma-separated, e.g. `vg.example.org,localhost`); empty = no check |

More worker variables (models, batch size, NVDEC): see [worker/README.md](worker/README.md).

## MCP server for AI agents

The `mcp` service exposes VisionGuard as an [MCP](https://modelcontextprotocol.io) server (Streamable HTTP), so AI
agents can answer questions like "Where is my remote?".

- **URL:** `http://<host>:8080/mcp` (via nginx; internally `http://mcp:8100/mcp`).
- **API key:** create one under **Administration → API-Zugänge** (API access). Scope `read` is enough for all reading
  tools; tools with side effects (marked ✎ below) need `write`. The key is shown only once and can be revoked there at
  any time. Without a valid key `/mcp` answers 401.
- **Tools** (23; answers are currently in German):

| Tool | Purpose |
|---|---|
| `find_object(query)` | find an item: "remote: currently visible on camera Living room, probably on the table, since 14:02" |
| `list_visible_objects(camera_id?, room?, camera_name?)` | all currently visible items |
| `get_object_history(item_id)` | history of an item |
| `list_cameras()` | cameras with room and state |
| `get_camera_snapshot(camera_id? \| camera_name?)` | current camera image (JPEG, max. 1600 px wide) |
| `who_is_where()` | which people/animals are visible where right now |
| `search_history(query, since?, until?, camera?, limit?, include_image?)` | time machine: "Tue 14:03–14:21 in the hallway, 71 % match" with a signed preview |
| `what_changed(since?, until?, …)` | what has appeared, disappeared or moved since … |
| `list_notifications(since?, limit?)` | latest notifications |
| `get_camera_health(camera_id? \| camera_name?)` | image health of the cameras (covered, dark, …) |
| `list_alerts(active_only?)` / `acknowledge_alert(event_id)` ✎ | show / acknowledge emergencies (e.g. a fall) |
| `list_open_questions()` / `answer_question(id, answer)` ✎ | "please confirm" questions |
| `get_pet_diary(pet?, days?)` | pet diary |
| `get_daily_report(date?)` | stored daily report or a preview (the preview never uses a language model) |
| `get_nightly_report(report_id?)` | report of the nightly face run (merges, questions, curation; read-only) |
| `ask_camera(question, camera_id? \| camera_name?)` | ask about the current image (local vision model; at most 12 questions/h via API keys in total) |
| `list_sensors(…)` / `get_sensor(…)` | virtual sensors and supplies |
| `get_shopping_list()` / `add_to_shopping_list(name)` ✎ | shopping list |
| `ptz_status(camera_id? \| camera_name?)` / `ptz_move_to(preset, camera_id? \| camera_name?, hold_s?)` ✎ | PTZ cameras: orientation and positions / move to a position (then `get_camera_snapshot`) |
| `show_on_tv(camera_id? \| camera_name?, tv?, duration_s?)` ✎ | show a camera's live image on the TV |

Example for Claude Code:

```bash
claude mcp add --transport http visionguard http://localhost:8080/mcp \
  --header "Authorization: Bearer $VG_API_KEY"
```

Example for clients with a JSON configuration (`mcpServers`):

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

Self-test in the container: `docker compose exec -e VG_API_KEY=vgt_… mcp python selftest.py`
(checks 401 without a key and that all tools are offered, and calls `list_cameras`, `find_object`,
`list_visible_objects`, `who_is_where`, `what_changed`, `list_alerts` and `search_history`, among others).

**Access from outside:** do not expose port 8080 directly to the internet; put a reverse proxy with TLS (e.g. Caddy,
Traefik, nginx with Let's Encrypt) or a tunnel (e.g. Cloudflare Tunnel, Tailscale Funnel) in front of it. Then set
`MCP_ALLOWED_HOSTS` to the public host name and enter `public_base_url` (e.g. `https://vg.example.org`) in the
settings so that image links in answers are absolute. Use API keys over HTTPS only, create separate keys with scope
`read` for agents and revoke keys you no longer need. Even a key with `write` cannot change push targets, the LLM
address, location, report delivery or `public_base_url` (browser login only) and cannot see these values. Note:
camera images and item crops are sent to the provider of the AI model you use.

## Performance (measured, RTX 4080 Laptop)

16 cameras × 5 frames/s: 80 frames/s in total, GPU load 20–35 %, 1.9 GB of video memory, about 4.3 GB of RAM.
With high-resolution cameras, CPU decoding is the bottleneck → use a sub stream as detect URL.

## Privacy and licences

- Face recognition processes **biometric data** (GDPR Art. 9). Identification can be switched off per camera;
  retention periods under Administration → Settings.
- The InsightFace weights `buffalo_l` are licensed for **non-commercial use only**.
- Ultralytics YOLO11 is licensed under **AGPL-3.0**.
- Test videos: Intel IoT DevKit sample-videos (CC BY 4.0).

## Licence

VisionGuard is free software under the **GNU Affero General Public License v3.0** – see [LICENSE](LICENSE). If you
run a modified version as a network service, you must offer its source code to its users. Model weights and test
videos are not part of this licence (see above).

## Development

Notes for developers and AI agents: [AGENTS.md](AGENTS.md) (German). Interfaces: [CONTRACT.md](CONTRACT.md) (German).
