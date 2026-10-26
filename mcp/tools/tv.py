"""Fernseher: MCP-Tool `show_on_tv` (CONTRACT „Nachträge: Fernseher“, TV.D).

    show_on_tv(camera_id?/camera_name?, tv?, duration_s=60) → TvStatus   (write, POST /tv/{id}/show)
"""

from __future__ import annotations

from typing import Optional

from mcp.server.mcpserver import Context, MCPServer
from mcp.types import CallToolResult

from .common import WRITE, BackendError, api, api_send, cameras, fail, find_camera, ok, require_write, t


def register(mcp: MCPServer) -> None:
    @mcp.tool(annotations=WRITE)
    async def show_on_tv(ctx: Context, camera_id: Optional[int] = None, camera_name: Optional[str] = None,
                         tv: Optional[str] = None, duration_s: int = 60) -> CallToolResult:
        """Show the live image of a camera for `duration_s` seconds (10–3600) on a TV in the home network
        (full screen, interrupts the current programme; switches back afterwards). `tv` = name or id of the TV,
        empty = the first active one. Requires an API key with write permission."""
        try:
            await require_write(ctx)
            cam = find_camera(await cameras(ctx), camera_id, camera_name)
            tvs = [tv_ for tv_ in await api(ctx, "/tv") if tv_.get("enabled")]
            if not tvs:
                return fail(t("tv.none"))
            if tv:
                want = tv.strip().casefold()
                hit = next((x for x in tvs if x["id"].casefold() == want or x["name"].casefold() == want), None)
                if hit is None:
                    return fail(t("tv.unknown", tv=tv, names=", ".join(x["name"] for x in tvs)))
            else:
                hit = tvs[0]
            st = await api_send(ctx, "POST", f"/tv/{hit['id']}/show",
                                json={"camera_id": cam["id"], "duration_s": max(10, min(int(duration_s), 3600))})
            return ok(t("tv.showing", tv=hit["name"], camera=cam.get("name"), seconds=int(duration_s)), st)
        except BackendError as e:
            return fail(str(e))
