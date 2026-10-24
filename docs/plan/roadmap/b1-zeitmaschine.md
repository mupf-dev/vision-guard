# B1 – Haus-Zeitmaschine (semantische Suche in der Vergangenheit)

Stand: 2026-10-24, Entwurf. Kennzeichnung: **[V]** = im Repo/Vertrag geprüft, **[A]** = Annahme/Schätzung, im
Machbarkeitstest zu messen. Zielzustand der Phasen 1–6 laut `CONTRACT.md` („Nachträge: … Inventar, MCP“).

## Zielverhalten

- Je Kamera mit `history_enabled` wird alle `history_interval_s` (Standard 30 s) ein Bild-Embedding erzeugt und
  zusammen mit einem Mini-Vorschaubild (256×144 JPEG) gespeichert. **Kein Video, keine Vollbilder.**
- Dedupe: unterscheidet sich das Embedding kaum vom zuletzt gespeicherten derselben Kamera
  (1 − cos < `history_min_change`), wird nur `last_ts` der vorhandenen Zeile verlängert. Ruhige Räume kosten so fast nichts.
- Textsuche („rotes Paket im Flur“, „Brille auf dem Sofa“) liefert **Zeiträume** (aufeinanderfolgende Treffer je
  Kamera zusammengefasst) mit Vorschau, Kamera, Raum und Score – auch per MCP `search_history`.
- Eigene Aufbewahrung `retention_days_history` (Standard 14 Tage).

## Wiederverwendung des YOLOE-Textencoders (MobileCLIP)? – Nein

- ultralytics 8.3.253 bringt nur den **Text**-Turm `mobileclip_blt.ts` mit [V, 03-modell.md]; der Bild-Turm fehlt.
  YOLOE vergleicht Text mit **Regions**-Merkmalen (über RepRTA angepasst), nicht mit einem globalen Bild-Embedding.
  Die Text-Embeddings passen also nicht zu einem separat geladenen Bild-Encoder [A, nicht sinnvoll testbar].
- MobileCLIP ist rein englisch; deutsche Suchanfragen wären nur über Übersetzung möglich.
- Gewinn wäre nur ~0,25 GB RAM bei starker Kopplung an das Inventar-Modell. Daher: eigenes, mehrsprachiges Modell.
- Erlaubte Wiederverwendung: `KeyframeGrabber` aus `worker/vg_worker/inventory.py` und `inventory_synonyms`
  (als Zusatzbegriffe für die Anfrage).

## Modell und VRAM

| Kandidat | Bibliothek | VRAM (nur Bild-Turm, fp16, Batch 8) | Bemerkung |
|---|---|---|---|
| **SigLIP 2 ViT-B/16-256** (`timm/ViT-B-16-SigLIP2-256`) | `open_clip_torch` (≥ 2.31, Version im Test pinnen) [A], `timm==1.0.20` schon im Image [V] | ≈ 0,25–0,4 GB [A] | **Empfohlen**: mehrsprachig (Gemma-Tokenizer) [A: Deutsch-Qualität messen], 768-d |
| MobileCLIP2-S2 / MobileCLIP-B(LT) | `open_clip_torch` | ≈ 0,1–0,2 GB [A] | Fallback bei VRAM-Knappheit, nur Englisch |

- **Text-Turm läuft auf der CPU** im Worker (SigLIP-2-Text ≈ 280 M Parameter, ≈ 1,1 GB RAM fp32, ≈ 50–150 ms je
  Anfrage [A]) → kein zusätzlicher VRAM. Lazy laden, nach 10 min Leerlauf entladen.
- Last: 16 Kameras / 30 s ≈ 0,5 Bilder/s (mit Kacheln ×5 ≈ 2,7/s) – für die GPU vernachlässigbar [A].
- Neue Abhängigkeiten (pinnen, eigene Dockerfile-Schicht): `open_clip_torch`, ggf. `transformers` +
  `sentencepiece` für den Tokenizer. Gewichte einmalig nach `/models/history/`.
- Lizenz: SigLIP 2 Apache-2.0 [A, prüfen].

## Speicherrechnung

Pro Zeile: `halfvec(768)` 1,5 KB + Zeile/btree ≈ 0,2 KB + HNSW ≈ 1,7 KB ≈ **3,4 KB DB** + **≈ 6 KB Vorschau** [A].

| Szenario (16 Kameras, 30 s) | Zeilen/Tag | pro Tag | 14 Tage | 30 Tage |
|---|---|---|---|---|
| ohne Dedupe, ohne Kacheln | 46 080 | ≈ 430 MB | ≈ 6 GB | ≈ 13 GB |
| Dedupe (−80 % [A]) | ≈ 9 200 | ≈ 90 MB | ≈ 1,2 GB | ≈ 2,6 GB |
| Dedupe + Kacheln (×5 Vektoren, 1 Vorschau) | ≈ 46 000 Vektoren | ≈ 210 MB | ≈ 3 GB | ≈ 6,3 GB |

Kacheln (2×2 + Gesamtbild) nur pro Kamera einschaltbar: ganze Bilder mit 256 px sehen kleine Dinge (Brille) kaum.

## Architektur

**Worker** – neu `worker/vg_worker/history.py`:
- `HistoryEncoder` (lazy, Fehler isoliert wie `identify.py`; Status `models.history`).
- `HistorySampler`: holt je Kamera im Intervall das letzte Keyframe (`KeyframeGrabber`, sonst letztes Detect-Frame),
  rechnet Gesamtbild (+ optional 4 Kacheln), Dedupe gegen eigenes letztes Embedding, schreibt Vorschau nach
  `history/YYYY-MM-DD/cam{N}_{ts}.jpg`, publiziert per MQTT. Verwirft statt zu queuen.
- Text-Kodierung auf Anfrage (MQTT-RPC wie `cmd`/`cmd_result`).

**Backend** – neu `backend/app/history.py` (Speichern, Suche, Episoden), `backend/app/routers/history.py`,
Aufbewahrung in `retention.py`. Datenbank: Extension `vector`.

**MCP** – Tool in `mcp/server.py`. **Frontend** – Seite `frontend/src/pages/History.tsx`, Typen/API/Mock.

## Datenbank: Umstieg auf pgvector

- Image `postgres:17-alpine` → `pgvector/pgvector:0.8.x-pg17` (Debian/glibc; Tag beim Pinnen prüfen [A]).
- **Nicht** dasselbe Volume weiterverwenden: musl → glibc ändert die Sortierung → Text-Indizes müssten neu gebaut
  werden. Daher Dump/Restore:
  1. `docker compose stop backend worker mcp`; `pg_dump -Fc visionguard > ~/vg-backup/vg-<datum>.dump` (außerhalb Repo).
  2. Neues Volume `db-data-pgv`, Dienst `db` auf neues Image + Volume, `pg_restore`, `CREATE EXTENSION vector`.
  3. Zeilenzahlen aller Tabellen vergleichen; altes Volume `db-data` bleibt als Rückfall bis zur Abnahme.
  4. Skripte `scripts/db-migrate-pgvector.sh` und `scripts/db-rollback.sh`.
- Alternative ohne Datenumzug: eigenes `db/Dockerfile` (`postgres:17-alpine` + pgvector aus Quellen gebaut).
- `init_db`: `CREATE EXTENSION IF NOT EXISTS vector`; fehlt sie, bleibt B1 abgeschaltet (Log + `/system`), Rest läuft.

## CONTRACT.md-Ergänzungen (additiv)

**DB:** `cameras.history_enabled` boolean DEFAULT false, `cameras.history_interval_s` double DEFAULT 30 (5..600),
`cameras.history_tiling` boolean DEFAULT false. Neue Tabelle `history_frames`:
`id bigserial, camera_id int, ts timestamptz, last_ts timestamptz, tile smallint (0=ganz, 1..4),
embedding halfvec(768), thumb_path varchar, model varchar(60)`; Indizes `(camera_id, ts)`,
HNSW `halfvec_cosine_ops`, Suche mit `hnsw.iterative_scan = relaxed_order` (pgvector ≥ 0.8) für Filter.

**Settings:** `retention_days_history: 14` (1..365), `history_min_change: 0.04` (0..0.5).

**MQTT intern:**
- Worker → `vg/internal/history/{camera_id}` (QoS 1): `{camera_id, ts, model, dim, thumb_path|null,
  extend: bool, vectors:[{tile, bbox:[x1,y1,x2,y2], vec_b64}]}` (`vec_b64` = float16 LE, L2-normiert;
  `extend=true` → nur `last_ts` verlängern).
- Backend → `vg/internal/history/encode` `{request_id, text:[str]}`; Worker → `vg/internal/history/encode_result`
  `{request_id, ok, model, vecs_b64:[…], error}`. Timeout 5 s → REST 503 „Textsuche derzeit nicht verfügbar“.
- `vg/internal/system`: `models.history`, `perf.history: {per_min, ms, skipped}`.

**REST:**
- `GET /history/search?q=&camera_id=&room=&since=&until=&limit=10&signed=` →
  `{query, model, items:[{camera_id, camera_name, room, start_ts, end_ts, best_ts, score (0..100), tile_bbox,
  thumb_url, frames}]}` (Episoden: Treffer derselben Kamera mit Abstand ≤ 2×Intervall).
- `GET /history/frames?camera_id=&around=&span_min=10` → `{items:[{ts, last_ts, thumb_url}]}` (Filmstreifen).
- `GET /history/stats` → `{frames, vectors, thumb_bytes, oldest, by_camera:{id: n}}`.
- `DELETE /history?camera_id=&before=` (Scope `write`) → `{deleted}`.
- Kamera: Felder `history_enabled`, `history_interval_s`, `history_tiling`; intern ebenso in `/api/internal/cameras`.

**MCP:** `search_history(query: str, since?: str, until?: str, camera?: str, limit: int = 5,
include_image: bool = false)` → `{query, items:[{camera_name, room, start_ts, end_ts, best_ts, score, thumb_url}]}`
+ deutscher Satz („Am Di 14:03–14:21 im Flur, Übereinstimmung 71 %“); optional Bild des besten Treffers.

**Medien:** Unterordner `history/YYYY-MM-DD/`, signierbar wie alle `/media/*`.

## UI-Skizze

- Neue Seite **„Zeitmaschine“**: Suchfeld, Filter Kamera/Raum, Zeitraum (Heute / 7 Tage / Alles / frei);
  Ergebnisraster mit Vorschau, Kamera, Zeitraum, Score-Balken. Klick → Filmstreifen ±10 min der Kamera,
  Links zu Events im selben Zeitraum.
- Admin › Kamera: Abschnitt „Zeitmaschine“ (Schalter, Intervall, Kacheln). Admin › Einstellungen: Aufbewahrung,
  Änderungsschwelle, Speicheranzeige aus `/history/stats`, Knopf „Verlauf löschen“.

## Schritte

0. Machbarkeitstest `worker/vg_worker/histtest.py` (SigLIP 2 vs. MobileCLIP2; 300 Bilder testcam + echte Kamera,
   25 deutsche/englische Anfragen, Recall@5, ms, VRAM-Spitze, Dedupe-Quote über 24 h) – **M**
1. pgvector-Umstieg mit Dump/Restore und Rückfallskript – **M**
2. Worker `history.py` + MQTT + Lasttest 16 Kameras – **M**
3. Backend Speicherung, Retention, Stats – **M**
4. Text-RPC, Suche, Episoden, REST – **M**
5. Frontend Seite + Admin-Felder + `types.ts`/`api.ts`/`mock.ts` – **M**
6. MCP-Tool + `mcp/selftest.py` – **S**
7. CONTRACT/AGENTS/README – **S**

Gesamt: **L**.

## Abnahmekriterien (e2e)

- Nach 10 min mit `cam1` aktiv: `/history/stats.frames > 0`, Vorschaudateien unter `history/`, keine `.mp4`/Vollbilder.
- `GET /history/search?q=Person mit Rucksack` findet in `cam1` mindestens einen Zeitraum mit Score > Median.
- Testobjekt (z. B. roter Karton) 5 min ins Bild stellen, entfernen; Suche „roter Karton“ liefert den Zeitraum ±1 Intervall unter den Top 3.
- MCP `search_history` mit read-Key von außen liefert signierte, abrufbare `thumb_url`.
- Worker ohne GPU-Platz/Modellfehler: Detektion läuft weiter, `/system` zeigt `history: error …`.
- Retention mit `retention_days_history=1` löscht Zeilen und Dateien.
- Lasttest 16 Kameras: Detektion-fps unverändert (±5 %), VRAM-Zuwachs ≤ 0,5 GB.
- Nach DB-Umstieg: alle Tabellen mit gleicher Zeilenzahl, Login/Events/Gegenstände funktionieren.

## Risiken

- Globale Embeddings übersehen kleine Gegenstände → Kacheln, Kombination mit Inventar (Phase 5) empfehlen.
- Deutsche Anfragen bei SigLIP 2 schwächer als Englisch [A] → Synonyme/Übersetzung als Zusatzbegriffe.
- Modellwechsel macht alte Vektoren unbrauchbar (Bilder nur als 256-px-Vorschau) → `model`-Spalte, Suche nur im
  aktuellen Modell, Neuberechnung aus Vorschauen nur als Notlösung.
- Datenschutz: Vorschauen sind „Mini-Aufzeichnung“ → nur pro Kamera aktivierbar, kurze Standard-Aufbewahrung.
- DB-Umstieg ist die riskanteste Einzelaktion → Backup, Probelauf, Rückfall.

## Abhängigkeiten

- Phase 2 (API-Keys, signierte Medien) für REST/MCP von außen; Phase 3/4 (`KeyframeGrabber`, YOLOE-VRAM-Messung)
  für Bildquelle und VRAM-Budget; Phase 5 (Retention-Muster, `inventory_synonyms`); Phase 6 (MCP-Dienst).
- Roadmap: A2 (VLM) kann Treffer nachträglich verifizieren; B2/B3 können Zeitraum-Vorschauen nutzen. Der
  pgvector-Umstieg nützt auch späteren Vektorsuchen (Gesichter/Haustiere statt Python-Vergleich).
