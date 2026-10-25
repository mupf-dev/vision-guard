"""Gegenstände (Inventar): find_object, list_visible_objects, get_object_history."""

from __future__ import annotations

from typing import Optional

from mcp.server.mcpserver import Context, MCPServer
from mcp.types import CallToolResult

from .common import (READ_ONLY, BackendError, api, cameras, compact_item, fail, find_camera, fmt_time, item_sentence,
                     n_de, ok)


def register(mcp: MCPServer) -> None:
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
                camera_id = find_camera(await cameras(ctx), None, camera_name)["id"]
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
