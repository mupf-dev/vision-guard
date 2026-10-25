"""Gemeinsame Bausteine der MCP-Tools: Backend-Zugriff (Bearer-Key durchreichen), Formatierung, Ergebnisse.

Jedes Themen-Modul unter `tools/` importiert von hier und bringt eine Funktion `register(mcp)` mit.
"""

from __future__ import annotations

import hashlib
import logging
import os
import time
from datetime import datetime, timezone
from typing import Any, Optional
from zoneinfo import ZoneInfo

import httpx
from mcp.server.mcpserver import Context
from mcp.types import CallToolResult, TextContent, ToolAnnotations

log = logging.getLogger("vg.mcp")

BACKEND_URL = os.environ.get("VG_BACKEND_URL", "http://backend:8000").rstrip("/")
try:
    LOCAL_TZ = ZoneInfo(os.environ.get("TZ") or "Europe/Berlin")
except Exception:  # noqa: BLE001
    LOCAL_TZ = ZoneInfo("Europe/Berlin")

READ_ONLY = ToolAnnotations(read_only_hint=True, open_world_hint=False)
# W2: Tools mit Wirkung (quittieren, Rückfrage beantworten …) – vorher `await require_write(ctx)`
WRITE = ToolAnnotations(read_only_hint=False, destructive_hint=False, idempotent_hint=True, open_world_hint=False)
WRITE_ERROR = "API-Key ohne Schreibrecht."
SCOPE_CACHE_S = 30.0
_scopes: dict[str, tuple[float, list[str]]] = {}   # sha256(Authorization) → (gültig bis, scopes)

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


async def api_send(ctx: Context, method: str, path: str, *, json: Optional[dict] = None,
                   params: Optional[dict] = None, not_found: Optional[str] = None) -> Any:
    """W2: Backend-Aufruf mit Wirkung (POST/PUT/PATCH/DELETE) mit dem Key des Clients. Vorher require_write()."""
    params = {k: v for k, v in (params or {}).items() if v is not None}
    try:
        resp = await http().request(method.upper(), "/api" + path, params=params, json=json,
                                    headers={"Authorization": _auth_header(ctx)})
    except httpx.HTTPError as e:
        log.warning("Backend nicht erreichbar (%s %s): %s", method, path, type(e).__name__)
        raise BackendError("VisionGuard-Backend ist nicht erreichbar.") from e
    if resp.status_code == 401:
        raise BackendError(AUTH_ERROR)
    if resp.status_code == 403:
        raise BackendError(WRITE_ERROR if "write" in _detail(resp) else AUTH_ERROR)
    if resp.status_code == 404:
        raise BackendError(not_found or "Nicht gefunden.")
    if resp.status_code >= 400:
        raise BackendError(f"Backend-Fehler: {_detail(resp)}")
    if resp.status_code == 204 or not resp.content:
        return None
    return resp.json()


async def scopes(ctx: Context) -> list[str]:
    """Scopes des Keys aus GET /auth/me (30 s gecacht, Schlüssel = SHA-256 des Headers)."""
    auth = _auth_header(ctx)
    digest = hashlib.sha256(auth.encode()).hexdigest()
    now = time.monotonic()
    hit = _scopes.get(digest)
    if hit and hit[0] > now:
        return hit[1]
    me = await api(ctx, "/auth/me")
    sc = [str(x) for x in (me.get("scopes") or [])] if isinstance(me, dict) else []
    if len(_scopes) > 1000:
        for k in [k for k, v in _scopes.items() if v[0] <= now]:
            _scopes.pop(k, None)
    _scopes[digest] = (now + SCOPE_CACHE_S, sc)
    return sc


async def require_write(ctx: Context) -> None:
    """W2-Regel für Schreibwerkzeuge: ohne Scope `write` → BackendError „API-Key ohne Schreibrecht.“"""
    if "write" not in await scopes(ctx):
        raise BackendError(WRITE_ERROR)


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


def where(camera_name: Any, room: Any) -> str:
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
    place = where(c.get("camera_name"), c.get("room"))
    if c.get("state") == "present":
        parts = [f"{name}: aktuell sichtbar in {place}"]
        if c.get("relation_text"):
            parts.append(str(c["relation_text"]))
        if c.get("zones"):
            parts.append("Zone " + ", ".join(map(str, c["zones"])))
        if c.get("first_seen"):
            parts.append(f"seit {fmt_time(c['first_seen'])}")
        text = ", ".join(parts)
    else:
        text = f"{name}: zuletzt gesehen {fmt_ago(c.get('last_seen'))} ({fmt_time(c.get('last_seen'))}) in {place}"
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


async def cameras(ctx: Context) -> list[dict]:
    data = await api(ctx, "/cameras")
    return data if isinstance(data, list) else data.get("items", [])


def find_camera(cams: list[dict], camera_id: Optional[int], camera_name: Optional[str]) -> dict:
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
