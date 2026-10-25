"""Personen und Tiere: who_is_where."""

from __future__ import annotations


from mcp.server.mcpserver import Context, MCPServer
from mcp.types import CallToolResult

from .common import READ_ONLY, BackendError, api, cameras, fail, fmt_time, ok, where

PEOPLE_LABELS = "person,dog,cat"



def register(mcp: MCPServer) -> None:
    @mcp.tool(annotations=READ_ONLY)
    async def who_is_where(ctx: Context) -> CallToolResult:
        """Wer (Personen, Hunde, Katzen) ist gerade wo zu sehen? Mit erkannter Identität, Kamera, Raum und Zeit."""
        try:
            cams = {c.get("id"): c for c in await cameras(ctx)}
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
                line = f"- {p['display_name']}: {where(p['camera_name'], p['room'])}"
                if p["zones"]:
                    line += ", Zone " + ", ".join(map(str, p["zones"]))
                line += f", seit {fmt_time(p['since'])}"
                lines.append(line)
            return ok(f"{len(people)} gerade sichtbar:\n" + "\n".join(lines), {"people": people})
        except BackendError as e:
            return fail(str(e))
