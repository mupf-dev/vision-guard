"""W1 (B2): MCP-Tool `what_changed` – „Was hat sich verändert?“ im Gegenstandsgedächtnis.

Vertrag (CONTRACT.md „Nachträge: Welle 1“, W1.E/W1.G):
    what_changed(since="today", until=None, camera_id=None, camera_name=None, room=None, limit=30)
    → InventoryChanges aus GET /inventory/changes?signed=1 (Gegenstände kompakt wie find_object).
"""

from __future__ import annotations

from typing import Any, Optional

from mcp.server.mcpserver import Context, MCPServer
from mcp.types import CallToolResult

from .common import READ_ONLY, BackendError, api, cameras, compact_item, fail, find_camera, fmt_time, mapped, ok, t


def _snap(s: Optional[dict]) -> Optional[dict]:
    if not s:
        return None
    return {"ts": s.get("ts"), "state": s.get("state"), "camera_id": s.get("camera_id"),
            "zones": s.get("zone_names") or s.get("zones") or [], "crop_url": s.get("crop_url")}


def _compact(ch: dict) -> dict[str, Any]:
    return {"type": ch.get("type"), "at": ch.get("at"), "text": ch.get("text"),
            "item": compact_item(ch.get("item") or {}), "before": _snap(ch.get("before")),
            "after": _snap(ch.get("after"))}


def _line(c: dict) -> str:
    line = f"- [{mapped('changes.type', c['type'])} {fmt_time(c.get('at'))}] {c.get('text')} [id {c['item'].get('id')}]"
    img = (c.get("after") or {}).get("crop_url") or (c.get("before") or {}).get("crop_url")
    if img:
        line += " " + t("common.image", url=img)
    return line


def register(mcp: MCPServer) -> None:
    @mcp.tool(annotations=READ_ONLY)
    async def what_changed(ctx: Context, since: str = "today", until: Optional[str] = None,
                           camera_id: Optional[int] = None, camera_name: Optional[str] = None,
                           room: Optional[str] = None, limit: int = 30) -> CallToolResult:
        """What changed among the objects? Lists newly appeared, disappeared and moved objects (with before/after
        crops) and objects with an unclear state (camera without analysis).
        `since`/`until`: ISO-8601 or relative like "3h", "90m", "2d", or "today" (00:00 today), "morning"
        (06:00 today), "yesterday" (00:00 yesterday). Optionally only one camera (camera_id/camera_name) or one
        room (room)."""
        try:
            if camera_id is None and camera_name:
                camera_id = find_camera(await cameras(ctx), None, camera_name)["id"]
            limit = max(1, min(int(limit), 200))
            data = await api(ctx, "/inventory/changes", {
                "since": (since or "today").strip(), "until": (until or "").strip() or None,
                "camera_id": camera_id, "room": room or None, "limit": limit, "signed": 1})
            changes = [_compact(ch) for ch in data.get("changes", [])]
            counts = data.get("counts") or {}
            structured = {"since": data.get("since"), "until": data.get("until"), "summary": data.get("summary"),
                          "counts": counts, "changes": changes}
            lines = [data.get("summary") or t("changes.no_data")]
            lines += [_line(c) for c in changes]
            total = sum(int(v or 0) for v in counts.values())
            if total > len(changes):
                lines.append(t("changes.truncated", shown=len(changes), total=total))
            return ok("\n".join(lines), structured)
        except BackendError as e:
            return fail(str(e))
