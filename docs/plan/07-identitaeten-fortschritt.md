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
| A Mehrfachauswahl (Backend + UI) | erledigt (Zweig `id/a-mehrfachauswahl`) |
| B Worker-Gesichter (Qualität, Track-Sammlung, volle Auflösung) | offen |
| C Zuordnungslogik (Backend) | offen |
| D Nachtlauf | offen |
| Integration, Messung „nachher“ | offen |

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
