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
