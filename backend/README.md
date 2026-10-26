# VisionGuard – Backend

FastAPI service (Python 3.12) for the REST API, WebSocket, MQTT bus, rule engine and identity matching.
Interfaces: see `../CONTRACT.md` (German, binding).

## Structure (`app/`)

| File | Purpose |
|---|---|
| `main.py` | App, lifespan (create DB, demo data, MQTT, go2rtc sync, 1-s tick, hourly cleanup), `/api/ws`, `/media` |
| `config.py` | Env variables (`VG_*`, `TZ`), default settings |
| `db.py`, `models.py` | SQLAlchemy 2 (sync, psycopg3), `create_all` at startup |
| `schemas.py`, `serializers.py` | Pydantic v2 inputs, JSON outputs as per the contract |
| `auth.py`, `routers/auth.py` | Admin from env, JWT in the httpOnly cookie `vg_session` (7 days) |
| `routers/*.py` | cameras, rules, events, identities, misc (settings/stats/system), internal (worker) |
| `go2rtc.py` | Stream sync `cam_N` / `cam_N_detect`, `frame.jpeg` proxy, URL probe via temporary stream `probe_<hex>` |
| `mqtt_bus.py` | paho-mqtt 2, subscribes to `vg/internal/#`, LWT `{prefix}/available` |
| `events.py` | Track → event (one track = one event), rule evaluation, actions, counters `{prefix}/{kamera_slug}/{label}` |
| `rules.py` | Conditions (schedule incl. across midnight, identity, zones, dwell), registry for triggers/conditions, time-trigger due check, templates, webhooks |
| `rule_conditions.py` | Evaluation of `conditions`: item present/gone, untouched, rain, came from zone (A1) |
| `weather.py` | Rain probability from Open-Meteo (no key, 30 min cache, `VG_WEATHER_URL` for tests) |
| `identity.py` | Cosine matching (numpy, in-memory cache) for face/pet, Levenshtein for license plates |
| `reid.py`, `routers/presence.py` | C2: RAM gallery (presence_key ↔ embeddings/tracks/identity), `POST /api/internal/reid`, `GET/DELETE /api/presence`, WS `presence` |
| `retention.py` | Events older than `retention_days_events`, files in snapshots/crops/clips older than `retention_days_media` |
| `ws.py`, `state.py`, `registry.py` | WebSocket hub, volatile status, camera cache |

## Behavior (summary)

- **Demo**: `VG_DEMO_CAMERAS=1` and an empty camera table on first start → "Demo Eingang" (Demo entrance),
  "Demo Straße" (Demo street) and two example rules. Once only (flag `seeded` in table `kv`).
- **go2rtc**: sync at startup (waits for go2rtc), on every camera change and every 30 s
  (in case go2rtc was restarted and lost the streams created via the API).
- **Rules**: `enter`/`dwell` fire at most once per track and rule, `leave` once.
  Cooldown per rule+camera. Dwell is checked on updates and every second.
  Snapshot: request to the worker (`vg/internal/cmd/{id}`); if there is no answer after 5 s, the backend
  fetches a frame from go2rtc itself. Clip: at most one request per event.
- **Notifications (A1)**: rules with `conditions` check them only `condition_delay_s` after firing (separate
  thread pool, never in the track thread); fulfilled → actions + notification. Time rules (`trigger=time`) are checked
  by the tick every 20 s; the last run is stored in `kv.rule_time_runs`, missed times are caught up for up to 15 min.
  `POST /api/rules/check` checks a draft immediately (UI "Jetzt prüfen" (Check now)).
- **Virtual sensors (A3/C4)** (`sensors.py`): in-memory state machine (the DB is updated in the effect
  thread). A differing raw state needs `confirm_n` consecutive measurements; occluded measurements do not count.
  `inventory_count` counts `present` items in the region (tick per `interval_s`), `vlm` asks `vlm.ask` (A2) only on
  image change, an open candidate or after `vlm_max_age_s`; `verify_vlm` has a cheap state change confirmed by the
  vision model. C4-VLM (W4, `vlm_measure.py`): `vlm` with `params.measure` `count` (number, `min_count` ≤ 3)
  or `fill` (only ok/empty); `inventory_count` with `verify_vlm` has changes recounted by the vision model (≤ 1×/5 min).
  Measurement tool: `scripts/vlm_supply_bench.py`. Confirmed change → `sensor_history`, still image (go2rtc, ≤ 1/min), WS `sensor`, MQTT
  `{prefix}/sensor/{slug}` (+ `/attributes`), rules with `trigger: sensor` (only between known states).
  Supplies (`supply`) → shopping list (`shopping.py`, `shopping_list_auto`) and notification `source: sensor`.
  No measurements for `max(5 × interval_s, 120 s)` → `unknown`.
- **Identities**: new unnamed ones → "Person #N" / "Tier #N" (Animal #N) (counter per kind), "Kennzeichen <Text>" (License plate <text>).
  Max. 20 samples per identity (best quality is kept), crops are copied to `identities/<id>/`.
  `identify` with `camera_id`+`track_id` links the identity directly to the active event.
- **Presence (C2)**: track hook for persons/dogs/cats, every track belongs to exactly one `presence_key`.
  ReID embeddings (only `reid_enabled` cameras) are matched against the gallery (cosine, maximum per entry,
  threshold `reid_match_threshold`), only against entries without a concurrently active track. Face takes precedence:
  an own identity names the entry or detaches a track linked via ReID. An identity is passed on via ReID only
  from threshold + 0.1. Everything in RAM only, expiry after `reid_ttl_min`, restart = empty.
- Active events from a previous run are ended at startup; tracks without a sign of life
  (60 s) are ended automatically.

## Testing locally

```bash
docker compose up -d --build db mosquitto go2rtc backend
docker compose exec backend python -c "import httpx;print(httpx.get('http://localhost:8000/api/health').json())"
# Simulate the worker
docker compose exec mosquitto mosquitto_pub -t vg/internal/track/1 -q 1 -m \
 '{"type":"start","camera_id":1,"track_id":1,"label":"person","score":0.9,"ts":1761300000,"first_seen":1761300000,"zones":[],"entered_zones":[],"left_zones":[],"identity":null,"attributes":{},"crop_path":null,"best_score":0.9}'
```

API docs: `/api/docs` (Swagger).
