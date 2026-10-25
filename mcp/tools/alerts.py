"""W2 (C1/D1): MCP-Tools `list_alerts`, `acknowledge_alert` – Notfälle (z. B. „liegt am Boden“) und Quittieren.

Vertrag (CONTRACT.md „Nachträge: Welle 2“, W2.H):
    list_alerts(active_only: bool = True, limit: int = 10) → {total, items:[{event_id, camera_id, camera_name, label,
        posture, since, acknowledged_at, repeat, snapshot_url}]}
    acknowledge_alert(event_id: str) → {event_id, acknowledged_at}   (write, POST /events/{id}/ack)
Notfall = Event mit `attributes.alert` (Haltungs-Regel oder Regel mit Wiederholung), aus
`GET /events?alert=true[&active=true&acknowledged=false]&signed=1`. `active_only` = laufend **und** unquittiert.
"""

from __future__ import annotations

import re

from mcp.server.mcpserver import Context, MCPServer
from mcp.types import CallToolResult

from .common import READ_ONLY, WRITE, BackendError, api, api_send, fail, fmt_ago, fmt_time, n_de, ok, require_write

POSTURE_DE = {"lying": "liegt", "sitting": "sitzt", "standing": "steht"}
_EVENT_ID = re.compile(r"^[0-9a-fA-F-]{8,64}$")


def compact_alert(e: dict) -> dict:
    al = (e.get("attributes") or {}).get("alert") or {}
    return {
        "event_id": e.get("id"),
        "camera_id": e.get("camera_id"),
        "camera_name": e.get("camera_name"),
        "label": e.get("label"),
        "rule": al.get("rule"),
        "posture": al.get("posture") or (e.get("attributes") or {}).get("posture"),
        "since": al.get("posture_since") or al.get("first_fired_at") or e.get("start_ts"),
        "active": bool(e.get("active")),
        "acknowledged_at": e.get("acknowledged_at"),
        "repeat": int(al.get("repeat") or 0),
        "snapshot_url": e.get("snapshot_url") or e.get("thumb_url"),
    }


def alert_sentence(a: dict) -> str:
    who = "Person" if a.get("label") == "person" else (a.get("label") or "Objekt")
    what = POSTURE_DE.get(a.get("posture") or "", "")
    head = f"{who} {what} " if what else f"{who}: "
    text = (f"{head}seit {fmt_time(a.get('since'))} ({fmt_ago(a.get('since'))}) an Kamera {a.get('camera_name') or '?'}"
            f" – Regel „{a.get('rule') or '?'}“")
    if a.get("repeat"):
        text += f", {n_de(a['repeat'], 'Wiederholung', 'Wiederholungen')}"
    if a.get("acknowledged_at"):
        text += f", quittiert {fmt_time(a['acknowledged_at'])}"
    elif not a.get("active"):
        text += ", beendet (nicht quittiert)"
    else:
        text += ", NICHT quittiert"
    text += f" [event_id {a.get('event_id')}]"
    if a.get("snapshot_url"):
        text += f" Bild: {a['snapshot_url']}"
    return text


def register(mcp: MCPServer) -> None:
    @mcp.tool(annotations=READ_ONLY)
    async def list_alerts(ctx: Context, active_only: bool = True, limit: int = 10) -> CallToolResult:
        """Listet Notfälle von VisionGuard, z. B. „Person liegt am Boden“ (Sturzerkennung) oder Regeln mit
        Wiederholung bis zur Quittierung. `active_only=True` (Standard): nur laufende, noch nicht quittierte;
        `False`: auch beendete/quittierte, neueste zuerst. Quittieren mit `acknowledge_alert(event_id)`."""
        try:
            limit = max(1, min(int(limit), 50))
            params = {"alert": "true", "limit": limit, "signed": 1}
            if active_only:
                params.update({"active": "true", "acknowledged": "false"})
            data = await api(ctx, "/events", params)
            items = [compact_alert(e) for e in data.get("items", [])]
            total = int(data.get("total", len(items)))
            if not items:
                msg = "Keine offenen Notfälle." if active_only else "Keine Notfälle gespeichert."
                return ok(msg, {"total": 0, "items": []})
            head = n_de(total, "offener Notfall", "offene Notfälle") if active_only else n_de(total, "Notfall", "Notfälle")
            lines = [head + (f", die neuesten {len(items)}:" if total > len(items) else ":")]
            lines += [f"- {alert_sentence(a)}" for a in items]
            return ok("\n".join(lines), {"total": total, "items": items})
        except BackendError as e:
            return fail(str(e))

    @mcp.tool(annotations=WRITE)
    async def acknowledge_alert(ctx: Context, event_id: str) -> CallToolResult:
        """Quittiert einen Notfall (Event aus `list_alerts`): stoppt weitere Wiederholungen/Benachrichtigungen.
        Braucht einen API-Key mit Schreibrecht. Mehrfaches Quittieren ist unschädlich."""
        try:
            eid = (event_id or "").strip()
            if not _EVENT_ID.match(eid):
                return fail("Ungültige event_id.")
            await require_write(ctx)
            ev = await api_send(ctx, "POST", f"/events/{eid}/ack", not_found=f"Ereignis {eid} ist unbekannt.")
            a = compact_alert(ev or {})
            return ok(f"Quittiert um {fmt_time(a.get('acknowledged_at'))}: {alert_sentence(a)}",
                      {"event_id": eid, "acknowledged_at": (ev or {}).get("acknowledged_at")})
        except BackendError as e:
            return fail(str(e))
