"""W3 (B1): MCP-Tool `search_history` – Haus-Zeitmaschine („Wann stand der rote Karton im Flur?“).

Vertrag (CONTRACT.md „Nachträge: Welle 3“, W3.H):
    search_history(query, since=None, until=None, camera=None, limit=5, include_image=False)
        → {query, items:[{camera_name, room, start_ts, end_ts, best_ts, score, thumb_url}]} aus GET /history/search?signed=1
Text in der Systemsprache (z. B. „Am Di 14:03–14:21 im Flur, Übereinstimmung 71 %“), Medien-URLs signiert, optional das
Vorschaubild des besten Treffers als ImageContent.
"""

from __future__ import annotations

import base64
from datetime import datetime
from typing import Any, Optional

import httpx
from mcp.server.mcpserver import Context, MCPServer
from mcp.types import CallToolResult, ImageContent, TextContent

from .common import (LOCAL_TZ, READ_ONLY, BackendError, _auth_header, _parse_ts, api, cameras, fail, find_camera,
                     http, ok, t, t_list)
from .notifications import parse_since


def _span(start: Any, end: Any) -> str:
    a, b = _parse_ts(start), _parse_ts(end)
    if a is None:
        return "?"
    la = a.astimezone(LOCAL_TZ)
    today = datetime.now(LOCAL_TZ).date()
    days = t_list("common.weekdays_short") or ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    dm = t("common.day_month_format")
    day = t("history.today") if la.date() == today else t("history.on_day", weekday=days[la.weekday()],
                                                           date=la.strftime(dm))
    text = f"{day} {la:%H:%M}"
    if b is not None:
        lb = b.astimezone(LOCAL_TZ)
        if (lb - la).total_seconds() >= 60:
            text += f"–{lb:%H:%M}" if lb.date() == la.date() else " " + t("history.until", time=lb.strftime(dm + " %H:%M"))
    return text


def _place(it: dict) -> str:
    cam, room = it.get("camera_name"), it.get("room")
    if room and room != cam:
        return t("history.place_room", room=room, camera=cam)
    return t("common.camera", name=cam)


async def _thumb(ctx: Context, url: str) -> Optional[bytes]:
    """Vorschau über das Backend laden (signierte URL, relativ oder absolut)."""
    path = url
    if "://" in path:                          # absolut (public_base_url) → nur Pfad + Query ans Backend
        path = "/" + path.split("://", 1)[1].split("/", 1)[1]
    try:
        r = await http().get(path, headers={"Authorization": _auth_header(ctx)})
    except httpx.HTTPError:
        return None
    return r.content if r.status_code == 200 and r.content else None


def register(mcp: MCPServer) -> None:
    @mcp.tool(annotations=READ_ONLY)
    async def search_history(ctx: Context, query: str, since: Optional[str] = None, until: Optional[str] = None,
                             camera: Optional[str] = None, limit: int = 5,
                             include_image: bool = False) -> CallToolResult:
        """Search the cameras' image memory ("time machine") for a description, e.g. "red box in the hallway",
        "person with a backpack", "dog on the sofa" (English or German; short, concrete descriptions work best).
        Returns time spans per camera with a match score (0–100) and a signed preview image (256 px). Only
        cameras with the time machine enabled; small objects are found more reliably on cameras with tiling.
        `since`/`until`: ISO-8601 or relative ("3h", "2d", "today", "yesterday"). `camera`: camera or room name.
        `include_image`: also send the preview of the best match."""
        try:
            q = (query or "").strip()
            if not q:
                return fail(t("history.query_required"))
            try:
                t_since, t_until = parse_since(since), parse_since(until)
            except ValueError as e:
                return fail(str(e))
            params: dict[str, Any] = {"q": q[:200], "limit": max(1, min(int(limit), 20)), "signed": 1,
                                      "since": t_since.isoformat() if t_since else None,
                                      "until": t_until.isoformat() if t_until else None}
            if camera and camera.strip():
                cams = await cameras(ctx)
                needle = camera.strip().casefold()
                if any(str(c.get("room") or "").casefold() == needle for c in cams) and \
                        sum(1 for c in cams if str(c.get("room") or "").casefold() == needle) > 1:
                    params["room"] = camera.strip()
                else:
                    params["camera_id"] = find_camera(cams, None, camera)["id"]
            data = await api(ctx, "/history/search", params)
            items = [{"camera_id": it.get("camera_id"), "camera_name": it.get("camera_name"),
                      "room": it.get("room"), "start_ts": it.get("start_ts"), "end_ts": it.get("end_ts"),
                      "best_ts": it.get("best_ts"), "score": it.get("score"), "frames": it.get("frames"),
                      "thumb_url": it.get("thumb_url")} for it in data.get("items", [])]
            structured = {"query": q, "queries": data.get("queries") or [q], "model": data.get("model"),
                          "items": items}
            if not items:
                return ok(t("history.nothing", query=q), structured)
            lines = [t("history.head", query=q, n=len(items))]
            for it in items:
                span = _span(it["start_ts"], it["end_ts"])
                line = "- " + t("history.line", span=span[:1].upper() + span[1:], place=_place(it), score=it["score"])
                if it.get("thumb_url"):
                    line += " " + t("common.image", url=it["thumb_url"])
                lines.append(line)
            lines.append(t("history.note"))
            content: list = [TextContent(type="text", text="\n".join(lines))]
            if include_image and items[0].get("thumb_url"):
                img = await _thumb(ctx, items[0]["thumb_url"])
                if img:
                    content.append(ImageContent(type="image", data=base64.b64encode(img).decode(),
                                                mime_type="image/jpeg"))
            return CallToolResult(content=content, structured_content=structured)
        except BackendError as e:
            return fail(str(e))
