"""VisionGuard MCP-Dienst (Streamable HTTP unter /mcp).

Der Dienst hat keinen eigenen Zugang: Er verlangt vom MCP-Client einen API-Key (`Authorization: Bearer vgt_…`),
prüft ihn über `GET /api/auth/me` und reicht denselben Header bei jedem Backend-Aufruf durch. Keys werden nie
geloggt. Alle Tools lesen nur (Scope `read` genügt). Siehe CONTRACT.md, Abschnitt „I. MCP-Dienst“.
"""

from __future__ import annotations

import base64
import hashlib
import io
import json
import logging
import os
import time
from datetime import datetime, timezone
from typing import Any, Optional
from zoneinfo import ZoneInfo

import httpx
import uvicorn
from mcp.server.mcpserver import Context, MCPServer
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import CallToolResult, ImageContent, TextContent, ToolAnnotations

log = logging.getLogger("vg.mcp")

BACKEND_URL = os.environ.get("VG_BACKEND_URL", "http://backend:8000").rstrip("/")
PORT = int(os.environ.get("VG_MCP_PORT", "8100"))
ALLOWED_HOSTS = [h.strip() for h in os.environ.get("VG_MCP_ALLOWED_HOSTS", "").split(",") if h.strip()]
try:
    LOCAL_TZ = ZoneInfo(os.environ.get("TZ") or "Europe/Berlin")
except Exception:  # noqa: BLE001
    LOCAL_TZ = ZoneInfo("Europe/Berlin")

AUTH_CACHE_S = 30.0
SNAPSHOT_MAX_WIDTH = 1600
PEOPLE_LABELS = "person,dog,cat"
AUTH_ERROR = "API-Key ungültig oder ohne Berechtigung."

_http: Optional[httpx.AsyncClient] = None


def http() -> httpx.AsyncClient:
    global _http
    if _http is None:
        _http = httpx.AsyncClient(base_url=BACKEND_URL, timeout=httpx.Timeout(20.0, connect=5.0))
    return _http


# ------------------------------------------------------------------ Backend-Zugriff


class BackendError(Exception):
    """Fehler mit deutscher Meldung, wird als Tool-Ergebnis mit isError zurückgegeben."""


def _auth_header(ctx: Context) -> str:
    value = ""
    try:
        rc = ctx.request_context
        # HTTP-Transport: Starlette-Request (bzw. TransportContext) mit den Headern der Anfrage
        for src in (getattr(rc, "request", None), getattr(rc, "transport", None), rc):
            headers = getattr(src, "headers", None) if src is not None else None
            if headers is not None and hasattr(headers, "get"):
                value = headers.get("authorization") or ""
                if value:
                    break
    except Exception:  # noqa: BLE001
        value = ""
    if not value.lower().startswith("bearer "):
        raise BackendError(AUTH_ERROR)
    return value


def _detail(resp: httpx.Response) -> str:
    try:
        d = resp.json().get("detail")
        if isinstance(d, str):
            return d
    except Exception:  # noqa: BLE001
        pass
    return f"HTTP {resp.status_code}"


async def api(ctx: Context, path: str, params: Optional[dict] = None, *, raw: bool = False,
              not_found: Optional[str] = None) -> Any:
    params = {k: v for k, v in (params or {}).items() if v is not None}
    try:
        resp = await http().get("/api" + path, params=params, headers={"Authorization": _auth_header(ctx)})
    except httpx.HTTPError as e:
        log.warning("Backend nicht erreichbar (%s): %s", path, type(e).__name__)
        raise BackendError("VisionGuard-Backend ist nicht erreichbar.") from e
    if resp.status_code in (401, 403):
        raise BackendError(AUTH_ERROR)
    if resp.status_code == 404:
        raise BackendError(not_found or "Nicht gefunden.")
    if resp.status_code >= 400:
        raise BackendError(f"Backend-Fehler: {_detail(resp)}")
    return resp if raw else resp.json()


# ------------------------------------------------------------------ Formatierung


def _parse_ts(value: Any) -> Optional[datetime]:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def fmt_time(value: Any) -> str:
    """„14:02“ für heute, sonst „23.10. 14:02“ (Ortszeit)."""
    dt = _parse_ts(value)
    if dt is None:
        return "?"
    local = dt.astimezone(LOCAL_TZ)
    if local.date() == datetime.now(LOCAL_TZ).date():
        return local.strftime("%H:%M")
    return local.strftime("%d.%m. %H:%M")


def fmt_ago(value: Any) -> str:
    dt = _parse_ts(value)
    if dt is None:
        return "zu unbekannter Zeit"
    s = max(0.0, (datetime.now(timezone.utc) - dt).total_seconds())
    if s < 90:
        return "gerade eben"
    if s < 3600:
        return f"vor {round(s / 60)} min"
    if s < 48 * 3600:
        return f"vor {round(s / 3600)} h"
    return f"vor {round(s / 86400)} Tagen"


def _where(camera_name: Any, room: Any) -> str:
    text = f"Kamera {camera_name or '?'}"
    if room:
        text += f" (Raum {room})"
    return text


def compact_item(it: dict) -> dict:
    rel = it.get("relation") or None
    return {
        "id": it.get("id"),
        "name_de": it.get("name_de") or it.get("label"),
        "label": it.get("label"),
        "camera_id": it.get("camera_id"),
        "camera_name": it.get("camera_name"),
        "room": it.get("room"),
        "state": it.get("state"),
        "visible": bool(it.get("visible", it.get("state") == "present")),
        "relation_text": rel.get("text") if isinstance(rel, dict) else None,
        "zones": it.get("zone_names") or it.get("zones") or [],
        "first_seen": it.get("first_seen"),
        "last_seen": it.get("last_seen"),
        "crop_url": it.get("crop_url"),
    }


def item_sentence(c: dict) -> str:
    """„Fernbedienung: aktuell sichtbar in Kamera Wohnzimmer (Raum Wohnzimmer), vermutlich auf dem Tisch, seit 14:02“."""
    name = c.get("name_de") or c.get("label") or "Gegenstand"
    where = _where(c.get("camera_name"), c.get("room"))
    if c.get("state") == "present":
        parts = [f"{name}: aktuell sichtbar in {where}"]
        if c.get("relation_text"):
            parts.append(str(c["relation_text"]))
        if c.get("zones"):
            parts.append("Zone " + ", ".join(map(str, c["zones"])))
        if c.get("first_seen"):
            parts.append(f"seit {fmt_time(c['first_seen'])}")
        text = ", ".join(parts)
    else:
        text = f"{name}: zuletzt gesehen {fmt_ago(c.get('last_seen'))} ({fmt_time(c.get('last_seen'))}) in {where}"
        if c.get("state") == "stale":
            text += " – Kamera liefert derzeit keine Daten"
        else:
            text += " – derzeit nicht sichtbar"
    text += f" [id {c.get('id')}]"
    if c.get("crop_url"):
        text += f" Bild: {c['crop_url']}"
    return text


def n_de(n: int, one: str, many: str) -> str:
    return f"{n} {one if n == 1 else many}"


def ok(text: str, data: dict) -> CallToolResult:
    return CallToolResult(content=[TextContent(type="text", text=text)], structured_content=data)


def fail(message: str) -> CallToolResult:
    return CallToolResult(content=[TextContent(type="text", text=message)], is_error=True)


async def _cameras(ctx: Context) -> list[dict]:
    data = await api(ctx, "/cameras")
    return data if isinstance(data, list) else data.get("items", [])


def _find_camera(cams: list[dict], camera_id: Optional[int], camera_name: Optional[str]) -> dict:
    if camera_id is not None:
        for c in cams:
            if c.get("id") == camera_id:
                return c
        raise BackendError(f"Kamera {camera_id} ist unbekannt.")
    needle = (camera_name or "").strip().casefold()
    if not needle:
        raise BackendError("Bitte camera_id oder camera_name angeben.")
    for key in ("name", "room"):
        hits = [c for c in cams if str(c.get(key) or "").casefold() == needle]
        if len(hits) == 1:
            return hits[0]
    hits = [c for c in cams if needle in str(c.get("name") or "").casefold()
            or needle in str(c.get("room") or "").casefold()]
    if len(hits) == 1:
        return hits[0]
    names = ", ".join(f"{c.get('name')} (id {c.get('id')})" for c in cams) or "keine"
    if not hits:
        raise BackendError(f"Kamera „{camera_name}“ ist unbekannt. Vorhanden: {names}.")
    raise BackendError(f"Kamera „{camera_name}“ ist nicht eindeutig. Vorhanden: {names}.")


# ------------------------------------------------------------------ MCP-Server

mcp = MCPServer(
    "VisionGuard",
    instructions=(
        "VisionGuard ist eine private Videoanalyse für Heimkameras. Die Tools beantworten, wo Gegenstände "
        "(z. B. Schlüssel, Fernbedienung, Buch) zuletzt gesehen wurden, welche Gegenstände gerade sichtbar sind, "
        "wer bzw. welches Tier gerade wo ist, und liefern aktuelle Kamerabilder. Lagebeziehungen („auf dem Tisch“) "
        "sind 2D-Schätzungen und deshalb mit „vermutlich“ formuliert. Bild-URLs sind signiert und zeitlich begrenzt."
    ),
    log_level="WARNING",
)

READ_ONLY = ToolAnnotations(read_only_hint=True, open_world_hint=False)


@mcp.tool(annotations=READ_ONLY)
async def find_object(query: str, ctx: Context, limit: int = 5) -> CallToolResult:
    """Sucht einen Gegenstand (deutsch oder englisch, z. B. „Wo ist meine Fernbedienung?“) und sagt, wo er
    aktuell sichtbar ist bzw. zuletzt gesehen wurde – mit Kamera, Raum, vermuteter Lage und Zeit."""
    try:
        q = (query or "").strip()
        if not q:
            return fail("Bitte einen Suchbegriff angeben, z. B. „Fernbedienung“.")
        limit = max(1, min(int(limit), 20))
        data = await api(ctx, "/inventory/search", {"q": q, "limit": limit, "signed": 1})
        items = [compact_item(i) for i in data.get("items", [])]
        answer = data.get("answer") or (item_sentence(items[0]) if items else f"Kein Gegenstand „{q}“ bekannt.")
        lines = [answer]
        if items:
            lines.append("Treffer:")
            lines += [f"- {item_sentence(c)}" for c in items]
        return ok("\n".join(lines), {"query": data.get("query", q), "answer": answer, "items": items})
    except BackendError as e:
        return fail(str(e))


@mcp.tool(annotations=READ_ONLY)
async def list_visible_objects(ctx: Context, camera_id: Optional[int] = None, room: Optional[str] = None,
                               camera_name: Optional[str] = None) -> CallToolResult:
    """Listet alle Gegenstände, die gerade sichtbar sind – optional nur für eine Kamera (camera_id oder
    camera_name) oder einen Raum (room)."""
    try:
        if camera_id is None and camera_name:
            camera_id = _find_camera(await _cameras(ctx), None, camera_name)["id"]
        data = await api(ctx, "/inventory", {"state": "present", "signed": 1, "camera_id": camera_id,
                                             "room": room or None, "limit": 200})
        items = [compact_item(i) for i in data.get("items", [])]
        scope = []
        if camera_id is not None:
            scope.append(f"Kamera {camera_name or camera_id}")
        if room:
            scope.append(f"Raum {room}")
        suffix = f" ({', '.join(scope)})" if scope else ""
        if not items:
            text = f"Derzeit sind keine Gegenstände sichtbar{suffix}."
        else:
            text = f"{n_de(len(items), 'Gegenstand', 'Gegenstände')} sichtbar{suffix}:\n" + "\n".join(f"- {item_sentence(c)}" for c in items)
        return ok(text, {"total": data.get("total", len(items)), "items": items})
    except BackendError as e:
        return fail(str(e))


@mcp.tool(annotations=READ_ONLY)
async def get_object_history(item_id: int, ctx: Context, limit: int = 20) -> CallToolResult:
    """Verlauf eines Gegenstands (id aus find_object/list_visible_objects): wann er wo sichtbar war oder fehlte."""
    try:
        limit = max(1, min(int(limit), 500))
        nf = f"Gegenstand {item_id} ist unbekannt."
        item = await api(ctx, f"/inventory/{item_id}", {"signed": 1}, not_found=nf)
        hist = await api(ctx, f"/inventory/{item_id}/history", {"limit": limit, "signed": 1}, not_found=nf)
        entries = [{"ts": h.get("ts"), "state": h.get("state"), "camera_id": h.get("camera_id"),
                    "zones": h.get("zones") or [], "crop_url": h.get("crop_url")} for h in hist]
        state_de = {"present": "sichtbar", "missing": "nicht sichtbar", "stale": "keine Daten"}
        name = item.get("name_de") or item.get("label")
        lines = [item_sentence(compact_item(item)), f"Verlauf ({n_de(len(entries), 'Eintrag', 'Einträge')}, neueste zuerst):"]
        lines += [f"- {fmt_time(e['ts'])}: {state_de.get(e['state'], e['state'])} (Kamera {e['camera_id']})"
                  for e in entries]
        if not entries:
            lines[-1] = f"Kein Verlauf für {name} gespeichert."
        return ok("\n".join(lines), {"item_id": item_id, "entries": entries})
    except BackendError as e:
        return fail(str(e))


@mcp.tool(annotations=READ_ONLY)
async def list_cameras(ctx: Context) -> CallToolResult:
    """Listet alle Kameras mit Raum, Zustand (online/offline/…) und ob die Gegenstandserkennung aktiv ist."""
    try:
        cams = [{"id": c.get("id"), "name": c.get("name"), "room": c.get("room"),
                 "state": (c.get("status") or {}).get("state"),
                 "inventory_enabled": bool(c.get("inventory_enabled"))} for c in await _cameras(ctx)]
        if not cams:
            return ok("Keine Kameras eingerichtet.", {"cameras": []})
        lines = [f"- {c['name']} (id {c['id']}, Raum {c['room'] or '–'}): {c['state'] or 'unbekannt'}"
                 f"{', Gegenstandserkennung an' if c['inventory_enabled'] else ''}" for c in cams]
        return ok(f"{n_de(len(cams), 'Kamera', 'Kameras')}:\n" + "\n".join(lines), {"cameras": cams})
    except BackendError as e:
        return fail(str(e))


def _shrink_jpeg(data: bytes, max_width: int) -> bytes:
    """Verkleinert große Standbilder (z. B. 2880×1616) für das Modell; bei Fehlern das Original."""
    try:
        from PIL import Image

        with Image.open(io.BytesIO(data)) as im:
            if im.width <= max_width:
                return data
            h = round(im.height * max_width / im.width)
            out = io.BytesIO()
            im.convert("RGB").resize((max_width, h), Image.LANCZOS).save(out, "JPEG", quality=85)
            return out.getvalue()
    except Exception:  # noqa: BLE001
        return data


@mcp.tool(annotations=READ_ONLY)
async def get_camera_snapshot(ctx: Context, camera_id: Optional[int] = None,
                              camera_name: Optional[str] = None) -> CallToolResult:
    """Liefert das aktuelle Bild einer Kamera (JPEG). Kamera per camera_id oder camera_name (auch Raumname)."""
    try:
        cam = _find_camera(await _cameras(ctx), camera_id, camera_name)
        resp = await api(ctx, f"/cameras/{cam['id']}/snapshot.jpg", raw=True,
                         not_found=f"Kamera {cam.get('name')} ist unbekannt.")
        img = _shrink_jpeg(resp.content, SNAPSHOT_MAX_WIDTH)
        now = datetime.now(LOCAL_TZ).strftime("%d.%m.%Y %H:%M:%S")
        text = f"Kamera {cam.get('name')}" + (f" (Raum {cam['room']})" if cam.get("room") else "") + f", Aufnahme {now}"
        return CallToolResult(content=[
            ImageContent(type="image", data=base64.b64encode(img).decode(), mime_type="image/jpeg"),
            TextContent(type="text", text=text),
        ])
    except BackendError as e:
        msg = str(e)
        if msg.startswith("Backend-Fehler"):
            msg = "Kamerabild nicht verfügbar (Kamera offline?)."
        return fail(msg)


@mcp.tool(annotations=READ_ONLY)
async def who_is_where(ctx: Context) -> CallToolResult:
    """Wer (Personen, Hunde, Katzen) ist gerade wo zu sehen? Mit erkannter Identität, Kamera, Raum und Zeit."""
    try:
        cams = {c.get("id"): c for c in await _cameras(ctx)}
        data = await api(ctx, "/events", {"active": "true", "label": PEOPLE_LABELS, "signed": 1, "limit": 100})
        label_de = {"person": "Person", "dog": "Hund", "cat": "Katze"}
        people = []
        for ev in data.get("items", []):
            ident = ev.get("identity") or {}
            cam = cams.get(ev.get("camera_id")) or {}
            zone_names = {z.get("id"): z.get("name") for z in cam.get("zones") or []}
            people.append({
                "display_name": ident.get("display_name") or f"Unbekannt ({label_de.get(ev.get('label'), ev.get('label'))})",
                "identity_id": ident.get("id"),
                "kind": ident.get("kind"),
                "label": ev.get("label"),
                "camera_id": ev.get("camera_id"),
                "camera_name": ev.get("camera_name") or cam.get("name"),
                "room": cam.get("room"),
                "since": ev.get("start_ts"),
                "zones": [zone_names.get(z, z) for z in ev.get("zones") or []],
                "thumb_url": ev.get("thumb_url"),
            })
        if not people:
            return ok("Derzeit ist niemand zu sehen.", {"people": []})
        lines = []
        for p in people:
            line = f"- {p['display_name']}: {_where(p['camera_name'], p['room'])}"
            if p["zones"]:
                line += ", Zone " + ", ".join(map(str, p["zones"]))
            line += f", seit {fmt_time(p['since'])}"
            lines.append(line)
        return ok(f"{len(people)} gerade sichtbar:\n" + "\n".join(lines), {"people": people})
    except BackendError as e:
        return fail(str(e))


# ------------------------------------------------------------------ HTTP-App mit Auth


class BearerAuth:
    """ASGI-Wrapper: prüft den Bearer-Key vor jeder MCP-Anfrage; /health bleibt offen."""

    def __init__(self, app):
        self.app = app
        self._cache: dict[str, float] = {}  # sha256(key) → gültig bis

    async def _send_json(self, send, status: int, body: dict, extra: Optional[list] = None) -> None:
        raw = json.dumps(body, ensure_ascii=False).encode()
        headers = [(b"content-type", b"application/json; charset=utf-8"),
                   (b"content-length", str(len(raw)).encode())] + (extra or [])
        await send({"type": "http.response.start", "status": status, "headers": headers})
        await send({"type": "http.response.body", "body": raw})

    async def _check(self, auth: str) -> int:
        digest = hashlib.sha256(auth.encode()).hexdigest()
        now = time.monotonic()
        if self._cache.get(digest, 0) > now:
            return 200
        try:
            resp = await http().get("/api/auth/me", headers={"Authorization": auth})
        except httpx.HTTPError:
            return 503
        if resp.status_code == 200:
            if len(self._cache) > 1000:
                self._cache = {k: v for k, v in self._cache.items() if v > now}
            self._cache[digest] = now + AUTH_CACHE_S
            return 200
        self._cache.pop(digest, None)
        return 401 if resp.status_code in (401, 403) else 503

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        if scope.get("path") == "/health":
            return await self._send_json(send, 200, {"status": "ok"})
        auth = ""
        for k, v in scope.get("headers") or []:
            if k == b"authorization":
                auth = v.decode("latin-1").strip()
                break
        challenge = [(b"www-authenticate", b'Bearer realm="VisionGuard MCP"')]
        if not auth.lower().startswith("bearer ") or len(auth) < 12:
            return await self._send_json(send, 401, {"detail": "API-Key fehlt (Authorization: Bearer vgt_…)"},
                                         challenge)
        status = await self._check(auth)
        if status == 401:
            return await self._send_json(send, 401, {"detail": AUTH_ERROR}, challenge)
        if status != 200:
            return await self._send_json(send, 503, {"detail": "VisionGuard-Backend ist nicht erreichbar."})
        return await self.app(scope, receive, send)


def build_app():
    if ALLOWED_HOSTS:
        origins = [f"{p}://{h}" for h in ALLOWED_HOSTS for p in ("http", "https")]
        security = TransportSecuritySettings(enable_dns_rebinding_protection=True,
                                             allowed_hosts=ALLOWED_HOSTS, allowed_origins=origins)
    else:
        security = TransportSecuritySettings(enable_dns_rebinding_protection=False)
    inner = mcp.streamable_http_app(streamable_http_path="/mcp", stateless_http=True, json_response=True,
                                    transport_security=security, host="0.0.0.0")
    return BearerAuth(inner)


app = build_app()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    # access_log aus: Zugriffslogs enthalten zwar keine Header, aber wir halten die Ausgabe knapp
    uvicorn.run(app, host="0.0.0.0", port=PORT, log_level="warning", access_log=False,
                proxy_headers=True, forwarded_allow_ips="*")
