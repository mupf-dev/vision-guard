# C3 Haustier-Tagebuch · C4 Vorräte · C5 Kamera-Gesundheit – Umsetzungsentwurf

Stand 2026-10-24. Zielzustand der Phasen 1–6 laut `CONTRACT.md` (Nachträge A–I) und `00-gesamtplan.md`.
Kennzeichnung: **[v]** = im Repo/Vertrag verifiziert, **[?]** = vermutet, im Test zu bestätigen.
Gemeinsame Bausteine: **Benachrichtigung** kommt aus A1 (Push-Kanal). Bis dahin: MQTT extern + Webhook aus Settings.

## Empfohlene Reihenfolge

C5 (S–M, 0 VRAM, sofort nutzbar) → C3 (S–M, 0 VRAM) → C4 (M–L, nach Phase 4/5, VLM-Teil erst nach A2).

---

## C5 Kamera-Gesundheit

**Ziel:** Je Kamera erkennen: `dark` (zu dunkel), `blur` (unscharf), `covered` (verdeckt/abgeklebt), `frozen`
(Standbild trotz laufender Frames), `moved` (verschoben/verdreht gegenüber Referenz). Warnung erscheint nach
≥ 60 s Dauer im Systemstatus, im Kamerakopf und per Push. Sie verschwindet nach 60 s Normalzustand.

**Architektur (Worker, nur CPU):** neues Modul `worker/vg_worker/health.py`, eigener Thread, alle 10 s je Kamera.
Liest das letzte Frame aus `ingest` [v: `Frame.image` BGR, „latest only“] und rechnet auf 320 px Graustufe:
- `dark`: Mittelwert < 25 **und** 95-Perzentil < 60 (IR-Nachtbild bleibt hell genug).
- `blur`: Laplace-Varianz < 35 % des Referenzwerts.
- `covered`: Std-Abw. < 8 und Kantendichte (Canny) < 0,5 % – gleichmäßige Fläche.
- `frozen`: mittlere Absolutdifferenz aufeinanderfolgender Prüfbilder < 0,2 über ≥ 30 s, Frames kommen aber an.
  Echtes Sensorrauschen liegt nachts darüber [?]. Ergänzt die bestehende Standbild-Erkennung im Frontend
  (`overlaySync.ts` [v]), die nur den Player betrifft.
- `moved`: ORB-Merkmale (500) gegen das Referenzbild, Homographie per RANSAC. Warnung bei Inlier-Anteil < 25 %
  oder Verschiebung > 5 % der Bildbreite bzw. Drehung > 3°. Nur prüfen, wenn nicht `dark`/`covered`. Für Tag- und
  Nachtbild (Sättigung ≈ 0 → IR) wird je **eine eigene Referenz** gespeichert.
- Hysterese: 6 schlechte Prüfungen in Folge → Problem, 6 gute → behoben. Keine Prüfung bei `state != online`.
- Referenz: automatisch beim ersten Lauf ohne Problem (je Tag/Nacht), manuell über das Kommando
  `health_reference`. Datei `health/cam<N>_<day|night>.jpg` (640 px).
- Bibliothek: OpenCV aus dem Worker-Image [v: bereits für Crops genutzt]. **VRAM: 0.** CPU-Last ca. 5 ms je Prüfung [?].

**CONTRACT-Ergänzungen**
- MQTT `vg/internal/health/{camera_id}` (retain, alle 30 s und bei Änderung):
  `{camera_id, ts, problems:[{type:"dark|blur|covered|frozen|moved", since, value}], metrics:{brightness, sharpness,
  sharpness_ref, diff, inliers, shift, rotation}, reference:{day:path|null, night:path|null}}`.
- `vg/internal/cmd/{camera_id}`: neue Aktion `{action:"health_reference", request_id, mode:"day|night|auto"}`.
- Camera `status.health = {state:"ok|warn|unknown", problems:[{type, since, text}]}` (read-only; `text` deutsch,
  z. B. „Kamera verschoben (ca. 8 % nach links)“). WS `camera_status` transportiert dies unverändert.
- REST `GET /cameras/{id}/health?hours=168` → `{current, reference_urls, log:[{type, start_ts, end_ts}]}`;
  `POST /cameras/{id}/health/reference` → 202 (Scope `write`).
- DB: `cameras.health_enabled` boolean NOT NULL DEFAULT true; neue Tabelle `camera_health_log`
  (id, camera_id, type, start_ts, end_ts NULL, value).
- Settings: `health_alert_after_s: 60` (10..3600), `health_notify: true`.
- MQTT extern `{prefix}/{camera_slug}/health` (retain): `{"state":"ok|warn","problems":[…]}`.
- `/system`: `cameras[].status.health`. MCP-Tool `get_camera_health(camera_id?)` → Liste mit Problemen (read).

**UI:** Systemseite mit Ampel je Kamera. Kamerakachel/Live mit gelbem Banner („Verdeckt seit 14:02“). In der
Kameraverwaltung Reiter „Zustand“ mit Referenzbild neben dem aktuellen Bild, Knopf „Referenz neu setzen“ und
Zeitleiste der Probleme.

**Schritte:** health.py + MQTT (M) · Backend-Status, Log, REST, WS (S) · UI (S) · Push/MQTT extern (S).

**Abnahme (e2e):** Linse abdecken → `covered` in `/api/system` innerhalb von 90 s. Kamera drehen → `moved`.
Demo-Stream pausieren (go2rtc-Quelle auf Standbild-Schleife) → `frozen`. Fokus verstellen → `blur`. Alles
verschwindet nach Behebung. Nacht-IR erzeugt **keine** Meldung (eine Nacht beobachten).

**Machbarkeitstest (S):** Neues Skript `worker/vg_worker/healthtest.py` wendet Verschiebung, Unschärfe,
Abdunkeln, Abdeckung und Einfrieren auf Testcam-Frames an. Ausgegeben werden Erkennungsrate und Fehlalarme
über 24 h echter Kameras (nur Logs).

**Risiken:** Fehlalarme durch Lichtwechsel, Regen auf der Linse und Autofokus-Pendeln (Gegenmittel: Hysterese,
zwei Referenzen). PTZ-Kameras müssen `health_enabled=false` setzen oder die Prüfung `moved` abschalten.

---

## C3 Haustier-Tagebuch

**Ziel:** Für Zonen mit Zweck `food`, `water` oder `litter` wird jeder Besuch eines Tiers als Eintrag
gespeichert: wer, wann, wie lange. Die Seite zeigt den Tag je Tier mit Vergleich zum Normalwert. Eine Abweichung
meldet z. B. „Moritz heute 0× am Napf (normal 4–6)“ oder „Klo 9× (normal 3–5)“. Häufigere Klobesuche sind
tierärztlich relevant.

**Architektur (nur Backend, keine neuen Modelle):** Neues Modul `backend/app/pet_diary.py` hängt sich an die
Track-Verarbeitung in `events.py` [v]. Labels `dog`, `cat` (konfigurierbar).
- Besuch beginnt mit `entered_zones` einer Zweck-Zone und endet mit `left_zones` oder `end` [v: `left_zones` im
  `end` enthält die letzten Zonen]. Gezählt wird ab `min_visit_s` (Default: food 5 s, water 3 s, litter 10 s).
  Mehrere Besuche desselben Tiers innerhalb von 60 s werden zusammengefasst.
- Tier = `identity` des Events (DINOv2-Haustier-ID [v]). Kommt die Identität später, wird der Besuch beim
  Track-Update nachgetragen. Ohne Identität: `identity_id=null`. Gibt es nur eine benannte Identität dieser
  Art, wird der Besuch ihr zugeordnet (Setting `pet_diary_single_pet_fallback`, Default true).
- Normalwert: je Tier und Zweck die Tageszahl der letzten 14 Tage (Median, P10/P90, mindestens 7 Tage Daten), dazu
  die längste übliche Pause (P95). Prüfung stündlich und um 21:00: Tageszahl außerhalb von
  [P10 − 1, P90 + 2] (bis zur Uhrzeit hochgerechnet) oder Pause > 1,5 × P95 → Hinweis, je Tier/Zweck/Tag höchstens
  einmal.
- **VRAM: 0** (YOLO und DINOv2 laufen bereits). Speicher: ca. 100 Zeilen/Tag.

**CONTRACT-Ergänzungen**
- Zone (JSON in `cameras.zones`, additiv): `"purpose": "food|water|litter" | null`. Keine DB-Spalte nötig.
- Tabellen `pet_visits` (id, identity_id NULL, label, camera_id, zone_id, purpose, event_id, start_ts, end_ts,
  duration_s) und `pet_alerts` (id, identity_id, purpose, kind `"too_few|too_many|long_gap"`, day, value,
  expected_lo, expected_hi, created_at).
- REST: `GET /pets/visits?identity_id=&purpose=&since=&until=&limit=` → `{items,total}`;
  `GET /pets/summary?days=14` → `[{identity, purposes:{food:{today, normal:[lo,hi], last_ts, daily:[{day,count}]}}}]`;
  `GET /pets/alerts?since=`.
- WS: `{"type":"pet_visit","visit":…}`, `{"type":"pet_alert","alert":…}`.
- MQTT extern: `{prefix}/pets/{pet_slug}/{purpose}` (retain, `{today, last_ts}`), `{prefix}/pets/alert`.
- Settings: `pet_diary_labels: ["dog","cat"]`, `pet_diary_min_visit_s: {food:5, water:3, litter:10}`,
  `pet_diary_notify: true`.
- MCP-Tool `get_pet_diary(pet?: str, days: int = 1)` → Besuche und Normalwerte (read).

**UI:** Neue Seite „Tiere“ (Navigation neben Identitäten). Je Tier eine Karte mit drei Zählern (Napf/Wasser/Klo,
heute vs. normal, farbig bei Abweichung) und einer 14-Tage-Balkenreihe. Darunter die Besuchsliste mit Vorschaubild
(`thumb_url` des Events). Im Zonen-Editor eine Auswahl „Zweck“.

**Schritte:** Zonen-Zweck in Editor, Backend und Mock (S) · Besuchserfassung (S) · Normalwerte und Hinweise (M) ·
Seite „Tiere“ (M) · MCP und MQTT (S).

**Abnahme:** Zone „Napf“ anlegen, Tier (oder Testvideo mit Katze) 10 s darin → Besuch in `/api/pets/visits` mit
Identität. Kürzere Besuche werden nicht gezählt. Mit 14 Tagen künstlicher Besuche (Seed-Skript) und einem Tag
ohne Napfbesuch → `pet_alert` und Push.

**Machbarkeit (S):** Vorhandene Events `label=cat|dog` auswerten. Wie viel Prozent haben eine Identität? Wie oft
wechselt die Identität bei zwei ähnlichen Tieren? Unter 70 % → Tagebuch zunächst je Art statt je Tier.

**Risiken:** Ähnliche Tiere werden verwechselt. Napf/Klo von oben gefilmt, das Tier ist teilweise verdeckt.
Abgedeckte Katzenklos: Zone an den Eingang legen und „Eintritt“ zählen.

---

## C4 Vorräte

**Ziel:** Ausgewählte Artikel in Regal oder Vorratsschrank melden `ok | low | out`. Steht ein Artikel auf
`low`/`out`, kommt er automatisch auf die Einkaufsliste und wird bei `ok` als erledigt markiert.
Kühlschrank-Innenkameras sind kein Ziel. Eine Kamera, die auf den Kühlschrank zeigt, sieht nur bei offener Tür
hinein; das kommt erst später über C5-Helligkeitssprung als Auslöser.

**Drei Messarten je Artikel (Bereich = Polygon wie Zonen):**
1. `presence` / `count` – Treffer des YOLOE-Vokabulars (Phase 3/4 [v]: `inventory_vocab`, Kacheln, voller
   Ringpuffer-Keyframe) im Bereich zählen; `low` bei `count < min_count`. Für Flaschen, Dosen, Packungen. Neue
   Vokabular-Einträge (z. B. `milk_carton`, `egg_carton`, `canned_food`) gehen über das bestehende Settings-Vokabular.
2. `fill` – Füllstand ohne VLM: Ausschnitt des Bereichs wird per **DINOv2 ViT-S/14** (bereits für Haustiere
   geladen [v]) mit Referenzbildern „voll“, „halb“, „leer“ verglichen; Füllstand = gewichtete Ähnlichkeit 0..1.
   Referenzen setzt der Nutzer per Knopf aus dem aktuellen Bild.
3. `vlm` – Frage an das VLM aus A2 („Wie viele Eier sind sichtbar?“), erst nach A2 und nur alle ≥ 30 min.

**Architektur:** Worker-Modul `worker/vg_worker/supplies.py` läuft im `InventoryEngine`-Takt mit [v: `GpuTurn`,
`KeyframeGrabber`]. Es wird höchstens alle `supply_interval_s` (Default 600) geprüft und **nur**, wenn seit 30 s
keine Person im Bereich war (sonst verdeckt). Ergebnis über MQTT. Das Backend (`backend/app/supplies.py`) setzt
den Zustand mit Hysterese (2 gleiche Messungen) und pflegt die Einkaufsliste.

**VRAM:** YOLOE ist aus Phase 3 schon gerechnet. DINOv2 ViT-S/14 ist bei aktiver Tiererkennung bereits geladen,
sonst kommen ca. 0,15–0,25 GB dazu [?]. Das VLM (A2, z. B. Moondream 2 int4) braucht ca. 1,5–2,5 GB [?]. Mit
~2,7 GB Grundlast plus YOLOE wird das in 6 GB knapp: nur lazy laden, nach 5 min Leerlauf entladen, und YOLOE pausiert
während der VLM-Abfrage.

**CONTRACT-Ergänzungen**
- Tabellen `supply_items` (id, camera_id, name, polygon jsonb, mode `presence|count|fill|vlm`, label NULL,
  min_count, low_below (fill, Default 0.3), question NULL, references jsonb, state, value, updated_at, enabled) und
  `shopping_list` (id, name, supply_id NULL, source `auto|manual`, created_at, done_at NULL).
- `GET /api/internal/cameras` zusätzlich `supplies:[{id, polygon, mode, label, references:[{level, path}]}]`.
- MQTT `vg/internal/supplies/{camera_id}` (QoS 1, retain): `{camera_id, ts, frame_ts, items:[{supply_id, value,
  count, occluded, crop_path}]}`.
- REST: `GET/POST /supplies`, `GET/PUT/DELETE /supplies/{id}`, `POST /supplies/{id}/reference {level:"full|half|empty"}`;
  `GET/POST /shopping-list`, `PATCH/DELETE /shopping-list/{id}` (`done: bool`).
- WS `{"type":"supply","item":…}`, `{"type":"shopping_list","items":[…]}`. MQTT extern `{prefix}/supplies/{slug}`
  (retain), `{prefix}/shopping_list` (retain).
- MCP `get_supplies()`, `get_shopping_list()` (read). Ein Schreib-Tool `add_to_shopping_list` braucht eine
  Vertragsänderung (bisher sind alle MCP-Tools nur lesend) und kommt erst danach.

**UI:** Kameraverwaltung → Reiter „Vorräte“: Bereiche zeichnen (wie `ZoneEditor`), Messart wählen, Referenzknöpfe.
Neue Seite „Vorräte“ mit Kacheln (Ausschnitt, Ampel, Füllbalken) und Einkaufsliste zum Abhaken und Ergänzen.

**Schritte:** Machbarkeitstest (M) · DB/REST/Liste (M) · Worker count/fill (M) · UI (M) · VLM-Modus nach A2 (S).

**Machbarkeitstest (M):** Neues Skript `worker/vg_worker/supplytest.py` mit 3 Regalszenen à 10 Fotos
(voll/halb/leer, Tag/Kunstlicht). Kriterien: Zählfehler YOLOE ≤ 1 bei ≤ 8 gleichartigen Artikeln; DINOv2 trennt
voll und leer zu ≥ 90 % und halb und leer zu ≥ 75 %. Wird das verfehlt, bleibt nur `vlm`, und C4 wartet auf A2.

**Abnahme:** Artikel „Wasserflaschen“ (`count`, min 2) einrichten, Flaschen entfernen → nach ≤ 2 Intervallen
`out` und Eintrag auf der Einkaufsliste. Zurückstellen → `ok`, Eintrag erledigt. Person vor dem Regal
→ keine Messung.

**Risiken:** Verdeckte hintere Reihen, Spiegelungen, unbekannte Verpackungen im offenen Vokabular. Erwartung
klar formulieren: Es wird „ungefähr“ gemessen, nicht exakt gezählt.

---

## Abhängigkeiten

| Thema | Phasen 1–6 | Roadmap |
|---|---|---|
| C5 | keine Pflicht. Nutzt `vg/internal/cmd`, Kamerastatus, Medien-Signatur (Phase 2) für Referenzbilder | A1 (Push), später A3 (gleiche Bereichs-/Zustandslogik) |
| C3 | Phase 2 (signierte `thumb_url` in Push/MCP), Phase 6 (MCP-Dienst) | A1 (Push), B3 (Tagesbericht nutzt `/pets/summary`), D2 (Rückfrage „Welches Tier?“) |
| C4 | Phase 3/4 (YOLOE, Keyframe, `GpuTurn`), Phase 5 (Vokabular-Settings), Phase 6 (MCP) | A2 (Modus `vlm`), A1 (Push), A3 (Bereichslogik teilen), B3 |
