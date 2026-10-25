"""W1 (C5): MCP-Tool `get_camera_health` – Kamera-Gesundheit (verdeckt, unscharf, dunkel, eingefroren, verschoben).

Vertrag (CONTRACT.md „Nachträge: Welle 1“, W1.G):
    get_camera_health(camera_id: int | None = None, camera_name: str | None = None)
    → {cameras:[{id, name, room, enabled, state, problems:[{type, since, text}]}]}
Quelle: `GET /cameras` (`status.health`, `health_enabled`). Zustand `ok|warn|unknown`; `off` = Prüfung
abgeschaltet, `disabled` = Kamera deaktiviert.
"""

from __future__ import annotations

from typing import Optional

from mcp.server.mcpserver import Context, MCPServer
from mcp.types import CallToolResult

from .common import READ_ONLY, BackendError, cameras, fail, find_camera, n_de, ok

STATE_DE = {"ok": "in Ordnung", "warn": "Problem", "unknown": "Zustand unbekannt (kein aktuelles Prüfbild)",
            "off": "Prüfung abgeschaltet", "disabled": "Kamera deaktiviert"}


def _entry(c: dict) -> dict:
    status = c.get("status") or {}
    health = status.get("health") or None
    enabled = c.get("health_enabled", True) is not False
    if not c.get("enabled", True) or status.get("state") == "disabled":
        state = "disabled"
    elif not enabled:
        state = "off"
    elif health is None:
        state = "unknown"
    else:
        state = health.get("state") or "unknown"
    problems = [{"type": p.get("type"), "since": p.get("since"), "text": p.get("text")}
                for p in ((health or {}).get("problems") or [])] if state not in ("off", "disabled") else []
    return {"id": c.get("id"), "name": c.get("name"), "room": c.get("room"), "enabled": enabled,
            "state": state, "problems": problems}


def register(mcp: MCPServer) -> None:
    @mcp.tool(annotations=READ_ONLY)
    async def get_camera_health(ctx: Context, camera_id: Optional[int] = None,
                                camera_name: Optional[str] = None) -> CallToolResult:
        """Zustand der Kamerabilder: verdeckt, unscharf, zu dunkel, eingefroren oder verschoben.
        Ohne Parameter alle Kameras; sonst eine Kamera per camera_id oder camera_name (auch Raumname)."""
        try:
            cams = await cameras(ctx)
            if camera_id is not None or camera_name:
                cams = [find_camera(cams, camera_id, camera_name)]
            items = [_entry(c) for c in cams]
            if not items:
                return ok("Keine Kameras eingerichtet.", {"cameras": []})
            lines = []
            for it in items:
                head = f"- {it['name']} (id {it['id']}{', Raum ' + it['room'] if it.get('room') else ''}): "
                if it["problems"]:
                    lines.append(head + "; ".join(p["text"] or p["type"] for p in it["problems"]))
                else:
                    lines.append(head + STATE_DE.get(it["state"], it["state"]))
            bad = sum(1 for it in items if it["state"] == "warn")
            intro = (f"{n_de(bad, 'Kamera hat', 'Kameras haben')} ein Problem." if bad
                     else "Alle geprüften Kamerabilder sind in Ordnung." if len(items) > 1 else "Kamerazustand:")
            return ok(intro + "\n" + "\n".join(lines), {"cameras": items})
        except BackendError as e:
            return fail(str(e))
