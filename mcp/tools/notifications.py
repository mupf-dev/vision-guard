"""W1 (A1): MCP-Tool `list_notifications` – Hinweise aus Regeln, Kamera-Zustand usw.

Vertrag (CONTRACT.md „Nachträge: Welle 1“, W1.G):
    list_notifications(since: str | None = None, limit: int = 10, source: str | None = None)
    → {total, items:[{id, ts, source, level, rule, title, message, camera_id, image_url}]} aus GET /notifications?signed=1
`since` wie bei B2: ISO-8601, relativ (`3h`, `90m`, `2d`), `today` (00:00), `morning` (06:00), `yesterday`.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Optional

from mcp.server.mcpserver import Context, MCPServer
from mcp.types import CallToolResult

from .common import LOCAL_TZ, READ_ONLY, BackendError, api, fail, fmt_ago, fmt_time, mapped, ok, t

SOURCES = ("rule", "health", "pet", "review", "report", "sensor")
_REL = re.compile(r"^\s*(\d+(?:[.,]\d+)?)\s*(m|min|h|std|d|t)\s*$", re.IGNORECASE)


def parse_since(value: Optional[str], now: Optional[datetime] = None) -> Optional[datetime]:
    """Zeitangabe → UTC-Zeitpunkt; None bei leerem Wert. ValueError bei unbekanntem Format."""
    if value is None or not str(value).strip():
        return None
    v = str(value).strip().lower()
    now = now or datetime.now(timezone.utc)
    loc = now.astimezone(LOCAL_TZ)
    midnight = loc.replace(hour=0, minute=0, second=0, microsecond=0)
    if v in ("now", "jetzt"):
        return now
    if v in ("today", "heute"):
        return midnight.astimezone(timezone.utc)
    if v in ("morning", "heute morgen", "morgens"):
        return midnight.replace(hour=6).astimezone(timezone.utc)
    if v in ("yesterday", "gestern"):
        return (midnight - timedelta(days=1)).astimezone(timezone.utc)
    m = _REL.match(v)
    if m:
        n = float(m.group(1).replace(",", "."))
        unit = m.group(2)
        secs = n * (60 if unit in ("m", "min") else 3600 if unit in ("h", "std") else 86400)
        return now - timedelta(seconds=secs)
    try:
        dt = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
    except ValueError:
        raise ValueError(t("notifications.bad_time", value=value)) from None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=LOCAL_TZ)
    return dt.astimezone(timezone.utc)


def register(mcp: MCPServer) -> None:
    @mcp.tool(annotations=READ_ONLY)
    async def list_notifications(ctx: Context, since: Optional[str] = None, limit: int = 10,
                                 source: Optional[str] = None) -> CallToolResult:
        """List the latest VisionGuard notifications (e.g. "keys are still on the hook", camera covered),
        newest first. `since`: ISO time or "3h", "90m", "2d", "today", "morning", "yesterday".
        `source`: rule, health, pet, review, report or sensor (comma-separated allowed)."""
        try:
            try:
                since_dt = parse_since(since)
            except ValueError as e:
                return fail(str(e))
            src = None
            if source:
                parts = [p.strip().lower() for p in source.split(",") if p.strip()]
                bad = [p for p in parts if p not in SOURCES]
                if bad:
                    return fail(t("notifications.bad_source", bad=", ".join(bad), allowed=", ".join(SOURCES)))
                src = ",".join(parts)
            limit = max(1, min(int(limit), 100))
            data = await api(ctx, "/notifications", {
                "since": since_dt.isoformat() if since_dt else None, "source": src, "limit": limit, "signed": 1})
            items = [{"id": n.get("id"), "ts": n.get("ts"), "source": n.get("source"), "level": n.get("level"),
                      "rule": n.get("rule"), "title": n.get("title"), "message": n.get("message"),
                      "camera_id": n.get("camera_id"), "image_url": n.get("image_url")}
                     for n in data.get("items", [])]
            total = int(data.get("total", len(items)))
            scope = " " + t("common.since", time=fmt_time(since_dt.isoformat())) if since_dt else ""
            if not items:
                return ok(t("notifications.none", scope=scope), {"total": 0, "items": []})
            lines = [t("notifications.n_items", n=total, scope=scope)
                     + (t("common.newest", n=len(items)) if total > len(items) else ":")]
            for n in items:
                head = (f"{fmt_time(n['ts'])} ({fmt_ago(n['ts'])}) – "
                        f"{mapped('notifications.level', n['level'], t('notifications.level.info'))}")
                head += f", {mapped('notifications.source', n['source'])}"
                if n["rule"] and n["source"] == "rule" and n["rule"] != n["title"]:
                    head += " " + t("common.quoted", text=n["rule"])
                text = f"- {head}: {n['title'] or ''}"
                if n["message"]:
                    text += f" – {n['message']}"
                if n["image_url"]:
                    text += " " + t("common.image", url=n["image_url"])
                lines.append(text)
            return ok("\n".join(lines), {"total": total, "items": items})
        except BackendError as e:
            return fail(str(e))
