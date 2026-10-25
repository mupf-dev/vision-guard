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
| B Worker-Gesichter (Qualität, Track-Sammlung, volle Auflösung) | offen |
| C Zuordnungslogik (Backend) | umgesetzt (Zweig `id/c-zuordnung`), Simulation siehe unten |
| D Nachtlauf | offen |
| Integration, Messung „nachher“ | offen |

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
