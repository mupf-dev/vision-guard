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

from .common import READ_ONLY, BackendError, cameras, fail, find_camera, mapped, ok, t


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
        """Health of the camera images: covered, blurry, too dark, frozen or moved.
        Without parameters all cameras; otherwise one camera by camera_id or camera_name (room name works too)."""
        try:
            cams = await cameras(ctx)
            if camera_id is not None or camera_name:
                cams = [find_camera(cams, camera_id, camera_name)]
            items = [_entry(c) for c in cams]
            if not items:
                return ok(t("cameras.none"), {"cameras": []})
            lines = []
            for it in items:
                head = f"- {it['name']} (id {it['id']}{', ' + t('common.room', name=it['room']) if it.get('room') else ''}): "
                if it["problems"]:
                    lines.append(head + "; ".join(p["text"] or p["type"] for p in it["problems"]))
                else:
                    lines.append(head + mapped("health.state", it["state"]))
            bad = sum(1 for it in items if it["state"] == "warn")
            intro = (t("health.n_problems", n=bad) if bad
                     else t("health.all_ok") if len(items) > 1 else t("health.single_head"))
            return ok(intro + "\n" + "\n".join(lines), {"cameras": items})
        except BackendError as e:
            return fail(str(e))
