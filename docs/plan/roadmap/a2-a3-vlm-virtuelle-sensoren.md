# A2 „Frag deine Kamera“ (VLM) und A3 Virtuelle Sensoren – Entwurf

Stand: 2026-10-24. Baut auf Phasen 1–6 (`../00-gesamtplan.md`) und CONTRACT.md „Nachträge: Gesichter-Lernsperre,
API-Keys, Gegenstände (Inventar), MCP“ auf. Kennzeichnung: **[V]** = durch Quelle/Code belegt, **[S]** = Schätzung,
im Machbarkeitstest zu messen.

## 1. Zielverhalten

- **A2:** Freie Frage zum *aktuellen* Bild einer Kamera (optional Bildausschnitt): „Ist der Herd aus?“ → kurze
  Antwort + `yes/no/unknown` + Standbild. Nur auf Anfrage, Modell lazy geladen, nach Leerlauf entladen. Kein Video
  gespeichert, nur das beantwortete Standbild (Aufbewahrung wie Snapshots). Claude als Fallback **nur**, wenn die
  Kamera `vlm_cloud_allowed=true` hat und ein Schlüssel per Env gesetzt ist.
- **A3:** Bereich + Frage/Art → Zustand `on|off|unknown` mit Hysterese. Günstige Auswertung (Helligkeit, Blinken,
  Referenzbilder, Inventar) zuerst; VLM nur, wenn sich der Bereich sichtbar verändert hat oder der letzte VLM-Befund
  zu alt ist. Ausgabe REST, WS, MCP, externes MQTT; Regel-Auslöser und -Bedingung.

## 2. Modellwahl (6 GB VRAM, ~2,7 GB belegt, YOLOE-Ziel: Spitze < 4,5 GB gesamt)

| Kandidat | Größe | VRAM | Bewertung |
|---|---|---|---|
| **Moondream 2 (INT4-QAT, Rev. 2025-04-14 ff.)** | 2B | 2,0 GiB Laufzeit laut Spezifikation, 2,4 GB laut Changelog **[V]** | Favorit: klein, `query/detect/point`, schnell (184 tok/s auf RTX 3090 **[V]**); auf A1000 ~1–3 s je Antwort **[S]**. Nur Englisch gut. |
| Qwen2.5-VL-3B (AWQ/bnb-4bit) | 3B | ~2,5–3,5 GB inkl. Bild-Token **[S]**, keine Messung gefunden | Bestes Deutsch/Verständnis, braucht `transformers ≥ 4.49` **[S]**; voraussichtlich zu groß neben YOLOE. |
| SmolVLM-500M / 256M | 0,5 B | ~1–1,5 GB fp16 **[S]** (Rechnerwerte schwanken) | Notlösung bei knappem VRAM, schwächere Antworten. |
| Florence-2-base | 0,23 B | ~0,6 GB fp16 **[S]** | Keine freie Frage (nur Caption/OCR/OD) → nur für Sensor-Art `ocr` (Display ablesen), später. |
| Moondream 3 (Preview, 9B MoE) | – | > 6 GB | ausgeschlossen. |

Konsequenz: Moondream 2 INT4 passt **nur knapp** neben Detektion + YOLOE (2,7 + ~1,5 + 2,0–2,4 + CUDA-Kontext
~0,3 ≈ 6,5 GB **[S]**). Deshalb **GPU-Abstimmung**: Solange das VLM geladen ist, pausiert der Worker das Inventar
(existiert bereits als `perf.inventory.skipped`) und gibt dessen Cache frei. Sprache: Prompt intern Englisch; die
REST-Schicht übersetzt nicht – MCP-Clients formulieren englisch (Werkzeugbeschreibung), die UI bietet Vorlagen.

## 3. Architektur

- **Neuer Dienst `vlm`** (`vlm/`, Compose-Profil `vlm`, nur intern, kein Port nach außen). Eigener Container, weil
  `transformers`/`pyvips`-Pins nicht mit ultralytics/insightface im Worker kollidieren sollen; Basis
  `pytorch/pytorch:2.5.1-cuda12.4-cudnn9-runtime` (CUDA ≤ 12.4). Modell im Volume `models`, Download einmalig.
  - `vlm/vg_vlm/server.py` (FastAPI): `POST /infer {image_b64, question, region?, mode:"query|yesno"}` →
    `{answer, yes_no, ms, model}`; `GET /health` → `{state:"off|loading|ready|error", vram_mb}`.
  - `vlm/vg_vlm/engine.py`: lazy Laden, Warteschlange Länge 1 (sonst 429), Timeout 20 s, Entladen nach
    `vlm_idle_unload_s`, vor dem Laden NVML-Prüfung (freier VRAM < Bedarf + 300 MB → 503).
  - Vor dem Laden MQTT `vg/internal/gpu {holder:"vlm", state:"loading|ready|released"}` (retain) → Worker pausiert/
    setzt Inventar fort.
- **Backend:** `backend/app/vlm.py` (Bild holen über bestehenden go2rtc-Proxy `frame.jpeg?src=cam_N`, Ausschnitt
  zuschneiden, an `vlm` oder Claude), `backend/app/cloud_vlm.py` (Anthropic-HTTP-Aufruf, Schlüssel nur aus Env
  `VG_ANTHROPIC_API_KEY`, nie geloggt; Modell-ID aus Setting), `backend/app/routers/ask.py`,
  `backend/app/sensors.py` (Zustandsautomat, Scheduler, MQTT), `backend/app/routers/sensors.py`.
- **Worker:** `worker/vg_worker/sensors.py` – billige Messungen auf den ohnehin dekodierten Detect-Frames (numpy,
  CPU): mittlere Helligkeit, Blinken (Std. über 3 s), Änderungsmaß zum letzten VLM-Bild, Referenzähnlichkeit über das
  bereits vorhandene DINOv2 (Haustiere, 384-d). Lädt Sensor-Definitionen über `/api/internal/cameras`.
- **MCP:** `mcp/server.py` um `ask_camera`, `list_sensors`, `get_sensor` erweitern (ruft REST mit dem Key des Clients).

## 4. CONTRACT.md-Ergänzungen (additiv)

**DB:** `cameras.vlm_cloud_allowed` boolean NOT NULL DEFAULT false; neue Tabellen `virtual_sensors`,
`sensor_history`, `sensor_references`; `rules.sensor` jsonb NULL.

**Settings:** `vlm_enabled: true`, `vlm_model: "moondream2-int4"`, `vlm_idle_unload_s: 600`,
`vlm_cloud_model: null` (Claude-Modell-ID, `null` = Cloud aus), `ask_retention_days: 7`.

**REST (Präfix `/api`):**
- `POST /cameras/{id}/ask {question: 1..300, region?: [x1,y1,x2,y2], engine?: "auto|local|cloud"}` →
  `{answer, yes_no: "yes"|"no"|"unknown", engine: "local"|"cloud", model, ms, snapshot_url, ts}`.
  **Scope `read` genügt** (Ausnahme wie `/media/sign`). Fehler: 503 „Bildmodell nicht verfügbar“, 429 „Bildmodell
  beschäftigt“, 403 „Cloud für diese Kamera nicht freigegeben“.
- `GET/POST /sensors`, `GET/PUT/DELETE /sensors/{id}`, `POST /sensors/{id}/evaluate` (sofort, write),
  `GET /sensors/{id}/history?limit=50`, `POST /sensors/{id}/references {state:"on"|"off"}` (aktuelles Bild als
  Referenz, max. 5 je Zustand), `DELETE /sensors/{id}/references/{rid}`.

```jsonc
// VirtualSensor
{ "id": 4, "name": "Bad-Fenster", "camera_id": 3, "enabled": true,
  "region": [0.62, 0.10, 0.80, 0.45],             // normiert x1,y1,x2,y2
  "kind": "brightness|blink|reference|inventory|vlm",
  "params": { "threshold": 0.55 }                 // je Art: brightness {threshold}, blink {min_std},
                                                  // reference {} , inventory {label}, vlm {question}
  "verify_vlm": false,                            // billige Art liefert Kandidat, VLM bestätigt Wechsel
  "interval_s": 10, "vlm_max_age_s": 900, "change_threshold": 0.08,
  "on_n": 2, "off_n": 3,                          // Hysterese: Messungen in Folge
  "state": "on|off|unknown", "value": 0.71, "since": "...", "last_eval": "...",
  "last_engine": "cheap|local|cloud", "snapshot_url": "/media/…" | null }
```

**WS:** `{"type":"sensor","sensor":VirtualSensor}` bei Zustandswechsel (und `deleted: true`).

**MQTT intern:** Worker → `vg/internal/sensor/{camera_id}` (QoS 0, je Intervall):
`{camera_id, ts, values: [{sensor_id, value, change}]}`; `vg/internal/gpu` siehe oben;
`vg/internal/system.models.vlm` (`off|loading|ready|error: …`, vom Backend aus `vlm /health` ergänzt).
**MQTT extern:** `{prefix}/sensor/{slug}` → `on`/`off`/`unknown` (retain), `{prefix}/sensor/{slug}/attributes`
→ VirtualSensor-JSON (signierte URLs).

**Regeln:** `trigger` zusätzlich `"sensor"` mit `sensor: {sensor_id, state: "on|off"}`; außerdem optionale Bedingung
`sensor: {sensor_id, state}` bei Track-Triggern („Person an Haustür **und** Fenster offen“). Neue Platzhalter
`{sensor}` `{sensor_state}`. Sensor-Regeln erzeugen kein Event, aber führen Aktionen aus (Snapshot/Clip über `cmd`).

**MCP-Tools:** `ask_camera(question, camera_id?, camera_name?, region?, allow_cloud=false)` → Text + Bild (wie
`get_camera_snapshot`); `list_sensors(camera_id?, room?)`, `get_sensor(sensor_id|name)` inkl. Verlauf.

## 5. UI-Skizze

- **Live-Ansicht:** Schaltfläche „Frag die Kamera“ → Eingabe + Vorlagen („Ist das Licht an?“), optional Rahmen
  ziehen; Antwort mit Standbild, Kennzeichnung „lokal“/„Cloud (Claude)“, Laufzeit.
- **Admin → Kamera:** Schalter „Cloud-Fallback erlauben (Bild verlässt das Haus)“ mit Warnhinweis.
- **Admin → Virtuelle Sensoren** (`frontend/src/pages/admin/Sensors.tsx`): Liste mit Zustand/seit; Editor nutzt
  `ZoneEditor` im Rechteckmodus, Art wählen, „Referenz ‚an‘/‚aus‘ aufnehmen“, „Jetzt prüfen“, Verlaufsleiste.
- **Regeln:** Auslöser „Virtueller Sensor wechselt“, Bedingung „Sensor ist …“.

## 6. Schritte

1. **Machbarkeitstest (M):** `vlm/bench.py` im Probe-Container: Moondream 2 INT4, Qwen2.5-VL-3B-AWQ, SmolVLM-500M
   gegen 20 eigene Bilder (Fenster/Licht/Herd, Ja/Nein-Wahrheit) **bei laufendem Worker + YOLOE**. Messen:
   VRAM-Spitze (NVML), Ladezeit, Antwortzeit, Trefferquote. Go: Spitze gesamt < 5,6 GB, Antwort < 5 s, ≥ 85 % richtig.
2. Dienst `vlm` + `vg/internal/gpu`-Abstimmung im Worker (M).
3. Backend `ask` + Cloud-Fallback + Settings/Kamera-Spalte + CONTRACT (M).
4. MCP `ask_camera`, Frontend „Frag die Kamera“ (S).
5. Sensoren: DB/REST/Zustandsautomat/MQTT/WS (M), Worker-Messungen inkl. Referenz (M).
6. Regel-Trigger/Bedingung (S–M), Sensor-UI (M), MCP-Sensortools (S).

## 7. Abnahmekriterien (e2e)

- `POST /api/cameras/1/ask {"question":"Is there a person?"}` mit Read-Key → Antwort < 5 s (warm), `engine:"local"`;
  `nvidia-smi` zeigt Gesamt-VRAM < 5,8 GB; Detektion läuft weiter (Status-fps ≥ 4,5).
- Nach `vlm_idle_unload_s` meldet `/api/system` `models.vlm = "off"`, VRAM wieder auf Ausgangswert, Inventar läuft.
- `engine:"cloud"` an Kamera ohne Freigabe → 403; mit Freigabe und Key → Antwort, `engine:"cloud"`; Key in keinem Log.
- Helligkeits-Sensor auf testcam: Bereich abdunkeln (Testvideo) → genau ein Wechsel nach `on_n` Messungen,
  MQTT `visionguard/sensor/<slug>` retain, WS-Nachricht, Regel mit `trigger:"sensor"` publiziert MQTT-Aktion.
- VLM-Sensor ruft das Modell bei unverändertem Bild höchstens alle `vlm_max_age_s` (Zähler in `/api/system`).
- MCP-Inspector: `ask_camera` liefert Text + Bild; `list_sensors` zeigt Zustände.
- `corepack pnpm build` grün.

## 8. Risiken

- **VRAM** (Hauptrisiko): Moondream + YOLOE zusammen evtl. > 6 GB → Inventar pausiert während Fragen; notfalls
  SmolVLM oder VLM nur auf CPU (langsam, ~10–30 s **[S]**).
- `trust_remote_code` bei Moondream: Revision fest pinnen (Hash), Code einmal prüfen.
- Halluzination bei Ja/Nein: immer `unknown` erlauben, Hysterese, „vermutlich“-Formulierung; für sicherheitsrelevante
  Zustände Referenz-Sensor bevorzugen.
- Nacht/IR-Bilder: Farbfragen unzuverlässig.
- Cloud: Datenschutz – Freigabe je Kamera, Hinweis in der UI, keine automatische Nutzung durch Sensoren ohne Freigabe.
- Externe Erreichbarkeit: `ask` ist teuer → Rate-Limit je Key (z. B. 10/min).

## 9. Abhängigkeiten

- **Phasen:** 2 (API-Keys, Scopes, signierte URLs – zwingend), 3/4 (YOLOE-VRAM-Budget und Inventar-Pause), 5 (Sensor-Art
  `inventory`), 6 (MCP-Dienst).
- **Roadmap:** A2 ist Grundlage für A3, B3 (Tagesbericht nutzt Sensorverläufe), C4 (Vorräte = VLM-/Inventar-Sensoren);
  A1 kann Sensorbedingungen mitnutzen; C5 (Kamera-Gesundheit) teilt die Helligkeits-/Änderungsmessung im Worker;
  D2 (Rückfrage) kann unsichere VLM-Antworten sammeln; D3 (verteilte Worker) – `vlm` als eigener Dienst ist bereits
  auslagerbar.

Quellen: Moondream-Spezifikation (docs.moondream.ai/specifications), Moondream-Changelog (moondream.ai/c/docs/changelog);
übrige VRAM-Werte aus Rechnern (spheron.network, willitrunai.com) – nur Schätzungen.
