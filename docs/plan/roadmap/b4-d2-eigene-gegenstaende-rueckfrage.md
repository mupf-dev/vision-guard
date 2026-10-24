# B4 Eigene Gegenstände wiedererkennen + D2 Lernen durch Rückfrage

Stand: 2026-10-24. Entwurf, setzt Phasen 1–6 voraus (Zielzustand laut `CONTRACT.md`, Abschnitt „Nachträge:
Gesichter-Lernsperre, API-Keys, Gegenstände (Inventar), MCP“). Beide Themen nutzen **ein gemeinsames Feedback-Modell**.

## 1. Zielverhalten

- **B4:** Ich lege „Annas Schlüssel“ an, wähle das Label `keys` und lade 3–5 Handyfotos hoch. Danach zeigt
  „Gegenstände“ statt „Schlüssel“ den Namen. `find_object("Annas Schlüssel")` antwortet: „aktuell sichtbar im Flur,
  vermutlich auf dem Tisch“. Fremde Schlüssel bleiben „Schlüssel“. Unbenannte Exemplare werden **nicht** automatisch
  angelegt, anders als bei Gesichtern.
- **D2:** Unsichere Fälle landen in „Bitte bestätigen“. Dazu gehören Gegenstände knapp unter oder über der Schwelle,
  neue Labels auf einer Kamera, mögliche Exemplar-Treffer und Vorschläge für Gesichter oder Tiere aus Phase 1. Jede
  Frage zeigt einen Bildausschnitt und lässt sich mit Ja oder Nein beantworten, in der Oberfläche oder per Push.
  Die Antworten bewirken Folgendes:
  - Die Schwelle je Kamera und Label wird angepasst.
  - Fehlerkennungen werden für diese Stelle unterdrückt.
  - Positive und negative Beispiele werden gesammelt.
  - Bei Gesichtern und Tieren wird zusammengeführt.
  - Es gibt höchstens N Fragen pro Tag, und keine Frage kommt doppelt.

## 2. Architektur

| Dienst | Neu / geändert |
|---|---|
| Worker | `vg_worker/item_embed.py`: berechnet Embeddings für Inventar-Crops mit der **vorhandenen** `PetModel`-Instanz (DINOv2 ViT-S/14, Queue mit niedriger Priorität; ist sie voll, wird verworfen). Beantwortet `vg/internal/embed`. `inventory.py`: Exemplar-Abgleich, Liste `uncertain`, Unterdrückungsflächen anwenden |
| Backend | `app/review.py` (Fragen erzeugen, begrenzen, Antworten anwenden, Schwellen nachführen), `routers/review.py`, `app/item_identity.py` (Abgleich `kind="item"`, Upload, Embedding-Anfrage an den Worker), Erweiterungen in `identity.py`, `inventory.py`, `inventory_names.py` (Suche über Exemplar-Namen) |
| Frontend | `pages/Review.tsx` (Route `bestaetigen`, Zähler in der Navigation), Tab „Meine Gegenstände“ in `Objects.tsx`, Komponente `ItemTeachDialog.tsx`, in `admin/CameraInventory.tsx` Schwellen- und Unterdrückungstabelle, in `admin/Settings.tsx` der Abschnitt „Rückfragen“ |
| MCP | `find_object` liefert `identity` mit; neu `list_open_questions` und `answer_question` (nur mit Scope `write`) |

**Exemplare als Identitäten mit `kind="item"`:** Samples, `manual`, Lernsperre, Merge, Embedding-Cache und
`prune_samples` aus Phase 1 werden wiederverwendet. Ein Abgleich findet nur innerhalb desselben `item_label` statt.
Gibt es keinen Treffer, wird `identity_id: null` zurückgegeben und keine neue Identität angelegt.

**Domänenlücke:** Ein Handyfoto zeigt den Gegenstand groß und scharf, ein Kamera-Crop ist dagegen oft nur 40–80 px
groß. Deshalb erzeugt der Worker beim Upload zusätzlich 2 verschlechterte Varianten (auf 64 bzw. 96 px verkleinert,
Unschärfe, JPEG mit q=40). Gespeichert wird der L2-normierte Mittelwert. Bestätigte Kamera-Crops aus D2 kommen als
Samples hinzu, was die Lücke mit der Zeit schließt.

## 3. Modelle und VRAM

| Komponente | Version | VRAM |
|---|---|---|
| DINOv2 ViT-S/14 (`vit_small_patch14_dinov2.lvd142m`, timm, fp16) | wie `VG_PET_MODEL` (ca. 22 M Parameter, **verifiziert** über das Modell) | **0 GB zusätzlich**, wenn die Tiererkennung schon geladen ist. Sonst ca. 0,1–0,2 GB (**vermutet**: Gewichte ca. 45 MB plus Aktivierungen bei 224², Batch 1–8) |
| D2 | keine neuen Modelle | 0 |

Es kommen keine neuen Pakete hinzu. HEIC wird nicht serverseitig dekodiert: Der Browser verkleinert das Bild per
Canvas und lädt es als JPEG mit höchstens 1600 px hoch.

## 4. CONTRACT.md-Ergänzungen (additiv)

**DB (`ADDITIVE_COLUMNS`):**

| Tabelle | Spalte | Typ / Default |
|---|---|---|
| `identities` | `item_label` | varchar(40) NULL |
| `identity_samples` | `negative` | boolean NOT NULL DEFAULT false |
| `identity_samples` | `source` | varchar(16) NOT NULL DEFAULT 'auto' (`auto\|merge\|upload\|review`) |
| `inventory_items` | `identity_id` | integer NULL |
| `inventory_items` | `identity_score` | double precision NULL |
| `cameras` | `inventory_label_min_scores` | jsonb NOT NULL DEFAULT '{}' |
| `cameras` | `inventory_suppress` | jsonb NOT NULL DEFAULT '[]' |

Neue Tabellen: `review_requests` (`ReviewRequest`) und `inventory_examples` (`label, camera_id, crop_path,
positive, embedding real[] NULL, review_id, created_at`). `inventory_examples` ist die Beispielsammlung für einen
späteren Few-shot-Ansatz oder visuelle YOLOE-Prompts.

**REST:**
```jsonc
// Identity: kind += "item"; zusätzlich "item_label": "keys" | null; display_name unbenannt: "Gegenstand #N"
POST /identities {kind:"item", item_label, name}          // nur kind item, sonst 422; item_label muss im Vokabular sein
POST /identities/{id}/samples  multipart file (jpeg/png/webp ≤10 MB), bbox?="x1,y1,x2,y2" (normiert)
     → 201 Sample (+negative, source:"upload", manual:true); 503 „Analyse-Dienst nicht erreichbar“, wenn der Worker nicht innerhalb von 15 s antwortet
POST /identities/{id}/samples/from-inventory {inventory_item_id} → 201 Sample   // aktuellen Crop übernehmen
// InventoryItem zusätzlich: "identity": {id, display_name, name, kind:"item"} | null, "identity_score": 0.78 | null
// Suche: match.via += "identity"; answer nennt den Namen
// Camera zusätzlich: inventory_label_min_scores {label: 0.05..0.95}, inventory_suppress [{label, bbox, review_id|null}]

// ReviewRequest
{ "id": 41, "kind": "inventory_label|inventory_identity|identity_match",
  "status": "open|yes|no|skipped|expired", "question": "Ist das eine Fernbedienung?",
  "camera_id": 3, "camera_name": "Wohnzimmer", "label": "remote", "name_de": "Fernbedienung",
  "inventory_item_id": 17 | null, "identity_id": 5 | null, "source_identity_id": 12 | null,
  "score": 0.31, "threshold": 0.35, "bbox": [..] | null, "crop_url": "/media/…", "snapshot_url": "/media/…" | null,
  "created_at": "...", "answered_at": null, "answer_source": "ui|push|mcp|api" | null, "expires_at": "...",
  "correct_label": null }
GET  /review?status=open&kind=&camera_id=&limit=50&offset=0 → {items,total,open_count,asked_today,max_per_day}
GET  /review/{id};  POST /review/{id}/answer {answer:"yes|no|skip", correct_label?} → ReviewRequest  (Scope write)
GET  /review/a/{id}?a=yes|no&exp=&sig= → kleine Bestätigungsseite mit Knopf (verhindert Link-Prefetch),
POST /review/a/{id}?a=…&exp=&sig= → beantwortet ohne Login   // sig = HMAC wie media_sign, msg "review:{id}:{a}:{exp}"
GET  /review/thresholds?camera_id= → [{camera_id,label,base,current,auto,yes,no}]
```
- `GET /identities/{id}/suggestions` liefert keine Ziele mehr, die in einer Frage mit „Nein“ beantwortet wurden.
- **Settings:** `item_match_threshold` 0.70 (**vermutet**, wird im Test kalibriert), `review_enabled` true,
  `review_max_per_day` 10, `review_max_open` 20, `review_expire_h` 48, `review_band` 0.10,
  `review_auto_tune` true, `review_push` false.
- **WS:** `{"type":"review","action":"new|answered|expired","review":ReviewRequest}`; `inventory` wie bisher, jetzt mit `identity`.

**Intern:**
- `/api/internal/cameras`:
  - `inventory_vocab[]` bekommt zusätzlich `instances: int`.
  - `min_score` ist bereits mit `inventory_label_min_scores` aufgelöst.
  - Die Kamera bekommt zusätzlich `inventory_suppress` und `inventory_review: bool`.
- `POST /api/internal/identify` mit `kind:"item"` und den Feldern `label`, `item_id` (statt `track_id`):
  - Der Abgleich läuft nur gegen dieses Label.
  - Liegt der beste Treffer auf einem negativen Sample über dem besten positiven, ist das Ergebnis kein Treffer.
  - Es wird nie eine neue Identität angelegt.
  - Lernt automatisch nur, wenn der Score ≥ Schwelle + 0,10 ist und keine Lernsperre besteht.
- **MQTT:**
  - Embedding-Anfrage: Backend → `vg/internal/embed` mit `{request_id, kind:"item", path, bbox|null, augment:true}`.
  - Antwort: Worker → `vg/internal/embed_result` mit `{request_id, ok, embedding:[384]|null, error}`.
- `vg/internal/inventory/{cid}`:
  - Items bekommen zusätzlich `identity`, `identity_score`.
  - Die Nachricht bekommt zusätzlich `uncertain: [{label, score, bbox, crop_path}]`. Das sind höchstens 3 je
    Durchlauf mit Score in [min − band, min). Je Kamera und Label wird höchstens 1 Crop alle 10 min geschrieben.
  - Der Abgleich der Exemplare läuft bei einem neuen Gegenstand, bei `moved`, sonst höchstens alle 10 min.
  - Er läuft nur für Labels mit `instances > 0`.
- Treffer innerhalb einer Unterdrückungsfläche (gleiches Label, IoU ≥ 0,5) verwirft der Worker.
- Extern `{prefix}/review` (nicht retained): `{review, answer_urls:{yes,no}}` mit signierten URLs.

**MCP:**
- `find_object` liefert in `items[]` zusätzlich `identity_name`.
- `list_open_questions(limit=10)`
- `answer_question(id, answer)`: Ohne Scope `write` gibt es `isError` „API-Key ohne Schreibrecht“.

## 5. Logik von D2

**Fragen entstehen aus:**
- `uncertain`
- `appear` eines Labels, das auf dieser Kamera noch nie bestätigt wurde
- `present` mit Score < min + band, einmal je Gegenstand
- Exemplar-Treffern mit Score in [Schwelle − 0,08, Schwelle + 0,05)
- unbenannten `face`/`pet` mit einem Vorschlag `likely` und ≥ 2 Events. Dafür läuft alle 10 min ein Job in `review.py`.

**Dedup-Schlüssel:**
- `lbl:{cam}:{label}:{Zelle 10×10 des Mittelpunkts}`
- `inst:{inventory_item_id}:{identity}`
- `idm:{src}:{dst}`

Ein Schlüssel wird nicht erneut gefragt, wenn er offen ist, in den letzten 30 Tagen mit „Ja“ beantwortet wurde
oder jemals mit „Nein“ beantwortet wurde.

**Begrenzung:** Es gibt höchstens `review_max_per_day` neue Fragen und `review_max_open` offene Fragen. Die
Priorität richtet sich nach dem Abstand zur Schwelle (je näher, desto höher). Nach `review_expire_h` verfallen
offene Fragen.

**Wirkung der Antworten:**

| Art | Ja | Nein |
|---|---|---|
| `inventory_label` | Positivbeispiel; Gegenstand bleibt bzw. wird übernommen | Negativbeispiel; Eintrag in `inventory_suppress`; Gegenstand löschen (wie `DELETE /inventory/{id}`). Mit `correct_label` stattdessen umetikettieren |
| `inventory_identity` | Crop als Sample (`source:"review"`, `manual:true`) | Sample mit `negative:true` |
| `identity_match` | Klassischer Merge Quelle → Ziel | Paar gesperrt |

**Schwelle nachführen** (`review_auto_tune`, ab 3 Antworten je Kamera und Label): Ziel ist die Mitte zwischen
dem höchsten Score mit „Nein“ und dem niedrigsten mit „Ja“. Je Antwort wird höchstens um 0,05 verschoben, insgesamt
in den Grenzen [Basis − 0,15, Basis + 0,20]. Das Ergebnis wird in `inventory_label_min_scores` geschrieben, was
`vg/internal/config` auslöst. Ein manueller Wert in der Kamera-Verwaltung hat Vorrang (`auto:false`).

**Push:** Nutzt den Push-Kanal aus A1 (ntfy/Pushover) mit Bild (signierte Crop-URL) und den Aktionen Ja/Nein
(signierte Antwort-URL, absolut über `public_base_url`). Ohne A1 wird nur `{prefix}/review` per MQTT gesendet.

## 6. UI-Skizze

- **„Bitte bestätigen“:**
  - Karten mit Crop (groß) und Übersichtsbild (klein), Frage, Kamera und Zeit.
  - Knöpfe **Ja** / **Nein** / Überspringen, Tasten J/N/S.
  - Bei Nein: Auswahl „Was ist es stattdessen?“ (Vokabular, optional).
  - Kopfzeile: „3 offen · heute 4/10 gefragt“.
- **„Gegenstände“ → Tab „Meine Gegenstände“:**
  - Kachel je Exemplar mit Fotos, Label, „zuletzt gesehen …“ und Schalter „Lernen gesperrt“.
  - „+ Neuer Gegenstand“ öffnet einen Dialog: Label, Name, Fotos über `<input accept="image/*"
    capture="environment" multiple>`, Rechteck je Foto aufziehen, alternativ „Von Kamera übernehmen“.
- **Liste „Gegenstände“:** Badge mit dem Namen des Exemplars.
- **Kamera-Inventar (Admin):**
  - Tabelle mit Label, Basis, aktueller Schwelle, Ja/Nein und Zurücksetzen.
  - Liste der Unterdrückungsflächen mit Vorschau und Entfernen.

## 7. Schritte

| # | Schritt | Aufwand |
|---|---|---|
| 0 | Machbarkeitstest `worker/vg_worker/itemtest.py` (siehe 8) | S |
| 1 | DB-Spalten, `kind:"item"`, Upload, `vg/internal/embed`, Worker `item_embed.py` | M |
| 2 | Exemplar-Abgleich im Worker und Backend, InventoryItem `identity`, Suche, MCP `find_object` | M |
| 3 | UI „Meine Gegenstände“, Teach-Dialog | M |
| 4 | `review_requests`, `review.py`: Erzeugung, Dedup, Limits, Antworten, WS | M |
| 5 | Worker `uncertain`, Unterdrückung; Schwellen nachführen | M |
| 6 | UI „Bitte bestätigen“, Admin-Tabellen, Settings | M |
| 7 | Push und signierte Antwort-URLs, MQTT `{prefix}/review`, MCP-Tools | S (+A1) |

Gesamt: ca. 6–8 Personentage.

## 8. Machbarkeitstest (vor Schritt 1)

- **Aufbau:** `itemtest.py <ordner>` mit Handyfotos von 2 eigenen und 2 fremden Exemplaren je Label (Schlüssel,
  Brille, Tasse, Rucksack) und Crops aus `inventory/` von `cam_3`.
- **Ausgabe:** Kosinus-Matrix, ROC-AUC eigene gegen fremde, jeweils mit und ohne Augmentierung, aufgeteilt nach
  Crop-Größe (< 60, 60–120, > 120 px).
- **Go:**
  - AUC ≥ 0,90 für Crops ≥ 60 px.
  - Ein Embedding braucht ≤ 15 ms (fp16).
  - Die VRAM-Spitze steigt um ≤ 0,2 GB.
- **Sonst:** B4 nur für große Gegenstände anbieten. Die UI warnt dann: „Gegenstand erscheint im Bild sehr klein“.

## 9. Abnahme (e2e)

1. Exemplar „Testtasse“ mit 3 Fotos anlegen: `sample_count` = 3. Tasse in `cam_3` stellen: `GET
   /api/inventory/search?q=testtasse` liefert `identity.name == "Testtasse"` und `visible:true`. Eine fremde Tasse
   bleibt ohne `identity`.
2. Den Worker stoppen und dann hochladen: Antwort 503, es entsteht keine halbe Identität.
3. `mosquitto_pub` einer Inventar-Nachricht mit `uncertain`: Eine Frage erscheint live (WS). Erneutes Senden
   erzeugt keine zweite Frage.
4. Mit Nein antworten:
   - `inventory_suppress` enthält die Box.
   - Der Worker meldet das Label dort nicht mehr (mitlesen per `mosquitto_sub`).
   - Nach 3 Antworten hat sich `/review/thresholds` verschoben (höchstens 0,05 je Antwort).
5. 15 unsichere Fälle einspielen: Es entstehen höchstens 10 Fragen pro Tag.
6. Signierte Antwort-URL ohne Login: GET zeigt eine Seite und antwortet noch nicht, POST beantwortet die Frage.
   Eine manipulierte `sig` ergibt 403.
7. Gesichter: Eine unbenannte Person mit `likely`-Vorschlag erzeugt eine Frage. Ja ergibt einen Merge, Nein sorgt
   dafür, dass der Vorschlag nicht mehr erscheint.
8. MCP mit Key `read`: `answer_question` ergibt `isError`. `corepack pnpm build` läuft fehlerfrei.

## 10. Risiken

- **Kleine Gegenstände:** Schlüssel in 3 m Entfernung sind oft nur 30–50 px groß. Ob sich eigene und fremde
  Exemplare dann unterscheiden lassen, ist fraglich. Der Test in 8 entscheidet.
- **Viele gleiche Exemplare:** Bei vielen ähnlichen Exemplaren (gleiche Tassen) gibt es Verwechslungen. Gegenmittel:
  Negativ-Samples und ein greedy-Abgleich je Kamera und Durchlauf mit höchstens einem Exemplar je Bild.
- **Ermüdung durch Rückfragen:** Wird zu viel gefragt, sinkt die Antwortbereitschaft. Gegenmittel: niedrige
  Standardwerte und automatischer Verfall offener Fragen.
- **Schwellen können wegdriften:** Begrenzung in [Basis − 0,15, Basis + 0,20] und Zurücksetzen in der UI.
- **Unterdrückungsflächen können echte Gegenstände ausblenden** (z. B. eine Fernbedienung, die später genau dort
  liegt). Daher gilt die Unterdrückung nur für dasselbe Label bei IoU ≥ 0,5 und lässt sich in der UI entfernen.
- **Signierte Antwort-URLs:** Wer sie hat, kann antworten. Sie verfallen deshalb nach `review_expire_h`.
- **Speicher:** Crops für Fragen landen unter `inventory/` und unterliegen `retention_days_inventory`. Crops, auf
  die offene Fragen oder Beispiele verweisen, bleiben erhalten.

## 11. Abhängigkeiten

- **Phase 1:** Samples, `manual`, Lernsperre, Merge, Vorschläge. Pflicht.
- **Phase 2:** Scope `write`, Signaturen für Antwort-URLs, `public_base_url`. Pflicht.
- **Phasen 4/5:** Crops, Inventar-Nachricht, Gegenstandsgedächtnis, Suche. Pflicht.
- **Phase 6:** MCP-Erweiterung. Optional.
- **A1:** Push-Kanal für D2. Ohne A1 gibt es nur UI und MQTT. Umgekehrt nutzt A1 die Exemplare, z. B. „Annas
  Schlüssel fehlt“.
- **B2:** zeigt Exemplar-Namen.
- **C3:** profitiert von D2 bei Tier-Vorschlägen.
- **A2 (VLM):** könnte später Fragen vorab beantworten. Das ist nicht Teil dieses Entwurfs.
