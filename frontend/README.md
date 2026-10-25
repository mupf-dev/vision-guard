# VisionGuard – Frontend

React 19 + TypeScript + Vite SPA (UI auf Deutsch) mit nginx als Reverse-Proxy.
Schnittstellen: siehe `../CONTRACT.md`.

## Stack

- React 19, react-router 8, @tanstack/react-query 5, zustand (Live-Store, Toasts, UI-Präferenzen)
- recharts (Statistik), lucide-react (Icons)
- Plain CSS mit CSS-Variablen (`src/styles.css`), dunkles "SOC"-Design
- go2rtc-Player vendored unter `public/vendor/` (`<video-stream>`, geladen in `index.html`)

## Entwicklung

```bash
corepack pnpm install
corepack pnpm dev          # Vite :5173, Proxy /api /media /go2rtc -> http://localhost:8080 (laufender Stack)
corepack pnpm dev:mock     # ohne Backend: simulierte REST-API + WebSocket (Login admin/admin)
corepack pnpm build        # tsc -b && vite build -> dist/
```

Proxy-Ziel für `pnpm dev` per `VG_PROXY_TARGET=http://host:port` änderbar.
Im Mock-Modus (`VITE_MOCK=1`, `src/lib/mock.ts`) werden `fetch('/api/...')` und `new WebSocket('/api/ws')`
im Browser abgefangen; Kamerabilder sind SVG-Platzhalter, Detektionen bewegen sich animiert.

## Produktion

Multi-Stage-`Dockerfile` (node:24-alpine Build -> nginx:alpine). `nginx.conf`:

| Pfad | Ziel |
|---|---|
| `/` | SPA, `index.html`-Fallback |
| `/api/` | `backend:8000` inkl. WebSocket-Upgrade (`/api/ws`) |
| `/api/internal/` | **403** |
| `/media/` | `backend:8000/media/` (Range-Header für Clips) |
| `/go2rtc/` | `go2rtc:1984/` mit WebSocket-Upgrade, geschützt per `auth_request` -> `/api/auth/check` |

Upstreams werden über `resolver 127.0.0.11` + Variablen aufgelöst, nginx startet daher auch,
wenn Backend/go2rtc noch nicht laufen.

```bash
docker compose build frontend
docker compose up -d frontend      # http://localhost:8080
```

## Aufbau

```
src/
  main.tsx, App.tsx          Routing, Auth-Guard (GET /api/auth/me), QueryClient
  styles.css                 Design-System
  lib/
    api.ts                   REST-Client (credentials: include, 401 -> Login)
    types.ts                 Typen laut CONTRACT.md
    store.ts                 zustand: Live-Daten (detections/status/feed), Toasts, UI-Prefs (localStorage)
    overlaySync.ts           Overlay-Synchronisation: Detektionspuffer je Kamera (nach Frame-ts), Messung der
                             Video-Verzögerung (WebRTC: requestVideoFrameCallback, MSE: Pufferlänge),
                             Standbild-Erkennung, Interpolation zwischen Analyse-Frames
    useLiveSocket.ts         einziger WebSocket /api/ws, Reconnect mit Exponential-Backoff,
                             react-query-Invalidierung bei event/identity/camera_status
    labels.ts, format.ts     Label-Farben/-Namen (Person/Fahrzeug/Tier), Datumsformat de-DE
    mock.ts                  Mock-Backend
  components/
    LivePlayer.tsx           <video-stream> imperativ, src=/go2rtc/api/ws?src=cam_N, mode webrtc,mse,mjpeg,
                             muted, ohne Controls; berechnet Letterbox-Rechteck (object-fit: contain)
                             und gibt es als Render-Prop an Overlays
    DetectionOverlay.tsx     SVG-Boxen (normierte bbox -> Bildrechteck), Label/Score/Track/Identität,
                             optionale Zonen-Polygone; zeichnet die zum Videobild passende (verzögerte)
                             Detektion, blendet Boxen > 1,5 s ohne Detektion aus, friert sie bei hängendem
                             Video ein („Video hängt“). Fokusansicht: „Sync“ = manuelle Overlay-Verzögerung
    ZoneEditor.tsx           Polygon-Editor auf /api/cameras/{id}/snapshot.jpg (zeichnen, Punkte ziehen,
                             Doppelklick auf Kante = Punkt einfügen, Rechtsklick = Punkt löschen)
    EventBits.tsx            Feed-Eintrag, Ereignis-Detaildialog (Clip/Snapshot/Ausschnitt, Attribute, Regeln)
    ui.tsx                   Dialog, Confirm, Switch, Range, ChipSelect, SearchSelect, LabelPill …
  pages/                     (Routen siehe src/App.tsx)
    Login                    /login
    Live                     / und /live/:id – Kachelraster, Fokusansicht, Live-Feed, „Frag die Kamera“
    Events                   /ereignisse – Filter in der URL, Deep-Link ?ev=<id>, Quittieren von Notfällen
    Identities               /identitaeten[/:id] – Umbenennen, Notizen, Samples, Lernsperre, Merge (auch Teil-Merge), Vorschläge
    Objects (+ ObjectChanges, MyItems)
                             /gegenstaende – Übersicht/Suche, Veränderungen, eigene Exemplare
    Notifications            /hinweise – Hinweise mit Bild, Push-Zustellung
    Review                   /bestaetigen – Rückfragen (Ja/Nein/Überspringen, Tasten J/N/S), Verlauf
    Pets                     /tiere – Haustier-Tagebuch
    Reports                  /berichte – Tagesberichte, Vorschau
    History                  /zeitmaschine – Bildsuche mit eigenen Worten, Filmstreifen
    Supplies                 /vorraete – Vorrats-Sensoren und Einkaufsliste
    Presence                 /anwesenheit – wer ist wo (auch über Kameras)
    Stats                    /statistik
    admin/                   /admin/{kameras, regeln, sensoren, einstellungen, system, zugaenge}
                             Cameras (Allgemein, Zonen, Gegenstände, Zustand), Rules (Auslöser, Bedingungen,
                             Aktionen inkl. Push), Sensors, Settings (je Thema eine Karte *Settings.tsx, Sprungmarken),
                             System (GPU, Modelle, Kameras), Tokens (API-Zugänge)
```

Navigation (R1): fünf Hauptpunkte (Live, Ereignisse, Identitäten, Gegenstände, Hinweise), seltene Seiten unter
„Mehr“, dann Administration; unter 1000 px ein Menü mit allen Punkten (`components/Layout.tsx`).

## Hinweise

- Live-Video läuft unabhängig vom Worker-Status (go2rtc direkt). Nur deaktivierte Kameras zeigen kein Bild.
  Der Status-Badge (Online/Offline) zeigt den Analysezustand aus `camera.status`.
- Der Medienfilter "nur mit Snapshot/Clip" ist im Vertrag nicht als Query-Parameter vorgesehen und wird
  clientseitig angewendet (lädt dann bis zu 500 Ereignisse).
- `GET /identities/{id}/events` wird als `{items,total}` erwartet, ein reines Array wird ebenfalls akzeptiert.
- Alarm-Toasts erscheinen für neue Events mit ausgelösten Regeln (abschaltbar über die Glocke in der Navigation).
