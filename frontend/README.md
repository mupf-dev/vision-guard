# VisionGuard – Frontend

React 19 + TypeScript + Vite SPA (UI in German) with nginx as reverse proxy.
Interfaces: see `../CONTRACT.md` (German).

## Stack

- React 19, react-router 8, @tanstack/react-query 5, zustand (live store, toasts, UI preferences)
- recharts (statistics), lucide-react (icons)
- Plain CSS with CSS variables (`src/styles.css`), dark "SOC" design
- go2rtc player vendored under `public/vendor/` (`<video-stream>`, loaded in `index.html`)

## Development

```bash
corepack pnpm install
corepack pnpm dev          # Vite :5173, proxy /api /media /go2rtc -> http://localhost:8080 (running stack)
corepack pnpm dev:mock     # without backend: simulated REST API + WebSocket (login admin/admin)
corepack pnpm build        # tsc -b && vite build -> dist/
```

The proxy target for `pnpm dev` can be changed via `VG_PROXY_TARGET=http://host:port`.
In mock mode (`VITE_MOCK=1`, `src/lib/mock.ts`), `fetch('/api/...')` and `new WebSocket('/api/ws')` are
intercepted in the browser; camera images are SVG placeholders, detections move in an animation.

## Production

Multi-stage `Dockerfile` (node:24-alpine build -> nginx:alpine). `nginx.conf`:

| Path | Target |
|---|---|
| `/` | SPA, `index.html` fallback |
| `/api/` | `backend:8000` incl. WebSocket upgrade (`/api/ws`) |
| `/api/internal/` | **403** |
| `/media/` | `backend:8000/media/` (Range header for clips) |
| `/go2rtc/` | `go2rtc:1984/` with WebSocket upgrade, protected via `auth_request` -> `/api/auth/check` |

Upstreams are resolved via `resolver 127.0.0.11` + variables, so nginx starts even
when backend/go2rtc are not yet running.

```bash
docker compose build frontend
docker compose up -d frontend      # http://localhost:8080
```

## Structure

```
src/
  main.tsx, App.tsx          Routing, auth guard (GET /api/auth/me), QueryClient
  styles.css                 Design system
  lib/
    api.ts                   REST client (credentials: include, 401 -> login)
    types.ts                 Types as per CONTRACT.md
    store.ts                 zustand: live data (detections/status/feed), toasts, UI prefs (localStorage)
    overlaySync.ts           Overlay synchronization: detection buffer per camera (by frame ts), measurement of
                             the video delay (WebRTC: requestVideoFrameCallback, MSE: buffer length),
                             still-image detection, interpolation between analysis frames
    useLiveSocket.ts         single WebSocket /api/ws, reconnect with exponential backoff,
                             react-query invalidation on event/identity/camera_status
    labels.ts, format.ts     Label colors/names (person/vehicle/animal), date format de-DE
    mock.ts                  Mock backend
  components/
    LivePlayer.tsx           <video-stream> imperatively, src=/go2rtc/api/ws?src=cam_N, mode webrtc,mse,mjpeg,
                             muted, without controls; computes the letterbox rectangle (object-fit: contain)
                             and passes it to overlays as a render prop
    DetectionOverlay.tsx     SVG boxes (normalized bbox -> image rectangle), label/score/track/identity,
                             optional zone polygons; draws the (delayed) detection matching the video frame,
                             hides boxes > 1.5 s without detection, freezes them when the video
                             stalls ("Video hängt" (video stalled)). Focus view: "Sync" = manual overlay delay
    ZoneEditor.tsx           Polygon editor on /api/cameras/{id}/snapshot.jpg (draw, drag points,
                             double-click on edge = insert point, right-click = delete point)
    EventBits.tsx            Feed entry, event detail dialog (clip/snapshot/crop, attributes, rules)
    ui.tsx                   Dialog, Confirm, Switch, Range, ChipSelect, SearchSelect, LabelPill …
  pages/                     (routes see src/App.tsx)
    Login                    /login
    Live                     / and /live/:id – tile grid, focus view, live feed, "Frag die Kamera" (Ask the camera)
    Events                   /ereignisse – filters in the URL, deep link ?ev=<id>, acknowledging emergencies
    Identities               /identitaeten[/:id] – rename, notes, samples, learning lock, merge (incl. partial merge), suggestions
    Objects (+ ObjectChanges, MyItems)
                             /gegenstaende – overview/search, changes, own item instances
    Notifications            /hinweise – notifications with image, push delivery
    Review                   /bestaetigen – review questions (yes/no/skip, keys J/N/S), history
    Pets                     /tiere – pet diary
    Reports                  /berichte – daily reports, preview
    History                  /zeitmaschine – image search in your own words, filmstrip
    Supplies                 /vorraete – supply sensors and shopping list
    Presence                 /anwesenheit – who is where (also across cameras)
    Stats                    /statistik
    admin/                   /admin/{kameras, regeln, sensoren, einstellungen, system, zugaenge}
                             Cameras (general, zones, items, condition), Rules (triggers, conditions,
                             actions incl. push), Sensors, Settings (one *Settings.tsx card per topic, jump marks),
                             System (GPU, models, cameras), Tokens (API access)
```

Navigation (R1): five main items (Live, Ereignisse (Events), Identitäten (Identities), Gegenstände (Items),
Hinweise (Notifications)), rarely used pages under "Mehr" (More), then administration; below 1000 px a menu with all
items (`components/Layout.tsx`).

## Notes

- Live video runs independently of the worker status (go2rtc directly). Only disabled cameras show no image.
  The status badge (Online/Offline) shows the analysis state from `camera.status`.
- The media filter "nur mit Snapshot/Clip" (only with snapshot/clip) is not provided as a query parameter in the
  contract and is applied client-side (then loads up to 500 events).
- `GET /identities/{id}/events` is expected as `{items,total}`; a plain array is accepted as well.
- Alarm toasts appear for new events with fired rules (can be turned off via the bell in the navigation).
