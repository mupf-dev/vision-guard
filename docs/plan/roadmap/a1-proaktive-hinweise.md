# A1 – Proaktive Hinweise („Du vergisst deinen Schlüssel“)

Stand: 2026-10-24 · Entwurf, baut auf Phase 5 (Gegenstandsgedächtnis) auf · Aufwand gesamt: **M** (ca. 3–4 Tage)

## 1. Zielverhalten

VisionGuard meldet sich **von selbst**, wenn ein Auslöser (Person geht zur Haustür, feste Uhrzeit) mit einem Zustand
des Gegenstandsgedächtnisses zusammentrifft. Beispiele (Vokabular-Labels existieren bereits: `keys`, `wallet`,
`phone`, `umbrella`, `medicine`):

| Hinweis | Auslöser | Bedingung(en) |
|---|---|---|
| „Schlüssel liegt noch am Brett“ | Person betritt Zone „Haustür“ (Flurkamera), kam aus Zone „Flur innen“ | `keys` **present** in Zone „Schlüsselbrett“, 15 s nach dem Auslöser geprüft |
| „Regenschirm mitnehmen – 70 % Regen ab 15 Uhr“ | wie oben | `umbrella` present im Raum „Flur“ **und** Regenwahrscheinlichkeit ≥ 60 % in den nächsten 12 h |
| „Medikamente heute noch nicht genommen“ | täglich 10:00 | `medicine` seit 06:00 **unberührt** (keine Bewegung/kein Verschwinden) |

Kein neues Video: Grundlage sind nur Inventar-Zustände, Tracks und ein bereits vorhandenes Übersichtsbild/Crop.

## 2. Architektur

Alles im **Backend**; der Worker bleibt unverändert (kein zusätzliches Modell, **0 MB VRAM**).

| Modul | Aufgabe |
|---|---|
| `backend/app/rule_conditions.py` (neu) | Auswertung der neuen `conditions` gegen `inventory_items`/`inventory_history`, Wetter, Track-Zonen |
| `backend/app/weather.py` (neu) | Open-Meteo-Abfrage alle 30 min, Cache im Speicher, `rain_outlook(hours)` |
| `backend/app/push.py` (neu) | Versand über Push-Ziele (ntfy, Pushover), Retry 1×, Timeout 10 s, Keys nie loggen |
| `backend/app/notifications.py` (neu) | `Notification` anlegen, WS-Meldung, Dedup |
| `backend/app/rules.py` | `time`-Trigger-Planer (minütlicher Tick), verzögerte Prüfung (`condition_delay_s`) |
| `backend/app/events.py` | `_fire()` ruft Bedingungen auf; Aktion `push`; Regeln ohne Track (Zeit-Trigger) ohne Event |
| `backend/app/routers/notifications.py` (neu) | REST |
| `mcp/` | Tool `list_notifications` |

Ablauf Track-Regel: Trigger feuert wie bisher → `static_match` → falls `conditions` vorhanden und
`condition_delay_s > 0`: Prüfung als `asyncio`-Task nach der Verzögerung (der Gegenstand muss Zeit haben, `missing` zu
werden: `inventory_miss_m × interval_s` ≈ 9 s; Default 15 s) → alle Bedingungen erfüllt → Aktionen + `Notification`.
Ablauf Zeit-Regel: `tick()` prüft je Minute `trigger="time"`-Regeln (`at`, `schedule.days`), höchstens 1× pro Tag.

**Richtung „hinaus“:** `TrackState` bekommt `zones_visited: list[str]` (Reihenfolge der betretenen Zonen). Bedingung
`track_came_from` prüft, ob eine der Zonen vorher besucht wurde – unterscheidet Gehen von Heimkommen ohne
Pose-Modell.

## 3. Push-Kanal – Empfehlung

**Empfehlung: ntfy** (Standard `https://ntfy.sh`, optional selbst gehostet als eigener Compose-Dienst später).
Gründe: kostenlos, Android/iOS-App, einfacher HTTP-POST, Token-Auth, Priorität, Klick-URL, Bild-Anhang; ohne Konto
nutzbar (Topic-Name dann wie ein Passwort behandeln). **Pushover** als zweiter Kanal (einmalig ca. 5 USD je
Plattform, sehr zuverlässig, Bild bis 2,5 MB als Upload). **Webhook** existiert bereits und bleibt für Rest/HA.

Bild: Ist `public_base_url` gesetzt, wird ein signierter Link (`media_link_ttl_h`) als `Attach`/`Click` geschickt,
sonst lädt das Backend das JPEG direkt hoch (ntfy: `PUT` mit `Filename`-Header; Pushover: `attachment`).
*Vermutet, im Machbarkeitstest prüfen:* Anhang-Grenzen von ntfy.sh ohne Konto (Doku: 15 MB, 3 h Ablauf).

Zugangsdaten stehen **nur in den Settings** (nicht in Regeln), Ausgabe maskiert wie Kamera-Passwörter
(`secrets_mask`), zurückgeschickte `***` werden durch den gespeicherten Wert ersetzt.

## 4. Bibliotheken / Dienste

| Was | Version | Status |
|---|---|---|
| `httpx` | 0.28.1 (bereits im Backend) | verifiziert (requirements.txt) |
| Open-Meteo Forecast-API (`/v1/forecast?hourly=precipitation_probability,precipitation`) | API ohne Key | aus Doku bekannt, im Projekt nicht getestet |
| Alternative Bright Sky (DWD-Daten) | API ohne Key | vermutet gleichwertig für DE |
| ntfy / Pushover HTTP-API | – | aus Doku bekannt, nicht getestet |

Keine neuen Python-Pakete, keine GPU-Last. Datenschutz: an Open-Meteo gehen nur auf 2 Nachkommastellen gerundete
Koordinaten.

## 5. CONTRACT.md-Ergänzungen (additiv)

**DB** (`ADDITIVE_COLUMNS`): `rules.conditions` jsonb NOT NULL DEFAULT '[]', `rules.condition_delay_s` double
precision NOT NULL DEFAULT 0, `rules.at` varchar(5) NULL. Neue Tabelle `notifications` (`Notification`).

**Rule** (zusätzlich):
```jsonc
{ ..., "trigger": "enter|leave|dwell|time",
  "at": "10:00" | null,                 // nur trigger=time; Tage aus schedule.days (schedule.start/end ignoriert)
  "condition_delay_s": 15,              // 0..120; Bedingungen erst nach dieser Zeit prüfen
  "conditions": [                       // alle müssen erfüllt sein (UND)
    { "type": "inventory", "labels": ["keys"], "state": "present|absent",
      "camera_ids": [], "room": null, "zone_ids": [] },          // absent = kein Gegenstand present (missing/stale/nie)
    { "type": "inventory_untouched", "labels": ["medicine"], "since": "06:00", "camera_ids": [], "zone_ids": [] },
                                        // erfüllt, wenn present und seit `since` (heute) kein Historieneintrag
                                        // missing/moved; zusätzlich kein person-Track in zone_ids (falls gesetzt)
    { "type": "weather", "metric": "rain_probability", "min": 60, "hours": 12 },
    { "type": "track_came_from", "zone_ids": ["z_flur"] } ],     // nur bei Track-Triggern
  "actions": [ ..., { "type": "push", "target": "handy", "title": "{rule}", "message": "{items} liegt noch da",
                      "priority": "default|high", "image": "snapshot|crop|none" } ] }
```
Neue Template-Platzhalter: `{items}` (deutsche Namen der passenden Gegenstände), `{item_place}`
(z. B. „Flur, Schlüsselbrett“), `{weather}` („70 % Regen ab 15 Uhr“).
Bei `trigger=time` sind `camera_ids/labels/identity` ohne Wirkung; `cooldown_s` gilt pro Regel.

**Settings:**
```jsonc
{ ..., "push_targets": [ { "id": "handy", "type": "ntfy|pushover", "name": "Marcs Handy",
     "server": "https://ntfy.sh", "topic": "vg-…", "token": "***" | null,      // ntfy
     "user_key": "***", "app_token": "***" } ],                                 // pushover
  "weather_location": { "lat": 50.11, "lon": 8.68 } | null,
  "notifications_retention_days": 30 }
```
`POST /api/settings/push_test {target}` → `{ok, error}` (Scope `write`).

**Notification:**
```jsonc
{ "id": 5, "rule_id": 3, "rule": "Schlüssel vergessen", "ts": "...", "title": "...", "message": "...",
  "camera_id": 2 | null, "event_id": "uuid" | null, "item_ids": [17], "image_url": "/media/…" | null,
  "delivery": [ { "target": "handy", "ok": true, "error": null } ] }
```
REST: `GET /notifications?rule_id=&since=&limit=50&offset=0` → `{items,total}`, `DELETE /notifications/{id}`.
WS: `{"type":"notification","notification":Notification}`.
MQTT extern: `{prefix}/notifications` (Payload = Notification, signierte URLs).
MCP: `list_notifications(since?: str, limit: int = 10)` → `{items:[{ts, rule, message, image_url}]}`.

## 6. UI-Skizze

- **Regeln** (`frontend/src/pages/admin/Rules.tsx`): Trigger-Auswahl um „Zu einer Uhrzeit“ erweitert; neuer Block
  „Nur wenn …“ mit Zeilen *Gegenstand [Schlüssel ▾] ist [noch da | weg] in [Raum/Zone ▾]*,
  *… unberührt seit [06:00]*, *Regen ≥ [60] % in [12] h*, *kam aus Zone [▾]*; Feld „Prüfen nach [15] s“.
  Aktion „Push an [Ziel ▾]“ mit Titel/Text/Bild.
- **Vorlagen-Knopf** „Schlüssel vergessen“, „Regenschirm“, „Medikamente“ füllt die Regel vor.
- **Einstellungen**: Abschnitt „Benachrichtigungen“ (Push-Ziele, Test-Knopf, Wetter-Standort).
- **Neue Seite „Hinweise“** (Liste mit Bild, Regel, Zustellstatus); Live-Toast über WS.

## 7. Schritte

1. S – DB-Spalten, Schemas, `types.ts`/`api.ts`/`mock.ts`, CONTRACT.md.
2. M – `rule_conditions.py`, `zones_visited`, verzögerte Prüfung, `time`-Trigger, `Notification`.
3. S – `push.py` (ntfy, Pushover), Settings-Maskierung, `push_test`.
4. S – `weather.py` mit Cache und Fehlertoleranz (Wetter unbekannt → Bedingung **nicht** erfüllt, Log-Hinweis).
5. M – UI Regeln/Einstellungen/Hinweise; MCP-Tool.
6. S – e2e-Test mit Testkamera `cam_3`.

## 8. Abnahmekriterien (e2e)

1. Regel „Fernbedienung noch da“ (Zone auf `cam_3`, Trigger Person betritt Zone, Verzögerung 15 s): Fernbedienung
   liegt → genau **ein** Push (Cooldown) mit Bild auf dem Handy, Eintrag unter `/api/notifications`, WS-Toast.
   Fernbedienung vorher entfernt → kein Push.
2. Zeit-Regel `at` = jetzt + 2 min mit `inventory_untouched` seit 1 h: Gegenstand liegt unberührt → Push; nach
   Verschieben (> 0,1) → kein Push am Folgetag (Test über verkürztes `since`).
3. Wetter-Bedingung mit `min: 0` → erfüllt; Open-Meteo blockiert (falsche URL) → nicht erfüllt, kein Absturz.
4. `GET /settings` zeigt Tokens nur als `***`; Logs enthalten keine Tokens; `PUT` mit `***` behält den Wert.
5. MCP `list_notifications` liefert den Hinweis mit signierter Bild-URL.
6. `corepack pnpm build` grün; Backend-Neustart verliert keine geplanten Zeit-Regeln (Planung zustandslos aus DB).

## 9. Machbarkeitstest (vor Schritt 2, ½ Tag)

Mit laufendem Inventar auf `cam_3`: Wie schnell und wie sicher wechselt ein kleiner Gegenstand (Schlüsselbund,
Tablettendose) nach Wegnehmen auf `missing`? Wie oft flackert er im Ruhezustand (`present` ↔ `missing`)? Ergebnis
bestimmt Default für `condition_delay_s` und ob `inventory_untouched` nur Zustandswechsel oder auch Verschiebungen
zählt. Ohne verlässliche Erkennung des Schlüsselbunds (kleines Objekt, ggf. Tiling nötig) ist das Leitbeispiel nicht
abnahmefähig.

## 10. Risiken

- **Fehlalarme durch Flackern** der Inventar-Erkennung → Verzögerung, Cooldown, „Hinweis-Müdigkeit“; max. N Pushes
  pro Regel und Tag (Setting, Default 3).
- **Richtung** Gehen/Kommen über Zonenfolge ist heuristisch; Kamera-Perspektive am Flur entscheidend.
- **Verdeckung:** Person steht vor dem Schlüsselbrett → Gegenstand bleibt `present` (gewollt), kann aber gerade
  genommen worden sein → deshalb Verzögerung.
- **Medikamente:** „unberührt“ ist nur indirekt messbar; Zone + Personen-Track als Zusatzsignal.
- **Externe Dienste:** ntfy.sh-Topic ist ohne Token öffentlich lesbar → Token/eigener Server empfehlen; Bild-Links
  nur signiert.

## 11. Abhängigkeiten

- **Phase 5 (Pflicht):** `inventory_items`, `inventory_history` (Verschiebungs-Einträge), `room`, Zonen-Zuordnung.
- **Phase 4:** stabile Zustände `present/missing` und `occluded`; kleine Gegenstände mit Tiling.
- **Phase 2:** signierte Links, `public_base_url`, API-Keys für MCP; **Phase 6:** MCP-Tool.
- **Roadmap:** B4 („mein Schlüssel“) macht Bedingungen auf benannte Exemplare möglich (`item_ids` statt `labels`);
  B3 (Tagesbericht) und C5 (Kamera-Gesundheit) nutzen denselben Push-Kanal; D2 (Rückfrage per Push) baut auf
  `push.py` auf; A3 (virtuelle Sensoren) liefert später weitere Bedingungstypen (`sensor`).
