"""PTZ: MCP-Tools `ptz_status`, `ptz_move_to` (CONTRACT „Nachträge: PTZ“, PTZ.E).

    ptz_status(camera_id?, camera_name?) → {cameras:[{id, name, state, presets:[{token,name}], home_preset}]}
    ptz_move_to(camera_id?/camera_name?, preset, hold_s?) → PtzState   (write, POST /cameras/{id}/ptz/goto)
Nach der Fahrt kann der Agent `get_camera_snapshot` aufrufen; `ptz_move_to` wartet, bis die Kamera ruhig steht.
"""

from __future__ import annotations

import asyncio
from typing import Optional

from mcp.server.mcpserver import Context, MCPServer
from mcp.types import CallToolResult

from .common import (READ_ONLY, WRITE, BackendError, api, api_send, cameras, fail, find_camera, mapped, ok,
                     require_write, t)

HOME_WORDS = {"home", "grundposition", "start", "standard", "zurück", "zurueck"}


def _state_text(name: str, st: Optional[dict]) -> str:
    if not st:
        return t("ptz.off", name=name)
    view = t("ptz.home") if st.get("view") == "home" else (t("ptz.position", name=st["preset_name"])
                                                           if st.get("preset_name") else t("ptz.free"))
    extra = t("ptz.moving") if st.get("moving") else ""
    err = f" – {st['error']}" if st.get("error") else ""
    return f"{name}: {view}, {mapped('ptz.mode', st.get('mode'))}{extra}{err}"


def register(mcp: MCPServer) -> None:
    @mcp.tool(annotations=READ_ONLY)
    async def ptz_status(ctx: Context, camera_id: Optional[int] = None, camera_name: Optional[str] = None) -> CallToolResult:
        """Show pan/tilt/zoom cameras (PTZ) with their current orientation, operating mode and saved positions
        (presets). Without parameters: all PTZ cameras."""
        try:
            cams = await cameras(ctx)
            if camera_id is not None or camera_name:
                cams = [find_camera(cams, camera_id, camera_name)]
            cams = [c for c in cams if c.get("ptz_enabled")]
            if not cams:
                return ok(t("ptz.none"), {"cameras": []})
            out, lines = [], []
            for c in cams:
                info = await api(ctx, f"/cameras/{c['id']}/ptz")
                home = (c.get("ptz") or {}).get("home_preset")
                presets = info.get("presets") or []
                out.append({"id": c["id"], "name": c.get("name"), "state": info.get("state"), "presets": presets,
                            "home_preset": home})
                names = ", ".join(p["name"] + (f" ({t('ptz.home')})" if p["token"] == home else "")
                                  for p in presets) or t("common.none")
                lines.append(f"- {_state_text(c.get('name') or str(c['id']), info.get('state'))}. "
                             + t("ptz.positions", names=names))
            return ok("\n".join(lines), {"cameras": out})
        except BackendError as e:
            return fail(str(e))

    @mcp.tool(annotations=WRITE)
    async def ptz_move_to(ctx: Context, preset: str, camera_id: Optional[int] = None, camera_name: Optional[str] = None,
                          hold_s: Optional[int] = None) -> CallToolResult:
        """Point a PTZ camera at a saved position (name or token from `ptz_status`; "home" = back to the home
        position). Keeps it there for `hold_s` seconds (default: the camera's setting), then it returns. Waits until
        the camera stands still – afterwards `get_camera_snapshot` shows the new view (the still image may lag a
        few seconds). Requires an API key with write permission."""
        try:
            await require_write(ctx)
            cam = find_camera(await cameras(ctx), camera_id, camera_name)
            if not cam.get("ptz_enabled"):
                return fail(t("ptz.not_ptz", name=cam.get("name")))
            info = await api(ctx, f"/cameras/{cam['id']}/ptz")
            want = (preset or "").strip()
            token: Optional[str]
            if want.casefold() in HOME_WORDS:
                token = None
            else:
                hit = next((p for p in info.get("presets") or [] if p["token"] == want
                            or p["name"].casefold() == want.casefold()), None)
                if hit is None:
                    names = ", ".join(p["name"] for p in info.get("presets") or []) or t("common.none")
                    return fail(t("ptz.unknown_preset", preset=want, camera=cam.get("name"), names=names))
                token = hit["token"]
            body: dict = {"preset": token}
            if hold_s is not None:
                body["hold_s"] = max(5, min(int(hold_s), 3600))
            st = await api_send(ctx, "POST", f"/cameras/{cam['id']}/ptz/goto", json=body)
            for _ in range(25):                       # bis die Kamera ruhig steht (≤ 25 s)
                if not (st or {}).get("moving"):
                    break
                await asyncio.sleep(1.0)
                st = (await api(ctx, f"/cameras/{cam['id']}/ptz")).get("state") or st
            return ok(_state_text(cam.get("name") or str(cam["id"]), st), st or {})
        except BackendError as e:
            return fail(str(e))
