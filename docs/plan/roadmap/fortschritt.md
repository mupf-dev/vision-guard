# Fortschritt der Wellen

Laufendes Protokoll der Integrations-Agenten: Ergebnis, Messwerte, Abweichungen und offene Punkte je Welle.
Grundlage: [00-wellenplan.md](00-wellenplan.md). Messdetails der Machbarkeitstests: [messungen.md](messungen.md).

## Welle 0 – Vorbereitung (Integration 2026-10-25, nachts, autonom)

### Ergebnis

Beide Fach-Zweige wurden ohne Konflikte per `git merge --no-ff` in `feature/inventar-gesichter-mcp` übernommen:

| Auftrag | Zweig | Merge | Abnahme |
|---|---|---|---|
| W0-a D3-Vorbereitung | `w0/d3-vorbereitung` | `9da2eff` | erfüllt: `selftest` unverändert, `worker_id` in allen Worker-Nachrichten |
| W0-b Machbarkeitstests | `w0/machbarkeit` | `e08a783` | erfüllt: `messungen.md` mit Go/No-Go je Thema |

Die Worktrees der Agenten wurden entfernt (`git worktree prune`), die gemergten Zweige gelöscht. Die drei
Zusatz-Testvideos (`sturz.mp4`, `gesten.mp4`, `regal.mp4`) liegen jetzt unter `testcam/videos/` im
Hauptverzeichnis (nicht im Repo, Lizenzen siehe messungen.md §8). Die Rohdaten (`testcam/videos/raw/`, ≈ 225 MB)
wurden mit dem Worktree verworfen; `testcam/download-extra-videos.sh` erzeugt sie bei Bedarf neu.

Nach dem Merge: `corepack pnpm build` grün, `docker compose up -d --build` (backend und worker neu gebaut),
`testcam` neu gestartet (neue Pfade `cam3..cam5`). Alle Dienste laufen; backend und mcp `healthy`.

### Ende-zu-Ende-Prüfung im Haupt-Stack

- **`worker_id`:** 40 s Mitschnitt `vg/internal/#` nach dem Neustart: alle 179 Nachrichten des neuen Workers
  (detections, status, system, inventory) tragen `"worker_id":"local"`. Ohne das Feld waren nur drei *retained*
  Altnachrichten vom Worker vor dem Update (status 1/2 der gelöschten Demo-Kameras, inventory 3 `stopped`) – die
  versteht das Backend weiterhin als `local`. `cmd_result` geprüft über einen manuellen Snapshot- und Clip-Befehl
  an `cam_3`: beide `ok:true` mit `worker_id`, Dateien über `store_media`/`media_target` vollständig geschrieben,
  keine `.part`-Reste (Testdateien sofort gelöscht, nicht angesehen). `track` war in diesem Zeitraum leer (niemand
  im Bild); W0-a hat `track` mit 121 Nachrichten im eigenen Stack belegt.
- **REST mit Test-API-Key** (Scope `read`, angelegt und am Ende widerrufen → danach 401):
  `/auth/me` = `token`; `/system.worker` online, alle Modelle bereit (`inventory: ready`); `cam_3` online
  5,04 fps, 2880×1616; `/inventory?state=present` 12 Gegenstände, signierter Medienlink 200;
  `/inventory/search` liefert den deutschen Antwortsatz; `/events`, `/stats`, `/identities` 200;
  Schreibversuch mit `read`-Key → 403; `/api/internal/cameras` liefert die eine aktive Kamera.
- **Logs:** keine Fehler in backend/worker. Einmalige Warnung beim Worker-Start
  `segment recorder exited (1): … dimensions not set … stream 0:1` an `cam_3`; der Ringpuffer startet sofort neu
  und schreibt Segmente. Betrifft unveränderten Code (Audiospur der Kamera), nicht W0 – siehe offene Punkte.

### Messwerte (Lasttest nach dem Merge)

`flock ~/vg-gpu.lock docker compose run --rm --no-deps worker python -m vg_worker.selftest rtsp://testcam:8554/cam1
rtsp://testcam:8554/cam2 --replicate 8 --seconds 60`, parallel zum Haupt-Worker (`cam_3` mit Inventar/YOLOE).

| Messgröße | Wert |
|---|---|
| VRAM Haupt-Worker allein (NVML, Gerät gesamt) | 2 688 MiB |
| VRAM-Spitze Haupt-Worker + Selftest 16 Kameras | 4 141 MiB (Selftest-Meldung), NVML-Abtastung alle 2 s max. 3 999 MiB |
| Selftest 16 Kameras | je 5,0 fps (alle 16 online), gleitend 80 fps gesamt, Batch Ø 31,7 ms bei Ø 5,24 Bildern, 6,0 ms/Bild |
| Selftest Ergebnisse | 96 Tracks gestartet und beendet, 176 Gesichter, 72 Identify-Aufrufe, 0 Fehler |
| `cam_3` während des Lasttests (60 Status-Proben) | 4,79–5,15 fps, Ø 4,99 fps; `inference_ms` max. 30,5 |
| GPU-Auslastung | Spitze 99 %, im Mittel 40–70 % |

Damit liegt die Dauerlast deutlich unter dem Budget (≤ 4,5 GB) – Welle 0 bringt kein zusätzliches VRAM.

### Go/No-Go aus den Machbarkeitstests (übernommen aus messungen.md)

| Thema | Entscheidung | Folge für die Wellen |
|---|---|---|
| C1 Sturz/Liegen | **Go mit Auflagen** | `yolo11n-pose` (+0,1 GB, 27,5 ms/16 Ausschnitte). Detektor findet liegende Personen nur in 48 % der Bilder → Track-Abriss im Liegen als eigenes Signal werten (W2). |
| D1 Armgesten | **Go (vorläufig)** | ≤ 1 Fehlalarm auf 56 Negativ-Clips; Halte-Gesten vor der Abnahme mit eigenen Aufnahmen prüfen. |
| B4/D2 eigene Exemplare | **Bedingt Go** | DINOv2 nur für Gegenstände ab ≈ 90–120 px (AUC 0,84 bei 60 px, 0,88 bei 120 px). Kleine Dinge (Schlüssel, Brille) No-Go → Hinweis in der UI. |
| C5 Kamera-Gesundheit | **Go** | 6 ms/Bild auf der CPU, an `cam_3` alle Störungen erkannt, 0 Fehlalarme (auch IR). |
| B1 Zeitmaschine | **Go** | SigLIP 2 B/16-256 (+0,29 GB), besser als MobileCLIP2; `history_min_change` 0,05. |
| A2 Frag die Kamera | **Go nur mit GPU-Lease** | Moondream 2 INT4: 0,99 s/Antwort, 21/22 richtig, Spitze 5,70 GB mit YOLOE → YOLOE vorher entladen. Freien VRAM per NVML prüfen (unter WSL2 lügt `cudaMemGetInfo`). Ausweichmodell SmolVLM-500M; Qwen2.5-VL-3B nur nicht-kommerziell. |
| A1 Flackern | **No-Go ohne Filter, Go mit Filter** | 250–300 Wechsel/h nachts. Empfehlung für K1: `inventory_confirm_n` 3, `inventory_miss_m` 10, Score-Hysterese ±0,05, `condition_delay_s` ≥ 120 s, ID-Zusammenführung (−93 % in der Simulation). |

Die empfohlenen Default-Änderungen (`inventory_confirm_n` 3, `inventory_miss_m` 10, `item_match_threshold` 0,55,
`history_min_change` 0,05) sind **nicht** umgesetzt – das ist laut Arbeitsweise Aufgabe von K1/K2.

### Abweichungen und Entscheidungen

- W0-a hatte keinen Kickoff und hat den Abschnitt „Nachträge: Welle 0“ in CONTRACT.md selbst geschrieben
  (rein additiv). Übernommen.
- `worker/.dockerignore` schließt jetzt `vg_worker/tests_feasibility` aus, damit die Machbarkeitsskripte nicht im
  Worker-Image landen (Hinweis von W0-b).
- Die Aufräum-Ressourcen von W0-b bleiben bewusst erhalten, weil W2/W3 die Tests erneut brauchen (Platte 729 GB
  frei): Volume `vg-w0_models` (≈ 12 GB Testgewichte), Images `vg-w0-feasibility:test`, `vg-w0-vlm:bench`,
  `curlimages/curl:8.10.1`. Entfernen: `docker volume rm vg-w0_models; docker rmi vg-w0-feasibility:test
  vg-w0-vlm:bench`.
- `/system.worker` hatte schon vor W0 kein Feld `perf`/`worker_id`; die Form ist unverändert (Frontend unberührt).

### Offene Punkte

- D3: Topic `vg/internal/system/{worker_id}`, Feld `capacity`, `/system.workers`, Upload-Modus mit Warten auf
  `crop_path` (Welle 4).
- Messungen offen: Lasttest mit allen Modellen gleichzeitig (Pose/SigLIP/VLM existieren noch nicht im Worker –
  in W2/W3 beim Wellenabschluss), ReID (OSNet), Wegnehm-Test Schlüsselbund/Tablettendose tagsüber (A1), gehaltene
  Gesten (D1), eigene Handyfotos (B4), Tag/Nacht-Wechsel (C5), Dubletten tagsüber (B1).
- Warnung des Segment-Recorders an `cam_3` beim Start (Audiospur ohne Parameter in den ersten Sekunden). Der
  Neustart greift; sinnvoll wäre `-map 0:v` oder ein späterer Start nach erstem Bild – nicht W0-Umfang.
- Retained `status`-Nachrichten der gelöschten Demo-Kameras 1/2 und alte `inventory`-Nachrichten ohne
  `worker_id` liegen noch im Broker; harmlos (Backend nimmt `local` an).

## Welle 1 – Kickoff K1 (2026-10-25, nachts, autonom)

Vertrag und Gerüst für A1, B2 und C5 stehen in CONTRACT.md, Abschnitt „Nachträge: Welle 1“ (W1.A–H).

- **Gebaut:** Spalten `rules.trigger_options/conditions/condition_delay_s`, `cameras.health_enabled`; Tabellen
  `notifications`, `camera_health_log`; Index `ix_inventory_history_camera_ts`; Settings `push_targets` (maskiert),
  `notify_routes`, `push_max_per_rule_per_day`, `weather_location`, `retention_days_notifications`,
  `inventory_change_min_s`, `health_alert_after_s`. Registry in `rules.py`, Hook `events.on_track`,
  `notifications.py`, `push.py`, `routers/notifications.py` (inkl. `POST /settings/push_test`), Aktion `push`,
  Aufbewahrung. Gerüste `rule_conditions.py` (A1) und `health.py` (C5) samt Verdrahtung (`main.py`,
  `mqtt_bus.py`, `state.py`), damit die Fach-Agenten diese gemeinsamen Dateien nicht anfassen müssen.
  MCP aufgeteilt in `mcp/tools/` mit Platzhaltern `notifications.py`, `changes.py`, `health.py`.
  Frontend: Typen/API/Mock für alle W1-Endpunkte, Seite „Hinweise“ (Grundgerüst), Einstellungen →
  „Benachrichtigungen“, Aktion „Push“ im Regel-Editor, Feld „Veränderungen: Mindestdauer“.
- **Geprüft:** `py_compile` aller Backend-/MCP-Module, `corepack pnpm build` grün, backend/frontend/mcp neu gebaut
  und `healthy`. Push gegen lokalen ntfy-Container (`binwiederhier/ntfy:v2.11.0`, nur im Docker-Netz):
  `push_test` gespeichert und ungespeichert (mit `***`), Bild-Upload mit Umlauten (RFC 2047), Route `health`,
  Tageslimit, Dedup, Pushover-Fehlertext ohne Zugangsdaten. E2E: simulierter Track (Label `k1test`, Kamera 3) →
  Regel mit `push` → Notification mit Event und Bild, Push zugestellt; danach Event, Regel, Notification, Ziel und
  retained Zähler-Topic entfernt. MCP-Selbsttest mit temporärem Read-Key OK (Key widerrufen).
- **Entscheidungen:** Image `binwiederhier/ntfy` (im Auftrag stand „binwiederhecht“, das gibt es nicht).
  Topic wird nicht maskiert (muss am Handy abonniert werden); Hinweis in der UI, Token/eigenen Server zu nutzen.
  Je `push`-Aktion eine Notification; Regeln ohne `push` erzeugen keine (außer A1: mit `conditions`).
  Defaults `inventory_confirm_n` 3 / `inventory_miss_m` 10 nur für neue Installationen – die gespeicherten Werte
  der laufenden Installation (2/3) habe ich **nicht** geändert (Einstellungen → Gegenstände).
- **Offen für die Fach-Agenten:** siehe Bericht K1 bzw. CONTRACT.md W1.B–W1.G.

## Welle 1 – Integration (2026-10-25, nachts, autonom)

### Ergebnis

Alle drei Fach-Zweige wurden per `git merge --no-ff` in `feature/inventar-gesichter-mcp` übernommen:

| Auftrag | Zweig | Merge | Abnahme |
|---|---|---|---|
| A1 Proaktive Hinweise | `w1/a1-hinweise` | `18dbe04` | Kriterien 1–6 im Teststack erfüllt; im Haupt-Stack Zeit-Regel + `rules/check` + MCP geprüft |
| B2 Was hat sich verändert? | `w1/b2-veraenderungen` | `b855d78` | Worker-Neustart ohne „neu/weg“-Paar (Haupt-Stack belegt), < 120 s gefiltert (Teststack) |
| C5 Kamera-Gesundheit | `w1/c5-kamerazustand` | `e5f0ece` | Verdecken/Verschieben/Einfrieren ≤ 90 s (Teststack), `cam_3` nachts ohne Befund |

Einziger Konflikt: `frontend/src/styles.css` (A1 `.contain-img` und
C5 `.health-*` jeweils am Ende angehängt) – beide Seiten übernommen. `types.ts`, `api.ts`, `mock.ts`, `CONTRACT.md`
wurden automatisch zusammengeführt. Nachgetragen: `mcp/selftest.py` erwartet jetzt auch `list_notifications` und
`get_camera_health` (Hinweis aus A1). Worktrees entfernt, `w1/*`-Zweige und die alten `worktree-wf_*`-Zweige
(alle bereits enthalten) gelöscht.

Nach dem Merge: `py_compile` aller Backend-/Worker-/MCP-Module ok, `corepack pnpm build` (tsc + vite) grün,
`docker compose up -d --build` → alle Dienste laufen, backend und mcp `healthy`. Logs nach dem Neustart: keine
Fehler; Worker meldet `camera 3: automatic night reference set` (C5). Die Warnung des Segment-Recorders aus W0
trat diesmal nicht auf.

### Ende-zu-Ende-Prüfung im Haupt-Stack (Test-API-Key read+write, am Ende widerrufen → 401)

- **A1:** `POST /api/rules/check` mit „Laptop sichtbar“ an Kamera 3 → `ok:true` mit Lage „vermutlich auf dem
  Stuhl“; „Schlüssel sichtbar“ → `ok:false`; `track_came_from` bei `time` → 422 (deutsche Meldung).
  Zeit-Regel auf jetzt + 2 min (am Tag der Zeitumstellung, 02:37 MEZ nach Rückstellung) löste **genau einmal**
  aus: Notification ohne Event, `data.trigger=time`, Übersichtsbild, Text „Laptop: Wohnzimmer, vermutlich auf dem
  Stuhl“; kein zweites Auslösen in den folgenden Minuten. Seite „Hinweise“ zeigt den Eintrag (Screenshot geprüft).
- **B2:** `GET /inventory/changes?since=3h` → „Seit 00:35: 3 neu (…)“ (Zeit korrekt in MESZ vor der Umstellung);
  `since=yesterday` 14 neu; `since=40d` → 422 „Zeitraum außerhalb der Aufbewahrung“. Nach dem Worker-Neustart
  durch den Rebuild (neue Session): `since=15m&include_transient=1` → keine Veränderungen. Reiter
  „Veränderungen“ mit Vorher/Nachher-Bildern geprüft (Screenshot).
- **C5:** `GET /cameras/3/health` → `ok`, Modus `night`, Nachtreferenz angelegt, signierte Referenz-URL ohne
  Anmeldung abrufbar (200 image/jpeg). Systemseite zeigt „Bild OK“. Keine Gesundheits-Hinweise erzeugt.
- **MCP:** `selftest.py` OK (ohne Key 401, 9 Tools). `list_notifications` liefert den Hinweis mit signiertem
  Bild; `get_camera_health` per Name → „in Ordnung“; unbekannte Kamera bzw. Zeitangabe → `isError` mit deutscher
  Meldung.
- **Aufgeräumt:** Testregel und Notification gelöscht, Key widerrufen und lokale Schlüsseldatei vernichtet.

### Messwerte (Lasttest nach dem Merge)

`flock ~/vg-gpu.lock docker compose run --rm --no-deps worker python -m vg_worker.selftest rtsp://testcam:8554/cam1
rtsp://testcam:8554/cam2 --replicate 8 --seconds 60 --health`, parallel zum Haupt-Worker (`cam_3` mit Inventar).

| Messgröße | Wert |
|---|---|
| VRAM Haupt-Worker allein (NVML, Gerät gesamt) | 2 466 MiB |
| VRAM-Spitze Haupt-Worker + Selftest 16 Kameras | 4 077 MiB (Selftest-Meldung), NVML-Abtastung alle 2 s max. 3 935 MiB |
| Selftest 16 Kameras | alle online, 4,95–5,26 fps je Kamera, gleitend ≈ 80 fps, Batch Ø 54,1 ms bei Ø 8,15 Bildern, 6,6 ms/Bild |
| Selftest Ergebnisse | 96 Tracks gestartet und beendet, 232 Gesichter, 104 Identify-Aufrufe, 0 Fehler |
| Bildprüfung C5 (CPU) | 2,6–3,8 ms je Prüfung, 16 Kameras; 0 VRAM |
| `cam_3` während des Lasttests (33 Status-Proben) | 4,65–5,13 fps, Ø 4,99 fps; `inference_ms` max. 34,9 |

Gegenüber W0 (4 141 MiB Spitze, Ø 4,99 fps an `cam_3`) unverändert – Welle 1 bringt kein zusätzliches VRAM.

### Abweichungen und Entscheidungen

- Nichts wurde abgeschaltet oder zurückgenommen; alle drei Features laufen mit ihren Standardwerten.
- Die gespeicherten Werte `inventory_confirm_n`/`inventory_miss_m` = 2/3 der laufenden Installation habe ich
  (wie K1) **nicht** geändert – das ist eine Nutzerentscheidung (Einstellungen → Gegenstände; Empfehlung 3/10).
- `push.py`: C5 sendet bei ntfy-Servern ohne Anhänge die Meldung ohne Bild statt sie zu verlieren (gilt auch für A1).
- „Seit heute Morgen“ im Reiter Veränderungen bedeutet vor 06:00 Uhr „seit gestern 06:00“ – so belassen.

### Offene Punkte

- Abnahmen mit echten Gegenständen tagsüber an `cam_3`: Schlüssel/Fernbedienung hinlegen und wegnehmen (A1, B2);
  Verdecken/Drehen an der echten Kamera (C5, bisher nur Testvideos).
- C5: eine ganze Nacht und der Wechsel IR → Farbe am Morgen (Tag-Referenz wird beim ersten Farbbild angelegt);
  Fehlalarme in der Dämmerung beobachten. „Eingefroren“ erkennt nur praktisch identische Bilder.
- B2: viele „neu“-Einträge durch Flackern/neu vergebene Worker-IDs (Stühle, Kissen) – ID-Zusammenführung und 3/10
  würden helfen.
- A1: Prüfungen, die auf `condition_delay_s` warten, gehen bei einem Backend-Neustart verloren (Vertrag).
- Das Zugriffslog des Backends enthält signierte Medien-URLs inkl. `sig` (zeitlich begrenzt, bestand schon vor W1);
  ggf. Query im Access-Log kürzen.
- `kv.rule_time_runs` behält den Eintrag der gelöschten Testregel (harmlos).

## Welle 2 – Kickoff K2 (2026-10-25, nachts, autonom)

Vertrag und Gerüst für B4+D2, C1+D1, C3 und B3 stehen in CONTRACT.md, Abschnitt „Nachträge: Welle 2“ (W2.A–I).

- **Gebaut:** Spalten `rules.repeat_s/repeat_max`, `events.acknowledged_at`, `cameras.pose_enabled/gesture_enabled/
  inventory_label_min_scores/inventory_suppress`, `identities.item_label`, `identity_samples.negative/source`,
  `inventory_items.identity_id/identity_score`; Tabellen `review_requests`, `inventory_examples`, `pet_visits`,
  `reports`; Index `ix_inventory_items_identity_id`; alle W2-Settings (inkl. `llm_*`, standardmäßig aus).
  Gemeinsame Bausteine **fertig**: Embedding-Dienst (`worker/vg_worker/embed.py` mit geteiltem DINOv2,
  `backend/app/embed_client.py`), Rückfragen-Kern (`review.py`: Dedup, Limits, Verfall, Antworten mit
  Wirkungs-Registry, REST `/review`, WS `review`), LLM-Client (`llm_client.py`, openai_compat + anthropic,
  `POST /settings/llm_test`), Track-Trigger-Evaluatoren in der Regel-Registry (`posture`/`gesture` registriert,
  `dispatch_track_phase`, Tick-Phase), MQTT-Handler-Registry (`mqtt_bus.register_handler`), `POST /events/{id}/ack`,
  MCP-Schreibregel (`require_write`, `api_send`, `WRITE`). Platzhalter mit fertiger Verdrahtung: `pose_events.py`,
  `item_identity.py`, `pet_diary.py`, `report/`, Router `pets`/`reports`, MCP `alerts/review/pets/reports`,
  Seiten „Bitte bestätigen“ (funktionsfähig), „Tiere“, „Berichte“, Einstellungs-Karten je Thema,
  „KI-Sprachmodell“ (fertig). Test-Server `scripts/llm_mock.py` (OpenAI-kompatibel, ohne Kosten).
- **Geprüft:** `py_compile` aller Module, `corepack pnpm build` grün, backend/worker/frontend/mcp neu gebaut
  (`healthy`). Embedding über `vg/internal/embed` an echten Inventar-Crops von `cam_3`: 384-d, L2-normiert,
  Rechenzeit Median 8,7 ms, **Rundlauf Median 9,9 ms, max. 13,6 ms** (Abnahme < 15 ms erfüllt), `augment` 12 ms,
  0 MB zusätzliches VRAM (Tiermodell war geladen); Pfad-Ausbruch und fehlende Datei → `ok:false`.
  REST mit temporärem Key (read+write, danach widerrufen): neue Settings inkl. 422 bei falschem Zweck, Regel
  `posture` gespeichert, `gesture` leer/falsch und `repeat_s` 5 → 422; Rückfrage anlegen → Dublette verworfen →
  „Nein“ (Quelle `mcp`) → zweite Antwort 409 → nach „Nein“ nie wieder; `ack` (danach zurückgesetzt); LLM-Client
  gegen `llm_mock` (Docker-Netz) ok, danach wieder `none`. Screenshots der neuen Seiten/Karten ohne JS-Fehler.
- **Entscheidungen:** `item_match_threshold` 0,55 und `item_min_px` 90 aus messungen.md §3 (statt 0,70 im Entwurf).
  `pet_alerts` und `review_push` entfallen (Notifications `pet`/`review`), C3-Hinweise also ohne eigene Tabelle.
  `reports.period_key` (eindeutig, nur `daily`) statt Unique über (`kind`, `period_start`). Posture-Ausschlusszonen
  als `trigger_options.exclude_zone_ids`. Augmentierung: Original, gespiegelt, 120 px und 64 px (Empfehlung
  messungen.md), Rückgabe Mittelwert **und** Einzelfassungen. `TCP_NODELAY` für Worker/Backend-MQTT und
  `set_tcp_nodelay true` im Broker (vorher +40 ms je Antwort durch Delayed ACK; Mosquitto neu gestartet, retained
  Nachrichten werden ohnehin alle 3–30 s neu gesendet). LLM-/SMTP-Schlüssel nur per Env (`docker-compose.yml`,
  `.env.example`), `.env` unverändert.
- **Offen für die Fach-Agenten:** siehe Bericht K2 bzw. CONTRACT.md W2.C–W2.I. MCP-Selbsttest erwartet die
  W2-Tools noch nicht (bei der Integration ergänzen).

## Welle 2 – Integration (2026-10-25, nachts, autonom)

### Ergebnis

Alle vier Fach-Zweige sind per `git merge --no-ff` im Arbeitszweig `feature/inventar-gesichter-mcp`:
`w2/b4-d2-lernen` (B4+D2), `w2/c1-d1-pose` (C1+D1), `w2/c3-tiere` (C3), `w2/b3-bericht` (B3). Konflikte gab es nur
in `backend/app/routers/identities.py::delete_identity` (Exemplar-Zuordnungen lösen + Tier-Besuche behalten) und in
`frontend/src/styles.css` (angehängte Blöcke); beide Seiten wurden jeweils vollständig übernommen. Die übrigen
gemeinsamen Dateien (`main.py`, `events.py`, `types.ts`, `mock.ts`, CONTRACT.md W2.C–G) liefen ohne Konflikt
zusammen. Nachgezogen: `mcp/selftest.py` erwartet jetzt auch `list_open_questions`, `answer_question`,
`get_pet_diary`, `get_daily_report`. Worktrees der Agenten entfernt (`git worktree prune`), gemergte Zweige und
die verwaisten `worktree-wf_*`-Zweige gelöscht, ein liegengebliebener Container `vg-c1-testcam-1` samt Netz
entfernt. **Kein Feature musste abgeschaltet oder zurückgenommen werden.**

Standardzustand nach der Welle (unverändert aus K2): Pose/Gesten je Kamera **aus**, Tagesbericht **aus**
(Generator `template`, `llm_provider: none`), Rückfragen **an** (`review_enabled`, max. 10/Tag), Haustier-Tagebuch
wirkt erst mit Zonen-Zweck Napf/Wasser/Klo, `notify_routes.*` leer (keine Pushes).

### Ende-zu-Ende-Prüfung im Haupt-Stack (Test-API-Keys read und read+write, am Ende widerrufen → 401)

- Build: `py_compile` aller Module, `corepack pnpm build` grün; `docker compose up -d --build` → backend/mcp
  `healthy`, worker lädt Detektor, Gesicht, Kennzeichen, Tier, YOLOE (`yoloe-11m-seg`, 50 Labels) und Embedding;
  keine Fehler/Tracebacks in den Logs von backend, worker, mcp, frontend.
- **D2:** Nach dem Neustart erzeugt das Inventar von `cam_3` binnen 40 s 10 offene Rückfragen („Erkannt als
  „Kissen“ – stimmt das?“ …), `asked_today 10/10` – Tageslimit greift. Die Seite „Bitte bestätigen“ zeigt sie mit
  Crop und Übersichtsbild. Die Fragen sind **für den Nutzer stehen gelassen** (nicht beantwortet, um keine
  Unterdrückungsflächen/Schwellen an der echten Kamera zu setzen).
- **B4 an der echten Kamera `cam_3` (GPU):** Exemplar `W2-Integrationstest` (Stuhl) per
  `POST /identities` + `samples/from-inventory` angelegt (Sample in 0,05 s). Der Worker ordnet es binnen Sekunden zu;
  `GET /inventory/search?q=wo ist W2-Integrationstest` → „W2-Integrationstest: aktuell sichtbar in Wohnzimmer …“,
  `match.via: "identity"`. Danach gelöscht (204), Zuordnung am Gegenstand wieder `null`, kein Ordner unter
  `identities/` übrig.
- **C1/D1:** Pose + Gesten an `cam_3` kurz eingeschaltet → `pose: ready (yolo11n-pose, CUDA fp16)` in 0,2 s geladen,
  danach per PUT wieder ausgeschaltet (übrige Kamera-Felder unverändert geprüft). Sturz/Geste an der echten Kamera
  nicht nachgestellt (nachts, niemand im Raum) – Nachweise siehe Bericht C1+D1 (posecheck, Live-Test im Projekt
  `vg-c1`). `GET /events?alert=true` → 200, leer.
- **B3:** `GET /reports/preview` → Satzbaustein-Bericht; `POST /reports/generate` mit Lese-Key → 403, mit
  Schreib-Key → 200 (`generator: template`), danach gelöscht (Liste wieder 0).
- **C3:** `/pets/summary`, `/pets/visits` → 200 (leer; im Haupt-Stack gibt es noch keine Zone mit Zweck).
- **MCP:** `selftest.py` mit Lese-Key: ohne Key 401, alle 15 Tools vorhanden, Selbsttest OK. Zusätzlich
  `get_daily_report` (Vorschau heute), `get_pet_diary` (Hinweis auf fehlende Zonen), `list_open_questions`
  (10 offen, signierte Bild-Links), `answer_question` mit Lese-Key → `isError` „API-Key ohne Schreibrecht.“
- **Oberfläche:** Screenshots (Live, Bitte bestätigen, Tiere, Berichte, Gegenstände, Ereignisse, Regeln,
  Einstellungen, Kameras) gegen den echten Stack ohne JS-Fehler.

### Messwerte

| Messung | Ergebnis |
|---|---|
| Lasttest 16 Kameras (`--replicate 8`, 60 s), ohne Pose | 62,3 fps gesamt, Batch 32,4 ms, VRAM ganze GPU max. 4,14 GB (selftest-Spitze 4,28 GB) |
| dto. mit `--pose --gestures --inventory` | 61,8 fps (**−0,8 %**, Grenze ±5 %), Batch 35,9 ms, VRAM max. 4,20 GB (selftest-Spitze 4,34 GB) – unter 4,5 GB; Pose 41,7 ms je Lauf, 247 Takte verworfen (Budget, Detektion unbeeinflusst) |
| `cam_3` (2880×1616) im Haupt-Stack, Pose+Gesten an | 4,94 fps im Mittel (13 Statusmeldungen, 4,71–5,07), Batch 19,8 ms, Inventar 20 Läufe/min à 152 ms |
| VRAM Haupt-Stack (nvidia-smi) | vor dem Merge 2,60 GB, danach 2,47 GB, mit Pose-Modell geladen 2,60 GB |

Beide Lasttest-Läufe liefen unter `flock ~/vg-gpu.lock`, der Haupt-Worker lief parallel (VRAM-Zahlen = ganze GPU).
Im Lasttest wurden 8 Personen kurz (0,2 s) als „liegt“ eingestuft – weit unter jeder Liegedauer, kein Alarm.
Zwei vom Selbsttest ins Medien-Volume geschriebene Inventar-Bilder (`cam1_…`) wurden gelöscht.

### Abweichungen und Entscheidungen

- Nichts abgeschaltet, nichts zurückgenommen; die Standardwerte aus K2 bleiben (siehe oben).
- Die 10 Rückfragen aus dem Start sind echte Fragen an den Nutzer und bleiben offen.
- Die Merge-Commits tragen die Konfliktlösung in der Nachricht.

### Offene Punkte

- **D2:** Das Tageslimit ist nach einem Start in weniger als einer Minute aufgebraucht, darunter mehrere Fragen zum
  gleichen Label („Kissen“ 3×). Kandidat für ein Refinement (z. B. je Label und Tag nur eine Frage, Fragen über den
  Tag verteilen).
- **Oberfläche „Bitte bestätigen“:** Bei Fragen ohne Übersichtsbild bleibt unter dem kleinen Crop eine leere Fläche
  (kosmetisch).
- **B4:** baugleiche Exemplare nicht unterscheidbar; Abnahme mit echten Handyfotos tagsüber steht aus.
- **C1/D1:** eigene Aufnahmen (echter Sturz, Sofa, Haltegesten in 2–4 m) fehlen; `max_motion` unkalibriert; ein
  Sturz kann zwei Notfälle erzeugen (zwei Tracks); Wiederholungen gehen bei Backend-Neustart verloren; ein einmaliger,
  nicht reproduzierter Worker-Hänger im Agenten-Test (Diagnose: `docker compose kill --signal=USR1 worker`).
- **C3:** nur mit nachgestellten Worker-Nachrichten getestet; im Haupt-Stack noch keine Zone mit Zweck.
- **B3:** kein Test mit echtem lokalem Sprachmodell (Anteil plausibler Texte unbekannt) → Standard bleibt
  `template`; die Oberfläche zeigt nicht an, ob SMTP eingerichtet ist.
- Selftest-Läufe schreiben Inventar-Bilder in das echte Medien-Volume (bestand vor W2).

## Welle 3 – Kickoff K3 (2026-10-25, nachts, autonom)

Vertrag und Gerüst für A2, A3+C4 und B1 stehen in CONTRACT.md, Abschnitt „Nachträge: Welle 3“ (W3.A–I).

- **DB-Umzug auf pgvector (vor allen Fach-Agenten):** Backup per `pg_dump -Fc` nach
  `~/vision-guard-backups/visionguard-20261025-042313-vor-pgvector.dump` (Verzeichnis 700, Datei 600, 196 KB);
  Dump/Restore von `postgres:17-alpine` (Volume `db-data`, PG 17.11) nach `pgvector/pgvector:0.8.2-pg17-bookworm`
  (neues Volume `db-data-pgv`, PG 17.10, Extension `vector` 0.8.2). **Zeilenzahlen aller 15 Tabellen identisch**
  (u. a. events 83, inventory_history 2793, inventory_items 48, identity_samples 43, review_requests 10,
  api_tokens 8); Sequenzen übernommen. Skripte ohne Zugangsdaten: `scripts/db-migrate-pgvector.sh` (stoppt die
  Dienste, Wegwerf-Container auf dem alten Volume, Zählung, Dump, Restore, Vergleich, Start) und
  `scripts/db-rollback-pgvector.sh [--dump-first]` (schreibt `docker-compose.override.yml` → alte DB; Datei steht in
  `.gitignore`). Das alte Volume bleibt bis zur Abnahme von W3 als Rückfall.
- **Gebaut:** Spalten `cameras.vlm_cloud_allowed/history_enabled/history_interval_s/history_tiling`; Tabellen
  `ask_log`, `virtual_sensors`, `sensor_history`, `sensor_references`, `shopping_list` und – nur mit pgvector –
  `history_frames` (`halfvec(768)`, HNSW-Index `halfvec_cosine_ops`); alle W3-Settings (`vlm_*`,
  `retention_days_ask/_sensors/_history`, `shopping_list_auto`, `history_min_change` 0,05, `history_model`).
  Gemeinsame Bausteine **fertig**: GPU-Lease im Worker (`gpu_lease.py`, Topics `vg/internal/gpu/request|granted|
  release|released`, Ablauf/Verlängerung/„busy“, NVML-Messung, Referenz-Client `LeaseClient`, Prüfwerkzeug
  `leasetest`), Frame-Tap (`frametap.py`, je Abnehmer eigener Thread, verwerfen statt queuen, `perf.taps`),
  `HalfVec`-Typ ohne Zusatzpaket, `run_eventless_rule`/`sensor_changed` (Regeln ohne Event für `time` und `sensor`),
  Trigger `sensor` + Bedingung `sensor` (ausgewertet), `require_read_limited` (Rate-Limit je Key),
  `llm_client.complete(images=…, model=…)`, `embed`-Anfrage mit `model` (`siglip2`-Haken im Worker).
  Platzhalter mit fertiger Verdrahtung: Backend `vlm.py`, `cloud_vlm.py`, `sensors.py` (inkl. fertigem
  `internal_sensors`), `shopping.py`, `history.py`, Router `ask`/`sensors`/`history`; Worker `sensors.py`,
  `history.py`; MCP `ask/sensors/history`; Seiten „Zeitmaschine“, „Vorräte“, Admin „Sensoren“, Einstellungs-Karten
  „Frag die Kamera“, „Sensoren und Vorräte“, „Zeitmaschine“, Kamera-Abschnitte (Cloud-Fallback, Zeitmaschine),
  Dialog `AskCamera.tsx` (noch nicht in der Live-Ansicht eingebunden – A2).
- **Geprüft:** `py_compile`, `corepack pnpm build` grün; backend/worker/mcp/frontend neu gebaut, `healthy`.
  **Lease-Abnahme:** Dummy-Anfrage (`leasetest --need-mb 3000`) → `granted` nach 0,40 s, YOLOE entladen,
  `free_mb` 3799 (vorher ≈ 3536 frei), `models.inventory = "paused: GPU-Lease leasetest"`, nach `release` YOLOE in
  0,0 s neu geladen (Warm-up 0,7 s), Inventar läuft weiter. Zweiter Halter → `busy`, Verlängerung 0,3 ms, Ablauf nach
  `max_s` → `released` mit `reason:"expired"`. Frame-Tap-Test: schneller Abnehmer 100/100 Frames, langsamer
  (0,5 s/Frame) verwirft statt zu stauen. pgvector: Einfügen/Lesen/`cosine_distance`-Sortierung über `HalfVec`.
  REST mit temporären Keys (read / read+write, danach widerrufen → 401): Settings inkl. `vlm_cloud_model: ""` →
  `null` und 422 bei unbekanntem Modell; Kamera-PUT ohne W3-Felder lässt sie unverändert; Regel `sensor` ohne
  `sensor_id` → 422, mit `track_came_from` → 422; `POST /cameras/3/ask` mit Lese-Key → 503 (Platzhalter), der
  11. Aufruf in der Minute → 429 mit `Retry-After`; ohne Key 401. `sensor_changed` startet die passende Regel
  (Notification „K3-Testsensor ist an“), falscher Zustand startet nichts; Zeit-Regel nach dem Umbau unverändert
  (Testdaten gelöscht). MCP-Selbsttest OK. Screenshots der neuen Seiten ohne JS-Fehler.
- **Entscheidungen:** Ein `confirm_n` je Sensor statt `on_n/off_n` (gilt auch für level-Sensoren);
  `inventory_count` und `vlm` rechnet das Backend, `brightness|blink|reference|fill` der Worker. Lease-`unloaded`
  nennt alle registrierten Modelle, die nicht im VRAM sind. `ask` hat einen eigenen Router (sonst verlangte das
  router-weite `require_user` Scope `write` für POST). Navigation: ab 1720 px Breite nur noch das aktive Ziel mit
  Beschriftung (11 Hauptpunkte). Kein `pgvector`-Python-Paket (eigener Typ, keine neue Abhängigkeit).
- **Offen für die Fach-Agenten:** siehe CONTRACT.md W3.C–W3.I. `scripts/llm_mock.py` versteht noch keine Bilder
  (A2 erweitert). Compose-Dienst `vlm` legt A2 an.

## Welle 3 – A2 „Frag die Kamera“ (2026-10-25, nachts, autonom, Zweig `w3/a2-vlm`)

Go/No-Go aus `messungen.md` §6: **Go nur mit GPU-Lease** → umgesetzt wie geplant (Moondream 2 INT4, Lease aus K3).

- **Dienst `vlm`** (`vlm/`, Compose-Profil `vlm`, intern `vlm:8200`): FastAPI `/infer`, `/health`, `/unload`;
  Modell lazy in einem **Kindprozess** (Entladen gibt auch den CUDA-Kontext frei), GPU-Lease über MQTT
  (`need_mb` aus Settings, 10 s Zeitlimit → 503), Speichergrenze = `free_mb` − 300 MB, Lease-Verlängerung bei jeder
  Frage, Entladen nach `vlm_idle_unload_s`, Rückfall auf SmolVLM-500M bei „zu wenig VRAM“, Warteschlange 1 (429),
  Antwortlimit 20 s. Gewichte beim Start ins Volume `models` (bei vorhandenem Cache ohne Netz). Dockerfile mit
  derselben ersten pip-Schicht wie `Dockerfile.bench` (Build aus dem Cache in Sekunden).
- **Backend:** `vlm.py` (go2rtc-Bild mit `cache=3s`, `yesno`-Erkennung, lokal → Cloud-Fallback bei `auto`,
  Standbild unter `ask/…` ≤ 1920 px, `ask_log`, Aufbewahrung inkl. Tagesordner, `models.vlm` in `/system` aus
  `vlm /health` alle 10 s), `cloud_vlm.py` (über `llm_client`, Bild ≤ 1600 px, nur mit Kamera-Freigabe und
  `vlm_cloud_model`), Router `ask` (Liste, Löschen, `Retry-After`). Neue Abhängigkeit `pillow==11.1.0` (Ausschnitt,
  Verkleinerung).
- **MCP:** `ask_camera` (ohne `allow_cloud` nur lokal; Standbild als `ImageContent`, signierte `snapshot_url`,
  `X-VG-Source: mcp`). **Oberfläche:** „Frag die Kamera“ in der Fokusansicht (Live) mit Ausschnitt ziehen,
  deutschen Vorlagen für englische Fragen, Modellwahl bei Cloud-Freigabe, Antwort mit Standbild, letzte Fragen
  mit Löschen; Einstellungs-Karte mit Modellzustand und Grafikspeicher-Bedarf. `scripts/llm_mock.py` versteht Bilder.

**Geprüft** (Testprojekt `vg-a2` ohne Worker, `vlm` lieh sich die GPU vom Worker des Haupt-Stacks; GPU-Läufe unter
`flock ~/vg-gpu.lock`):
- Dienst direkt: Laden inkl. Lease 4,2 s, erste Antwort 6,6 s, danach 1,0–1,4 s; 5/5 Ja/Nein richtig;
  VRAM 2,53 → Spitze **5,05 GB** → nach Leerlauf-Entladen wieder 2,53 GB; `cam_3` 4,66–5,16 fps während der Lease,
  Inventar `paused: GPU-Lease vlm` → `ready`; 4 parallele Anfragen → 2 × 200 / 2 × 429; ungültiges Bild → 400.
- REST e2e: ohne Dienst 503 („Dienst vlm läuft nicht“), 422 (leere Frage, falsche Region), 404, Cloud ohne Freigabe
  403, mit Freigabe über `llm_mock` 200 `engine:"cloud"`, `auto` → Cloud-Fallback, Ausschnitt verkleinert das
  Cloud-Bild; Anbieter nicht erreichbar → 503; Liste/Standbild (Cookie 200, signiert 200, ohne Anmeldung 401),
  Löschen 204 → Bild 404; Lese-Key: 10 × 200, 11. → 429 „Zu viele Anfragen“ mit `Retry-After`, `source:"api"`,
  Löschen mit Lese-Key 403. Mit Dienst: `engine:"local"`, `auto` → lokal, Ausschnitt, `/system.models.vlm`
  `loading` → `ready`, nach Stopp des Dienstes VRAM wieder 2,53 GB. Aufbewahrung: 26 Einträge/22 Bilder gelöscht.
- MCP mit Lese-Key: `ask_camera` per `camera_id` und `camera_name` → Bild + Text, signierte URL, `source:"mcp"`;
  unbekannte Kamera und falsche Region → `isError`.
- `corepack pnpm build` grün; Screenshots (Desktop und 390 px) ohne JS-Fehler.

**Entscheidungen:** Kindprozess statt Laden im Hauptprozess (sonst bleiben ≈ 0,1–0,3 GB CUDA-Kontext belegt);
Standbild = ganzes Bild (Region nur als Rahmen gespeichert/angezeigt); fehlgeschlagene Fragen nur im Log; MCP fragt
ohne `allow_cloud` ausschließlich lokal; die UI zeigt die Modellwahl nur bei Kameras mit Cloud-Freigabe.

**Offen:** Kaltstart über das Backend dauert 5–7 s (Laden + Lease), warm 2–3,5 s an `cam_3` (davon go2rtc-Bild
0,6–2 s) – Abnahme „< 5 s“ gilt warm. Keine Fragen an echte `cam_3`-Bilder gestellt (Personen im Bild; bewusst nur
Demo-Kameras). Worker-Neustart während einer Lease nicht nachgestellt (der Dienst fragt bei der nächsten Frage neu an).
Gewichte liegen bisher nur im Test-Volume `vg-w0_models`; der erste Start im Haupt-Stack lädt ≈ 2 GB ins Volume `models`.

## Welle 3 – B1 Haus-Zeitmaschine (Zweig `w3/b1-zeitmaschine`)

- **Modell laut messungen.md §5 (Go):** SigLIP 2 ViT-B/16-256 (`open_clip_torch==3.2.0`, `transformers==4.57.6`,
  `tokenizers==0.22.2`, `sentencepiece==0.2.0`, eigene Dockerfile-Schicht mit `--no-deps`). Bildturm fp16 auf der
  GPU (+248 MiB torch), Textturm auf der CPU (≈ 30 ms/Anfrage). Laden aus dem Cache 4,8–5,9 s.
- **Umgesetzt:** Worker `history.py` (Frame-Tap, Kacheln, Dedupe, 30-min-Auffrischung, Lease, `embed` mit
  `siglip2`), Backend `history.py` + `history_query.py` (deutsch → englische Zusatzanfrage ohne LLM), Router
  `/history/search|frames|stats`, `DELETE /history`, Aufbewahrung, Kamera löschen; MCP `search_history`; Seite
  „Zeitmaschine“ mit Filmstreifen, Einstellungs-Karte mit Speicheranzeige/Modell/„Verlauf löschen“; `selftest --history`.
- **Geprüft (Test-Stack `vg-b1`, Port 18091, danach `down -v`):**
  - Roter Karton (synthetisches Testvideo: Flur aus `cam1`, Karton 240–540 s im 12-min-Loop, Kamera mit Kacheln,
    Intervall 10 s): Kosinus mit Karton 0,13–0,16, ohne 0,03–0,07. „roter Karton“ → Platz 1, Zeitraum
    03:59:39–04:04:33 UTC bei Wahrheit 03:59:37–04:04:37 (± 1 Intervall ✓), Score 78, Kachel unten links markiert.
    „roter Karton im Flur“, „ein Paket“ ebenfalls Platz 1.
  - „Person mit Rucksack“ auf `cam1`: bester Zeitraum Kosinus 0,074 (Score 37) > Median aller cam1-Bilder 0,048 ✓
    (im Testvideo ist kein eindeutiger Rucksack; Kriterium formal erfüllt). „Auto auf der Straße“, „Fahrrad“ → cam2.
  - MCP von außen (nginx, read-Key, danach widerrufen): `search_history` liefert signierte `thumb_url`, ohne
    Signatur 401, mit 200 `image/jpeg`; `include_image` liefert das Vorschaubild; unbekannte Kamera → `isError`.
  - Lasttest 16 Kameras (`selftest --replicate 8`, 75 s, Haupt-Stack lief parallel auf der GPU):
    ohne Zeitmaschine 66,3 fps gesamt / Spitze 4219 MiB; mit (30 s) 64,1 fps / 4337 MiB (+118 MiB);
    mit Kacheln und 5 s 64,6 fps / 4465 MiB (+246 MiB), `skipped` 0. fps −3 % (±5 % ✓), VRAM ≤ +0,5 GB ✓.
  - GPU-Lease (`leasetest`): `unloaded: ["inventory","history"]`, danach Bildturm zurück, Speichern läuft weiter.
  - Worker aus → Suche 503 „Textsuche derzeit nicht verfügbar“. `DELETE /history?camera_id=2` → 18 Bilder;
    Aufbewahrung (`retention_days_history=1`, Zeit +2 Tage) löscht Zeilen und Dateien.
  - `corepack pnpm build` grün, Screenshots (Desktop, Filmstreifen, Einstellungen, 390 px) ohne JS-Fehler.
- **Entscheidungen:** Bildquelle = Frame-Tap (≤ 1920 px, kein zusätzliches ffmpeg) statt `KeyframeGrabber`.
  Zeiträume nur aus Bildern nahe am besten Treffer der Kamera (Kosinus − 0,03), Lücke ≤ 2,5 × Intervall (2 × war
  durch Taktschwankung zu knapp). Score = Kosinus [0; 0,20] → 0..100 (kalibriert am Testvideo). Englische
  Zusatzanfrage per Wörterbuch statt LLM (kostenlos, standardmäßig an). Textturm bleibt geladen, solange eine Kamera
  aktiv ist (RAM reichlich), sonst Entladen nach 10 min. CONTRACT W3.G nur additiv ergänzt.
- **Offen:** Dedupe-Quote tagsüber mit echter Kamera (nachts 99 % laut W0) und Test mit `cam_3` – im Haupt-Stack
  nach dem Merge `history_enabled` einschalten. Gewichte (≈ 1,4 GB) lädt der Worker beim ersten Start nach
  `/models/history` (oder vorab aus `vg-w0_models` kopieren). Deutsche Anfragen mit seltenen Wörtern bleiben
  schwächer (Wörterbuch erweitern oder optional LLM-Übersetzung über `llm_client`).

## Welle 3 – Integration (2026-10-25, nachts, autonom)

**Ergebnis:** Alle drei Fach-Zweige sind per `--no-ff` im Arbeitszweig (`w3/a2-vlm`, `w3/a3-c4-sensoren`,
`w3/b1-zeitmaschine`). Der Haupt-Stack läuft mit Frag die Kamera (Profil `vlm` aktiv), Sensoren/Vorräten und
Zeitmaschine. Kein Feature musste zurückgenommen werden; eine Korrektur (VLM-Fenster für Sensoren, s. u.).

- **Konflikte:** `frontend/src/lib/mock.ts` (Ask-Routen A2 + Sensor-Routen A3 zusammengeführt), `mcp/selftest.py`
  (erwartete Tools aller drei), `AGENTS.md`, `worker/README.md`, `fortschritt.md` (beide Seiten erhalten).
  Worker-Gerüst (Frame-Tap, Lease, Embed) stammte aus K3, daher keine Code-Konflikte im Worker.
- **Modellgewichte** aus dem Test-Volume `vg-w0_models` in `vision-guard_models` kopiert (Moondream 2 INT4,
  SmolVLM-500M → `hf/hub/`, SigLIP 2 → `history/`), kein Download nötig.
- **Build:** `corepack pnpm build` grün; `docker compose up -d --build` (backend, worker, frontend, mcp) und
  `docker compose --profile vlm up -d --build vlm` fehlerfrei; Logs ohne Fehler/Tracebacks.

### Gefundener Fehler und Korrektur

Ein einziger `vlm`-Sensor (Straßenkamera, `vlm_max_age_s` 120) hielt das Bildmodell ~70 % der Zeit geladen: jede
Frage verlängert die GPU-Lease, Inventar und Zeitmaschine waren entsprechend lange pausiert, die Gegenstände von
`cam_3` wurden „Stand unklar“. Korrektur in `backend/app/sensors.py`: periodische `vlm`-Sensor-Fragen nur in einem
gemeinsamen 60-s-Fenster, höchstens alle `VG_SENSOR_VLM_GAP_S` (Standard 900 s); „Jetzt prüfen“ und `verify_vlm`
ausgenommen. Danach im 16-min-Lauf nur noch eine Lease (≈ 2 min). Zusätzlich `GET /api/system.sensors` mit den
Zählern (offener Punkt von A3). CONTRACT W3.F um „Ergänzungen der Integration“ erweitert.

### Messwerte (Haupt-Stack, `cam_3` mit Inventar, zeitweise 2 Demo-Kameras)

| Prüfung | Ergebnis |
|---|---|
| VRAM Dauerlast (Inventar + Zeitmaschine auf 3 Kameras) | 2,92–3,06 GB (≤ 4,5 ✓) |
| VRAM-Spitze während VLM-Lease | 5,20 GB (≤ 5,6 ✓) |
| `cam_3` während VLM-Leases (≈ 60 Proben) | 4,58–5,09 fps (≥ 4,5 ✓) |
| `cam_3` gesamt (≈ 160 Proben über 30 min) | Ø ≈ 5,0 fps; eine Einzelprobe 4,36 fps ohne Lease (Ausreißer) |
| Frag die Kamera über Backend, kalt (Lease + Laden) | 10,9 s |
| … warm | 1,8–3,3 s (9 Fragen), Ja/Nein an Demo-Kameras plausibel |
| Lasttest 16 Kameras (`--replicate 8`, 60 s, unter `flock`, Haupt-Stack parallel) | 61,2 fps gesamt (W2: 62,3; −1,8 %), Batch 43 ms, VRAM ganze GPU max. 4,46 GB; `cam_3` dabei 4,78–5,01 fps |
| Zeitmaschine | „Auto auf der Straße“/„Fahrrad“ → cam2-Demo vorn, „Flur mit Holzboden“ → cam1-Demo, „Sofa“ → `cam_3` |
| Sensoren | Helligkeit `on`; `vlm`-Sensor (echtes Moondream) liefert `off`/`on`; Vorrat `inventory_count` an `cam_3` (Kissen): `low` → Einkaufsliste + Hinweis `source:sensor`, Schwelle gesenkt → `ok`, Eintrag erledigt |
| MCP-Selbsttest (alle W3-Tools erwartet) | OK |

Test-API-Key (1 Tag) angelegt und widerrufen (danach 401). Testdaten entfernt: Demo-Kameras 4/5 samt Zeitmaschine,
617 Events, 7 unbenannte Test-Gesichter, Sensoren, Einkaufseintrag, Hinweise, Frag-Verlauf. Images `vg-a3-*` gelöscht.

### Entscheidungen

- Profil `vlm` bleibt im Haupt-Stack an (stabil, Grenzen eingehalten). Damit es nach `docker compose down` wieder
  startet, muss `COMPOSE_PROFILES=vlm` in `.env` stehen (nicht geändert – Nutzerentscheidung).
- `history_enabled` an `cam_3` (Intervall 30 s, ohne Kacheln) bleibt **an**, damit die offene Dedupe-Messung
  tagsüber Daten bekommt. Abschalten: Kamera-Dialog → Zeitmaschine, Verlauf löschen in den Einstellungen.
- Sensor-Fragen landen mit `source:"sensor"` im Frag-Verlauf (mit Standbild, Aufbewahrung 7 Tage) – belassen.

### Offen

- Kaltstart von „Frag die Kamera“ dauert ~11 s (Ziel < 5 s nur warm erfüllt); keine Fragen an echte `cam_3`-Bilder.
- Während einer VLM-Lease sind Inventar und Zeitmaschine pausiert; Gegenstände werden in der Zeit „Stand unklar“.
- `vlm`-Sensoren reagieren durch das Fenster erst nach bis zu 15 min; für schnellere Sensoren braucht es D3.
- Füllstand/Referenz-Sensoren nur synthetisch geprüft; Worker-Neustart während laufender Lease nicht nachgestellt.
- Sicherheitshinweis aus A2: Die Kamera-URL von `cam_3` mit Passwort stand in einem Sitzungsprotokoll (keine Datei,
  kein Commit) – Passwortwechsel erwägen.

## Welle 4 – C4-VLM-Modus „Vorräte per Bildmodell“ (Zweig `w4/c4-vlm-modus`, autonom)

Auftrag (S): Vorrats-Füllstand per VLM über die A2-Schnittstelle. Es gab keinen Kickoff K4; die nötigen
Vertragsergänzungen sind rein additiv (keine Spalten/Tabellen/Settings) und stehen in CONTRACT.md
„Nachträge: Welle 4 – C4-VLM-Modus“.

- **Messung zuerst** (messungen.md §11, echter Moondream 2 INT4 über `vlm /infer` des Haupt-Stacks, unter
  `flock`): Anzahl 0–3 Flaschen 19/19 exakt, größere Mengen zu niedrig (9 → 5); Füllstand in Stufen unbrauchbar
  (halb/wenig → „full“, Ja/Nein immer „Yes“), „leer“ vs. „nicht leer“ 11/12. **Go mit Grenzen / eingeschränkte
  Variante:** Anzahl nur mit Schwelle ≤ 3, Füllstand nur `ok`/`out`.
- **Umgesetzt:** `backend/app/vlm_measure.py` (Fragevorlagen, Antworten lesen: Ziffern, Zahlwörter de/en,
  voll/halb/leer, Prozent); Sensor-Art `vlm` mit `params.measure` `count|fill` (Vorrat möglich, Höchstalter ≥ 30 min);
  `verify_vlm` für `inventory_count` (Bildmodell zählt einen Wechsel nach, ≤ 1×/5 min, dritter Zustand des Modells
  gilt); `last_answer` im Sensor-JSON; „Jetzt prüfen“ übernimmt den Zustand des Bildmodells direkt (behebt
  nebenbei: bei `verify_vlm` + Helligkeit „umgekehrt“ wurde „ja“ als „aus“ gewertet). MCP `list_sensors`/`get_sensor`
  mit `measure`, Stückzahl und letzter Antwort. Oberfläche: Messart „Ja/Nein · Anzahl · leer / nicht leer“,
  Artikel-Vorlagen, Fragevorschau, Hinweise zu den Grenzen, „Wechsel vom Bildmodell nachzählen lassen“ bei
  „Gegenstände zählen“. Messwerkzeug `scripts/vlm_supply_bench.py`.
- **Geprüft** (Testprojekt `vg-c4vlm`, Port 18094, ohne Worker, `vlm` = Mock mit steuerbarer Antwort, Kamera =
  testcam `cam5`; danach `down -v`): 8 × 422 (fehlender Artikel, `min_count` 4, Höchstalter 900 s, unbekannte
  Messart, Vorrat mit Ja/Nein, `fill` binär, Nachzählen mit `min_count` 4, `verify_vlm` bei `fill`); Zählsensor
  „3“ → ok (Frage = Vorlage, Region = Bounding-Box, Modus `query`), „There is only one bottle …“ → knapp +
  Einkaufsliste (`auto`) + Hinweis, „no bottles“ → leer, unklare Antwort → Zustand bleibt, „3“ → ok und Eintrag
  erledigt; Verlauf ok/leer/knapp/ok; periodischer Pfad mit `confirm_n` 2: unknown → knapp nach 2 Fragen (65 s);
  `fill`: empty → leer, half/low → ok; Ja/Nein-Sensor ohne `measure` unverändert; `inventory_count` + Nachzählen
  (Gegenstände per SQL): unknown → ok ohne Rückfrage, Kandidat knapp + Modell „3“ → bleibt ok mit genau einer
  Rückfrage, „Jetzt prüfen“ + Modell „1“ → knapp. MCP mit Lese-Key (danach widerrufen → 401 nach Ablauf des
  30-s-Caches). `corepack pnpm build` grün; Screenshots Desktop/390 px (Sensoren, beide Dialoge, Vorräte) ohne
  JS-Fehler.
- **Entscheidungen:** keine neue Spalte (Messart in `params`); `fill` per Bildmodell nur „leer“ statt No-Go, weil
  „Obstschale/Futter leer?“ der häufigste Fall ist; Grenze `min_count` ≤ 3 hart (422) statt nur Warnung; Nachzählen
  nicht für `fill` (DINOv2-Stufen kann das Modell nicht bestätigen); `last_answer` nur im Speicher.
- **Offen:** Messung mit echten Vorratsfotos (Eier, Dosen, Schrank), nachts/IR und mit Cloud-Modell; keine Fragen an
  `cam_3`. Ein Zählsensor mit Nachzählen an `cam_3` (flackerndes Inventar, messungen.md §7) erzeugt höchstens
  12 Fragen/h – im Haupt-Stack nach dem Merge beobachten (`/system.sensors.vlm_calls`).
