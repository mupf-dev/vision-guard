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

from .common import READ_ONLY, WRITE, BackendError, api, api_send, fail, fmt_ago, fmt_time, mapped, ok, require_write, t
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
    who = mapped("common.labels", a.get("label"), a.get("label") or t("alerts.object"))
    what = mapped("alerts.posture", a.get("posture"), "")
    head = f"{who} {what} " if what else f"{who}: "
    text = head + t("alerts.sentence", time=fmt_time(a.get("since")), ago=fmt_ago(a.get("since")),
                    camera=a.get("camera_name") or "?", rule=a.get("rule") or "?")
    if a.get("repeat"):
        text += ", " + t("alerts.n_repeats", n=a["repeat"])
    if a.get("acknowledged_at"):
        text += ", " + t("alerts.acknowledged", time=fmt_time(a["acknowledged_at"]))
    elif not a.get("active"):
        text += ", " + t("alerts.ended_unacknowledged")
    else:
        text += ", " + t("alerts.not_acknowledged")
    text += f" [event_id {a.get('event_id')}]"
    if a.get("snapshot_url"):
        text += " " + t("common.image", url=a["snapshot_url"])
    return text


def register(mcp: MCPServer) -> None:
    @mcp.tool(annotations=READ_ONLY)
    async def list_alerts(ctx: Context, active_only: bool = True, limit: int = 10) -> CallToolResult:
        """List VisionGuard alerts (emergencies), e.g. "person lying on the floor" (fall detection) or rules that
        repeat until acknowledged. `active_only=True` (default): only ongoing, not yet acknowledged ones;
        `False`: also ended/acknowledged ones, newest first. Acknowledge with `acknowledge_alert(event_id)`."""
        try:
            limit = max(1, min(int(limit), 50))
            params = {"alert": "true", "limit": limit, "signed": 1}
            if active_only:
                params.update({"active": "true", "acknowledged": "false"})
            data = await api(ctx, "/events", params)
            items = [compact_alert(e) for e in data.get("items", [])]
            total = int(data.get("total", len(items)))
            if not items:
                msg = t("alerts.none_open") if active_only else t("alerts.none_stored")
                return ok(msg, {"total": 0, "items": []})
            head = t("alerts.n_open", n=total) if active_only else t("alerts.n_alerts", n=total)
            lines = [head + (t("common.newest", n=len(items)) if total > len(items) else ":")]
            lines += [f"- {alert_sentence(a)}" for a in items]
            return ok("\n".join(lines), {"total": total, "items": items})
        except BackendError as e:
            return fail(str(e))

    @mcp.tool(annotations=WRITE)
    async def acknowledge_alert(ctx: Context, event_id: str) -> CallToolResult:
        """Acknowledge an alert (event from `list_alerts`): stops further repetitions/notifications.
        Requires an API key with write permission. Acknowledging more than once is harmless."""
        try:
            eid = (event_id or "").strip()
            if not _EVENT_ID.match(eid):
                return fail(t("alerts.bad_event_id"))
            await require_write(ctx)
            ev = await api_send(ctx, "POST", f"/events/{eid}/ack", not_found=t("alerts.unknown_event", id=eid))
            a = compact_alert(ev or {})
            return ok(t("alerts.acknowledged_at", time=fmt_time(a.get("acknowledged_at")), text=alert_sentence(a)),
                      {"event_id": eid, "acknowledged_at": (ev or {}).get("acknowledged_at")})
        except BackendError as e:
            return fail(str(e))
