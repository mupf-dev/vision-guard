"""Kameras: list_cameras, get_camera_snapshot."""

from __future__ import annotations

import base64
import io
from datetime import datetime, timezone
from typing import Optional

from mcp.server.mcpserver import Context, MCPServer
from mcp.types import CallToolResult, ImageContent, TextContent

from .common import READ_ONLY, BackendError, api, cameras, fail, find_camera, fmt_datetime, mapped, ok, t, where

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
        """List all cameras with room, state (online/offline/…) and whether object detection (inventory) is
        active."""
        try:
            cams = [{"id": c.get("id"), "name": c.get("name"), "room": c.get("room"),
                     "state": (c.get("status") or {}).get("state"),
                     "inventory_enabled": bool(c.get("inventory_enabled"))} for c in await cameras(ctx)]
            if not cams:
                return ok(t("cameras.none"), {"cameras": []})
            lines = [t("cameras.line", name=c["name"], id=c["id"], room=c["room"] or "–",
                       state=mapped("cameras.state", c["state"], t("cameras.state.unknown")))
                     + (t("cameras.inventory_on") if c["inventory_enabled"] else "") for c in cams]
            return ok(t("cameras.n_cameras", n=len(cams)) + "\n" + "\n".join(lines), {"cameras": cams})
        except BackendError as e:
            return fail(str(e))


    @mcp.tool(annotations=READ_ONLY)
    async def get_camera_snapshot(ctx: Context, camera_id: Optional[int] = None,
                                  camera_name: Optional[str] = None) -> CallToolResult:
        """Return the current image of a camera (JPEG). Camera by camera_id or camera_name (room name works too)."""
        try:
            cam = find_camera(await cameras(ctx), camera_id, camera_name)
            resp = await api(ctx, f"/cameras/{cam['id']}/snapshot.jpg", raw=True,
                             not_found=t("cameras.unknown", name=cam.get("name")))
            img = _shrink_jpeg(resp.content, SNAPSHOT_MAX_WIDTH)
            text = t("cameras.snapshot", where=where(cam.get("name"), cam.get("room")),
                     time=fmt_datetime(datetime.now(timezone.utc), seconds=True))
            return CallToolResult(content=[
                ImageContent(type="image", data=base64.b64encode(img).decode(), mime_type="image/jpeg"),
                TextContent(type="text", text=text),
            ])
        except BackendError as e:
            msg = str(e)
            if e.status is not None and e.status >= 400 and e.status not in (401, 403, 404):
                msg = t("cameras.snapshot_unavailable")
            return fail(msg)
