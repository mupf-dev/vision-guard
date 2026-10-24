# Roadmap – Wellenplan für die agentische Umsetzung

Stand: 2026-10-24. Grundlage sind die acht Entwürfe in diesem Ordner, `../06-roadmap-ideen.md` und der Zielzustand
der Phasen 1–6 laut CONTRACT.md („Nachträge: Gesichter-Lernsperre, API-Keys, Gegenstände (Inventar), MCP“).
Welle 1 beginnt erst, wenn die Phasen 1–6 abgeschlossen und abgenommen sind.
Kennzeichnung: **[G]** = gemessen, **[S]** = Schätzung aus den Entwürfen.

## 0. Arbeitsweise (gilt für alle Wellen)

- **Vertrag zuerst:** Jede Welle beginnt mit einem Kickoff-Auftrag **K**. Ein einzelner Agent erledigt ihn vor allen
  anderen. Er schreibt die CONTRACT.md-Ergänzungen der Welle und alle Spalten (`models.py` und `ADDITIVE_COLUMNS`).
  Dazu kommen Pydantic-Schemas, Typ-Gerüste in `types.ts`/`api.ts`/`mock.ts`, Routen und Navigation als Platzhalter
  sowie die gemeinsamen Bausteine der Welle. Danach ändern Fach-Agenten CONTRACT.md, `db.py` und die
  Settings-Defaults nicht mehr selbst. Fehlt ihnen etwas, melden sie es dem Koordinator.
- **Parallel arbeiten:** Jeder Agent arbeitet in einem eigenen git-Worktree auf einem eigenen Zweig. Für
  Backend- und Frontend-Tests nutzt er einen eigenen Compose-Projektnamen (`-p vg-<thema>`) und einen eigenen
  Host-Port. Es gibt **eine GPU**: Worker-, Last- und VRAM-Tests laufen deshalb nacheinander, abgesichert durch eine
  Sperre (`flock ~/vg-gpu.lock`). VRAM-Zahlen aus gleichzeitigen Läufen gelten nicht.
- **Abschluss jeder Welle:** Der Koordinator führt die Zweige zusammen. Danach folgen ein gemeinsamer Lasttest
  (`selftest --replicate 8`, 16 Kameras, alle Funktionen dieser Welle eingeschaltet), `corepack pnpm build` und
  ein e2e-Test im gemeinsamen Stack.

## 1. Wellen im Überblick

| Welle | Themen (parallel) | Warum hier | zusätzliches VRAM |
|---|---|---|---|
| **0** Vorbereitung | D3-Vorbereitung, Machbarkeitstests (nacheinander auf der GPU) | billig; Messungen entscheiden über die Wellen 2–3 | 0 |
| **1** Hinweise & Überblick | K1 · A1 Proaktive Hinweise · B2 Veränderungen · C5 Kamera-Gesundheit | großer Nutzen, 0 VRAM, legt Regel-Engine v2 und Push an | 0 [G: kein Modell] |
| **2** Lernen & Personen | K2 · B4+D2 Exemplare/Rückfrage · C1+D1 Pose/Sturz/Gesten · C3 Haustier-Tagebuch · B3 Tagesbericht | nutzt Push/Regeln aus W1; nur kleine Modelle | Pose 0,1–0,2 [S] |
| **3** Bildverständnis & Gedächtnis | K3 (inkl. pgvector-Umzug) · A2 VLM · A3+C4 Sensoren/Vorräte · B1 Zeitmaschine | großes Modell nur per GPU-Lease; DB-Umzug isoliert | SigLIP 0,25–0,4 dauerhaft, VLM 2,0–2,4 exklusiv [S] |
| **4** Skalierung | C2 ReID/Anwesenheit · C4-VLM-Modus · D1b Handformen (optional) · D3 verteilte Worker (bei Hardware) | geringster Nutzen bzw. erst mit zweitem Rechner sinnvoll | OSNet 0,05–0,1 [S] |

## 2. Konsolidierte CONTRACT.md-Ergänzungen und aufgelöste Widersprüche

**Querschnitt (gilt ab W1):**
- **Regel-Engine v2:** Trigger-spezifische Parameter kommen **einheitlich** in `rules.trigger_options` (jsonb,
  Default `{}`). Damit entfallen A1 `rules.at` und A3 `rules.sensor`. Zusätzliche Prüfungen kommen einheitlich in
  `rules.conditions` (jsonb, Default `[]`, alle müssen erfüllt sein). Typen: W1 `inventory`, `inventory_untouched`,
  `weather`, `track_came_from`; W3 `sensor`. Trigger, die keinen Track haben (`time`, `sensor`), erzeugen
  **kein Event**, sondern eine Notification.
- **Notification als zentrale Sammelstelle:** Eine Tabelle `notifications` nimmt alle proaktiven Meldungen auf und
  hat zusätzlich `source: "rule|health|pet|review|report|sensor"` und `level: "info|warning|alarm"`. Damit
  entfallen C3 `pet_alerts`, `health_notify`, `pet_diary_notify`, `review_push` und `report_channels.push`. Welche
  Quelle wohin geht, regelt das Setting **`notify_routes`** `{health:[target_id], pet:[], review:[], report:[], sensor:[]}`.
  Regeln wählen ihr Ziel in der Aktion `push`.
- **Settings-Namen** folgen dem Muster `retention_days_*`: `retention_days_notifications` (statt
  `notifications_retention_days`), `retention_days_ask` (statt `ask_retention_days`), `retention_days_history`.
- **Externe MQTT-Topics:** `{prefix}/{camera_slug}/{label}` ist schon belegt. Neue Themen-Topics stehen deshalb
  immer **direkt hinter dem Präfix**: `{prefix}/health/{camera_slug}` (statt `{prefix}/{slug}/health`),
  `{prefix}/sensor/{slug}`, `{prefix}/gesture/{camera_slug}`, `{prefix}/pets/…`, `{prefix}/notifications`,
  `{prefix}/report`, `{prefix}/review`. Kamera-Slugs, die mit diesen reservierten Wörtern zusammenfallen würden,
  bekommen das Suffix `_cam` (gehört zu W0).
- **Worker-RPC:** Es gibt **ein** Topic-Paar `vg/internal/embed` → `vg/internal/embed_result`
  `{request_id, kind:"item|image|text", …}`. Es ersetzt B4 `embed` und B1 `history/encode`.
- **MCP-Schreibwerkzeuge:** Bisher gilt „Alle Tools lesen nur“. Ab W2 sind Tools mit Wirkung erlaubt. Sie prüfen die
  `scopes` aus `/auth/me` und liefern ohne `write` `isError` „API-Key ohne Schreibrecht“.
- **Ausnahme von der Methodenregel:** `POST /cameras/{id}/ask` braucht nur Scope `read`, wie `/media/sign`. Dafür
  gilt ein Rate-Limit je Key (10/min). Die Liste der Ausnahmen steht zentral in `auth.py`.
- **Ein LLM-Client** (`backend/app/llm_client.py`, `anthropic|openai_compat`) für B3 und den Cloud-Fallback von A2.
  Schlüssel kommen nur aus der Env: `VG_ANTHROPIC_API_KEY` bzw. `VG_LLM_API_KEY`. Damit entfällt
  `VG_REPORT_LLM_API_KEY`. Die Modell-ID steht im Setting. Getestet wird `openai_compat` über die LLM-Bridge.

**W0:** `worker_id` (Default `"local"`) in allen Worker-Nachrichten; `ADDITIVE_INDEXES` in `db.py`;
`util.store_media`; `require_internal`; reservierte Slugs.

**W1:**
- Regeln:
  - Trigger `time` mit `trigger_options.at`.
  - Neue Felder `conditions` und `condition_delay_s`.
  - Neue Aktion `push`.
  - Neue Platzhalter `{items}`, `{item_place}`, `{weather}`.
- Notifications:
  - Tabelle `notifications`.
  - REST `GET /notifications?source=&level=&since=`, `DELETE /notifications/{id}`.
  - WS `notification`.
  - Settings `push_targets` (maskiert), `notify_routes`, `push_max_per_rule_per_day: 3`, `weather_location`.
  - `POST /settings/push_test`.
- B2: `GET /inventory/changes`, Setting `inventory_change_min_s`, Index `(camera_id, ts)` auf `inventory_history`.
- C5 Kamera-Gesundheit:
  - MQTT `vg/internal/health/{cid}`.
  - Neue `cmd`-Aktion `health_reference`.
  - Feld `status.health`.
  - REST `GET /cameras/{id}/health`, `POST /cameras/{id}/health/reference`.
  - Tabelle `camera_health_log`, Spalte `cameras.health_enabled`, Setting `health_alert_after_s`.
- MCP: `list_notifications`, `what_changed`, `get_camera_health`.

**W2:**
- C1/D1 Pose und Gesten:
  - Trigger `posture`/`gesture` (über `trigger_options`).
  - Regelfelder `repeat_s`/`repeat_max`.
  - `events.acknowledged_at`, `POST /events/{id}/ack`.
  - Kamerafelder `cameras.pose_enabled`/`gesture_enabled`.
  - `Obj.attributes.posture`/`motion`.
  - MQTT `vg/internal/gesture/{cid}`.
- B4/D2 Exemplare und Rückfragen:
  - Gegenstands-Identitäten `kind:"item"` mit `identities.item_label`.
  - Sample-Felder `identity_samples.negative`/`source`.
  - `inventory_items.identity_id`/`identity_score`.
  - Kamerafelder `cameras.inventory_label_min_scores`/`inventory_suppress`.
  - Tabellen `review_requests`, `inventory_examples`; REST `/review*`; WS `review`.
  - Settings `item_match_threshold`, `review_*`.
- C3 Haustier-Tagebuch: Zonenfeld `purpose`, Tabelle `pet_visits`, REST `/pets/visits`, `/pets/summary`. Meldungen
  laufen als Notifications mit `source:"pet"`.
- B3 Tagesbericht: Tabelle `reports`, REST `/reports*`, Settings `report_*`, WS `report`.
- MCP: `acknowledge_alert`, `list_alerts`, `list_open_questions`, `answer_question`, `get_pet_diary`,
  `get_daily_report`; `find_object` liefert zusätzlich `identity_name`.

**W3:**
- GPU-Lease:
  - Der VLM-Dienst fragt an: `vg/internal/gpu/request {request_id, holder, need_mb}`.
  - Der Worker entlädt YOLOE und SigLIP, ruft `empty_cache` auf und antwortet mit `vg/internal/gpu/granted
    {request_id, free_mb}`.
  - Danach gibt der VLM-Dienst die GPU mit `vg/internal/gpu/release` wieder frei.
  - Zeitlimit 10 s, sonst 503.
  - Diese Ergänzung ersetzt das einseitige `vg/internal/gpu` aus A2.
- A2 Frag die Kamera:
  - REST `POST /cameras/{id}/ask`.
  - Spalte `cameras.vlm_cloud_allowed`.
  - Settings `vlm_*`, `retention_days_ask`.
  - MCP `ask_camera`.
- A3+C4 Sensoren und Vorräte:
  - Tabelle `virtual_sensors`. `region` ist ein **Polygon** wie bei Zonen; das Rechteck aus A3 ist ein
    Sonderfall.
  - Sensor-Arten `brightness|blink|reference|inventory_count|fill|vlm`.
  - Zustandsart `state_set: "binary"` (`on|off|unknown`) oder `"level"` (`ok|low|out|unknown`).
  - Flag `supply` für Vorräte.
  - **C4 geht in A3 auf:** `supply_items` und `vg/internal/supplies` entfallen. Neu bleibt nur `shopping_list`
    (Tabelle + REST).
  - Weitere Tabellen `sensor_history`, `sensor_references`; MQTT `vg/internal/sensor/{cid}`.
  - Regeln: Trigger `sensor`, Bedingung `sensor`.
  - MCP: `list_sensors`, `get_sensor`, `get_shopping_list`, `add_to_shopping_list` (write).
- B1 Zeitmaschine:
  - Datenbank-Image `pgvector`.
  - Kamerafelder `cameras.history_*`, Tabelle `history_frames`.
  - MQTT `vg/internal/history/{cid}`; der Text-Encoder läuft über `embed` mit `kind:"text"`.
  - REST `/history*`, Settings `retention_days_history`/`history_min_change`.
  - MCP `search_history`.

**W4:**
- C2: Spalte `cameras.reid_enabled`, `POST /api/internal/reid`, `GET /presence`, WS `presence`, Settings `reid_*`;
  `who_is_where` liest aus `/presence`.
- D3: Tabelle `workers`, Topics `system/{worker_id}` und `assign/{worker_id}`, Endpunkt
  `PUT /api/internal/media/{path}`, Spalten `cameras.worker_pin`/`assigned_worker`.

## 3. VRAM-Budget (RTX A1000, 6 GB)

| Baustein | VRAM | Ladeart |
|---|---|---|
| Grundlast (YOLO11s, Gesicht, Kennzeichen, DINOv2-Tier, CUDA-Kontext) | 2,7 [G] | dauerhaft |
| YOLOE (Phase 4) | **Phase-3-Messwert**; die Entwürfe nennen 0,3 bis 1,5 [S] | dauerhaft, solange eine Inventar-Kamera aktiv ist; bei GPU-Lease entladen |
| Pose `yolo11n-pose` | 0,1–0,2 [S] | dauerhaft, solange `pose/gesture_enabled` gesetzt ist |
| DINOv2 für Exemplare, Füllstand, Referenzen | 0, da geteilt mit der Tier-Erkennung (sonst 0,1–0,2) [S] | geteilt |
| SigLIP 2 B/16 (nur Bildteil) | 0,25–0,4 [S] | dauerhaft bei `history_enabled`; bei Lease entladen |
| OSNet x0_25 | 0,05–0,1 [S] | lazy, nach Leerlauf entladen |
| VLM Moondream 2 INT4 + eigener CUDA-Kontext | 2,0–2,4 + ~0,3 [S] | **exklusiv** per Lease, nach `vlm_idle_unload_s` entladen |

- **Regeln:** Die Dauerlast bleibt bei höchstens **4,5 GB**, die Spitze bei höchstens **5,6 GB**. Rechnung:
  Dauerlast = 2,7 + YOLOE + 0,2 + 0,4 ≈ 3,3 GB plus YOLOE. Das passt nur, wenn YOLOE höchstens 1,2 GB braucht.
  Braucht es mehr, ersetzt MobileCLIP2 das SigLIP-Modell (≈ 0,15 GB), oder das Modell lädt nur alle 5 min und
  verarbeitet dann im Stapel die Keyframes, die gerade vorliegen.
- **VLM-Lease:** Während der Lease sind 2,7 + 0,3 (Pose/OSNet) + 2,7 (VLM inkl. Kontext) ≈ **5,4–5,7 GB** belegt.
  Das ist grenzwertig. Liegt der gemessene Spitzenwert über 5,6 GB, nutzt A2 SmolVLM-500M oder läuft nur auf einem
  D3-Knoten.
- **Ohne zweiten Rechner (D3) geht nicht:**
  - VLM dauerhaft geladen oder VLM-Sensoren in kurzen Abständen;
  - Qwen2.5-VL-3B;
  - lokales LLM auf der GPU für B3 (≈ 2,3 GB [S]); bleibt auf der CPU oder extern;
  - Inventar ohne Pause während Fragen an die Kamera;
  - mehr als 16 Kameras mit allen Funktionen.

## 4. Gemeinsame Bausteine

| Baustein | Welle | genutzt von |
|---|---|---|
| Regel-Engine v2 (Registry für Trigger und Bedingungen, `trigger_options`, Regeln ohne Event) | K1/A1 | A1, C1, D1, A3 |
| `notifications.py` + `push.py` (ntfy/Pushover) + `notify_routes` | K1 | A1, C5, C3, D2, B3, A3 |
| Hook-API `events.on_track(callback)` | K1 | C3, C1, C2 |
| MCP-Tools als Module `mcp/tools/<thema>.py` | K1 | alle |
| `worker/vg_worker/frame_metrics.py` (CPU: Helligkeit, Schärfe, Änderung) | W1 (C5) | A3 |
| Embedding-Dienst `worker/vg_worker/embed.py` (geteiltes DINOv2, `vg/internal/embed`, Queue mit niedriger Priorität) | K2/B4 | D2, A3-Referenz, C4-Füllstand, B1-Text |
| Pose-Stufe `pose.py` | W2 (C1) | D1, später D1b |
| Feedback-Modell `review_requests` | W2 (D2) | C3, A2, C2 (unsichere Fälle) |
| `llm_client.py` | W2 (B3) | A2 (Cloud) |
| GPU-Lease + Modellregister mit Entladen im Leerlauf | K3/A2 | B1, C4, C2 |
| Frame-Tap in `pipeline.py` (Abnehmer pro Kamera) | K3 | A3, B1 |
| pgvector | K3 | B1, später Gesichts-/Tierabgleich |

## 5. Agenten-Aufträge

**Welle 0**
- **W0-a D3-Vorbereitung (S):** Führe `VG_WORKER_ID` und `worker_id` in allen Worker-Nachrichten ein. Schreibe
  Medien nur noch über `util.store_media`, hänge interne Router an `require_internal` (vorerst ohne Wirkung) und
  speichere `system` im Backend je `worker_id`. Ergänze `ADDITIVE_INDEXES` und die reservierten Slugs.
  *Dateien:* `worker/vg_worker/{settings,service,util,recorder,inventory}.py`, `backend/app/{db,mqtt_bus,util,routers/internal}.py`.
  *Abnahme:* `selftest` läuft unverändert; `mosquitto_sub` zeigt `worker_id` in allen Nachrichten.
- **W0-b Machbarkeitstests (M, nacheinander auf der GPU):** Führe die Tests nacheinander aus: `posetest`,
  `itemtest`, `healthtest` (CPU), `histtest`, `vlm/bench.py`, dazu den Flacker-Test aus A1 auf `cam_3`. Für Sturz,
  Gesten und Regale vorher eigene Testvideos aufnehmen und unter `testcam/` als `cam3..5` einbinden.
  *Abnahme:* Ergebnisse unter `docs/plan/roadmap/messungen.md` mit Go/No-Go je Thema.

**Welle 1**
- **K1 (M):** Schreibe die W1-Ergänzungen in CONTRACT.md und lege Spalten, Tabellen, Schemas und Typ-Gerüste an.
  Baue `notifications.py`, `push.py` und `routers/notifications.py`, die Hook-API in `events.py`, die
  Registry-Struktur in `rules.py` und die Aufteilung `mcp/tools/`. Lege Navigation und Routen als Platzhalter an.
  *Abnahme:* `POST /settings/push_test` kommt auf dem Handy an, der Build ist grün.
- **A1 (M):** Setze `rule_conditions.py`, `weather.py`, den `time`-Trigger, `condition_delay_s` und
  `zones_visited` um. Dazu kommen der Regel-Editor-Block „Nur wenn …“, die Seite „Hinweise“ und
  `list_notifications`. *Dateien:* `backend/app/{rules,rule_conditions,weather}.py`, `Rules.tsx`,
  `pages/Notifications.tsx`. *Abnahme:* Abnahmekriterien 1–6 aus `a1-proaktive-hinweise.md`.
- **B2 (S–M):** Setze `inventory_diff.py` mit Paarung und Rauschfilter um, dazu den Endpunkt `changes`, den Reiter
  „Veränderungen“ in `Objects.tsx` und `what_changed`. *Abnahme:* Ein Worker-Neustart erzeugt kein Paar aus
  „neu“ und „verschwunden“; Gegenstände unter 120 s erscheinen nicht.
- **C5 (S–M):** Setze `health.py` und `frame_metrics.py` im Worker um, außerdem Log, REST und Status im Backend,
  den Reiter „Zustand“ und Meldungen über `notify_routes.health`. *Abnahme:* Verdecken, Drehen und Einfrieren werden
  innerhalb von 90 s erkannt; nach einer Nacht IR-Betrieb gibt es 0 Meldungen.

**Welle 2**
- **K2 (S–M):** Schreibe die W2-Ergänzungen in CONTRACT.md, darunter die Regel für MCP-Schreibwerkzeuge. Baue
  `worker/vg_worker/embed.py` (geteiltes DINOv2) und `review_requests` als Gerüst.
  *Abnahme:* `vg/internal/embed` liefert einen 384-d-Vektor in unter 15 ms.
- **B4+D2 (L, ein Agent, weil beide `inventory.py` ändern):** Setze die Schritte 1–7 aus dem Entwurf um, ohne
  einen eigenen Push-Weg zu bauen; Push läuft über `notify_routes.review`. *Abnahme:* Kriterien 1–8 aus dem
  Entwurf. Ist das Ergebnis von `itemtest` No-Go, B4 nur für große Gegenstände.
- **C1+D1 (L):** Setze die Pose-Stufe um, außerdem Haltung, Gesten, die Trigger über die Registry, `repeat`, `ack`,
  Overlay, `list_alerts` und `acknowledge_alert`. *Dateien:* `worker/vg_worker/{pose,posture,gestures,pipeline}.py`,
  `backend/app/{rules,events}.py` (nur Registrierungen). *Abnahme:* gestellter Sturz löst aus und eskaliert, Sofa
  nicht; Lasttest ±5 %.
- **C3 (S–M):** Setze `pet_diary.py` über `on_track` um, dazu Zonen-`purpose` im `ZoneEditor`, die Seite „Tiere“
  und `get_pet_diary`. *Abnahme:* Seed von 14 Tagen plus ein Tag ohne Napf ergibt eine Notification mit
  `source:"pet"`.
- **B3 (M–L):** Setze `backend/app/report/` und `llm_client.py` um, dazu die Seite „Berichte“ und
  `get_daily_report`. *Abnahme:* genau ein Bericht pro Tag; eine erfundene Uhrzeit führt zum Fallback auf
  Satzbausteine (Test über die LLM-Bridge).

**Welle 3**
- **K3 (M):** Schreibe die W3-Ergänzungen in CONTRACT.md. Ziehe die Datenbank per Dump/Restore auf pgvector um
  (mit Backup und Rückfallskript). Baue den Frame-Tap in `pipeline.py` und das GPU-Lease-Protokoll im Worker.
  *Abnahme:* Alle Tabellen haben gleich viele Zeilen wie vorher; Lease mit Dummy-Anfrage: YOLOE wird entladen,
  `free_mb` gemeldet und YOLOE danach wieder geladen.
- **A2 (M+M):** Setze den Dienst `vlm/` (Compose-Profil `vlm`), `vlm.py` und `cloud_vlm.py` über `llm_client`,
  `ask` und „Frag die Kamera“ um. *Abnahme:* Antwort in unter 5 s, Spitze unter 5,6 GB, Detektion mit mindestens
  4,5 fps.
- **A3+C4 (L):** Baue Sensoren mit den günstigen Arten zuerst, dann `inventory_count`, `fill` und die
  Einkaufsliste; die Art `vlm` erst, wenn A2 im Wellenzweig ist. *Abnahme:* Helligkeits-Sensor wechselt genau einmal;
  Flaschen entfernen ergibt `out` und einen Eintrag auf der Einkaufsliste.
- **B1 (L):** Setze `history.py` (SigLIP oder Ausweichmodell laut `histtest`), Suche, Episoden, die Seite
  „Zeitmaschine“ und `search_history` um. *Abnahme:* Ein roter Karton wird unter den Top 3 gefunden; VRAM steigt um
  höchstens 0,5 GB.

**Welle 4:** C2 nach dem Entwurf (Abnahme: `presence_key` bleibt über Kameras gleich, kein Embedding auf Platte)
und D3 erst mit vorhandener Hardware. Der VLM-Modus für C4 kommt als S-Auftrag dazu.

## 6. Kritik an den Entwürfen

- **Die YOLOE-Zahlen widersprechen sich:** A2 rechnet mit etwa 1,5 GB, C1 mit 0,3–0,6 GB. Alle Budgets hängen am
  Messwert aus Phase 3; erst danach lassen sich die Wellen 2 und 3 freigeben.
- **A2:** „Inventar pausieren“ allein gibt kein VRAM frei. Der PyTorch-Cache im Worker behält den Speicher, bis
  `empty_cache` läuft und das Modell entladen ist. Der VLM-Container bringt einen eigenen CUDA-Kontext mit. Es
  fehlt ein Test, ob Moondream INT4 mit torch 2.5.1 und den gepinnten transformers-Versionen läuft (Kernel,
  `trust_remote_code`).
- **B4:** Mit DINOv2 auf 40–80 px großen Ausschnitten ist eine AUC von 0,90 bei Schlüsseln unwahrscheinlich. Das
  Leitbeispiel „Annas Schlüssel“ dürfte scheitern; realistisch sind eher Taschen, Rucksäcke und Jacken.
- **A1:** Das Schlüssel-Beispiel steht auf derselben unsicheren Erkennung kleiner Gegenstände (Phase-3-Grenze nur
  60 %). Ob ein Medikament „unberührt“ ist, lässt sich höchstens indirekt messen; das sollte als „Hinweis“ gelten,
  nicht als Fakt.
- **C1:** Der Testcam fehlen Sturzszenen. Pose auf Ausschnitten des Detect-Frames ist bei kleinen Personen
  ungenau. Ob ein liegender Track abreißt, ist nicht belegt.
- **C2:** OSNet x0_25 bei Mitbewohnern mit ähnlicher Kleidung ist schwach. Für den ONNX-Export über `torchreid`
  gibt es keine geprüften Abhängigkeiten.
- **C4:** Zählfehler ≤ 1 bei ≤ 8 Artikeln ist optimistisch (Verdeckung in hinteren Reihen). Der Füllstand über
  DINOv2-Ähnlichkeit ist ungetestet.
- **B1:** Die Deutsch-Qualität von SigLIP 2, die Dedupe-Quote von 80 % und die `open_clip`-Version sind
  Annahmen. Der DB-Umzug ist die riskanteste Einzelaktion und gehört deshalb in K3, nicht neben andere Arbeit.
- **B3:** Die Plausibilitätsprüfung erkennt Zahlwörter wie „zwei“ oder „halb elf“ nicht. Zahlwörter normalisieren
  oder verbieten.
- **D3:** Das Jetson-Image ist komplett unverifiziert (JetPack 6.1, Torch-Wheel).
- **Entwürfe gesamt:** Jeder Entwurf hat eigene Push-, Alarm- und RPC-Wege erfunden. Das ist oben vereinheitlicht.
  Es fehlte ein gemeinsamer Lasttest mit allen Modellen gleichzeitig, und sieben neue Seiten brauchen eine neue
  Navigationsgruppierung (Gegenstände · Hinweise · Tiere · Zeitmaschine · Anwesenheit).
- **Aufwand:** Die Entwürfe summieren sich auf etwa 35–45 Personentage. Realistisch sind 50 und mehr, vor allem
  wegen Testvideos, Feinabstimmung der Schwellen und UI.
