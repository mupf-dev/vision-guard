# Fortschritt: Identitäten – Mehrfachauswahl, Gesichtsqualität, Nachtlauf

Plan: [07-identitaeten-mehrfachauswahl-und-training.md](07-identitaeten-mehrfachauswahl-und-training.md).
Schnittstellen: CONTRACT.md, Abschnitt „Nachträge: Identitäten – Mehrfachauswahl, Gesichtsqualität, Nachtlauf“
(ID.A–ID.I).

## Entscheidungen

Vom Nutzer (2026-10-25):
1. Nachtlauf darf sichere Fälle automatisch zusammenführen – nur unbenannte Fragmente (in unbenannt oder benannt),
   nie zwei benannte.
2. Gesichter aus voller Auflösung: ja, wenn der Machbarkeitstest im Rahmen bleibt; sonst Standard aus und je
   Kamera abschaltbar.
3. Vorhandene Fragmente erst nach einer gesehenen Vorschau aufräumen → `nightly_mode` wird als `"preview"`
   (Probelauf) ausgeliefert.

Im Kickoff getroffen (ohne Rückfrage):
- **Schwelle je Person** als Spalte `identities.match_threshold` (null = global); Abgleich künftig mit dem Mittel der
  besten 3 Bild-Ähnlichkeiten statt Einzelmaximum, plus Mindestabstand zum Zweitbesten (`face_match_margin` 0,05).
- **Rücksetzbarkeit** über eine eigene Tabelle `identity_merges` (alle vollständigen Zusammenführungen, auch
  einzeln/Rückfrage), Zurücksetzen stellt die Quelle mit derselben ID wieder her. Teil-Merges werden nicht
  protokolliert (Quelle bleibt ohnehin bestehen).
- **Nachtlauf-Berichte** in `nightly_reports`; eine Vorschau kann später genau so angewandt werden
  (`POST /nightly/reports/{id}/apply`, jede Aktion neu geprüft). Anwenden und `nightly_mode` ändern nur mit Anmeldung
  im Browser (kein API-Key), weil damit automatisch Daten verändert werden.
- Nachtlauf-Hinweis als Notification `source:"report"` **ohne Push** (03:00 Uhr soll niemanden wecken).
- `face_fullres` global zunächst `false` (Paket B setzt den Standard nach dem Machbarkeitstest), je Kamera
  `cameras.face_fullres` (Standard `true` = folgt der globalen Einstellung).
- Galerie: benannte Gesichter bis 40 Bilder (`face_max_samples_named`), unbenannte weiter 20.
- Nachtlauf zunächst nur für `face` (Tiere unverändert).

## Ausgangsstand für Tests (Kickoff, 2026-10-25 ≈ 09:10)

- Höchste IDs vor allen Tests: `identities` **129**, `cameras` **3**, `rules` **2**; aktive API-Keys: 0.
  Alles mit höherer ID, das durch Tests entsteht, wird am Ende wieder entfernt.
- Backup vor der Schemaänderung: `~/vision-guard-backups/visionguard-20261025-090530-vor-identitaeten07.dump`
  (chmod 600), Zeilenzahlen `zeilen-20261025-090530-vor.txt`/`-nach.txt`.

## Messung „vorher“ (Kickoff)

`docker compose exec -T backend python - < scripts/face_stats.py` (nur lesend, ohne Bilder/Namen), 2026-10-25 08:11 UTC,
Beobachtungszeitraum ≈ 0,5 Tage, Schwelle `face_match_threshold` 0,30.

| Kennzahl | vorher | Ziel |
|---|---|---|
| Gesichts-Identitäten (benannt / gesperrt) | 35 (2 / 2) | – |
| Einzelbild-Identitäten | **54 %** | < 20 % |
| 1–2 Bilder | 60 % | – |
| Lebensdauer einer Identität (Median / p90) | 0 h / 0,6 h | – |
| Bildbreite Beispielbilder (p10 / Median / p90) | 71 / 100 / 132 px | – |
| Qualität (p10 / Median / p90) | 0,21 / 0,40 / 0,57 | – |
| Personen-Ereignisse mit Gesichts-Identität | 79 % (196 / 247) | – |
| max. Ähnlichkeit zwischen Benannten | 0,17 | keine Verwechslung |
| Fragmente benannt #15 (≥ 0,40 / ≥ 0,55) | 6 / 2 (≈ 12 je Tag) | ≤ 1 je Tag |
| Fragmente benannt #19 (≥ 0,40 / ≥ 0,55) | 2 / 1 (≈ 4 je Tag) | ≤ 1 je Tag |
| unbenannte Paare ≥ 0,40 / ≥ 0,55 (Maximum) | 10 / 1 von 528 | – |
| Rückfragen identity_match Ja / Nein | 0 / 0 | – |

## Pakete

| Paket | Stand |
|---|---|
| Kickoff (CONTRACT, Gerüste, Messskript, Messung vorher) | erledigt |
| A Mehrfachauswahl (Backend + UI) | erledigt, gemergt |
| B Worker-Gesichter (Qualität, Track-Sammlung, volle Auflösung) | erledigt, gemergt (siehe unten) |
| C Zuordnungslogik (Backend) | erledigt, gemergt (siehe unten) |
| D Nachtlauf | erledigt (Probelauf-Modus in dieser Installation, siehe unten) |
| Integration (Merge A–C, Build, Deploy, Ende-zu-Ende, Lasttest, Probelauf echt) | erledigt (siehe unten); Messung nach einem Tag Betrieb offen |

## Paket A – Mehrfachauswahl (Stand 2026-10-25)

**Backend** (`backend/app/identity_bulk.py`):
- `bulk_delete`, `bulk_merge`, `log_merge`, `list_merges`, `undo_merge` wie CONTRACT ID.B/ID.C. Eine Transaktion unter
  `identity._lock`, Zeilen `FOR UPDATE` in ID-Reihenfolge, Dateien erst nach dem Commit (Verschieben, gekürzte
  Galerie-Bilder löschen, Quellordner entfernen). Fehlgeschlagenes Verschieben → alter Pfad wird zurückgeschrieben,
  der Quellordner bleibt dann stehen; `bulk_delete` löscht solche „fremden“ Pfade gezielt mit.
- `bulk_merge(score=…)` nimmt eine Zahl oder `{src_id: score}` (für den Nachtlauf, Paket D).
- `identity.prune_samples(s, id, limit=None, defer_remove=None)` – optionales Limit (`face_match.sample_limit`) und
  verzögertes Löschen der Dateien; gibt die IDs der entfernten Bilder zurück. Einzel-Merge (`identity.merge`) prüft das
  Bildlimit jetzt mit `sample_limit` und protokolliert vollständige Merges (`actor` `user`, Rückfrage „Ja“: `review`).
- `GET /identities/merges?actor=` akzeptiert auch eine kommagetrennte Liste.

**Grenzen beim Zurücksetzen** (bewusst, nicht protokolliert): Bilder, die beim Kürzen der Galerie nach dem Merge
entfallen sind, kommen nicht zurück; Haustier-Tagebuch-Besuche und Gegenstands-Zuordnungen bleiben beim Ziel; ein
beim Merge vom Ziel übernommener Name bleibt (die Notiz „früher: …“ wird wieder entfernt).

**Oberfläche** (`frontend/src/pages/Identities.tsx`, neue Datei `IdentityBulk.tsx`): Schalter „Auswählen“, Häkchen
(auch per Hover ohne Auswahlmodus), Umschalt-Klick-Bereich, „Alle (N) auswählen“ (berücksichtigt Suche/Ansicht),
„Ähnliche auswählen“ (Vorschläge `with_suggestion` + Merge/Rückfrage-Paare des letzten Nachtlauf-Berichts),
Aktionsleiste mit Zähler (inkl. „nicht in dieser Ansicht“), Dialog „Zusammenführen“ (Ziel: Ausgewählte oder andere
Identität gleicher Art; Vorschlag benannt > Ereignisse > Bilder; Vorschau „5 Identitäten, 15 Bilder, 29 Ereignisse →
Anna“), Dialog „Löschen“ (Anzahl, „Ereignisse bleiben … ohne Person“), Verlauf mit „Zurücksetzen“. Esc beendet die
Auswahl, beim Wechsel der Art wird die Auswahl geleert. WS: `identities`/`identity` lösen ein entprelltes (400 ms)
Neuladen aus (`useLiveSocket.ts`).

**Tests** (Wegwerf-Container `vg-ida-*` mit leerer DB, synthetische Identitäten): 47 API-Prüfungen
(Fehlerfälle 404/422/409, gemischte Arten, 500er-Limit, kumulatives Bildlimit, Rücksetzen inkl. Dateien, gebündelte
WS-Nachricht, 500er-Merge in < 1 s mit Galerie-Kürzung) und ein simuliertes Verschiebe-Problem; 22 UI-Prüfungen per
headless Chromium gegen `dev:mock` (Desktop und 390 px).

## Paket B – Gesichtsbilder im Worker (2026-10-25)

Umsetzung: `worker/vg_worker/face_quality.py` (Kopfdrehung, Schärfe, Tag/Nacht, Qualität), `face_track.py`
(Sammeln je Track, `provisional`/`final`), `fullres.py` (Vollbild-Leser), Anpassungen in `identify.py`,
`pipeline.py`, `tracking.py` (Track-Ende wartet auf `final`), `selftest.py` (`--face-fullres`, Fake nach ID.F).
Alle Tests in eigenen Compose-Projekten (`vg-faceb`: nur Testkameras + Selbsttest; `vg-faceb-e2e`: eigene leere DB),
GPU-Läufe unter `flock ~/vg-gpu.lock`, danach `down -v`. Am Haupt-Stack wurde nichts geändert; von `cam_3` wurde
für die Decoder-Messung nur über go2rtc gelesen (keine Bilder gespeichert).

### Machbarkeitstest volle Auflösung

**Decoder-Kosten** (30 s je Variante, CPU in % eines Kerns; dazu das Lesen der Pipe in Python):

| Variante | `cam_3` echt (2880×1616 H.264 High) | Testvideo `camhd` (2880×1616, hochskaliert) |
|---|---|---|
| Analyse-Leser heute (5 fps, skaliert auf 1920) | 14,0 % + 1,2 %, RSS 87 MB | 10,2 % + 1,0 % |
| zusätzlicher Vollbild-Leser 3 fps | 12,9 % + 1,5 %, RSS 110 MB | 8,2 % + 1,3 % |
| zusätzlicher Vollbild-Leser 5 fps | 14,1 % + 2,4 %, RSS 110 MB | 9,0 % + 1,9 % |
| Vollbild 3 fps mit NVDEC (`-hwaccel cuda`) | 11,6 % + 1,5 %, RSS 215 MB + VRAM | – |
| nur Keyframes (`-skip_frame nokey`) | 2,5 %, aber nur 0,5 Bilder/s | – |

Erstes Bild nach dem Start eines Lesers: 2,2–3,6 s (Keyframe abwarten). NVDEC spart kaum CPU (Kopieren/Umwandeln
überwiegt) und kostet VRAM → CPU-Dekodierung.

**Varianten:** (a) Analysestrom immer in voller Auflösung dekodieren – verworfen: ein Wechsel von `face_fullres` müsste
ffmpeg des Analysestroms neu starten (CONTRACT ID.G verbietet das) und kostet bei jeder Kamera dauerhaft RAM und
Bandbreite (14 MB je Bild). (b) Ausschnitt aus dem Ringpuffer bzw. go2rtc-Standbild – verworfen: nur Keyframes, bis
2 s Versatz (die Person steht dann woanders). (c) **Gewählt:** zusätzlicher Vollbild-Leser nur auf Anforderung (Person
im Bild, 30 s Nachlauf), 2 Bilder/s, 1-MB-Pipe; Ausschnitt nur aus einem zeitgleichen Vollbild (≤ 0,12 s), sonst nach
≤ 1,5 s Warten aus dem Analysebild. Die erste Fassung mit 5 Bildern/s und 64-KB-Pipe war im Lasttest zu teuer
(16 Kameras: 34,8 statt ≈ 60 Bilder/s, CPU 588 %) und wurde verworfen.

**Lasttest 16 Kameras** (`selftest … --replicate 8 --seconds 60`, parallel zum laufenden Haupt-Stack – die GPU teilt
sich mit dessen Worker, daher ±15 % Streuung zwischen gleichen Läufen):

| Lauf | Bilder/s gesamt | ms je Bild | CPU Worker (Median) | RAM max |
|---|---|---|---|---|
| Standard (8× cam1 + 8× cam2), vorher (3 Läufe) | 59,2 / 60,8 / 60,3 | 8,8 / 7,1 / 6,9 | 115 / 116 / 108 % | 2,7 GB |
| Standard, nachher (`--face-fullres`; 768 px → kein Leser) | 61,0 | 6,7 | 88 % | 2,7 GB |
| HD (8× camhd 2880 + 8× cam2), vorher (2 Läufe) | 63,1 / 54,2 | 9,5 / 15,2 | 251 / 293 % | 3,6 GB |
| HD, nachher ohne volle Auflösung (3 Läufe) | 58,8 / 57,2 / 63,1 | 14,8 / 9,0 / 12,0 | 318 / 326 / 250 % | 3,6 GB |
| HD, nachher **mit** voller Auflösung (2 Läufe) | 64,3 / 56,8 | 8,1 / 11,9 | 463 / 496 % | 5,0 GB |

Das HD-Szenario ist der ungünstigste Fall (8 HD-Kameras, ständig Personen im Bild → 8 Vollbild-Leser dauerhaft
aktiv): +1,4–2,5 CPU-Kerne von 28 und +1,4 GB RAM, Bildrate im Rahmen der Streuung. In dieser Installation hat nur
`cam_3` mehr als 1920 px → ein Leser, ≈ 15 % eines Kerns und ≈ 150 MB, nur solange jemand im Bild ist.
**Ergebnis: im Rahmen → `face_fullres` ist jetzt standardmäßig an** (Settings-Standard `true`, je Kamera abschaltbar
über `cameras.face_fullres`). Hinweis: Wurden die Settings dieser Installation seit dem Kickoff schon gespeichert,
steht dort noch `false` – dann in den Einstellungen einschalten (bewusst nicht automatisch geändert).

**Wirkung** (HD-Läufe, je Gesicht): Gesichtsgröße Median 127 px aus dem Vollbild gegenüber 80 px aus dem Analysebild
(≈ 1,5×, wie 2880/1920), Qualität Median 0,43 gegenüber 0,33; Ausschnitte mit gefundenem Gesicht 93 % gegenüber
88 %. Kosten je Ausschnitt aus dem Vollbild 1,6 ms CPU.

### Qualitätsmaß

- Gier/Nicken grob aus den 5 Gesichtspunkten (Nase relativ zu Augen- und Mundlinie, Rollwinkel herausgerechnet;
  geprüft an einem 3D-Modellgesicht: 0/15/30/45/60° Gier → 0/15/30/45/60°), Schärfe = Laplace-Varianz des
  ausgerichteten 112×112-Gesichts (innerer Bereich), `day_night` = mittlere Sättigung < 12 (IR-Graubild).
- `quality = det_score · min(1, face_size/112) · cos²(yaw)·cos(pitch) · (0,5 + 0,5·min(1, sharpness/60))`.
  Kalibriert an den gespeicherten Gesichtsbildern dieser Installation (nur lesend, 200 Bilder mit Gesicht, keine
  Bilder/Namen ausgegeben): Schärfe bei Gesichtern < 90 px Median 12–22, 90–130 px 224, > 130 px 490; |Gier| Median 9°,
  p90 60°.
- **Achtung Skala (für Paket C/Integration):** Die neue Qualität ist niedriger als die alte (Pose und Schärfe kommen
  dazu). An den gespeicherten (Analysebild-)Beispielen erreichen 32 % ≥ 0,35 und 48 % ≥ 0,30 (alte Formel ≈ 90 %);
  aus dem Vollbild liegen die Werte höher (Median 0,43). `face_new_min_quality`/`face_sample_min_quality` nach einem
  Tag Betrieb mit `scripts/face_stats.py` prüfen.

### Track-Sammlung

- Je Personen-Track Jobs alle 0,4 s, ab 5 guten Gesichtern (≥ 0,5) alle 2 s, höchstens 120; die ersten 3 Jobs je
  Track ohne Budget (wie früher 3 Versuche zu Beginn), danach global ≤ 15 Jobs/s – sonst verwerfen.
- `provisional` beim ersten Gesicht ≥ 0,2 (ein zweites Mal nur ohne Identität und bei ≥ 1,5× Qualität); `final` nach
  30 s und am Track-Ende (nur wenn neue Gesichter dazukamen) mit L2-normiertem Mittel der besten `face_track_faces`
  (Ausreißer mit Kosinus < 0,3 zum Mittel fliegen raus) und den Einzelbildern als `samples`. Antwort `none`/`ambiguous`
  entfernt eine vorläufige Gesichts-Identität vom Track (nicht eine per ReID übernommene).
- Selbsttest Standard: 47 `final` bei 72 Personen-Tracks (Rest ohne Gesicht ≥ 36 px), nichts aus der Warteschlange
  verworfen; Gesichtsmodell 13–18 ms je Job (mit Vollbild-Ausschnitten bis 45 ms wegen CPU-lastiger Vorverarbeitung).

### End-to-end (eigenes Compose-Projekt, leere DB)

Backend-Stand dieses Zweigs, Testkameras `cam1` (768 px) und `camhd` (2880 px), Setting `face_fullres` an:
`/api/system` → `models.face_fullres: "on"`, `perf.face` z. B. `{faces_per_min: 100, provisional_per_min: 12,
final_per_min: 13, fullres_per_min: 72, fullres_ms: 0.57, dropped: 12}`; 21 von 22 Personen-Ereignissen mit
Identität; MQTT `track`: alle 17 beendeten Personen-Tracks enden mit `identity.phase: "final"` (≤ 2,5 s nach dem
letzten Treffer), vorläufige Identitäten kommen per `update` mit `phase: "provisional"`. Für `cam1` meldet der Worker
„kein Gewinn“ (kein Leser). Der Vollbild-Leser endet beim Herunterfahren sauber. Die neuen Sample-Spalten bleiben bis
Paket C leer (das Backend des Kickoffs wertet `phase`/`samples` noch nicht aus).

## Paket C – Zuordnungslogik (Zweig `id/c-zuordnung`)

Umgesetzt in `backend/app/face_match.py` (`top_k_scores`, `decide`, `select_gallery`, `is_poor`,
`mean_embedding`), `backend/app/identity.py` (`_identify_face`, Galerie `_learn_face`/`_prune_face`,
`final_track_identity`) und `backend/app/events.py` (`Processor._final_guard`: endgültige Zuordnung gewinnt gegen
spätere Track-Meldungen). Tiere/Kennzeichen/Exemplare unverändert.

Entscheidungen (ohne Rückfrage):
- Profil/unscharf als feste Konstanten (|Gier| > 40°, |Nicken| > 30°, Laplace < 25), nicht als Settings.
- Neue Identität nur, wenn mindestens ein Bild als Beispiel taugt – sonst wäre sie nie wiedererkennbar (Fragment).
- Gelernt werden nur Einzelbilder, die zum Track-Mittel passen (≥ 0,35) und bei Treffer auch einzeln zur Person
  (Top-3 ≥ Schwelle − Abstand) – Schutz gegen vertauschte Tracks.
- `ambiguous` erzeugt eine Rückfrage `identity_match` (Bester ↔ Zweitbester), nie zwischen zwei Benannten.
- Galerie: Ausreißer (Ähnlichkeit zum Zentroid > 0,25 unter dem Median) zählen wie Profilbilder; k-Center gewichtet
  den Abstand mit der Qualität (0,5 + 0,5·q). `prune_samples` nutzt für Gesichter dieselbe Auswahl.

Simulation (synthetische 512-d-Embeddings, 10 Personen, 400 Tracks à 1–8 Gesichter, 30 % Nacht, Wegwerf-DB;
„alt“ = Code vor Paket C mit altem Worker (3× `single`), „kompatibel“ = neuer Code mit altem Worker,
„neu“ = neuer Code mit `provisional` + `final`):

| Kennzahl | alt | kompatibel | neu |
|---|---|---|---|
| Identitäten (Seed 7) | 72 | 26 | 11 |
| davon ohne Ereignis (verwaist) | 32 | 4 | 0 |
| Einzelbild-Identitäten | 79 % | 54 % | 9 % |
| Fragmente je Person (Mittel) | 4,0 | 2,2 | 1,1 |
| Ereignisse mit Identität | 100 % | 91 % | 97 % |
| Fehlzuordnungen | 0 | 0 | 0 |
| Rückfragen (knapp) | 0 | 14 | 1 |

Mit ähnlicheren Personen (Seed 11, gemeinsamer Anteil 0,6): Fragmente je Person 3,2 → 2,1 → **1,0**,
Einzelbild-Identitäten 77 % → 57 % → **0 %**, Fehlzuordnungen jeweils 0. Die Simulation prüft die Logik, nicht die
echte Bildqualität; Messung „nachher“ an echten Daten erst nach Integration mit Paket B.

## Integration A–C und Paket D – Nachtlauf (2026-10-25)

**Merge:** `id/a-mehrfachauswahl`, `id/b-gesichtsbilder`, `id/c-zuordnung` per `--no-ff` in
`feature/inventar-gesichter-mcp`. Konflikt `identity.prune_samples`: Signatur aus A (`limit`, `defer_remove`,
Rückgabe gelöschte IDs) und Vielfalts-Galerie aus C vereint – Gesichter laufen über `_prune_face(…, limit,
defer_remove)`, damit `bulk_merge` Dateien weiterhin erst nach dem Commit löscht. Worktrees und Zweige entfernt.

**Paket D** (`backend/app/nightly.py`, `routers/nightly.py`, `frontend/src/pages/admin/FaceSettings.tsx`,
`mcp/tools/nightly.py`; Details CONTRACT ID.H „Ergänzungen der Umsetzung (Paket D)“ und ID.J):
- Entscheidungen: Zweitbester = bester *konkurrierender* Kandidat (Fragmente derselben Person blockieren sich sonst
  gegenseitig – im ersten Test wurden deshalb 2 von 4 sicheren Fällen nur Rückfragen); Union-Find-Bündel, Quelle nur
  unbenannt **und ohne Lernsperre**, direkte Ähnlichkeit zum Ziel ≥ `nightly_review_min` gegen Ketten-Drift;
  Kuratieren/Aufräumen lassen gesperrte Identitäten und manuelle Bilder unangetastet, max. 20 % Negative je Lauf;
  Hinweis nur bei geplanten Läufen/Anwenden mit Aktionen.
- Oberfläche: Einstellungen → „Gesichtserkennung“ (face_*) und „Nachtlauf (Gesichter)“ mit „Probelauf jetzt“,
  Berichtsliste, Dialog mit Vorschaubildern Quelle → Ziel, Auswahl je Aktion, „Jetzt anwenden (N)“ + Bestätigung.
- **Tests** (Wegwerf-Postgres + Backend mit eingebundenem Code, synthetische 512-d-Gesichter, danach entfernt):
  43 Prüfungen – Probelauf ändert nichts (Identitäten, Bilder, Negative, Ereignisse, Rückfragen, Verlauf,
  Schwellen), geplante Merges/Rückfragen/Kuratierung/Schwelle/Aufräumen, „Nein“-Paar nie ins Ziel, Gesperrte/Benannte
  nie Quelle, signierte Vorschaubilder, Bearer → 403 bei apply, Anwenden einer Teilauswahl mit Neuprüfung
  („Quelle inzwischen benannt“), zweites Anwenden → 409, Verlauf `actor:"nightly"` + `nightly_report_id`,
  Zurücksetzen eines Nachtlauf-Merges, Modus apply direkt, Zeitplan genau ein Lauf je Tag. UI (Mock, headless
  Chromium, 1280 px und 390 px): Karte, Bericht, Bestätigung, Anwenden – keine JS-Fehler, kein seitliches Scrollen.
- **Probelauf an echten Daten** (Haupt-Stack, nur Vorschau): 40 Gesichts-Identitäten, 38 unbenannt → 2 sichere
  Zusammenführungen in benannte Personen (0,58 / 0,72, Abstand zum Zweitbesten ≥ 0,17), 5 Rückfragen (0,42–0,55),
  1 Galerie (1 Ausreißer), 2 Schwellen je Person, kein Aufräumen. Zeilenzahlen vorher/nachher identisch
  (Identitäten 75, Bilder 317, Negative 0, Verlauf 0, gesetzte Schwellen 0). `nightly_mode` bleibt `preview`.
- Hinweis: In dieser Installation steht `face_fullres` in den gespeicherten Settings noch auf `false` (vor Paket B
  gespeichert) – bei der Integration nach dem Lasttest eingeschaltet (siehe unten).

## Integration und Ende-zu-Ende im Haupt-Stack (2026-10-25 ≈ 10:30–11:00)

Ausgangsstand: höchste IDs `identities` 135, `identity_samples` 647, `cameras` 3, `rules` 2, `identity_merges` 0,
`nightly_reports` 1, `api_tokens` 16 (0 aktiv). Backup `~/vision-guard-backups/vg-20261025-103233-vor-integration07.sql.gz`
(chmod 600; keine Schemaänderung).

**Ende-zu-Ende** (Testkamera 11 = `rtsp://testcam:8554/cam1`, danach vollständig entfernt):
- MQTT `vg/internal/track/11` (4 min): 28 Starts, 123 Updates (45 mit `identity.phase:"provisional"`), 26 Enden – alle
  26 Personen-Tracks enden mit `phase:"final"`. Neue Beispielbilder tragen `quality`, `face_size`, `yaw`, `sharpness`,
  `day_night` (z. B. 4 Bilder, Qualität Mittel 0,36, Breite 77 px). `/api/system`: `perf.face` mit `faces_per_min`,
  `final_per_min`, `dropped`.
- Track-Konsolidierung wirkt: in ≈ 15 min Testkamera entstand nur **1** neue Gesichts-Identität; alle übrigen Tracks
  wurden wiedererkannt.
- **Befund:** Die Identitäten **84–91** stammen von einer früher gelöschten Testkamera 10 (`cam1`, 07:17–07:23 UTC,
  45 Ereignisse, vor dem Kickoff) – das Löschen einer Kamera entfernt Ereignisse/Identitäten nicht. Sie zählen in
  allen Messungen „echter“ Daten mit (8 von 41 Gesichts-Identitäten). Nicht gelöscht (nicht von diesem Lauf
  angelegt); Empfehlung: in der Oberfläche prüfen und per Mehrfachauswahl löschen. Die Testkamera hat ihnen 6 Bilder
  und neue `last_seen` gegeben – beides wieder zurückgesetzt (Bilder gelöscht, `last_seen` aus dem Backup).
- **Mehrfachauswahl über die Oberfläche** (headless Chromium gegen `localhost:8080`, synthetische Test-Identitäten
  137–141 über `/api/internal/identify phase:"final"`): Auswahl 2 → „Zusammenführen …“ → Ziel gewählt → 139 in 138
  aufgegangen (Verlauf `actor:"bulk"`); 140 per „Löschen …“ gelöscht; keine JS-Fehler, kein seitliches Scrollen.
  Screenshots `scratchpad/ident/i1-merge-echt.png`, `i2-loeschen-echt.png` (nicht im Repo).
- **Nachtlauf anwenden:** Test-Fragment 141 (Ähnlichkeit 0,69–0,74 zu 137) → Probelauf schlägt `merge 141 → 137`
  (0,78, Zweitbester 0,09) vor, daneben die echten Aktionen; `apply {idx:[0]}` wendet **nur** die Test-Aktion an
  (Bericht `trigger:"apply"`, `applied:true`, Verlauf `actor:"nightly"` + `nightly_report_id`, Hinweis erzeugt).
  Echte Identitäten unverändert.
- Aufräumen: Test-Identitäten (Mehrfach-Löschen), 107 Test-Ereignisse samt Medien, Kamera 11, Verlauf, Test-Berichte
  und Hinweis entfernt. Danach: Beispielbild-IDs identisch zum Backup, Zeilen wie Ausgangsstand (Ereignisse nur
  durch `cam_3` mehr). Keine API-Keys angelegt.

**Fehler behoben:** Zusammenführen-Dialog der Detailansicht nutzte fest 20 Bilder als Grenze → jetzt wie das Backend
`sample_limit` (benannte Gesichter `face_max_samples_named` = 40), `frontend/src/pages/Identities.tsx`.

**Lasttest 16 Kameras** (integriert, `--face-fullres`, `flock ~/vg-gpu.lock`, parallel zum Haupt-Stack): 60,8 Bilder/s,
7,0 ms je Bild, Batch Ø 5,9, GPU-Spitze 4608 MiB, 0 Fehler bei `identify` – wie Paket B (59–61 Bilder/s).
`dropped` 74 Gesichts-Jobs (Budget 15/s greift wie vorgesehen).

**`cam_3` mit/ohne volle Auflösung** (je 12 Proben über 2 min, Medianwerte):

| `face_fullres` | Bilder/s `cam_3` | Inferenz ms | CPU Worker | VRAM gesamt | Vollbild-Ausschnitte/min | Gesichter/min |
|---|---|---|---|---|---|---|
| aus | 9,7 | 30,2 | 64 % | 3004 MiB | 0 | 0 |
| an | 9,5 | 16,2 | 65 % | 3004 MiB | 37 | 29 |

Kein messbarer Einbruch (Leser 2880×1616 @ 2 fps, nur solange eine Person im Bild ist) → **`face_fullres` in dieser
Installation eingeschaltet** (Entscheidung 2 des Nutzers). Je Kamera abschaltbar.

**Probelauf an echten Daten** (Bericht 4, nur Vorschau; alle Zeilenzahlen vorher = nachher: Identitäten 76, Bilder 319,
Verlauf 0, Rückfragen 10): 41 Gesichts-Identitäten, 39 unbenannt → 2 sichere Zusammenführungen in benannte Personen
(0,71/Zweitbester 0,52 mit 20 Bildern/69 Ereignissen; 0,58/0,41 mit 19 Bildern/11 Ereignissen), 5 Rückfragen
(0,42–0,55), 1 Galerie (1 Ausreißer als Negativ), 2 Schwellen je Person (0,27 und 0,28), kein Aufräumen.

**Messung jetzt und „nachher“** (`scripts/face_stats.py`; „nachher“ = Aktionen von Bericht 4 per SQL in einer
Transaktion nachgebildet, danach ROLLBACK – ohne Galerie-Kürzung):

| Kennzahl | Kickoff | jetzt | nach Probelauf |
|---|---|---|---|
| Gesichts-Identitäten (benannt) | 35 (2) | 41 (2) | 39 (2) |
| Einzelbild / 1–2 Bilder | 54 % / 60 % | 54 % / 63 % | 56 % / 67 % |
| Bilder benannt #15 / #19 | – | 20 / 20 | 40 / 39 |
| Fragmente #15 ≥ 0,40 / ≥ 0,55 | 6 / 2 | 6 / 2 | 7 / 1 |
| Fragmente #19 ≥ 0,40 / ≥ 0,55 | 2 / 1 | 2 / 1 | 2 / 1 |
| unbenannte Paare ≥ 0,40 / ≥ 0,55 | 10 / 1 | 10 / 0 | 3 / 0 |
| max. Ähnlichkeit zwischen Benannten | 0,17 | 0,17 | 0,17 |
| Ähnlichkeit innen (Median) | – | 0,49 | 0,54 |

Lesart: Der Probelauf führt die zwei großen Fragmente zusammen und macht die Unbenannten deutlich trennschärfer
(Paare ≥ 0,40: 10 → 3); die restlichen Fragmente der Benannten liegen unter der Auto-Grenze und gehen als Rückfragen
an den Nutzer. Die Einzelbild-Quote sinkt erst durch neue Aufnahmen (Track-Konsolidierung) und das Aufräumen nach
`nightly_cleanup_days`. Die 8 Testkamera-Reste (84–91) verfälschen die Quoten leicht.

**Offen:** Vorschau (Bericht 4) unter Einstellungen → Nachtlauf freigeben; Testkamera-Reste 84–91 löschen; Messung
nach einigen Tagen Betrieb wiederholen (Schwellen `face_new_min_quality`/`face_sample_min_quality`); `nightly_mode`
erst nach Freigabe auf `apply` stellen.


## Prüfung (Daten-Robustheit, Bedienung/Erkennung) – Umsetzung der Funde (2026-10-25)

Vertrag: CONTRACT.md ID.K. Backup vor der Schemaänderung: `~/vision-guard-backups/vg-pruefung07-20261025-111031.sql.gz`
(chmod 600); Zeilen vorher/nachher identisch (identities 76, identity_samples 327, identity_merges 0, events 626,
nightly_reports 2; höchste Identity-ID 135). Neue Spalte `identity_merges.dst_before` (additiv).

Behoben (Stichprobe je Fund an Test-Identitäten `PR07-Test-*` im Backend-Container, danach entfernt, Zähler
`counter_face` zurückgesetzt):
- **Hoch – Nachtlauf in gesperrte Galerie:** Ziel mit Lernsperre → Rückfrage statt Merge (`nightly._plan_merges`),
  ältere Vorschau → „Ziel hat Lernsperre (nur per Rückfrage)“. Beleg: Plan {L, F} → 0 Merges, Rückfrage F→L.
  Echter Probelauf (nur gerechnet, nichts gespeichert): vorher 2 Merges 63→15/64→19, jetzt 0 Merges, 5 Rückfragen,
  2 Kuratieren, 0 Schwellen. Entscheidung: lieber fragen als Bilder ungeprüft in die Galerie der Nutzer-Personen;
  „Ja“ ist eine Nutzeraktion (CONTRACT D).
- **Hoch – Fragmente blockieren Zuordnung:** `identity._identify_face` + `face_match.pair_score`. Beleg: A (benannt)
  0,838 vs. B (Fragment) 0,833, Paar 0,852 → vorher `ambiguous`, jetzt `match` an A (+ Rückfrage).
- **Dateien bei Absturz:** Kopieren vor dem Commit, alte nach dem Commit löschen (`identity.stage_sample_files`, auch
  Einzel-Merge und Zurücksetzen); `sweep_orphan_dirs` in der Aufbewahrung. Beleg: simulierter Commit-Fehler → Pfade
  und Dateien unverändert, keine Kopien; Merge/Zurücksetzen → alle Dateien am neuen Ort.
- **Protokoll bei Abbruch:** `_checkpoint` nach jedem Bündel/jeder Aktion; Fehlerbericht behält den Zwischenstand.
  Beleg: Testbericht mit 2 Aktionen gespeichert inkl. `skipped_reason`.
- **TOCTOU:** `bulk_merge(guard=…)`, `bulk_delete(select_fn=…)` prüfen unter Zeilensperre. Beleg: Quelle benannt → 409.
- **Aufräumen alles-oder-nichts:** einzeln übersprungen, `except Exception`. Beleg: [gelöscht, benannt→übersprungen,
  unbekannt→übersprungen].
- **Zurücksetzen unvollständig:** `dst_before`. Beleg: Merge benannt→unbenannt, Zurücksetzen → Name `None`, Notizen
  „B-Notiz“, `first_seen`/`last_seen` wie vorher.
- **Schwelle unter global (B2) / ohne Rücksetzweg (D8):** nie bei Lernsperre; nie unter global ohne Fremdabstand.
  Beleg: Fremde weit (0,06) → 0,34 vorgeschlagen; Fragment nah (0,41) → kein Vorschlag.
- **Bericht wählt alles vor (B3):** nur `merge` vorgewählt, Zahlen deutsch, „erkennt großzügiger/strenger“.
- Niedrig, mit erledigt: Bilder erst nach Commit löschen (`_learn_face`, Einzel-Merge); Track-Cache nach Sammel-
  Merge/-Löschen (Beleg: Eintrag zeigt auf Ziel); Bildgrenze im Sammel-Dialog aus Settings; Schloss-Symbol mit Klasse
  `lock-badge` links vom Häkchen; Handy-Auswahlmodus 2 Spalten, Meldungen oberhalb der Leiste (Screenshot
  `scratchpad/ident/p7-mobil-auswahl.png`, kein Überlauf, keine JS-Fehler).

Teilweise / nicht behoben:
- **B4 Vollbild-Ausbeute:** nur Kennzahlen `perf.face.by_source` (jobs/faces je Quelle, live sichtbar). Versatzmessung
  und automatischer Rückfall je Kamera nicht umgesetzt: ein fairer Vergleich braucht beide Quellen gleichzeitig für
  dieselben Tracks (A/B-Messung) – erst mit den neuen Zählern über einige Stunden auswerten. Kein Lasttest (nur
  Zähler, keine Rechenlast; Worker-fps danach 9,8).
- **B8 Qualität je Quelle:** nur vermerkt – bei der Nachmessung `quality`/`face_size` getrennt nach `source_res`
  auswerten; `cam_3` (2880 px) liefert im Vollbild 1,5× größere Gesichter, `face_new_min_size` greift dort lockerer.
- D8 Rücksetz-Endpunkt für Schwellen nicht gebaut (durch Auslassen gesperrter Identitäten und die Untergrenze
  entschärft; alter Wert steht im Bericht).
