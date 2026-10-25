"""Personen und Tiere: who_is_where (W4/C2: aus GET /presence – aktuell sichtbar und „zuletzt gesehen“)."""

from __future__ import annotations

from typing import Optional

from mcp.server.mcpserver import Context
from mcp.types import CallToolResult

from .common import READ_ONLY, BackendError, api, fail, fmt_ago, fmt_time, ok, where

LABEL_DE = {"person": "Person", "dog": "Hund", "cat": "Katze"}
SOURCE_DE = {"face": "Gesicht", "pet": "Tiererkennung", "reid": "Wiedererkennung über Kameras", "none": ""}


def _entry(p: dict) -> dict:
    ident = p.get("identity") or {}
    label = p.get("label") or "person"
    return {
        "display_name": ident.get("display_name") or f"Unbekannt ({LABEL_DE.get(label, label)})",
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
        text += f"{where(p['camera_name'], p['room'])}, seit {fmt_time(p['since'])}"
    else:
        text += f"zuletzt gesehen {fmt_ago(p['last_seen'])} ({fmt_time(p['last_seen'])}), {where(p['camera_name'], p['room'])}"
    if p["zones"]:
        text += ", Zone " + ", ".join(map(str, p["zones"]))
    src = SOURCE_DE.get(p.get("source") or "", "")
    if src and p.get("identity_id"):
        conf = p.get("confidence")
        text += f" – erkannt per {src}" + (f" ({round(conf * 100)} %)" if p.get("source") == "reid" and conf else "")
    return text


def register(mcp) -> None:
    @mcp.tool(annotations=READ_ONLY)
    async def who_is_where(ctx: Context, minutes: Optional[int] = 60) -> CallToolResult:
        """Wer (Personen, Hunde, Katzen) ist gerade wo zu sehen – und wer wurde in den letzten `minutes` Minuten
        (Standard 60) zuletzt wo gesehen? Mit Identität (Gesicht, Tiererkennung oder Wiedererkennung über Kameras
        anhand der Kleidung), Kamera, Raum und Zeit."""
        try:
            m = max(1, min(720, int(minutes or 60)))
            data = await api(ctx, "/presence", {"minutes": m, "signed": 1})
            people = [_entry(p) for p in (data or []) if isinstance(p, dict)]
            if not people:
                return ok(f"Derzeit ist niemand zu sehen, und in den letzten {m} min wurde niemand erfasst.",
                          {"people": []})
            now = [p for p in people if p["active"]]
            before = [p for p in people if not p["active"]]
            parts = []
            if now:
                parts.append(f"{len(now)} gerade sichtbar:\n" + "\n".join(_line(p) for p in now))
            else:
                parts.append("Gerade ist niemand zu sehen.")
            if before:
                parts.append(f"Zuletzt gesehen (letzte {m} min):\n" + "\n".join(_line(p) for p in before))
            return ok("\n".join(parts), {"people": people})
        except BackendError as e:
            return fail(str(e))
