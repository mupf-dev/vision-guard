"""W2 (C3): MCP-Tool `get_pet_diary` – Haustier-Tagebuch (Napf, Wasser, Katzenklo).

Vertrag (CONTRACT.md „Nachträge: Welle 2“, W2.H):
    get_pet_diary(pet: str | None = None, days: int = 1)
    → {pets:[{name, label, identity_id, purposes:{food|water|litter:{today, normal, status, last_ts, daily}}}],
       visits:[{id, name, purpose, camera_name, zone_name, start_ts, duration_s, thumb_url}]}
Quellen: `GET /pets/summary?days=` und `GET /pets/visits?signed=1` (Vorschaubilder signiert).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

from mcp.server.mcpserver import Context, MCPServer
from mcp.types import CallToolResult

from .common import LOCAL_TZ, READ_ONLY, BackendError, api, fail, fmt_ago, fmt_time, mapped, ok, t

LABEL_ALIASES = {"katze": "cat", "kater": "cat", "katzen": "cat", "hund": "dog", "hunde": "dog",
                 "cats": "cat", "kitty": "cat", "dogs": "dog", "puppy": "dog"}


def _by_name(entry: dict, needle: str) -> bool:
    ident = entry.get("identity") or {}
    names = {str(entry.get("name") or ""), str(ident.get("name") or ""), str(ident.get("display_name") or "")}
    return any(needle == n.casefold() for n in names if n)


def _select(pets: list[dict], needle: str) -> list[dict]:
    """Erst Name (exakt, ohne Groß-/Kleinschreibung), dann Teilname, dann Tierart („Katze“ → alle Katzen)."""
    hits = [e for e in pets if _by_name(e, needle)]
    if not hits:
        hits = [e for e in pets if needle in str(e.get("name") or "").casefold()]
    if not hits:
        label = LABEL_ALIASES.get(needle, needle)
        hits = [e for e in pets if e.get("label") == label]
    return hits


def _purpose_line(purpose: str, p: dict) -> str:
    text = f"{mapped('pets.purpose', purpose)} {p.get('today', 0)}×"
    if p.get("normal"):
        lo, hi = p["normal"]
        text += " " + (t("pets.normal_range", lo=lo, hi=hi) if lo != hi else t("pets.normal_value", value=lo))
    if p.get("status") in ("low", "high", "gap"):
        text += f" – {mapped('pets.status', p['status'])}"
    if p.get("last_ts"):
        text += ", " + t("pets.last", time=fmt_time(p["last_ts"]))
    return text


def register(mcp: MCPServer) -> None:
    @mcp.tool(annotations=READ_ONLY)
    async def get_pet_diary(ctx: Context, pet: Optional[str] = None, days: int = 1) -> CallToolResult:
        """Pet diary: how often a pet was at the food bowl, the water bowl and the litter box today (or in the
        last `days` days) – with the normal range from the last 14 days and deviations.
        `pet` = name of the pet or species ("cat"/"dog" = all pets of that species); without `pet` all pets."""
        try:
            days = max(1, min(int(days or 1), 90))
            summary = await api(ctx, "/pets/summary", {"days": max(days, 7)})
            pets = list(summary or [])
            if pet:
                needle = pet.strip().casefold()
                pets = _select(pets, needle)
                if not pets:
                    known = ", ".join(e.get("name") or "?" for e in summary) or t("common.none")
                    return fail(t("pets.unknown", pet=pet, known=known))
            if not pets:
                return ok(t("pets.empty"), {"pets": [], "visits": []})
            since = (datetime.now(LOCAL_TZ).replace(hour=0, minute=0, second=0, microsecond=0)
                     - timedelta(days=days - 1)).astimezone(timezone.utc)
            visits: list[dict] = []
            for e in pets:
                ident = e.get("identity") or {}
                params = {"since": since.isoformat(), "limit": 200, "signed": 1}
                if ident.get("id"):
                    params["identity_id"] = ident["id"]
                else:
                    params.update(identity_id=0, label=e.get("label"))
                data = await api(ctx, "/pets/visits", params)
                for v in data.get("items", []):
                    visits.append({"id": v.get("id"), "name": e.get("name"), "purpose": v.get("purpose"),
                                   "camera_name": v.get("camera_name"), "zone_name": v.get("zone_name"),
                                   "start_ts": v.get("start_ts"), "duration_s": v.get("duration_s"),
                                   "thumb_url": v.get("thumb_url")})
            visits.sort(key=lambda v: v.get("start_ts") or "", reverse=True)
            out_pets = []
            lines = []
            for e in pets:
                ident = e.get("identity") or {}
                purposes = {}
                for k, p in (e.get("purposes") or {}).items():
                    daily = (p.get("daily") or [])[-days:]
                    purposes[k] = {"today": p.get("today", 0), "normal": p.get("normal"), "status": p.get("status"),
                                   "last_ts": p.get("last_ts"), "daily": daily,
                                   "total": sum(d.get("count", 0) for d in daily)}
                out_pets.append({"name": e.get("name"), "label": e.get("label"), "identity_id": ident.get("id"),
                                 "purposes": purposes})
                kind = mapped("common.labels", e.get("label"), e.get("label") or t("pets.animal"))
                if days == 1:
                    body = "; ".join(_purpose_line(k, p) for k, p in purposes.items()) or t("pets.no_visits")
                    lines.append(t("pets.line_today", name=e.get("name"), kind=kind, body=body))
                else:
                    body = "; ".join(t("pets.purpose_days", purpose=mapped("pets.purpose", k), total=p["total"],
                                       days=days, today=p["today"])
                                     for k, p in purposes.items()) or t("pets.no_visits")
                    lines.append(t("pets.line_days", name=e.get("name"), kind=kind, body=body))
            if visits:
                v = visits[0]
                lines.append(t("pets.last_visit", name=v["name"], purpose=mapped("pets.purpose", v["purpose"]),
                               zone=v.get("zone_name"), ago=fmt_ago(v.get("start_ts"))))
            return ok(t("pets.head") + "\n" + "\n".join(lines), {"pets": out_pets, "visits": visits[:100]})
        except BackendError as e:
            return fail(str(e))
