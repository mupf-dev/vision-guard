# VisionGuard – Gesamtplan: Gesichter, API-Zugänge, Gegenstände und MCP

## Entscheidungen des Nutzers (haben Vorrang vor allem Folgenden)

- **Merge mit Bildauswahl:** Die Events der Quelle wandern **nicht** mit. Nur die ausgewählten Samples gehen
  (mit `manual=true`) zum Ziel; die Quelle bleibt mit den restlichen Samples und ihren Events bestehen. Nur wenn
  **alle** Samples übernommen werden bzw. `sample_ids` `null` ist (klassischer Merge), wird die Quelle gelöscht und
  ihre Events wandern zum Ziel. (Ersetzt „Events wandern immer komplett“ in Abschnitt 3 und Teilplan 01.)
- **Inventar-Vokabular:** die 50 Gegenstände aus `vokabular-50.md` als Standard (deutscher Name → englische Prompts).
- **Lizenz/Nutzung:** rein privat (AGPL/YOLOE, MobileCLIP, `buffalo_l` damit unkritisch).
- **Erreichbarkeit:** MCP und REST-API sollen **auch von außen** erreichbar sein, abgesichert mit API-Keys, die im
  Admin-Bereich („API-Zugänge“) erzeugt und widerrufen werden.
- **MCP als eigener Dienst `mcp`** (Verzeichnis `mcp/`), damit die gepinnten Backend-Versionen (fastapi 0.115.6,
  pydantic 2.10.4) nicht kollidieren. Er reicht den Bearer-Key des Clients an die Backend-REST-API durch; nginx:
  `location /mcp` → mcp-Dienst. (Ersetzt Phase 6 „`backend/app/mcp_server.py`, eingehängt unter `/mcp`“.)
- **Testkamera:** id=3 „Wohnzimmer“, 2880×1616, go2rtc-Stream `cam_3`, Blick auf ein Tischchen mit Lampe, Buch und
  Fernbedienung.
- **Hardware:** RTX A1000 mit 6 GB VRAM, im Betrieb bereits ca. 2,7 GB belegt (Budget für YOLOE entsprechend knapp).
- Die verbindlichen Schnittstellen dazu stehen in `CONTRACT.md`, Abschnitt
  „Nachträge: Gesichter-Lernsperre, API-Keys, Gegenstände (Inventar), MCP“.

## 1. Kurzfassung

Als Erstes kommen die Gesichter. Es gibt dann zwei Ansichten, „Unbekannt“ und „Bekannt“. „Bekannt“ heißt wie bisher „hat einen Namen“. Eine Lernsperre verhindert, dass für eine Person automatisch neue Samples gespeichert werden. Neue Samples kommen nur noch über einen Merge, bei dem man die Bilder selbst auswählt.

Danach folgen API-Tokens und ein geschützter Zugriff auf `/media`. Beides ist nötig, bevor ein KI-Agent Daten und Bilder bekommt.

Auf ausgewählten Kameras läuft dann eine langsame Inventar-Erkennung. Sie arbeitet mit YOLOE-11 und offenem Vokabular, nutzt Kacheln und holt das Bild in voller Auflösung aus dem Ringpuffer. Der Worker meldet einen stabilisierten Zustand je Kamera. Das Backend speichert diesen Zustand dauerhaft im „Gegenstandsgedächtnis“. Abfragen gehen über REST, WebSocket, die neue Seite „Gegenstände“ und einen MCP-Server („Wo ist mein Schlüssel?“). Home Assistant kommt ausdrücklich erst später.

## 2. Phasen und Abhängigkeiten

| Phase | Inhalt | hängt ab von |
|---|---|---|
| **0** | Gemeinsame Spalten-Nachrüstung in `db.py::init_db` (S) | – |
| **1** | Gesichter: Lernsperre, Ansichten, Merge mit Bildauswahl, Vorschläge | 0 |
| **2** | API-Tokens, Bearer-Auth, geschütztes `/media`, signierte URLs | 0 |
| **3** | Machbarkeitstest YOLOE an der 2K-Kamera (Go/No-Go) | – (parallel zu 1/2, nach dem laufenden Worker-Build) |
| **4** | Worker: Inventar-Pipeline | 3 |
| **5** | Backend: Gegenstandsgedächtnis, REST, WS, Seite „Gegenstände“ | 0; Worker-Teil kann per `mosquitto_pub` simuliert werden |
| **6** | MCP-Server | 2, 5 |
| **7** (später) | Home Assistant (MQTT Discovery für Gegenstände), Grounding-DINO auf Nachfrage | 5 |

## 3. Konsolidierte CONTRACT.md-Ergänzungen

**Widersprüche zwischen den Teilplänen und wie ich sie auflöse:**
- **Begriff:** einheitlich **`inventory`** statt `objects`. `objects` ist im Vertrag schon belegt (`detections.objects`, `Obj`) und würde Verwechslungen erzeugen. Die Anzeige auf Deutsch heißt „Gegenstände“.
- **Topic und Format:** Ich nehme den Worker-Vorschlag, `vg/internal/inventory/{camera_id}`, QoS 1, retain. Die Nachricht enthält den **vollständigen Zustand nach jedem Durchlauf**, keine Deltas. Das ist idempotent und übersteht Neustarts.
- **Wer stabilisiert:** nur der **Worker** (`confirm_n`, `miss_m`, Verdeckung durch Personen). Das Backend übernimmt den `state` und braucht kein eigenes `object_gone_s`. Es setzt einen Gegenstand nur auf `stale`, wenn von der Kamera länger als 3 × Intervall nichts kommt. Doppelte Hysterese an zwei Stellen würde Flackern erzeugen.
- **Schlüssel:** `item_id` (UUID vom Worker). Das Backend gleicht nach einem Worker-Neustart (neue `session`) über Label und IoU ab.
- **Kamerafelder:** `inventory_*` statt `object_detection`/`object_labels`. Dazu kommt `room` aus dem Gedächtnis-Plan.
- **Tokens:** nur ein Modell, nämlich das aus dem Token-Plan (Scopes, Prefix, Widerruf). Der Gedächtnis-Plan muss keine eigene Tabelle `api_tokens` mehr anlegen.

**Ergänzungen:**

```text
Auth:   Bearer `vgt_<prefix>_<secret>`, Scopes read|write; Nicht-GET verlangt write.
        /api/tokens* nur mit Session-Cookie. /media/* verlangt Cookie | Bearer(read) | ?exp=&sig=.
Identity:  + learning_locked: bool          Sample: + manual: bool, quality (bisher nur im Nachtrag)
REST:   PATCH /identities/{id} {name?, notes?, learning_locked?}
        POST  /identities/{id}/merge {into_id, sample_ids?: int[]|null}   // Events wandern immer komplett
        GET   /identities/{id}/suggestions?limit=3 → [{identity_id,display_name,cover_url,score,likely}]
        GET   /identities?kind=face&named=&with_suggestion=1
        GET/POST /tokens, DELETE /tokens/{id};  POST /media/sign {paths,ttl_s}
        GET /events?...&signed=1
Camera: + inventory_enabled(false), inventory_interval_s(3; 1–60), inventory_labels([] = global),
        inventory_min_score(0.35), inventory_tiling(true), room(null)   → auch /api/internal/cameras
        (Backend liefert dort das aufgelöste Vokabular `inventory_vocab` mit)
Settings: + inventory_vocabulary[], inventory_confirm_n 2, inventory_miss_m 3,
          retention_days_inventory 30, object_synonyms {}, media_link_ttl_h 24, public_base_url null
MQTT intern: vg/internal/inventory/{cid} (QoS1, retain, je Durchlauf, voller Zustand; Format wie Worker-Plan,
          plus "stopped": true beim Abschalten); vg/internal/system.models.inventory, perf.inventory
REST Gegenstände: GET /inventory?camera_id=&room=&label=&state=  → {items,total}
          GET /inventory/search?q=&limit=     GET /inventory/labels     GET /inventory/{id}/history
WS:     {"type":"inventory","camera_id","action":"appear|update|gone","items":[InventoryItem]} (nur bei Änderung)
Medien: Unterordner inventory/YYYY-MM-DD/
MCP:    /mcp (Streamable HTTP, Bearer read), Tools find_object, list_visible_objects,
        get_camera_snapshot, who_is_where
```

## 4. Phasen im Detail

### Phase 0 – Spalten-Nachrüstung (S)
- **Datei:** `backend/app/db.py:38`. Dort läuft nur `create_all`, das habe ich geprüft.
- **Schritt:** Eine Liste mit `ALTER TABLE … ADD COLUMN IF NOT EXISTS …` direkt nach `create_all` ausführen. Alle Phasen tragen ihre Spalten nur dort ein.
- **Abnahme:** Das Backend startet zweimal hintereinander gegen die bestehende Datenbank ohne Fehler, und `\d cameras` zeigt die neuen Spalten.

### Phase 1 – Gesichter (M+, ca. 1,5–2 Tage)
1. **Lernsperre (S):**
   - Spalten `Identity.learning_locked` und `IdentitySample.manual` anlegen.
   - In `identity.py:209` (Treffer-Zweig) `_add_sample` nur aufrufen, wenn die Person nicht gesperrt ist.
   - `invalidate(kind)` nur auslösen, wenn tatsächlich ein Sample hinzukam.
   - Dazu das PATCH-Schema und den Serializer anpassen.
2. **Merge mit `sample_ids` (M):**
   - `identity.py:234` verschiebt heute *alle* Samples (geprüft).
   - Neu: Nur die ausgewählten Samples wandern und bekommen `manual=True`. Die übrigen Samples der Quelle werden gelöscht.
   - `prune_samples` (Z. 127) verdrängt zuerst nicht-manuelle Samples.
   - Mehr als 20 manuelle Samples ergeben 422.
3. **Suggestions (M):**
   - Die Samples der Quelle werden gegen den vorhandenen Embedding-Cache `_matrix(kind)` gerechnet. Ziele sind nur benannte Identitäten.
   - Zusätzlich eine Batch-Variante für die Personenliste.
4. **Frontend (M–L):**
   - `types.ts`, `api.ts`, `mock.ts` anpassen.
   - `pages/Identities.tsx`:
     - Segment „Unbekannt | Bekannt“.
     - Schloss-Icon an gesperrten Personen.
     - Schalter „Lernen gesperrt“.
     - Merge-Dialog in zwei Schritten: Ziel wählen, Vorschläge oben; danach das Bilderraster mit Checkboxen.
- **Abnahme mit testcam `cam1`:**
  - Person benennen und sperren. 5 Minuten laufen lassen: `sample_count` bleibt gleich, neue Events tragen trotzdem die Identität.
  - Eine unbekannte Person mit 3 Bildern zusammenführen: `sample_count` steigt um 3, die Samples zeigen das Badge „manual“.
  - `corepack pnpm build` läuft fehlerfrei.

### Phase 2 – API-Zugänge und /media (M–L, ca. 1,5–2 Tage)
1. **Tokens und Auth (M):**
   - Neues Modell `ApiToken`.
   - In `auth.py:43` lernt `require_user` zusätzlich Bearer-Tokens. Weil alle Router diese Abhängigkeit schon nutzen, ist das eine zentrale Stelle.
   - Prüfung der Scopes nach HTTP-Methode.
   - Neuer Router `routers/tokens.py`.
2. **Medien-Route (M):**
   - `main.py:144` (StaticFiles-Mount, geprüft) wird durch `routers/media.py` mit `FileResponse` ersetzt.
   - Schutz gegen Pfad-Traversal.
   - Zugriff per Cookie, Bearer oder Signatur.
   - Range-Anfragen testen (FastAPI 0.115.6 bringt ein Starlette mit Range-Unterstützung mit).
3. **Signierte URLs nach außen (M):**
   - Signierte Links in Webhooks und `{prefix}/events`.
   - Neue Template-Platzhalter `{snapshot_url}` und `{clip_url}`.
   - Übergangsschalter `VG_MEDIA_PUBLIC`.
4. **Frontend (M):**
   - Neue Seite `admin/Tokens.tsx` („API-Zugänge“).
   - In AGENTS.md die bekannte Grenze „Medien ohne Login“ streichen.
- **Abnahme (curl-Matrix):**
  - ohne Anmeldung → 401; widerrufenes Token → 401;
  - `read`-Token: GET → 200, PUT → 403;
  - `/media` ohne Anmeldung → 401, mit Cookie oder Signatur → 200;
  - abgelaufene Signatur → 403;
  - Clip mit Range → 206;
  - Bilder in der Oberfläche werden weiter angezeigt (Screenshot).

### Phase 3 – Machbarkeitstest (M, ca. 1 Tag)
- **Datei:** `worker/vg_worker/objtest.py`, nach dem Muster von `selftest.py`.
- **Vergleich:** YOLOE-11s und 11m, jeweils mit Text-Prompts und prompt-frei. Auflösungen: 1280 ganzes Bild, 2×2 Kacheln à 960, 3×2 Kacheln à 640.
- **Bildquelle:** das Keyframe in voller Auflösung, nicht das Ingest-Bild. `ingest.py:143` skaliert auf 1920 px herunter (geprüft).
- **Vorher klären:** ob `set_classes` in 8.3.253 zur Laufzeit CLIP von GitHub nachlädt. Falls ja, gehört das als eigene Schicht ins Dockerfile.
- **Go/No-Go:**
  - Schlüssel auf 3 m Abstand in mindestens 60 % der Bilder erkannt.
  - Lasttest mit 16 Kameras bleibt bei 80 fps, `batch_ms` steigt um höchstens 20 %.
  - VRAM-Spitze unter 4,5 GB.

### Phase 4 – Worker-Inventar (L, ca. 3–4 Tage)
1. **`inventory.py::KeyframeGrabber` (M):**
   - Holt das Bild aus dem letzten fertigen 2-s-TS-Segment des Ringpuffers. Laut `recorder.py:68–70` landen dort `%s.ts`-Segmente des Hauptstreams (geprüft).
   - Ersatzweg: ein eigenes ffmpeg mit `-skip_frame nokey`.
2. **`InventoryEngine` (M):**
   - Muster von `ModelWorker`: höchstens ein offener Auftrag je Kamera.
   - Läuft abwechselnd mit `_infer_loop`.
   - Rechenbudget `MAX_BUSY` 25 %.
   - YOLOE wird erst geladen, wenn die erste Kamera `inventory_enabled` hat. Fehler des Modells stoppen die Detektion nicht.
3. **`InventoryState` (M):**
   - Zuordnung über Label und IoU, `confirm_n` / `miss_m`.
   - Verdeckung über `tracker.snapshot_objects()` (`tracking.py:281`, vorhanden).
   - Crops höchstens 1× pro Minute.
4. **`pipeline.py` (S):**
   - Inventar-Felder kommen nicht in `_STREAM_KEYS` (Z. 23), damit eine Änderung kein ffmpeg neu startet.
   - Status in `models_status` und `perf` aufnehmen.
- **Abnahme:**
  - Mit `mosquitto_sub -t 'vg/internal/inventory/#'` mitlesen: Schlüssel hinlegen → nach ≤ 2 Intervallen `present`; wegnehmen → nach `miss_m` Intervallen `missing`; eine Person davorstellen → kein `missing`.
  - Lasttest wie in Phase 3.

### Phase 5 – Gegenstandsgedächtnis und UI (L, ca. 3 Tage)
1. **Tabellen (S):** `inventory_items` (eine Zeile je Gegenstand) und `inventory_history` (höchstens ein Eintrag alle 5 min).
2. **`backend/app/inventory.py` (M/L):**
   - Übernimmt den Zustand aus MQTT. Neuer Zweig `kind == "inventory"` in `mqtt_bus.py:115/148`.
   - `tick()` für `stale`.
   - Lagebeziehung „vermutlich auf …“.
3. **`inventory_names.py` (M):** deutsche Namen und Synonyme. Suche mit `rapidfuzz`, das bereits gepinnt ist (geprüft).
4. **Router, Schemas, `internal.py`, Retention (M):** `inventory` in `MEDIA_SUBDIRS` aufnehmen (`retention.py:17`).
5. **Frontend (M):**
   - Neue Seite `Objects.tsx` (Route `gegenstaende`).
   - In der Kamera-Verwaltung: Schalter, Feld „Raum“, Label-Auswahl.
   - WS-Store für Nachrichten vom Typ `inventory`.
- **Abnahme:**
  - Zunächst mit `mosquitto_pub` Nachrichten einspielen, danach echt mit dem Worker.
  - `GET /api/inventory/search?q=schlüssel` liefert die Kamera, `visible: true` und eine `crop_url`.
  - Die Seite aktualisiert sich live.
  - `corepack pnpm build` läuft fehlerfrei.

### Phase 6 – MCP (M, ca. 1–1,5 Tage)
- **Server:** `backend/app/mcp_server.py`, eingehängt unter `/mcp`. Der `session_manager.run()` kommt in den FastAPI-`lifespan` (`main.py:89`).
- **Auth:** Middleware für Bearer-Tokens aus Phase 2.
- **Medien:** Bilder gehen als signierte URLs mit 1 h Laufzeit hinaus.
- **nginx:** neue `location /mcp` ohne Pufferung und mit langem Timeout.
- **Vor dem Pinnen:** Prüfen, welche Versionen der MCP-SDK für pydantic und starlette verlangt. Siehe Risiken.
- **Abnahme:**
  - MCP-Inspector gegen `http://localhost:8080/mcp`: `find_object("Schlüssel")` antwortet mit „aktuell sichtbar in Kamera X …“.
  - Ohne Token → 401.

## 5. Offene Fragen an den Nutzer

1. **Vokabular:** Welche 20–50 Gegenstände sind wirklich wichtig (z. B. Schlüssel, Geldbörse, Brille, Handy, Fernbedienung)? Sie bekommen eigene Schwellen und Vorrang. Der Rest nutzt eine allgemeine Liste.
2. **Medien nach außen:** Dürfen bestehende Webhook- und MQTT-Empfänger nach Phase 2 nur noch signierte Links bekommen? Oder soll `VG_MEDIA_PUBLIC=1` vorerst der Standard bleiben?
3. **Merge mit nur einem Teil der Bilder:** Sollen die Events der Quelle trotzdem immer zur Zielperson wandern? Oder braucht es die Option „Events bei der Quelle lassen“ bzw. „einzelnes Sample verschieben“?
4. **MCP-Erreichbarkeit:** Läuft der Agent nur lokal, oder muss `/mcp` von außen erreichbar sein (Reverse-Proxy/TLS, `allowed_hosts`)? Davon hängen `public_base_url` und das Sicherheitsniveau ab.
5. **Lizenz:** Ist die Nutzung privat bzw. nicht-kommerziell? YOLOE ist AGPL, MobileCLIP hat eine Apple-Lizenz, `buffalo_l` ist ohnehin nur nicht-kommerziell nutzbar.

## 6. Kritik an den Teilplänen

- **Stufe 1 „COCO genügt“ ist für das Kernziel falsch.** COCO hat keine Klasse `key`, keinen Teppich und keine Geldbörse. „Wo ist mein Schlüssel?“ geht damit nicht. Der Gedächtnis-Plan verwendet außerdem das Label `keys`, das kein Modell liefert. Deshalb führt der Weg direkt zu YOLOE, und COCO ist nur noch der Fallback.
- **Topic, Feldnamen und Hysterese widersprachen sich** zwischen dem Worker-Plan und dem Gedächtnis-Plan (`inventory` vs. `objects`, `item_id` vs. `key`, `miss_m` vs. `object_gone_s`, QoS 1/retain vs. QoS 0). Das ist oben aufgelöst. Die API-Tokens waren zudem doppelt geplant.
- **Aufbewahrung widerspricht „zuletzt gesehen“.** Die Retention löscht Medien nach `retention_days_media` (Standard 7 Tage, nach Datumsordnern). Ein Gegenstand, der vor 10 Tagen zuletzt gesehen wurde, hätte dann kein Bild mehr. Der Ordner `inventory/` braucht deshalb eine eigene Frist (`retention_days_inventory`), oder der Crop des letzten Stands wird ausgenommen.
- **Die Version der MCP-SDK ist nicht belegt.** „mcp 2.0.1, Stand Oktober 2026“ stammt laut Plan aus Drittquellen. Wichtiger ist ein Risiko, das kein Teilplan nennt: Das Backend pinnt `pydantic==2.10.4` und `fastapi==0.115.6` (geprüft). Neuere `mcp`-Versionen verlangen wahrscheinlich ein neueres pydantic und starlette. Vor Phase 6 muss das in einem Probe-Container aufgelöst werden. Falls es nicht passt, kommt MCP in einen eigenen kleinen Dienst, der die REST-API mit einem Token aufruft. Das wäre ohnehin sauber getrennt.
- **Ringpuffer als Bildquelle:** Er ist standardmäßig an (`VG_RECORDER`, `settings.py:50`), und das tmpfs `/buffer` ist 1 GB groß. Das reicht. Der Grabber muss aber „letztes *fertiges* Segment“ zuverlässig erkennen, also nicht das Segment, in das gerade geschrieben wird. Ist der Hauptstream H.265, kostet das Dekodieren auf der CPU mehr.
- **Abwechseln auf der GPU** über ein `Event` nach jedem Sicherheits-Batch ist plausibel; `_wake`/`_stop` sind in `pipeline.py:84/90` vorhanden. Ob eine YOLOE-Kachel bei 960 px die Detektion für 16 Kameras spürbar verzögert, zeigt erst der Lasttest. Deshalb ist Phase 3 ein echtes Go/No-Go.
- **Aufwand zu optimistisch:** Zusammen ergeben die Teilpläne etwa 11–14 Personentage, nicht 7–9. Am meisten unterschätzt sind die Stabilisierung statischer Gegenstände und die Feinarbeit an den Schwellen.
- **Datenschutz fehlt im Gesichter-Plan:** Gesperrte Personen erzeugen bei schlechtem Abgleich viele neue „Person #N“. Ohne Aufräumregel für unbenannte Identitäten wächst die Liste „Unbekannt“ unbegrenzt. Das sollte in Phase 1 mit Aufbewahrungsfrist für unbenannte Identitäten ohne Events kommen.
- **Stichproben, die stimmen:**
  - `merge()` verschiebt alle Samples und Events (`identity.py:234ff`).
  - `_add_sample` wird im Treffer-Zweig aufgerufen (Z. 209).
  - `require_user` ist die zentrale Stelle für die Auth.
  - Die Detektor-Klassen sind fest eingestellt (`classes=self.class_ids`, `detector.py:77`).
  - `ultralytics==8.3.253` ist gepinnt.

Die Kritik oben beruht auf Stichproben in `backend/app/identity.py`, `backend/app/db.py`, `backend/app/main.py`, `backend/app/auth.py`, `backend/app/mqtt_bus.py`, `backend/app/retention.py`, `backend/requirements.txt`, `worker/vg_worker/pipeline.py`, `worker/vg_worker/detector.py`, `worker/vg_worker/ingest.py`, `worker/vg_worker/recorder.py` und `worker/vg_worker/settings.py` (alle unter `/home/miefda/vision-guard/`).