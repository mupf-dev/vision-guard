# Plan: Gesichter „Unbekannt“/„Bekannt“, Lernsperre, Zusammenführen mit Bildauswahl

## Ausgangslage im Code
- `backend/app/identity.py::identify()` (Z. ~150–235) hängt bei jedem Treffer `_add_sample()` an. Das gilt auch für neue Identitäten. Es bleiben höchstens 20 Samples, die mit der besten Qualität (`MAX_SAMPLES`).
- `merge()` (Z. ~240–264) verschiebt alle Samples und Events der Quelle aufs Ziel, ruft dann `prune_samples(dst)` auf und löscht die Quelle.
- „Bekannt“ heißt im Code schon „hat einen Namen“: `rules.py::identity_matches` mit `mode known/unknown` und `GET /identities?named=`. Das übernehme ich, es braucht kein eigenes Flag.
- `db.py::init_db()` ruft nur `create_all` auf. **Neue Spalten in bestehenden Tabellen legt das nicht an.** Deshalb braucht es eine kleine additive DDL-Routine.

## Datenmodell (`backend/app/models.py`)
- `Identity.learning_locked: bool = False`.
- `IdentitySample.manual: bool = False`. So werden manuell übernommene Samples beim Kürzen nicht verdrängt.
- `db.py::init_db()`: nach `create_all` diese Liste ausführen: `ALTER TABLE identities ADD COLUMN IF NOT EXISTS learning_locked boolean NOT NULL DEFAULT false` (analog für `identity_samples.manual`). Das ist eine idempotente Mini-Migration, bis Alembic kommt.

## Matching bei gesperrter Person
Erkennen ja, lernen nein:
- `identify()` läuft im Matching unverändert. Die Samples der gesperrten Person bleiben in `_matrix()`.
- Nur der Zweig `else` (Treffer) ändert sich: `if not ident.learning_locked: _add_sample(...)`. `last_seen` und `_link_event` laufen weiter wie bisher.
- Liegt die Ähnlichkeit unter der Schwelle, entsteht wie bisher eine neue „Person #N“. Sie landet unter „Unbekannt“ und ist genau der Kandidat für den Merge mit Bildauswahl.
- Den Cache nach `commit` (`invalidate(kind)`) überspringen, wenn kein Sample hinzukam. Das spart Neuaufbauten.
- `IdentifyOut` bekommt optional `locked: bool`. Der Worker braucht das nicht, es kann also auch entfallen.

## Merge mit Bildauswahl
**API-Form** (`schemas.py::MergeIn`):
```jsonc
POST /api/identities/{src}/merge
{ "into_id": 5, "sample_ids": [101, 104] | null }   // null = alle (bisheriges Verhalten)
```
Ablauf in `merge()`:
1. Liegen `sample_ids` vor, müssen alle zu `src` gehören, sonst 422.
2. Nur diese Samples werden verschoben und mit `manual=True` markiert. Die übrigen Samples der Quelle werden samt Dateien gelöscht.
3. Ist das Ziel `learning_locked` und `sample_ids` fehlt, wird keins übernommen. Die UI erzwingt die Auswahl, das Backend behandelt `null` bei gesperrtem Ziel wie `[]`.
4. **Events der Quelle** gehen immer komplett aufs Ziel (`update(Event)`, wie heute). Es ist dieselbe Person, die Historie bleibt erhalten. Name, Notizen und `first_seen`/`last_seen` werden wie bisher zusammengeführt.
5. `prune_samples()` kürzt zuerst Samples mit `manual=False` und dann nach Qualität. Sind mehr als 20 manuelle Samples ausgewählt, kommt 422 mit dem Hinweis „max. 20 Samples – erst alte löschen“.

Zusätzlich gibt es `POST /identities/{id}/samples/{sid}/move {into_id}` für einzelne falsch zugeordnete Bilder (optional, S).

## Vorschlag „Ist das vielleicht X?“
Das lohnt sich und ist günstig. Die Embeddings liegen schon im numpy-Cache.
- `GET /api/identities/{id}/suggestions?limit=3` liefert `[{identity_id, display_name, cover_url, score, likely}]`.
- Berechnung: Samples der Quelle × `_matrix(kind)`, je Ziel-Identität das Maximum (oder den Mittelwert der Top 3). Nur benannte Ziele, ohne die Quelle selbst. `likely = score ≥ threshold − 0.10`.
- Live berechnen statt beim Identifizieren speichern: Samples ändern sich ständig, und das Rechnen dauert bei wenigen hundert Samples unter 1 ms.
- UI: Banner in der Detailansicht einer unbekannten Person, zum Beispiel „Ähnlich wie **Anna** (0,41)“ mit dem Button „Zusammenführen …“. Die Merge-Liste nach Ähnlichkeit sortieren.
- Die Karte unter „Unbekannt“ bekommt optional ein Badge „ähnelt Anna“. Dafür gibt es `?with_suggestion=1` an der Liste, berechnet in einem Batch in `identities_json`.

## Gilt die Sperre für Tiere und Kennzeichen?
- **Tiere:** ja, gleicher Embedding-Pfad, also kostenlos dabei.
- **Kennzeichen:** Das Matching läuft über `plate_norm`, Samples sind nur Bilder. Eine Sperre verhindert dort nur neue Bilder. Vorschlag: technisch für alle Arten erlauben, den Schalter in der UI aber nur bei Gesichtern und Tieren zeigen.
- Ähnlichkeitsvorschläge gibt es nur für Gesichter und Tiere. Bei Kennzeichen wäre Levenshtein schon die Zuordnung.

## CONTRACT.md (Nachträge)
- Das Identity-JSON bekommt `learning_locked: bool`. Der Sample-JSON bekommt `manual: bool`.
- `PATCH /identities/{id}` akzeptiert `learning_locked`.
- `POST /identities/{id}/merge {into_id, sample_ids?: int[]|null}`. Dazu die Regeln für gesperrte Ziele und dass Events immer komplett wandern.
- Neu: `GET /identities/{id}/suggestions`, optional `POST /identities/{id}/samples/{sid}/move`.
- `/api/internal/identify`: Bei einer gesperrten Identität wird kein Sample gespeichert. Die Antwort ist unverändert.
- WS: `identity`-Nachrichten tragen das neue Feld. MQTT ändert sich nicht.

## Frontend
- `lib/types.ts`: `Identity.learning_locked`, `Sample.manual`, `Sample.quality` (fehlt heute im Typ), neuer Typ `IdentitySuggestion`.
- `lib/api.ts`: `updateIdentity` nimmt `learning_locked` an, `mergeIdentity(id, into, sampleIds?)`, `suggestions(id)`.
- `lib/mock.ts`: neue Felder, Merge mit `sample_ids`, Suggestions-Route.
- `pages/Identities.tsx`:
  - Bei „Personen“ ersetzt das Segment `Unbekannt | Bekannt` (Standard: Unbekannt, mit Zähler) das bisherige „Alle/Benannt/Unbenannt“. Bei Kennzeichen und Tieren bleibt der bisherige Filter.
  - In `IdCard` gibt es ein Schloss-Icon, wenn `learning_locked`.
  - In `IdentityDetail` kommt ein Schalter „Lernen gesperrt“ in den Kopfbereich, mit Hinweistext. Beim ersten Benennen schlägt ein Dialog vor: „Jetzt sperren?“.
  - Neuer Merge-Dialog in zwei Schritten:
    1. Ziel wählen. Die Vorschläge stehen oben, die Liste ist auf „Bekannt“ beschränkt, falls die Quelle unbekannt ist.
    2. Sample-Raster der Quelle mit Checkboxen. Vorausgewählt sind die Top 5 nach Qualität, bei gesperrtem Ziel keins. Ein Zähler zeigt „x von max. 20 im Ziel“.
  - Samples mit `manual` bekommen ein Badge.

## Schritte in Reihenfolge
1. CONTRACT.md-Nachträge (S)
2. Modell-Spalten und additive DDL in `db.py` (S)
3. Lernsperre in `identify()`, PATCH-Feld und Serializer (S)
4. `merge()` mit `sample_ids` und `manual`-bewusstem `prune_samples` (M)
5. Suggestions-Endpunkt, Batch-Variante für die Liste (M)
6. Typen, API und Mock im Frontend (S)
7. Identities-UI: Ansichten, Schalter, zweistufiger Merge, Vorschlags-Banner (M–L)
8. End-to-End mit testcam cam1:
   - Person benennen und sperren.
   - Prüfen, dass `sample_count` konstant bleibt, Events aber weiter zugeordnet werden.
   - Unbekannte Person mit Auswahl zusammenführen.
   - `corepack pnpm build` laufen lassen (M)

Gesamt etwa M+ (1,5–2 Tage).

## Risiken und offene Fragen
- **Schwelle bei Sperre:** Erkennt das System eine gesperrte Person schlecht, entstehen viele „Person #N“. Der Vorschlags-Banner fängt das ab. Optional ein Knopf „Alle ähnlichen zusammenführen“ (später).
- **Verwaiste Unbekannte:** Wer räumt nicht zusammengeführte Unbekannte auf? Vorschlag: Aufbewahrung über `retention_days_events` für unbenannte Identitäten ohne Events (eigener Punkt).
- **„Bekannt“ = benannt:** Nutzer könnten auch einen Namen ohne Sperre erwarten. Das passt zur bestehenden Regel-Logik und soll so bleiben.
- **Parallelität:** Merge während eines laufenden `identify` ist durch `_lock` abgesichert. Ein Track der Quelle kann nach dem Merge aber kurz eine neue Identität erzeugen. Das ist akzeptabel.
- **Event-Zuordnung bei Teilübernahme:** Werden nur einige Bilder übernommen, wandern trotzdem alle Events der Quelle. Falls die Quelle eine Mischung zweier Personen ist, braucht es eher „Sample verschieben“ und keinen Merge. Zu klären: Soll es eine Option „Events der Quelle bei ihr lassen“ geben?

Relevante Dateien:
- /home/miefda/vision-guard/backend/app/identity.py
- /home/miefda/vision-guard/backend/app/models.py
- /home/miefda/vision-guard/backend/app/db.py
- /home/miefda/vision-guard/backend/app/schemas.py
- /home/miefda/vision-guard/backend/app/serializers.py
- /home/miefda/vision-guard/backend/app/routers/identities.py
- /home/miefda/vision-guard/backend/app/rules.py
- /home/miefda/vision-guard/frontend/src/pages/Identities.tsx
- /home/miefda/vision-guard/frontend/src/lib/api.ts
- /home/miefda/vision-guard/frontend/src/lib/types.ts
- /home/miefda/vision-guard/frontend/src/lib/mock.ts
- /home/miefda/vision-guard/CONTRACT.md