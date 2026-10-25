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

from .common import READ_ONLY, WRITE, BackendError, api, api_send, cameras, fail, find_camera, ok, require_write

MODE_DE = {"idle": "in Ruhe", "manual": "Handsteuerung", "rule": "durch Regel ausgerichtet", "follow": "verfolgt ein Ziel",
           "patrol": "Patrouille", "error": "nicht verbunden"}
HOME_WORDS = {"home", "grundposition", "start", "standard", "zurück", "zurueck"}


def _state_text(name: str, st: Optional[dict]) -> str:
    if not st:
        return f"{name}: PTZ aus"
    view = "Grundposition" if st.get("view") == "home" else (f"Position „{st['preset_name']}“" if st.get("preset_name")
                                                              else "freie Ausrichtung")
    extra = " (fährt gerade)" if st.get("moving") else ""
    err = f" – {st['error']}" if st.get("error") else ""
    return f"{name}: {view}, {MODE_DE.get(st.get('mode'), st.get('mode'))}{extra}{err}"


def register(mcp: MCPServer) -> None:
    @mcp.tool(annotations=READ_ONLY)
    async def ptz_status(ctx: Context, camera_id: Optional[int] = None, camera_name: Optional[str] = None) -> CallToolResult:
        """Zeigt schwenkbare Kameras (PTZ) mit aktueller Ausrichtung, Betriebsart und gespeicherten Positionen.
        Ohne Parameter: alle PTZ-Kameras."""
        try:
            cams = await cameras(ctx)
            if camera_id is not None or camera_name:
                cams = [find_camera(cams, camera_id, camera_name)]
            cams = [c for c in cams if c.get("ptz_enabled")]
            if not cams:
                return ok("Keine schwenkbare Kamera (PTZ) eingerichtet.", {"cameras": []})
            out, lines = [], []
            for c in cams:
                info = await api(ctx, f"/cameras/{c['id']}/ptz")
                home = (c.get("ptz") or {}).get("home_preset")
                presets = info.get("presets") or []
                out.append({"id": c["id"], "name": c.get("name"), "state": info.get("state"), "presets": presets,
                            "home_preset": home})
                names = ", ".join(p["name"] + (" (Grundposition)" if p["token"] == home else "") for p in presets) or "keine"
                lines.append(f"- {_state_text(c.get('name') or str(c['id']), info.get('state'))}. Positionen: {names}")
            return ok("\n".join(lines), {"cameras": out})
        except BackendError as e:
            return fail(str(e))

    @mcp.tool(annotations=WRITE)
    async def ptz_move_to(ctx: Context, preset: str, camera_id: Optional[int] = None, camera_name: Optional[str] = None,
                          hold_s: Optional[int] = None) -> CallToolResult:
        """Richtet eine schwenkbare Kamera auf eine gespeicherte Position (Name oder Token aus `ptz_status`;
        „Grundposition“/„home“ = zurück). Hält sie dort `hold_s` Sekunden (Standard: Einstellung der Kamera), danach
        kehrt sie zurück. Wartet, bis die Kamera ruhig steht – danach zeigt `get_camera_snapshot` den neuen Blick
        (das Standbild kann wenige Sekunden nachlaufen). Braucht einen API-Key mit Schreibrecht."""
        try:
            await require_write(ctx)
            cam = find_camera(await cameras(ctx), camera_id, camera_name)
            if not cam.get("ptz_enabled"):
                return fail(f"Kamera {cam.get('name')} ist nicht schwenkbar (PTZ aus).")
            info = await api(ctx, f"/cameras/{cam['id']}/ptz")
            want = (preset or "").strip()
            token: Optional[str]
            if want.casefold() in HOME_WORDS:
                token = None
            else:
                hit = next((p for p in info.get("presets") or [] if p["token"] == want
                            or p["name"].casefold() == want.casefold()), None)
                if hit is None:
                    names = ", ".join(p["name"] for p in info.get("presets") or []) or "keine"
                    return fail(f"Position „{want}“ gibt es an {cam.get('name')} nicht. Vorhanden: {names}.")
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
