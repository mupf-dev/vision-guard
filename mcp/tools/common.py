"""Gemeinsame Bausteine der MCP-Tools: Backend-Zugriff (Bearer-Key durchreichen), Sprache, Formatierung, Ergebnisse.

Jedes Themen-Modul unter `tools/` importiert von hier und bringt eine Funktion `register(mcp)` mit.
Antworttexte kommen über `t("<bereich>.<schlüssel>")` aus `locales/<code>.json` (Systemsprache des Backends);
jeder Backend-Aufruf schickt `Accept-Language` in dieser Sprache (I18N.D).
"""

from __future__ import annotations

import hashlib
import logging
import os
import time
from typing import Any, Optional

import httpx
from mcp.server.mcpserver import Context
from mcp.types import CallToolResult, TextContent, ToolAnnotations

import i18n
from i18n import LOCAL_TZ, fmt_ago, fmt_datetime, fmt_num, fmt_time, mapped, t, t_list  # noqa: F401 (Re-Export)

log = logging.getLogger("vg.mcp")

BACKEND_URL = os.environ.get("VG_BACKEND_URL", "http://backend:8000").rstrip("/")

READ_ONLY = ToolAnnotations(read_only_hint=True, open_world_hint=False)
# W2: Tools mit Wirkung (quittieren, Rückfrage beantworten …) – vorher `await require_write(ctx)`
WRITE = ToolAnnotations(read_only_hint=False, destructive_hint=False, idempotent_hint=True, open_world_hint=False)
SCOPE_CACHE_S = 30.0
_scopes: dict[str, tuple[float, list[str]]] = {}   # sha256(Authorization) → (gültig bis, scopes)

LANG_CACHE_S = 60.0
LANG_RETRY_S = 10.0
_lang_until = 0.0


def auth_error() -> str:
    return t("common.auth_error")


def write_error() -> str:
    return t("common.write_error")


def unreachable_error() -> str:
    return t("common.backend_unreachable")


_http: Optional[httpx.AsyncClient] = None


async def _accept_language(request: httpx.Request) -> None:
    """Jede Backend-Anfrage in der Antwortsprache (Texte wie status_text, answer, display_name)."""
    request.headers["Accept-Language"] = i18n.current()


def http() -> httpx.AsyncClient:
    global _http
    if _http is None:
        _http = httpx.AsyncClient(base_url=BACKEND_URL, timeout=httpx.Timeout(20.0, connect=5.0),
                                  event_hooks={"request": [_accept_language]})
    return _http


async def ensure_language(auth: str) -> str:
    """Systemsprache aus `GET /api/settings` → `language` (60 s gecacht, Rückfall Englisch). Mit dem Key des Clients."""
    global _lang_until
    now = time.monotonic()
    if now < _lang_until:
        return i18n.current()
    _lang_until = now + LANG_RETRY_S
    try:
        resp = await http().get("/api/settings", headers={"Authorization": auth}, timeout=5.0)
        if resp.status_code == 200:
            data = resp.json()
            i18n.set_lang(data.get("language") if isinstance(data, dict) else None)
            _lang_until = now + LANG_CACHE_S
    except (httpx.HTTPError, ValueError):
        pass
    return i18n.current()


# ------------------------------------------------------------------ Backend-Zugriff


class BackendError(Exception):
    """Fehler mit Meldung in der Antwortsprache, wird als Tool-Ergebnis mit isError zurückgegeben."""

    def __init__(self, message: str, status: Optional[int] = None):
        super().__init__(message)
        self.status = status


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
        raise BackendError(auth_error())
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
        raise BackendError(unreachable_error()) from e
    if resp.status_code in (401, 403):
        raise BackendError(auth_error(), resp.status_code)
    if resp.status_code == 404:
        raise BackendError(not_found or t("common.not_found"), 404)
    if resp.status_code >= 400:
        raise BackendError(t("common.backend_error", detail=_detail(resp)), resp.status_code)
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
        raise BackendError(unreachable_error()) from e
    if resp.status_code == 401:
        raise BackendError(auth_error(), 401)
    if resp.status_code == 403:
        raise BackendError(write_error() if "write" in _detail(resp) else auth_error(), 403)
    if resp.status_code == 404:
        raise BackendError(not_found or t("common.not_found"), 404)
    if resp.status_code >= 400:
        raise BackendError(t("common.backend_error", detail=_detail(resp)), resp.status_code)
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
    """W2-Regel für Schreibwerkzeuge: ohne Scope `write` → BackendError (`common.write_error`)."""
    if "write" not in await scopes(ctx):
        raise BackendError(write_error(), 403)


# ------------------------------------------------------------------ Formatierung

_parse_ts = i18n.parse_ts   # bisheriger Name (reports, history)


def where(camera_name: Any, room: Any) -> str:
    if room:
        return t("common.where_room", camera=camera_name or "?", room=room)
    return t("common.where", camera=camera_name or "?")


def compact_item(it: dict) -> dict:
    rel = it.get("relation") or None
    return {
        "id": it.get("id"),
        "name_de": it.get("name_de") or it.get("label"),
        "name": it.get("name") or it.get("name_de") or it.get("label"),   # I18N.C: Anzeigename in der Antwortsprache
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
        # W2 (B4): Name des eigenen Exemplars („Annas Rucksack“) oder None
        "identity_name": (it.get("identity") or {}).get("display_name") if isinstance(it.get("identity"), dict) else None,
    }


def item_sentence(c: dict) -> str:
    """„Fernbedienung: aktuell sichtbar in Kamera Wohnzimmer (Raum Wohnzimmer), vermutlich auf dem Tisch, seit 14:02“."""
    name = c.get("name") or c.get("name_de") or c.get("label") or t("inventory.item")
    if c.get("identity_name"):
        name = f"{c['identity_name']} ({name})"
    place = where(c.get("camera_name"), c.get("room"))
    if c.get("state") == "present":
        parts = [t("inventory.visible_in", name=name, place=place)]
        if c.get("relation_text"):
            parts.append(str(c["relation_text"]))
        if c.get("zones"):
            parts.append(t("common.zone", zones=", ".join(map(str, c["zones"]))))
        if c.get("first_seen"):
            parts.append(t("common.since", time=fmt_time(c["first_seen"])))
        text = ", ".join(parts)
    else:
        text = t("inventory.last_seen_in", name=name, ago=fmt_ago(c.get("last_seen")),
                 time=fmt_time(c.get("last_seen")), place=place)
        text += t("inventory.stale_suffix") if c.get("state") == "stale" else t("inventory.missing_suffix")
    text += f" [id {c.get('id')}]"
    if c.get("crop_url"):
        text += " " + t("common.image", url=c["crop_url"])
    return text


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
        raise BackendError(t("common.camera_unknown_id", id=camera_id))
    needle = (camera_name or "").strip().casefold()
    if not needle:
        raise BackendError(t("common.camera_required"))
    for key in ("name", "room"):
        hits = [c for c in cams if str(c.get(key) or "").casefold() == needle]
        if len(hits) == 1:
            return hits[0]
    hits = [c for c in cams if needle in str(c.get("name") or "").casefold()
            or needle in str(c.get("room") or "").casefold()]
    if len(hits) == 1:
        return hits[0]
    names = ", ".join(f"{c.get('name')} (id {c.get('id')})" for c in cams) or t("common.none")
    if not hits:
        raise BackendError(t("common.camera_unknown_name", name=camera_name, names=names))
    raise BackendError(t("common.camera_ambiguous", name=camera_name, names=names))
