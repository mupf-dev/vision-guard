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
| A Mehrfachauswahl (Backend + UI) | offen |
| B Worker-Gesichter (Qualität, Track-Sammlung, volle Auflösung) | erledigt (Zweig `id/b-gesichtsbilder`, siehe unten) |
| C Zuordnungslogik (Backend) | offen |
| D Nachtlauf | offen |
| Integration, Messung „nachher“ | offen |

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
