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
| C Zuordnungslogik (Backend) | offen |
| D Nachtlauf | offen |
| Integration, Messung „nachher“ | offen |
