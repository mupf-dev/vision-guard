# VisionGuard – Backend

FastAPI-Dienst (Python 3.12) für REST-API, WebSocket, MQTT-Bus, Regel-Engine und Identitätsabgleich.
Schnittstellen: siehe `../CONTRACT.md` (verbindlich).

## Aufbau (`app/`)

| Datei | Aufgabe |
|---|---|
| `main.py` | App, Lifespan (DB anlegen, Demo-Daten, MQTT, go2rtc-Sync, 1-s-Tick, stündliches Aufräumen), `/api/ws`, `/media` |
| `config.py` | Env-Variablen (`VG_*`, `TZ`), Standard-Settings |
| `db.py`, `models.py` | SQLAlchemy 2 (sync, psycopg3), `create_all` beim Start |
| `schemas.py`, `serializers.py` | Pydantic-v2-Eingaben, JSON-Ausgaben laut Contract |
| `auth.py`, `routers/auth.py` | Admin aus Env, JWT im httpOnly-Cookie `vg_session` (7 Tage) |
| `routers/*.py` | cameras, rules, events, identities, misc (settings/stats/system), internal (Worker) |
| `go2rtc.py` | Stream-Sync `cam_N` / `cam_N_detect`, `frame.jpeg`-Proxy, URL-Probe über temporären Stream `probe_<hex>` |
| `mqtt_bus.py` | paho-mqtt 2, abonniert `vg/internal/#`, LWT `{prefix}/available` |
| `events.py` | Track → Event (ein Track = ein Event), Regel-Auswertung, Aktionen, Zähler `{prefix}/{kamera_slug}/{label}` |
| `rules.py` | Bedingungen (Zeitplan inkl. über Mitternacht, Identität, Zonen, Dwell), Registry für Trigger/Bedingungen, Zeit-Trigger-Fälligkeit, Templates, Webhooks |
| `rule_conditions.py` | Auswertung `conditions`: Gegenstand da/weg, unberührt, Regen, kam aus Zone (A1) |
| `weather.py` | Regenwahrscheinlichkeit von Open-Meteo (ohne Key, Cache 30 min, `VG_WEATHER_URL` für Tests) |
| `identity.py` | Kosinus-Abgleich (numpy, In-Memory-Cache) für face/pet, Levenshtein für Kennzeichen |
| `retention.py` | Events älter `retention_days_events`, Dateien in snapshots/crops/clips älter `retention_days_media` |
| `ws.py`, `state.py`, `registry.py` | WebSocket-Hub, flüchtiger Status, Kamera-Cache |

## Verhalten (Kurzfassung)

- **Demo**: `VG_DEMO_CAMERAS=1` und leere Kameratabelle beim ersten Start → „Demo Eingang“, „Demo Straße“
  und zwei Beispielregeln. Einmalig (Flag `seeded` in Tabelle `kv`).
- **go2rtc**: Sync beim Start (wartet auf go2rtc), bei jeder Kamera-Änderung und alle 30 s
  (falls go2rtc neu gestartet wurde und die per API angelegten Streams verloren hat).
- **Regeln**: `enter`/`dwell` feuern höchstens einmal pro Track und Regel, `leave` einmal.
  Cooldown pro Regel+Kamera. Dwell wird bei Updates und jede Sekunde geprüft.
  Snapshot: Auftrag an Worker (`vg/internal/cmd/{id}`); ohne Antwort nach 5 s holt das Backend
  selbst ein Bild von go2rtc. Clip: höchstens ein Auftrag pro Event.
- **Hinweise (A1)**: Regeln mit `conditions` prüfen diese erst `condition_delay_s` nach dem Auslösen (eigener
  Thread-Pool, nie im Track-Thread); erfüllt → Aktionen + Notification. Zeit-Regeln (`trigger=time`) prüft der
  Tick alle 20 s; der letzte Lauf steht in `kv.rule_time_runs`, verpasste Zeitpunkte werden bis 15 min nachgeholt.
  `POST /api/rules/check` prüft einen Entwurf sofort (UI „Jetzt prüfen“).
- **Virtuelle Sensoren (A3/C4)** (`sensors.py`): Zustandsautomat im Speicher (DB wird im Effekt-Thread
  nachgezogen). Ein abweichender Rohzustand braucht `confirm_n` Messungen in Folge; verdeckte Messungen zählen nicht.
  `inventory_count` zählt `present`-Gegenstände im Bereich (Tick je `interval_s`), `vlm` fragt `vlm.ask` (A2) nur bei
  Bildänderung, offenem Kandidaten oder nach `vlm_max_age_s`; `verify_vlm` lässt einen billigen Wechsel vom
  Bildmodell bestätigen. Bestätigter Wechsel → `sensor_history`, Standbild (go2rtc, ≤ 1/min), WS `sensor`, MQTT
  `{prefix}/sensor/{slug}` (+ `/attributes`), Regeln mit `trigger: sensor` (nur zwischen bekannten Zuständen).
  Vorräte (`supply`) → Einkaufsliste (`shopping.py`, `shopping_list_auto`) und Hinweis `source: sensor`.
  Ohne Messwerte seit `max(5 × interval_s, 120 s)` → `unknown`.
- **Identitäten**: neue unbenannte → „Person #N“ / „Tier #N“ (Zähler je Art), „Kennzeichen <Text>“.
  Max. 20 Samples je Identität (beste Qualität bleibt), Crops werden nach `identities/<id>/` kopiert.
  `identify` mit `camera_id`+`track_id` verknüpft die Identität direkt mit dem aktiven Event.
- Aktive Events aus einem früheren Lauf werden beim Start beendet; Tracks ohne Lebenszeichen
  (60 s) werden automatisch beendet.

## Lokal testen

```bash
docker compose up -d --build db mosquitto go2rtc backend
docker compose exec backend python -c "import httpx;print(httpx.get('http://localhost:8000/api/health').json())"
# Worker simulieren
docker compose exec mosquitto mosquitto_pub -t vg/internal/track/1 -q 1 -m \
 '{"type":"start","camera_id":1,"track_id":1,"label":"person","score":0.9,"ts":1761300000,"first_seen":1761300000,"zones":[],"entered_zones":[],"left_zones":[],"identity":null,"attributes":{},"crop_path":null,"best_score":0.9}'
```

API-Doku: `/api/docs` (Swagger).
