"""Fernseher: MCP-Tool `show_on_tv` (CONTRACT „Nachträge: Fernseher“, TV.D).

    show_on_tv(camera_id?/camera_name?, tv?, duration_s=60) → TvStatus   (write, POST /tv/{id}/show)
"""

from __future__ import annotations

from typing import Optional

from mcp.server.mcpserver import Context, MCPServer
from mcp.types import CallToolResult

from .common import WRITE, BackendError, api, api_send, cameras, fail, find_camera, ok, require_write


def register(mcp: MCPServer) -> None:
    @mcp.tool(annotations=WRITE)
    async def show_on_tv(ctx: Context, camera_id: Optional[int] = None, camera_name: Optional[str] = None,
                         tv: Optional[str] = None, duration_s: int = 60) -> CallToolResult:
        """Zeigt das Livebild einer Kamera für `duration_s` Sekunden (10–3600) auf einem Fernseher im Heimnetz
        (Vollbild, unterbricht das laufende Programm; danach zurück). `tv` = Name oder ID des Fernsehers, leer = der
        erste aktive. Braucht einen API-Key mit Schreibrecht."""
        try:
            await require_write(ctx)
            cam = find_camera(await cameras(ctx), camera_id, camera_name)
            tvs = [t for t in await api(ctx, "/tv") if t.get("enabled")]
            if not tvs:
                return fail("Kein aktiver Fernseher eingerichtet (Einstellungen → Fernseher).")
            if tv:
                want = tv.strip().casefold()
                hit = next((t for t in tvs if t["id"].casefold() == want or t["name"].casefold() == want), None)
                if hit is None:
                    return fail(f"Fernseher „{tv}“ unbekannt. Vorhanden: {', '.join(t['name'] for t in tvs)}.")
            else:
                hit = tvs[0]
            st = await api_send(ctx, "POST", f"/tv/{hit['id']}/show",
                                json={"camera_id": cam["id"], "duration_s": max(10, min(int(duration_s), 3600))})
            return ok(f"{hit['name']} zeigt jetzt {cam.get('name')} für {int(duration_s)} s.", st)
        except BackendError as e:
            return fail(str(e))
