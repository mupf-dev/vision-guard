"""Gegenstände (Inventar): find_object, list_visible_objects, get_object_history."""

from __future__ import annotations

from typing import Optional

from mcp.server.mcpserver import Context, MCPServer
from mcp.types import CallToolResult

from .common import (READ_ONLY, BackendError, api, cameras, compact_item, fail, find_camera, fmt_time, item_sentence,
                     mapped, ok, t)


def register(mcp: MCPServer) -> None:
    @mcp.tool(annotations=READ_ONLY)
    async def find_object(query: str, ctx: Context, limit: int = 5) -> CallToolResult:
        """Find an object (query in English or the system language, e.g. "Where is my remote control?") and tell
        where it is visible right now or was last seen – with camera, room, probable location and time. Answers
        come in the configured system language."""
        try:
            q = (query or "").strip()
            if not q:
                return fail(t("inventory.query_required"))
            limit = max(1, min(int(limit), 20))
            data = await api(ctx, "/inventory/search", {"q": q, "limit": limit, "signed": 1})
            items = [compact_item(i) for i in data.get("items", [])]
            answer = data.get("answer") or (item_sentence(items[0]) if items else t("inventory.not_known", query=q))
            lines = [answer]
            if items:
                lines.append(t("inventory.hits"))
                lines += [f"- {item_sentence(c)}" for c in items]
            return ok("\n".join(lines), {"query": data.get("query", q), "answer": answer, "items": items})
        except BackendError as e:
            return fail(str(e))


    @mcp.tool(annotations=READ_ONLY)
    async def list_visible_objects(ctx: Context, camera_id: Optional[int] = None, room: Optional[str] = None,
                                   camera_name: Optional[str] = None) -> CallToolResult:
        """List all objects that are visible right now – optionally only for one camera (camera_id or
        camera_name) or one room (room)."""
        try:
            if camera_id is None and camera_name:
                camera_id = find_camera(await cameras(ctx), None, camera_name)["id"]
            data = await api(ctx, "/inventory", {"state": "present", "signed": 1, "camera_id": camera_id,
                                                 "room": room or None, "limit": 200})
            items = [compact_item(i) for i in data.get("items", [])]
            scope = []
            if camera_id is not None:
                scope.append(t("common.camera", name=camera_name or camera_id))
            if room:
                scope.append(t("common.room", name=room))
            suffix = f" ({', '.join(scope)})" if scope else ""
            if not items:
                text = t("inventory.none_visible", scope=suffix)
            else:
                text = (t("inventory.n_visible", n=len(items), scope=suffix) + "\n"
                        + "\n".join(f"- {item_sentence(c)}" for c in items))
            return ok(text, {"total": data.get("total", len(items)), "items": items})
        except BackendError as e:
            return fail(str(e))


    @mcp.tool(annotations=READ_ONLY)
    async def get_object_history(item_id: int, ctx: Context, limit: int = 20) -> CallToolResult:
        """History of an object (id from find_object/list_visible_objects): when and where it was visible or
        missing, newest first."""
        try:
            limit = max(1, min(int(limit), 500))
            nf = t("inventory.unknown_id", id=item_id)
            item = await api(ctx, f"/inventory/{item_id}", {"signed": 1}, not_found=nf)
            hist = await api(ctx, f"/inventory/{item_id}/history", {"limit": limit, "signed": 1}, not_found=nf)
            entries = [{"ts": h.get("ts"), "state": h.get("state"), "camera_id": h.get("camera_id"),
                        "zones": h.get("zones") or [], "crop_url": h.get("crop_url")} for h in hist]
            name = item.get("name") or item.get("name_de") or item.get("label")
            lines = [item_sentence(compact_item(item)), t("inventory.history_head", n=len(entries))]
            lines += [f"- {fmt_time(e['ts'])}: {mapped('inventory.state', e['state'])} "
                      f"({t('common.camera', name=e['camera_id'])})" for e in entries]
            if not entries:
                lines[-1] = t("inventory.no_history", name=name)
            return ok("\n".join(lines), {"item_id": item_id, "entries": entries})
        except BackendError as e:
            return fail(str(e))
