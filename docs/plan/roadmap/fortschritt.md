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
