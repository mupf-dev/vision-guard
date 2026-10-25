# Plan: API-Tokens und Schutz von /media

## Ist-Zustand (aus dem Code)
- `backend/app/auth.py`: Ein Admin, JWT im Cookie `vg_session`. `require_user` liest nur das Cookie, `ws_user` ebenso.
- Alle Router hängen `dependencies=[Depends(require_user)]` an (`routers/events.py:15`, `cameras.py:18`, `identities.py:17`, `rules.py:12`, `misc.py:17`). Ein Bearer-Token wird also an genau einer Stelle wirksam.
- `/media` ist ungeschützt: `main.py:144` hängt es per `app.mount("/media", StaticFiles(...))` ein, und nginx reicht `location /media/` ohne `auth_request` durch.
- Medien-URLs entstehen in `schemas.py:241-243` (`"/media/" + path`). Diese URLs gehen auch in MQTT- und Webhook-Payloads an externe Empfänger.

## Datenmodell (`backend/app/models.py`, nur neue Tabelle, `create_all` reicht)
```python
class ApiToken(Base):
    __tablename__ = "api_tokens"
    id: int (PK)
    name: str(100)                 # "Claude-MCP", "Home Assistant"
    prefix: str(16), index         # "vgt_ab12cd34" – angezeigt, zum Nachschlagen
    token_hash: str(64), unique    # SHA-256(secret + pepper)
    scopes: ARRAY(String)          # ["read"] | ["read","write"], Standard ["read"]
    created_at, last_used_at (nullable), expires_at (nullable), revoked_at (nullable)
```
- Format: `vgt_<8 Zeichen prefix>_<32 Byte base64url>`. Gespeichert wird nur der Hash. Als Pepper dient `config.secret_key`. Ein schneller Hash wie SHA-256 genügt, weil das Geheimnis 256 Bit hat; bcrypt ist nicht nötig.
- Den Klartext liefert das Backend genau einmal, in der Antwort auf `POST`.
- `last_used_at` wird höchstens einmal pro Minute geschrieben (Cache im Speicher), damit nicht jeder Request einen DB-Schreibzugriff auslöst.

## Auth-Erweiterung (`backend/app/auth.py`)
- Neues `Principal`-Objekt mit `kind`, `name` und `scopes`, ermittelt von `current_principal(request)`:
  1. Cookie wie bisher, mit allen Rechten.
  2. Sonst `Authorization: Bearer vgt_…`: Prefix nachschlagen, Hash vergleichen mit `hmac.compare_digest`, `revoked_at` und `expires_at` prüfen. Ein Token-Cache mit 30 s TTL wird beim Widerruf geleert.
- `require_user` bleibt als Name erhalten und akzeptiert jetzt beides, die Router müssen also nicht angefasst werden.
- Neue Abhängigkeit `require_scope("write")`:
  - verpflichtend für alle nicht-GET-Methoden;
  - am einfachsten als zentrale Prüfung in `require_user`: Ist die Methode nicht GET/HEAD und fehlt `write`, gibt es 403.
- Tokens dürfen keine Tokens verwalten: Die Endpunkte `/api/tokens*` verlangen das Cookie (`require_session`). So kann ein gestohlenes Token sich nicht selbst verlängern.
- WebSocket `/api/ws`: Optional ist ein Bearer im Header für externe Clients möglich. Ein Token als Query-Parameter gibt es nicht, weil es in den Logs landen würde.
- `/api/auth/check` (für nginx `auth_request`) bleibt nur für das Cookie. go2rtc bleibt damit für Tokens gesperrt; das kann bei Bedarf später kommen.

## Neuer Router `backend/app/routers/tokens.py`
- `GET /api/tokens` liefert `[{id,name,prefix,scopes,created_at,last_used_at,expires_at,revoked}]`.
- `POST /api/tokens {name, scopes, expires_days|null}` liefert dasselbe plus `token` (Klartext, nur dieses eine Mal).
- `DELETE /api/tokens/{id}` widerruft das Token: `revoked_at` wird gesetzt, der Eintrag bleibt für die Nachvollziehbarkeit erhalten.
- `GET /api/auth/me` liefert zusätzlich `auth: "session"|"token"`. Das ist abwärtskompatibel.

## Schutz von /media
Der `StaticFiles`-Mount wird ersetzt durch eine eigene Route `GET /media/{path:path}` in `backend/app/routers/media.py`. Sie arbeitet mit `FileResponse`, dessen Range-Unterstützung für `<video>` vorhanden ist (in Starlette ≥ 0.39 prüfen) und das ETag liefert. Die Route schützt außerdem gegen Pfad-Traversal per `resolve()` und `is_relative_to(media_dir)`.

Akzeptiert werden, in dieser Reihenfolge:
1. **Cookie.** Damit funktionieren `<img>` und `<video>` im Browser ohne jede Änderung, weil sie Cookies gleicher Herkunft mitsenden. Das Cookie ist `samesite=lax` und `path=/`.
2. **Bearer-Token** mit dem Scope `read`, für REST- und MCP-Clients, die Medien selbst laden.
3. **Signierte URL** `?exp=<unix>&sig=<base64url>`, für Einbettung in Agenten-Antworten, Webhooks, MQTT und Benachrichtigungen. Die Signatur ist `HMAC-SHA256(secret_key, "media:" + path + ":" + exp)`, gekürzt auf 128 Bit. Es wird nichts gespeichert.

Weitere Punkte:
- **Erzeugen signierter URLs:**
  - Hilfsfunktion `sign_media_url(path, ttl)` in `backend/app/media_sign.py`.
  - Neuer Endpunkt `POST /api/media/sign {paths:[...], ttl_s}`.
  - Zusätzlich der Query-Parameter `?signed=1` an `GET /events` bzw. `/events/{id}` (und später an den Abfragen zu Gegenständen). Er gibt `thumb_url`, `snapshot_url` und `clip_url` signiert und absolut zurück, falls die Einstellung `public_base_url` gesetzt ist.
- **Externe Payloads:** Webhook, MQTT `{prefix}/events` und Regel-Templates bekommen signierte URLs.
  - Neue Einstellung `media_link_ttl_h` (Standard 24).
  - Im Template-Kontext von `rules.py` und `events.py` neue Platzhalter `{snapshot_url}` und `{clip_url}`.
- **nginx (`frontend/nginx.conf`):** Am bestehenden `location /media/` ändert sich nichts. Das Backend prüft selbst, weil `auth_request` keine signierten URLs kennt. Zusätzlich:
  - `proxy_set_header Authorization $http_authorization;` (wird standardmäßig ohnehin weitergereicht);
  - `add_header Cache-Control "private"`, damit Proxys nichts zwischenspeichern.
- **Übergangsschalter:** Einstellung bzw. Env `VG_MEDIA_PUBLIC=0|1`, Standard 0. Bei 1 gilt das alte Verhalten für Setups, die sich auf öffentliche Links verlassen.

## CONTRACT.md – Ergänzungen
- **Abschnitt Auth:**
  - Bearer-Tokens, ihr Format und die Scopes `read` und `write`.
  - `/api/tokens*` nur mit Session-Cookie.
  - Der Satz „`/media/*` ohne Cookie“ entfällt.
- **REST:** `GET/POST /tokens`, `DELETE /tokens/{id}`, `POST /media/sign`, Parameter `signed` an `/events`.
- **Medien:** Zugriff per Cookie, Bearer oder `?exp&sig`. Externe Payloads (MQTT `{prefix}/events`, Webhooks) enthalten signierte URLs. Die Settings bekommen `media_link_ttl_h` und `public_base_url`.
- **Bekannte Grenzen in AGENTS.md:** Der Punkt „Medien ohne Login“ wird entfernt.
- **MCP (späterer Bereich):** Der MCP-Server nutzt ein Token mit Scope `read` und gibt Medien als signierte URLs aus.

## Frontend
- `src/lib/types.ts`: `ApiToken`, `ApiTokenCreated`.
- `src/lib/api.ts`: `listTokens`, `createToken`, `revokeToken`.
- `src/lib/mock.ts`: Mock dazu.
- Neue Seite `src/pages/admin/Tokens.tsx`, NavLink „API-Zugänge“ mit dem Icon `KeyRound` in `AdminLayout.tsx`, Route im Router.

Skizze der Seite:
```
API-Zugänge                                   [+ Neuer Zugang]
Name            Kennung        Rechte       Erstellt   Zuletzt genutzt  Ablauf
Claude-MCP      vgt_ab12cd34   Lesen        12.10.     vor 3 Min.       nie     [Widerrufen]
Dialog „Neuer Zugang“: Name, Rechte (Lesen / Lesen+Schreiben), Ablauf (nie/30/90/365 Tage)
→ Ergebnis: Token einmalig anzeigen + [Kopieren], Hinweis „wird nicht erneut angezeigt“, curl-Beispiel
```
Bestehende `<img>`- und `<video>`-Elemente brauchen keine Änderung, weil das Cookie mitgeht.

## Schritte
1. CONTRACT.md und AGENTS.md anpassen (S).
2. `models.ApiToken`, `auth.py` (Principal, Bearer, Scope-Prüfung) und der Token-Cache (M).
3. `routers/tokens.py` und `me` erweitern (S).
4. `media_sign.py`, `routers/media.py` als Ersatz des Mounts, Traversal-Schutz, Range testen (M).
5. Signierte URLs in den externen Payloads und im Template-Kontext, Settings `media_link_ttl_h` und `public_base_url` (M).
6. Frontend: Typen, API, Mock, Seite `Tokens.tsx`, `corepack pnpm build` (M).
7. nginx `Cache-Control private`, Ende-zu-Ende-Test (S):
   - curl mit und ohne Bearer;
   - widerrufenes Token ergibt 401;
   - GET mit `read`-Token geht, PUT ergibt 403;
   - `/media` ohne Anmeldung ergibt 401, mit Cookie und mit Signatur 200;
   - abgelaufene Signatur ergibt 403;
   - Clip mit Range ergibt 206;
   - Screenshot der Ereignisse prüfen.

Gesamtaufwand etwa M bis L, ungefähr 1,5 bis 2 Tage.

## Risiken und offene Fragen
- **Bricht bestehende Empfänger:** Webhooks und MQTT, die heute relative `/media`-Links öffnen, funktionieren nach der Umstellung nur noch mit Signatur. Abgefedert wird das durch den Schalter `VG_MEDIA_PUBLIC` und einen Hinweis in den Release-Notes.
- **Absolute URLs** für Agenten brauchen `public_base_url`. Das Backend kennt seine externe Adresse nicht zuverlässig, `X-Forwarded-Host` hilft nur bei Requests.
- **Wechsel von `VG_SECRET_KEY`** macht alle signierten Links und alle Token-Hashes ungültig, weil der Pepper wegfällt. Alternative: ein eigener `VG_TOKEN_PEPPER`. Zu entscheiden.
- **Leistung:** Eine eigene Route statt StaticFiles bedeutet mehr Python pro Thumbnail. Bei 16 Kameras unkritisch; bei Bedarf auf nginx `X-Accel-Redirect` umstellen (Backend prüft, nginx liefert aus). Dafür bräuchte nginx das Volume `media` read-only.
- **Granularität:** Reichen `read` und `write`, oder braucht man einen eigenen Scope `media` bzw. eine Beschränkung auf einzelne Kameras? Vorschlag: zunächst nur `read` und `write`.
- **Signierte Links in Agenten-Antworten** sind bis zum Ablauf von jedem nutzbar, der den Link hat. Deshalb kurze TTL, bei MCP etwa 1 h.
- **CORS:** `main.py` erlaubt nur `localhost:5173`. Externe Bearer-Clients brauchen kein CORS, weil sie keine Browser sind.

## Relevante Dateien
- /home/miefda/vision-guard/backend/app/auth.py
- /home/miefda/vision-guard/backend/app/routers/auth.py
- /home/miefda/vision-guard/backend/app/main.py
- /home/miefda/vision-guard/backend/app/schemas.py
- /home/miefda/vision-guard/backend/app/models.py
- /home/miefda/vision-guard/backend/app/rules.py
- /home/miefda/vision-guard/backend/app/events.py
- /home/miefda/vision-guard/frontend/nginx.conf
- /home/miefda/vision-guard/frontend/src/pages/admin/AdminLayout.tsx
- /home/miefda/vision-guard/CONTRACT.md