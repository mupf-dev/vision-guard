"""Kameras: list_cameras, get_camera_snapshot."""

from __future__ import annotations

import base64
import io
from datetime import datetime
from typing import Optional

from mcp.server.mcpserver import Context, MCPServer
from mcp.types import CallToolResult, ImageContent, TextContent

from .common import READ_ONLY, BackendError, LOCAL_TZ, api, cameras, fail, find_camera, n_de, ok

SNAPSHOT_MAX_WIDTH = 1600


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



def register(mcp: MCPServer) -> None:
    @mcp.tool(annotations=READ_ONLY)
    async def list_cameras(ctx: Context) -> CallToolResult:
        """Listet alle Kameras mit Raum, Zustand (online/offline/…) und ob die Gegenstandserkennung aktiv ist."""
        try:
            cams = [{"id": c.get("id"), "name": c.get("name"), "room": c.get("room"),
                     "state": (c.get("status") or {}).get("state"),
                     "inventory_enabled": bool(c.get("inventory_enabled"))} for c in await cameras(ctx)]
            if not cams:
                return ok("Keine Kameras eingerichtet.", {"cameras": []})
            lines = [f"- {c['name']} (id {c['id']}, Raum {c['room'] or '–'}): {c['state'] or 'unbekannt'}"
                     f"{', Gegenstandserkennung an' if c['inventory_enabled'] else ''}" for c in cams]
            return ok(f"{n_de(len(cams), 'Kamera', 'Kameras')}:\n" + "\n".join(lines), {"cameras": cams})
        except BackendError as e:
            return fail(str(e))


    @mcp.tool(annotations=READ_ONLY)
    async def get_camera_snapshot(ctx: Context, camera_id: Optional[int] = None,
                                  camera_name: Optional[str] = None) -> CallToolResult:
        """Liefert das aktuelle Bild einer Kamera (JPEG). Kamera per camera_id oder camera_name (auch Raumname)."""
        try:
            cam = find_camera(await cameras(ctx), camera_id, camera_name)
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
