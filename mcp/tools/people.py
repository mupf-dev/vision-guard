"""Personen und Tiere: who_is_where (W4/C2: aus GET /presence – aktuell sichtbar und „zuletzt gesehen“)."""

from __future__ import annotations

from typing import Optional

from mcp.server.mcpserver import Context
from mcp.types import CallToolResult

from .common import READ_ONLY, BackendError, api, fail, fmt_ago, fmt_time, mapped, ok, t, where


def _entry(p: dict) -> dict:
    ident = p.get("identity") or {}
    label = p.get("label") or "person"
    return {
        "display_name": ident.get("display_name") or t("people.unknown", label=mapped("common.labels", label)),
        "identity_id": ident.get("id"),
        "kind": ident.get("kind"),
        "label": label,
        "presence_key": p.get("presence_key"),
        "source": p.get("source"),
        "confidence": p.get("confidence"),
        "active": bool(p.get("active")),
        "camera_id": p.get("camera_id"),
        "camera_name": p.get("camera_name"),
        "room": p.get("room"),
        "zones": p.get("zone_names") or p.get("zones") or [],
        "since": p.get("since"),
        "last_seen": p.get("last_seen"),
        "thumb_url": p.get("thumb_url"),
    }


def _line(p: dict) -> str:
    text = f"- {p['display_name']}: "
    if p["active"]:
        text += f"{where(p['camera_name'], p['room'])}, " + t("common.since", time=fmt_time(p["since"]))
    else:
        text += t("people.last_seen", ago=fmt_ago(p["last_seen"]), time=fmt_time(p["last_seen"]),
                  place=where(p["camera_name"], p["room"]))
    if p["zones"]:
        text += ", " + t("common.zone", zones=", ".join(map(str, p["zones"])))
    src = mapped("people.source", p.get("source"), "") if p.get("source") != "none" else ""
    if src and p.get("identity_id"):
        conf = p.get("confidence")
        text += t("people.recognized_by", source=src) + (f" ({round(conf * 100)} %)" if p.get("source") == "reid" and conf else "")
    return text


def register(mcp) -> None:
    @mcp.tool(annotations=READ_ONLY)
    async def who_is_where(ctx: Context, minutes: Optional[int] = 60) -> CallToolResult:
        """Who (people, dogs, cats) is visible where right now – and who was last seen where within the last
        `minutes` minutes (default 60)? With identity (face, pet recognition or re-identification across cameras
        by clothing), camera, room and time."""
        try:
            m = max(1, min(720, int(minutes or 60)))
            data = await api(ctx, "/presence", {"minutes": m, "signed": 1})
            people = [_entry(p) for p in (data or []) if isinstance(p, dict)]
            if not people:
                return ok(t("people.nobody", minutes=m), {"people": []})
            now = [p for p in people if p["active"]]
            before = [p for p in people if not p["active"]]
            parts = []
            if now:
                parts.append(t("people.n_visible", n=len(now)) + "\n" + "\n".join(_line(p) for p in now))
            else:
                parts.append(t("people.nobody_now"))
            if before:
                parts.append(t("people.last_seen_head", minutes=m) + "\n" + "\n".join(_line(p) for p in before))
            return ok("\n".join(parts), {"people": people})
        except BackendError as e:
            return fail(str(e))
