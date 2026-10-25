# Roadmap C1 · D1 · C2 – Pose-Stufe, Sturzerkennung, Gesten, Person über mehrere Kameras

Stand: 2026-10-24, Entwurf. Kennzeichnung: **[V]** im Repo/Image geprüft, **[A]** vermutet oder aus Drittquellen, vor
der Umsetzung messen. Zielzustand der Phasen 1–6 laut `CONTRACT.md` („Nachträge: Gesichter-Lernsperre, API-Keys,
Gegenstände (Inventar), MCP“) und `docs/plan/00-gesamtplan.md`.

## 1. Zielverhalten

- **C1 Sturz/Notfall:** Liegt eine Person in einer überwachten Kamera/Zone länger als N Sekunden am Boden (optional:
  kaum Bewegung), löst eine Regel aus. Wird nicht quittiert, wiederholt sie sich (Eskalation). Sofa, Bett und
  Yogamatte werden über Zonen ausgeschlossen.
- **D1 Gesten:** Eine Person hält eine Armgeste ≥ 1 s (z. B. beide Arme hoch, linker/rechter Arm seitlich, Arme
  über dem Kopf gekreuzt). Die Regel mit dem Auslöser `gesture` führt die bestehenden Aktionen aus (MQTT, Webhook).
  Optional nur für bekannte Identitäten und nur in Zonen.
- **C2 Person über mehrere Kameras:** Tracks ohne Gesicht werden über Kleidung und Statur kurzzeitig (Standard 60 min)
  mit Tracks anderer Kameras verknüpft. Hat ein Track ein Gesicht, wird diese Identität an die verknüpften Tracks
  weitergegeben. Das MCP-Werkzeug `who_is_where` liefert damit auch „zuletzt vor 3 min in der Küche“.

## 2. Architektur

**Gemeinsame Pose-Stufe (Worker):** `worker/vg_worker/pose.py` mit `PoseModel(ModelWorker)` nach dem Muster von
`identify.py` (eigener Thread, Warteschlange `qsize` 16, bei voller Warteschlange verwerfen).
- Eingabe: nur Personen-Crops bestätigter Tracks (≥ 3 Treffer), nur auf Kameras mit `pose_enabled` oder
  `gesture_enabled`. Crop aus dem Detect-Frame mit 10 % Rand, Batch bis 16, `imgsz=256`, fp16.
- Rate je Track: 2 Hz für die Haltung (C1) und 5 Hz in Gestenzonen (D1). Rechenbudget wie beim Inventar, also nach
  jedem Sicherheits-Batch abwechseln. Die Detektion hat immer Vorrang.
- Ausgabe: 17 COCO-Keypoints je Track. Daraus berechnet `worker/vg_worker/posture.py` die Haltung
  `standing|sitting|lying|unknown` (Rumpfwinkel Schulter→Hüfte, Seitenverhältnis der Box, Kopfhöhe relativ zur Hüfte,
  über 2 s geglättet) und die Bewegung (Mittel der Keypoint-Verschiebung, normiert).
- `worker/vg_worker/gestures.py`: regelbasierter Klassifikator auf Keypoints (Handgelenk über Kopf, Ellbogenwinkel,
  Armrichtung). Eine Geste gilt, wenn sie in ≥ 80 % der Frames über `gesture_hold_s` (1,0 s) erkannt wurde.
  Danach 5 s Sperre je Track.
- **ReID:** `worker/vg_worker/reid.py`, `ReidModel(ModelWorker)` mit onnxruntime-gpu (bereits im Image [V]).
  Je Track höchstens alle 3 s und höchstens 5 Embeddings, nur bei Box-Höhe ≥ 128 px und nicht am Bildrand
  abgeschnitten. Gesendet wird an `POST /api/internal/reid`.
- **Backend:** `backend/app/reid.py` hält eine Galerie im RAM (keine DB, keine Platte) mit
  `presence_key → {embeddings (max. 10), tracks, identity, last_camera, last_seen}`. Abgleich per Kosinus, und
  verknüpft wird nur, wenn kein gleichzeitig aktiver Track desselben Schlüssels auf einer anderen Kamera existiert.
  Nach `reid_ttl_min` wird verworfen. `backend/app/events.py`/`rules.py` bekommen die Auslöser `posture` und `gesture`
  sowie die Wiederholung (`repeat_s`). Die Regelauswertung bleibt im Backend.
- **Fehlertoleranz:** Pose- und ReID-Modelle werden erst bei Bedarf geladen. Ein Ladefehler setzt `models.pose`
  bzw. `models.reid` auf `"error: …"`, die Detektion läuft weiter.

## 3. Modelle, Bibliotheken, VRAM

| Zweck | Modell / Paket | Status | VRAM (zusätzlich) |
|---|---|---|---|
| Pose | `yolo11n-pose.pt` (ultralytics 8.3.253, schon gepinnt [V]), 17 COCO-Keypoints, AGPL-3.0 | Klasse vorhanden [V], Gewichte noch nicht im Volume | ≈ 100–200 MB bei Batch 16 und 256 px [A]; `s`-Variante ≈ 250 MB [A] |
| Handform (optional, D1b) | `mediapipe==0.10.x` Gesture Recognizer, Apache-2.0 | unter Linux praktisch nur CPU [A] | 0 MB GPU, ≈ 5–15 ms CPU je Hand-Crop [A] |
| ReID | OSNet `osnet_x0_25` (MSMT17), als ONNX aus `torchreid`/boxmot exportiert, 512-d, MIT [A] | Export einmalig im Machbarkeitstest | ≈ 50–100 MB [A] (`x1_0`: ≈ 150 MB [A]) |

Der Bedarf liegt bei etwa **0,2–0,35 GB** zusätzlich [A]. Mit YOLOE (0,3–0,6 GB [A]) und 2,7 GB Grundlast liegt die
Summe bei ≈ 3,6 GB, also unter der Grenze von 4,5 GB aus Phase 3. Pose-Keypoints enthalten **keine Finger** [V].
Handformen (Daumen hoch, Faust) brauchen deshalb ein Handmodell. Dafür muss die Hand ≥ ≈ 60 px groß sein [A], was bei
Raumkameras nur über einen Crop aus `main_rtsp` erreichbar ist. **Empfehlung:** D1 startet nur mit Armgesten. D1b
(MediaPipe) kommt erst, wenn Bedarf belegt ist.

## 4. CONTRACT.md-Ergänzungen (additiv)

**DB (`ADDITIVE_COLUMNS`):** `cameras.pose_enabled`, `cameras.gesture_enabled`, `cameras.reid_enabled`
(jeweils boolean NOT NULL DEFAULT false), `rules.trigger_options` jsonb NOT NULL DEFAULT '{}',
`rules.repeat_s` double precision NOT NULL DEFAULT 0, `rules.repeat_max` integer NOT NULL DEFAULT 0,
`events.acknowledged_at` timestamptz NULL.

**Rule:**
```jsonc
{ ..., "trigger": "enter|leave|dwell|posture|gesture",
  "trigger_options": { "posture": "lying", "max_motion": 0.02 }    // posture: min_dwell_s = Liegedauer
                   | { "gestures": ["arms_up","arm_left","arm_right","arms_cross"] },
  "repeat_s": 120, "repeat_max": 3 }   // Eskalation: erneut auslösen, bis Event quittiert/beendet
```
Neue Template-Platzhalter: `{posture}`, `{gesture}`, `{repeat}` (0 = erste Auslösung).

**Camera/internal cameras:** `pose_enabled`, `gesture_enabled`, `reid_enabled`.
**Settings:** `gesture_hold_s: 1.0` (0.5..5), `reid_ttl_min: 60` (5..720), `reid_match_threshold: 0.60` (0.3..0.95, [A]),
`fall_default_dwell_s: 60`.

**MQTT intern:**
- `Obj.attributes` zusätzlich `posture?`, `motion?`. Das Track-`update` geht zusätzlich bei Haltungswechsel raus
  (`attributes.posture`, `attributes.posture_since`).
- Worker → `vg/internal/gesture/{camera_id}` (QoS 1):
  `{camera_id, track_id, gesture, score, held_s, ts, zones:[...], identity:{...}|null}`.

**REST:**
- `POST /api/internal/reid {camera_id, track_id, embedding:[512 float], quality, ts}` →
  `{presence_key, identity_id|null, score, is_new}`.
- `POST /events/{id}/ack` → Event mit `acknowledged_at` (stoppt die Wiederholung, Scope `write`).
- `GET /presence?minutes=60` →
  `[{presence_key, identity:{id,kind,display_name,name}|null, source:"face|reid|none", confidence, camera_id,
  camera_name, room, zones, active, since, last_seen, event_id}]`.
- Event: `attributes.posture`, `attributes.presence_key`, `attributes.identity_source: "face"|"reid"`. Die
  `identity` eines Events wird über ReID nur gesetzt, wenn der Score ≥ Schwelle + 0,1 ist.

**WebSocket:** `{"type":"gesture", ...wie MQTT}` und `{"type":"presence","presence":Presence}`.
**MQTT extern:** `{prefix}/gesture/{camera_slug}` mit Payload `{gesture, camera, identity, ts}`.
**MCP:** `who_is_where` nutzt `/presence` (Feld `source`, `confidence`, „zuletzt gesehen“). Neu sind
`list_alerts(active_only)` (unquittierte Haltungs-Events) und `acknowledge_alert(event_id)` (Scope `write`).

## 5. UI-Skizze

- Admin › Kameras: Abschnitt „Personenanalyse“ mit den Schaltern „Haltung/Sturz“, „Gesten“ und
  „Wiedererkennung über Kameras“, dazu eine VRAM-Anzeige aus `/system`.
- Admin › Regeln: Auslöser „Liegt am Boden“ (Dauer, „regungslos“) und „Geste“ (Mehrfachauswahl mit Piktogrammen),
  Bereich „Wiederholen alle … s, max. …×“.
- Live-Overlay: Skelett optional einblendbar, Haltungs-Badge („liegt seit 1:20“), kurzer Gesten-Toast.
- Ereignisse: Banner für unquittierte Notfälle mit dem Knopf „Quittieren“.
- Neue Seite „Anwesenheit“ (`/presence`): Karten je Person mit Raum, Quelle (Gesicht/ReID) und Zeit.
- Mock (`src/lib/mock.ts`) und `types.ts`/`api.ts` werden passend ergänzt.

## 6. Schritte

1. **Machbarkeitstest** `worker/vg_worker/posetest.py` (M): Pose und ReID auf `cam1`, gestellte Sturz- und
   Gestenvideos (selbst aufgenommen, 10 Szenen), VRAM-Spitze, Lasttest mit 16 Kameras.
2. Pose-Stufe und Haltung im Worker, Felder in MQTT/Contract (M).
3. Backend: Auslöser `posture`, `repeat_s`, `ack`, Spalten, WS (M).
4. Frontend: Kamera-Schalter, Regel-Editor, Overlay und Quittieren (M).
5. Gesten: Klassifikator, Topic, Auslöser `gesture`, Toast (M).
6. ReID: ONNX-Export, `reid.py` im Worker und Backend, `/presence` (L).
7. Seite „Anwesenheit“, MCP `who_is_where`/`list_alerts`/`acknowledge_alert`, Doku (M).
8. Optional D1b MediaPipe-Handformen (M).

## 7. Abnahmekriterien (e2e)

- `posetest` zeigt: VRAM-Spitze gesamt < 4,5 GB, Lasttest 16 × 5 fps ohne Rückgang (±5 %), Pose ≤ 15 ms je Batch [A].
- Gestellter Sturz auf `cam1` (eigene Aufnahme in testcam): Nach N s Liegen steht ein Event mit `rules` gefüllt in
  `/api/events`, MQTT-Alarm wird mitgelesen und nach `repeat_s` wiederholt. Nach `POST …/ack` gibt es keine
  Wiederholung mehr.
- Liegen in der Zone „Sofa“ (ausgeschlossen) löst nicht aus. Sitzen und Bücken lösen in 10 Testszenen nicht aus.
- Geste „arms_up“ 1,5 s → genau ein MQTT-Topic und ein WS-Toast. 0,5 s → nichts. Bei Regel `identity.mode=known`
  löst eine unbekannte Person nichts aus.
- Person geht von `cam1` zu `cam2` (zwei Aufnahmen derselben Person): `/presence` zeigt denselben `presence_key`.
  Hat sie auf `cam1` ein Gesicht, erscheint auf `cam2` `identity_source:"reid"`. Nach `reid_ttl_min` ist alles
  verworfen, kein Embedding liegt in der DB oder unter `/data/media`.
- MCP `who_is_where` liefert „zuletzt gesehen“.

## 8. Risiken

- **Liegende Personen** erkennt YOLO11s schlechter, der Track kann abreißen. Gegenmaßnahme: Beim Track-Ende mit
  Haltung `lying` gilt ein „Nachlauf“ von 30 s, und ein neuer Track an derselben Stelle erbt die Liegedauer.
  Das muss im Test belegt werden.
- Fehlalarme (Spielen am Boden, Haustier-Decke) → Zonen, Mindestdauer, `max_motion`.
- ReID ist bei gleicher Kleidung mehrerer Personen und bei Kleidungswechsel unzuverlässig. Es gilt deshalb nur als
  Hinweis (`confidence`) und überschreibt nie eine Gesichts-Identität. Das Gesicht hat immer Vorrang.
- Gesten mit Fehlauslösung durch Strecken/Winken → Zonen, bekannte Identität, 1 s Haltezeit.
- Lizenzen: YOLO11-pose AGPL (wie bisher), OSNet-Gewichte prüfen [A], MediaPipe Apache-2.0.
- Datenschutz: ReID-Embeddings sind biometrienah. Sie liegen nur im RAM und werden pro Kamera eingeschaltet.

## 9. Abhängigkeiten

- **Phase 1** (Gesichter, Lernsperre): ReID gibt Gesichts-Identitäten weiter. Gesperrte Identitäten bleiben
  unverändert, da ReID keine Samples speichert.
- **Phase 2** (API-Keys): `ack` und MCP-Schreibwerkzeuge brauchen den Scope `write`. Notfall-Payloads enthalten
  signierte Snapshot-URLs.
- **Phase 3/4** (YOLOE, GPU-Abwechseln): Die Pose-Stufe teilt sich das Rechenbudget und das Muster
  `_wake`/Event in `pipeline.py`. Deshalb erst nach Phase 4 umsetzen, Lasttest gemeinsam.
- **Phase 6** (MCP-Dienst `mcp/server.py`): `who_is_where` wird erweitert.
- **Andere Roadmap-Themen:** D1 baut auf der Pose-Stufe aus C1 auf (C1 zuerst). Eskalation und Quittieren sind
  nützlich für Alarm-Themen und eine spätere Home-Assistant-Anbindung. D2 (Rückfrage) kann unsichere ReID-Verknüpfungen
  zur Bestätigung vorlegen.
