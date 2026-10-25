# Plan: Objektgedächtnis, REST-API, Seite „Gegenstände“ und MCP-Server

## Grundentscheidung
Inventar-Objekte (Schlüssel, Gabel, Tisch …) erzeugen **keine Events**. Über die Pipeline `events.py` → `Processor._handle_track` würde die `events`-Tabelle mit Möbeln geflutet, und die Regeln (`rules.py`) würden ständig auslösen. Stattdessen gibt es einen eigenen Pfad:

Worker → `vg/internal/objects/{camera_id}` → `objects.py` (Zustand im Speicher plus Tabelle `object_sightings`) → REST, WebSocket und MCP.

Personen, Fahrzeuge und Tiere laufen weiter wie bisher über Events.

## CONTRACT.md – Ergänzungen (zuerst)

**MQTT, intern**
- `vg/internal/objects/{camera_id}` (Worker → Backend, QoS 0, etwa alle 2 s und bei Änderung):
  `{camera_id, ts, width, height, objects:[{key, label, score, bbox, zones, crop_path|null, stable_s}]}`
  - `key` ist ein stabiler Schlüssel je Objekt: `track_id` oder ein Hash aus Label und gerundetem Rasterplatz, weil statische Objekte selten sauber getrackt werden.
  - `crop_path` kommt höchstens einmal je Objekt alle 60 s.
- Kamera-Datensatz neu: `object_detection: bool` (Standard `false`), `object_labels: []` (leer heißt alle) und `room: str|null`. Diese drei Felder gehen auch an `/api/internal/cameras`.

**REST (`/api`)**
- `GET /objects?camera_id=&room=&label=&visible=` → `{items:[ObjectSighting], total}`
- `GET /objects/search?q=schlüssel&limit=` → Treffer mit `visible_now`, `camera`, `room`, `zone`, `relation`, `last_seen` und `crop_url`. Sortierung: sichtbare Treffer zuerst, dann nach `last_seen` absteigend.
- `GET /objects/labels` → `[{label, display_name, synonyms[], count_visible}]`
- `GET /objects/{id}/history?limit=`: liest aus der Sichtungs-Historie, siehe Schritt 2.
- `GET/POST/DELETE /tokens`: API-Token für MCP. Der Klartext wird nur einmal angezeigt, gespeichert wird ein SHA-256-Hash.

Datenmodell `ObjectSighting`:
```jsonc
{ "id":1,"camera_id":3,"camera_name":"Wohnzimmer","room":"Wohnzimmer","label":"keys",
  "display_name":"Schlüssel","score":0.71,"zones":["z1"],"zone_names":["Kommode"],
  "bbox":[..],"relation":{"type":"on","label":"dining table","display_name":"Tisch"}|null,
  "first_seen":"…","last_seen":"…","visible":true,"crop_url":"/media/objects/…jpg" }
```

**WebSocket:** `{"type":"objects","camera_id":3,"action":"appear|update|gone","items":[ObjectSighting]}`. Es wird nur bei Änderungen gesendet, nicht bei jedem Frame.

**Settings:** `retention_days_objects: 30` und `object_gone_s: 30` (nach dieser Zeit ohne Sichtung gilt ein Objekt als nicht mehr sichtbar). `object_gone_s` ist nötig, weil kleine Objekte flackern.

**Medien:** neuer Unterordner `objects/YYYY-MM-DD/`.

## Datenmodell (`backend/app/models.py`, nur additiv)
- `Camera`: neue Spalten `object_detection Boolean default False`, `object_labels JSONB default list`, `room String(100) nullable`.
  - **Wichtig:** `create_all` legt keine neuen Spalten in bestehenden Tabellen an. Deshalb kommt in `db.py:init_db` ein kleines `ALTER TABLE … ADD COLUMN IF NOT EXISTS`-Hilfsstück (gilt auch für die Spalten der anderen Teams, z. B. „Lernen gesperrt“ an `identities`).
- Neue Tabelle `object_sightings` (eine Zeile pro Kamera, Label und Ort, aktualisiert statt angehängt):
  - Spalten: `id, camera_id idx, label idx, key, bbox JSONB, zones JSONB, relation JSONB, score, crop_path, first_seen, last_seen idx, visible bool idx`
  - Unique-Index auf `(camera_id, label, key)`
- Neue Tabelle `object_history`: `sighting_id, camera_id, label, ts, bbox, zones`. Sie dient für „Wo war es zuletzt?“ über die Zeit; je Sichtung wird höchstens ein Eintrag pro 5 min geschrieben.
- Neue Tabelle `api_tokens`: `id, name, token_hash, created_at, last_used`.

## Backend-Dateien
1. **`backend/app/objects.py` (neu):** `ObjectMemory` mit RLock, analog zu `Processor` (`events.py:31`).
   - `handle(camera_id, msg)`: Abgleich über `key`; bei einer Abweichung des Mittelpunkts unter 5 % gilt die Box als derselbe Gegenstand. Danach Upsert in `object_sightings`, Schreiben in DB und History gedrosselt (höchstens alle 10 s).
   - `tick()`: setzt `visible=false` nach `object_gone_s` und sendet `gone` per WebSocket.
   - Lagebeziehung: Bei kleinen Objekten (keys, cell phone, remote, fork, spoon, cup …) wird geprüft, welche Fläche (dining table, bed, couch, chair, Teppich-Klasse) sie überlappt. Bedingung: Der untere Mittelpunkt der Objekt-Box liegt in der Flächen-Box, und die Überlappung beträgt mindestens 60 % der Objektfläche. Das ergibt `relation: on`, sonst `near` (Abstand unter 10 % der Bildbreite). Eine Heuristik ohne Tiefe, im UI als „vermutlich“ gekennzeichnet.
2. **`backend/app/mqtt_bus.py:101`:** neuer Zweig `kind == "objects"` → `self._enqueue(("objects", …))`; in `_work_loop` (Zeile 144) `object_memory.handle` aufrufen.
3. **`backend/app/main.py`:** `object_memory.tick()` in `_ticker` aufrufen. Den Router `objects` registrieren. Das MCP-Mount und `session_manager.run()` kommen in `lifespan` (Zeile 91).
4. **`backend/app/object_names.py` (neu):** deutsches Wörterbuch `label → (Anzeigename, Synonyme)`, z. B. `keys → Schlüssel, Schlüsselbund, Haustürschlüssel`, `dining table → Tisch, Esstisch`. Die Suche normalisiert (Kleinschreibung, ä→ae, Plural-s/-e/-n) und nutzt `rapidfuzz` (bereits in `requirements.txt`) als Fuzzy-Fallback. Eigene Synonyme lassen sich per Setting `object_synonyms: {label: [..]}` erweitern.
5. **`backend/app/routers/objects.py` (neu):** Endpunkte wie oben, mit `dependencies=[Depends(require_user)]` wie in `routers/events.py:15`.
6. **`backend/app/routers/cameras.py` und `schemas.py`:** neue Kamerafelder. `routers/internal.py` ergänzen.
7. **`backend/app/retention.py:20`:** löscht `object_history` älter als `retention_days_objects` und `object_sightings` mit `visible=false`, deren `last_seen` älter als `retention_days_objects` ist. Der Medienordner `objects` kommt in `MEDIA_SUBDIRS`.
8. **`backend/app/mcp_server.py` (neu):**
   - Neue Abhängigkeit in `requirements.txt`: **`mcp==2.0.1`**. Stand Oktober 2026 ist v2 stabil, 1.x nur noch in Wartung; `FastMCP` heißt jetzt `MCPServer` (`from mcp.server import MCPServer`). Die genauen Importe und Parameter vor dem Pinnen noch prüfen, ein Fallback wäre `mcp>=1.28,<2`.
   - Ablauf: `mcp = MCPServer("VisionGuard")` → `app.mount("/mcp", mcp.streamable_http_app(streamable_http_path="/", transport_security=TransportSecuritySettings(allowed_hosts=[…])))`. Der Lifespan des Mounts läuft nicht von selbst, deshalb im FastAPI-`lifespan` `async with mcp.session_manager.run(): yield` aufrufen.
   - Auth über eine eigene ASGI-Middleware vor dem Mount: `Authorization: Bearer <token>` gegen `api_tokens`, sonst 401. Laut SDK-Doku sind `custom_route`s nicht authentifiziert, also keine eigenen Routen verwenden.
   - Werkzeuge, alle lesen über die Funktionen aus `objects.py` (keine HTTP-Schleife):
     - `find_object(query)` → Text wie „Schlüssel: aktuell sichtbar in Kamera Wohnzimmer (Zone Kommode), vermutlich auf dem Tisch, seit 14:02“ plus strukturierte Daten
     - `list_visible_objects(camera?|room?)`
     - `get_camera_snapshot(camera)` → Bild über `go2rtc` `frame.jpeg`, wie `routers/cameras.py:86`
     - `who_is_where()` → aktive Events mit Identität aus `Event.active`
9. **`frontend/nginx.conf`:** neue `location /mcp` → Backend, mit `proxy_buffering off`, `proxy_read_timeout 3600` und ohne Cookie-`auth_request`, weil die Anmeldung per Token läuft.

## Frontend
- `src/lib/types.ts`, `api.ts`, `mock.ts`: Typen `ObjectSighting`, `ApiToken` und die neuen Kamerafelder.
- `src/lib/useLiveSocket.ts` und `store.ts`: WebSocket-Typ `objects` im Store je Kamera halten.
- `src/lib/labels.ts`: `DE` um die COCO-Klassen bzw. das erweiterte Vokabular ergänzen und eine Gruppe `object` hinzufügen.
- `src/pages/Objects.tsx` (neu), Route `gegenstaende` in `App.tsx:48`, Navigationspunkt in `components/Layout.tsx:38` (Icon `Package`).
- `pages/admin/Cameras.tsx`: Schalter „Gegenstandserkennung“, Feld „Raum“, Label-Auswahl.
- `pages/admin/Settings.tsx`: Abschnitt „API-Token (MCP)“ und Aufbewahrung für Sichtungen.

UI-Skizze der Seite „Gegenstände“:
```
[ Suche: „Wo ist …?“ ______ ] [Raum ▾] [Kamera ▾] [☐ nur sichtbar]
┌ Schlüssel ● sichtbar ───────┐ ┌ Gabel ○ zuletzt vor 2 h ┐
│ [Crop]  Wohnzimmer · Kommode │ │ [Crop] Küche            │
│ vermutl. auf: Tisch  14:02   │ │ Verlauf ▸  Live ▸        │
└──────────────────────────────┘ └──────────────────────────┘
Gruppiert nach Raum; Klick → Live-Ansicht mit hervorgehobener Box.
```

## Schritte (Aufwand)
1. CONTRACT.md ergänzen – S
2. Modelle und Spalten-Nachrüstung in `init_db` – S
3. `object_names.py` mit Synonymen und Suche – M
4. `objects.py` (Zustand, Abgleich, Lagebeziehung, Drosselung) und MQTT-Zweig – M/L
5. REST-Router, `schemas`, `internal` – M
6. Retention – S
7. WebSocket-Nachricht und Store – S
8. Seite „Gegenstände“ und Kamera-Admin – M
9. API-Token und MCP-Server mit Auth-Middleware und nginx-Route – M
10. End-to-end-Prüfung – M
    - Das MQTT-Topic manuell mit `mosquitto_pub` füttern, solange der Worker-Teil noch fehlt.
    - MCP mit `npx @modelcontextprotocol/inspector` gegen `http://localhost:8080/mcp` testen.

## Risiken und offene Fragen
- **Abgleich statischer Objekte:** Flackernde Erkennungen erzeugen Duplikate oder ein ständiges Wechseln zwischen sichtbar und weg. Gegenmittel sind `object_gone_s` und die Hysterese im Abgleich; die Worker-Seite muss den `key` liefern. Das ist die Schnittstelle zum Worker-Plan und muss mit ihm abgestimmt werden.
- **Vokabular:** Mit Open-Vocabulary-Erkennung (YOLO-World/YOLOE) sind die Labels freier Text. Das Wörterbuch braucht dann einen Fallback „Label = Anzeigename“. Die deutsche Suche könnte sogar direkt als Prompt an den Worker gehen. Offen: Gibt es ein festes Vokabular?
- **„auf dem Tisch“** ist reine 2D-Heuristik, deshalb im UI und in MCP als „vermutlich“ formulieren.
- **MCP-Exposition:** Wenn der Agent nicht auf demselben Rechner läuft, muss `allowed_hosts` gepflegt werden. Snapshots gehen an externe LLMs, das braucht einen Hinweis in der Doku.
- **MCP-SDK v2:** recht neu. Die API vor dem Pinnen mit einem kleinen Probe-Container prüfen; die Websuche lieferte die Version nur aus Drittquellen.
- **Datenschutz:** Gegenstands-Crops sind über `/media/` ohne Login abrufbar (bekannte Grenze laut AGENTS.md).
- **Spalten-Nachrüstung ohne Alembic:** Sie berührt auch die Pläne der anderen Bereiche, daher ein gemeinsames Hilfsstück vereinbaren.

Quellen:
- [MCP Python SDK – Einbindung in eine ASGI-App (v2)](https://py.sdk.modelcontextprotocol.io/v2/run/asgi/)
- [MCP Python SDK – Einbindung in eine ASGI-App](https://py.sdk.modelcontextprotocol.io/run/asgi/)
- [mcp 2.0.1 auf PyPI](https://pypi.org/project/mcp/2.0.1/)
- [mcp 2.0.0 auf PyPI](https://pypi.org/project/mcp/2.0.0/)
- [Release-Notes zum MCP Python SDK (Releasebot)](https://releasebot.io/updates/modelcontextprotocol/python-sdk)
- [python-sdk PR #1712](https://github.com/modelcontextprotocol/python-sdk/pull/1712)