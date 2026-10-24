# B2 „Was hat sich verändert?“ und B3 Tagesbericht – Umsetzungsentwurf

Stand: 2026-10-24. Setzt Phasen 1–6 voraus (Zielzustand: `CONTRACT.md`, Abschnitt „Nachträge: Gesichter-Lernsperre,
API-Keys, Gegenstände (Inventar), MCP“, und `docs/plan/00-gesamtplan.md`). Beide Themen laufen **nur im Backend
bzw. MCP-Dienst**, der Worker bleibt unverändert. Keine Videoaufzeichnung, keine Bildanalyse.

## B2 „Was hat sich verändert?“

### Zielverhalten
„Was ist seit heute Morgen anders?“ → Liste **neu aufgetaucht**, **verschwunden**, **verschoben**, **Stand unklar**
(Kamera `stale`) je Raum/Kamera, mit Vorher-/Nachher-Ausschnitt. Beispiele: Paket im Flur neu, Schlüssel vom
Brett weg, Fernbedienung vom Tisch aufs Sofa.

### Architektur
- Neues Modul `backend/app/inventory_diff.py`:
  - `state_at(session, t, filters) -> dict[item_id, Snapshot]`: je Gegenstand der letzte `inventory_history`-Eintrag
    mit `ts ≤ t` (eine SQL-Abfrage mit `DISTINCT ON (item_id) … ORDER BY item_id, ts DESC`). Gegenstände mit
    `first_seen > t` existieren zu `t` nicht.
  - `compute_changes(since, until, camera_id?, room?, labels?)`: Vergleich beider Zustände.
    `appeared`: zu `until` `present`, zu `since` nicht vorhanden/`missing`. `disappeared`: umgekehrt.
    `moved`: beide `present`, Mittelpunkt > 0,10 verschoben **oder** Zonen geändert. `uncertain`: zu `until`
    `stale`. `transient` (optional, Schalter): im Zeitraum aufgetaucht **und** wieder verschwunden.
  - **Paarung gegen Doppelzählung:** Nach Worker-Neustart kann derselbe Gegenstand als neue Zeile erscheinen
    (Session-Abgleich IoU < 0,3). Ein `disappeared` und ein `appeared` mit gleichem `label` und gleicher Kamera
    werden zu einem `moved` zusammengefasst (nächster Mittelpunkt zuerst).
  - Rauschfilter: Gegenstände, die im Zeitraum kürzer als `inventory_change_min_s` (Default 120 s) `present`
    waren, zählen nicht als `appeared`.
  - Deutscher Satz `summary` (z. B. „Seit 07:00: 2 neu (Paket im Flur, Tasche im Flur), 1 weg (Schlüssel),
    1 verschoben (Fernbedienung: Tisch → Sofa).“).
- Router: Endpunkt in `backend/app/routers/inventory.py` (vor `/{id}` registrieren!).
- `since`-Parser in `inventory_diff.py`: ISO-8601, relative Angaben `3h`, `90m`, `2d`, Schlüsselwörter `today`
  (lokal 00:00), `morning` (lokal 06:00), `yesterday` (gestern 00:00); Zeitzone `config.tz`.
  „Seit ich weg bin“ = Ausbaustufe (braucht Anwesenheit/Identität, siehe A1/C2).

### CONTRACT.md-Ergänzungen (additiv)
```jsonc
// GET /api/inventory/changes?since=&until=&camera_id=&room=&label=&include_transient=0&signed=&limit=100
// → InventoryChanges
{ "since": "...", "until": "...", "summary": "Seit 07:00: …",
  "counts": { "appeared": 2, "disappeared": 1, "moved": 1, "uncertain": 0, "transient": 0 },
  "changes": [ { "type": "appeared|disappeared|moved|uncertain|transient", "at": "...",   // Zeitpunkt der Änderung
                 "item": InventoryItem,                                                  // aktueller Stand
                 "before": { "ts", "state", "camera_id", "bbox", "zones", "zone_names", "crop_url" } | null,
                 "after":  { …wie before… } | null,
                 "text": "Fernbedienung: vermutlich vom Tisch zum Sofa verschoben (Wohnzimmer)" } ] }
```
- `since` Pflicht, `until` Default jetzt; `since` älter als `retention_days_inventory` → 422 „Zeitraum außerhalb der
  Aufbewahrung“. Sortierung: `disappeared`, `appeared`, `moved`, `uncertain`, je `at` absteigend.
- Settings: `inventory_change_min_s: 120` (0..3600).
- DB: keine neuen Spalten. Neuer Index `ix_inventory_history_camera_ts (camera_id, ts)` – `db.py::init_db` erhält
  analog zu `ADDITIVE_COLUMNS` eine Liste `ADDITIVE_INDEXES` (`CREATE INDEX IF NOT EXISTS`).
- MCP-Tool `what_changed`: `since: str = "today"`, `until: str?`, `camera_id: int?`, `camera_name: str?`,
  `room: str?`, `limit: int = 30` → `structuredContent` = InventoryChanges (Items kompakt wie `find_object`, URLs
  signiert), Text = `summary` + eine Zeile je Änderung.

### UI
Seite „Gegenstände“ (`frontend/src/pages/Objects.tsx`) bekommt einen Reiter **„Veränderungen“**: Zeitraum-Chips
(Seit heute Morgen · Letzte 3 h · Seit gestern · Eigener Zeitraum), Filter Raum/Kamera, drei Spalten
Neu / Weg / Verschoben mit Karten (Vorher → Nachher-Ausschnitt, Uhrzeit, Raum). Hinweisleiste bei `uncertain`.

### Schritte
1. `inventory_diff.py` + Parser + Paarung (M) · 2. Endpunkt, Setting, Index, CONTRACT, `types.ts`/`api.ts`/`mock.ts` (S)
3. MCP `what_changed` + Selbsttest-Erweiterung (S) · 4. UI-Reiter (M).

### Abnahme (e2e)
- Testkamera mit Inventar: Gegenstand in den Bildbereich legen (bzw. Testvideo mit Objekt), nach Bestätigung
  `GET /api/inventory/changes?since=<vorher>` → genau ein `appeared` mit `crop_url`.
- Gegenstand entfernen → nach `inventory_miss_m` Durchläufen `disappeared`; Worker neu starten, gleicher Gegenstand
  liegt noch → **kein** `appeared`/`disappeared`-Paar (Paarung/Session-Abgleich).
- Kurz (< 120 s) gezeigter Gegenstand erscheint nicht; mit `include_transient=1` als `transient`.
- MCP-Selbsttest ruft `what_changed(since="1h")` mit Read-Key auf, Ergebnis ohne `isError`, URLs signiert.
- Bearer ohne Key → 401; `since` vor Aufbewahrung → 422.

## B3 Tagesbericht

### Zielverhalten
Täglich zur eingestellten Zeit (Default 21:00, lokal) oder auf Anfrage entsteht ein kurzer deutscher Bericht:
„Postbote 10:42, 2 Pakete im Flur. Anna kam 17:10. Hund 4× im Garten. Schlüssel liegt seit 18:05 auf der Kommode.
Bad-Fenster 3 h offen.“ Versand per MQTT, Push, Mail, Webhook; Archiv in der Oberfläche.

### Architektur (Backend, Paket `backend/app/report/`)
- `facts.py` – `collect_facts(start, end) -> dict`, rein deterministisch aus der DB:
  Events je Label × Kamera (Anzahl, erste/letzte Zeit); benannte Identitäten mit erstem/letztem Auftreten;
  unbekannte Personen (Anzahl, neue Identitäten), Kennzeichen; Tiere je Zone; ausgelöste Regeln (Name, Zeit,
  Snapshot); Gegenstände = `compute_changes(start, end)` aus B2; virtuelle Sensoren (A3, falls vorhanden:
  Zustandsdauern); Systemhinweise (Kamera aktuell offline). Begrenzung auf die Top-N je Abschnitt.
- `render.py` – zwei Wege, Ergebnis jeweils `text` + einfaches `html`:
  - `template` (Default, ohne KI): feste deutsche Satzbausteine je Abschnitt.
  - `llm`: Fakten-JSON (ohne Bilder, ohne URLs) an ein Sprachmodell mit Anweisung „nur diese Fakten, max. 120
    Wörter, Deutsch“. **Plausibilitätsprüfung:** alle Uhrzeiten/Zahlen/Namen im Text müssen in den Fakten
    vorkommen, sonst Rückfall auf `template` (`generator: "template (Fallback)"`). Timeout 60 s.
  - Anbieter: `openai_compat` (lokal z. B. Ollama/llama.cpp über Base-URL, auch für Tests mit der LLM-Bridge)
    oder `anthropic` (Messages-API per `httpx`, kein neues Paket). Schlüssel nur per Env
    `VG_REPORT_LLM_API_KEY`, nie in Settings/API/Logs.
- `deliver.py` – Kanäle: MQTT extern `{prefix}/report` (retain), Webhook (wie Regel-Aktion), Push (Kanal aus A1;
  bis dahin ntfy-URL als Webhook), Mail per `smtplib` (Standardbibliothek, im Thread) mit Env
  `VG_SMTP_HOST/PORT/USER/PASSWORD/FROM`, `VG_SMTP_STARTTLS`. Fehler je Kanal protokollieren, nicht abbrechen.
- `scheduler.py` – asyncio-Schleife in `main.py::lifespan` (wie `_retention_loop`), prüft minütlich; idempotent über
  eindeutigen Schlüssel (`kind`, `period_date`); verpasster Lauf wird nach Backend-Start bis 6 h später nachgeholt.
- Router `backend/app/routers/reports.py`.

### Modelle / VRAM
| Weg | Bibliothek/Modell | VRAM | Status |
|---|---|---|---|
| template | – | 0 | verifiziert (keine KI) |
| lokal CPU | Ollama, `qwen2.5:3b-instruct` Q4 (~2 GB RAM), ~20–60 s je Bericht | 0 | vermutet, Test nötig |
| lokal GPU | dasselbe Modell auf der A1000 | ~2,3 GB | vermutet – **nicht empfohlen**: kollidiert mit Worker + YOLOE |
| Claude | Anthropic Messages-API, kleines Modell (z. B. Haiku-Klasse; Modell-ID vor Umsetzung prüfen) | 0 | verifiziert machbar; Daten verlassen das Haus |

Empfehlung: Default `template`, optional lokal auf CPU; Claude nur nach ausdrücklicher Freigabe (Settings-Hinweis
„Namen und Uhrzeiten werden an Anthropic übertragen“).

### CONTRACT.md-Ergänzungen (additiv)
- Neue Tabelle `reports` (`create_all`): `id, kind ("daily"|"manual"), period_start, period_end, created_at,
  facts jsonb, text, html, generator, delivery jsonb` (je Kanal `{ok, error, ts}`), Unique (`kind`, `period_start`)
  für `daily`. Aufbewahrung = `retention_days_events`.
```jsonc
// Report
{ "id": 4, "kind": "daily", "period_start": "...", "period_end": "...", "created_at": "...",
  "text": "…", "html": "…", "generator": "template|llm:<modell>|template (Fallback)",
  "facts": { "events": [...], "identities": [...], "inventory": InventoryChanges, "sensors": [...], "rules": [...] },
  "delivery": { "mqtt": {"ok": true, "ts": "..."}, "mail": {"ok": false, "error": "SMTP nicht konfiguriert"} } }
```
- `GET /reports?limit=30&offset=0` → `{items: Report ohne facts/html, total}`; `GET /reports/{id}` (inkl. Fakten,
  `signed=1` signiert Bild-URLs in den Fakten); `DELETE /reports/{id}`.
- `GET /reports/preview?since=&until=` (Scope `read`, Default: heute 00:00 bis jetzt) → Report-Form, ohne Speichern.
- `POST /reports/generate {since?, until?, deliver: false}` (Scope `write`) → Report (speichert, optional Versand).
- Settings: `report_enabled: false`, `report_time: "21:00"`, `report_days: [0..6]`,
  `report_generator: "template"|"openai_compat"|"anthropic"`, `report_llm_base_url: null`,
  `report_llm_model: null`, `report_channels: {mqtt: true, webhook_url: null, push: false, mail_to: []}`.
- MQTT extern `{prefix}/report` (retain): `{date, text, report_id, counts}`.
- WS: `{"type":"report","report":Report ohne facts}` nach Erzeugung.
- MCP-Tool `get_daily_report(date: str?)`: vorhandener Bericht des Tages, sonst Vorschau über `/reports/preview`
  (nur lesen). Rückgabe Text + `structuredContent` (Report).

### UI
- Neue Seite **„Berichte“** (Navigation): Liste nach Datum, Ansicht mit Text oben, darunter aufklappbare
  Abschnitte (Personen, Tiere, Gegenstände mit Ausschnitten aus B2, Regeln), Knopf „Bericht für heute erstellen“.
- Admin › Einstellungen › **Tagesbericht**: Uhrzeit, Wochentage, Generator inkl. Datenschutzhinweis, Kanäle,
  „Testbericht senden“, Zustellstatus des letzten Laufs.

### Schritte
1. `facts.py` + `template`-Renderer (M) · 2. Tabelle, Router, Settings, CONTRACT, Frontend-Typen/Mock (M)
3. Scheduler + MQTT/Webhook (S) · 4. Mail (S) · 5. LLM-Renderer + Plausibilitätsprüfung (M) · 6. UI Seite +
Einstellungen (M) · 7. MCP `get_daily_report` (S). Gesamt: M–L.

### Abnahme (e2e)
- Demo-Stack mit `cam1`/`cam2`: `GET /api/reports/preview` liefert Text mit Personen-/Fahrzeugzahlen, die mit
  `GET /api/events?since=…` übereinstimmen.
- `report_time` auf jetzt + 2 min → genau **ein** Bericht entsteht, MQTT `visionguard/report` empfangen
  (`mosquitto_sub`), Backend-Neustart erzeugt keinen zweiten.
- LLM über LLM-Bridge/Ollama: Antwort mit erfundener Uhrzeit → Fallback `template (Fallback)` sichtbar.
- Mail an Testpostfach (z. B. lokaler Mailpit-Container im Profil `demo`) kommt an; fehlendes SMTP → Status
  `ok:false`, Bericht trotzdem gespeichert.
- Read-Key: `POST /reports/generate` → 403; MCP `get_daily_report` funktioniert.

### Machbarkeitstest
Nur für lokales LLM: 5 Fakten-JSONs aus dem Demo-Stack, `qwen2.5:3b` auf CPU – Dauer, Anteil der Texte, die die
Plausibilitätsprüfung bestehen (Ziel ≥ 80 %). Sonst Default `template` belassen.

## Risiken
- History-Ausdünnung (present ≤ 1×/5 min) → Änderungszeitpunkt ± 5 min ungenau; bei Verschiebungen 1×/min.
- Fehldetektionen von YOLOE erzeugen Schein-Änderungen → Mindestdauer, Paarung, Label-Mindestscore; D2 hilft später.
- `stale` nach Worker-Ausfall darf nicht als „verschwunden“ gemeldet werden (eigene Kategorie).
- LLM-Halluzinationen → Plausibilitätsprüfung + Template-Fallback; Datenschutz bei Claude → Opt-in.
- Lokales LLM auf GPU gefährdet Worker (OOM) → CPU bzw. extern.
- Zeitzone/Sommerzeit im Scheduler → `zoneinfo`, Test für Umstellungstag.

## Abhängigkeiten
- **Phase 5** (Gegenstandsgedächtnis, `inventory_history`, `status_text`, Relation) – Pflicht für B2.
- **Phase 2** (API-Keys, signierte URLs, `public_base_url`) – für MCP/Mail/Push-Links.
- **Phase 6** (MCP-Dienst) – für `what_changed`, `get_daily_report`.
- **B3 → B2** (Gegenstandsabschnitt), **B3 ↔ A1** (Push-Kanal gemeinsam nutzen), **B3 ← A3** (Sensor-Abschnitt,
  optional; ohne A3 entfällt er), **B3 ← A2** nicht nötig (kein VLM). C3 (Haustier-Tagebuch) kann seine Zählungen
  später als Abschnitt in den Bericht einspeisen.
