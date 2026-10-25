"""W2 (B3): MCP-Tool `get_daily_report` – Tagesbericht eines Tages (nur lesen).

Vertrag (CONTRACT.md „Nachträge: Welle 2“, W2.H):
    get_daily_report(date: str | None = None) → Report (Text + Zustellstatus); ohne gespeicherten Bericht Vorschau
    über /reports/preview (Tag 00:00 bis 24:00 bzw. bis jetzt).
Ein gespeicherter Tagesbericht gehört zum Tag seines Endzeitpunkts (`period_end`, Ortszeit; endet er genau um
Mitternacht, zum Vortag) – wie die Überschrift des Berichts im Backend.
"""

from __future__ import annotations

from datetime import date as _date, datetime, timedelta
from typing import Any, Optional

from mcp.server.mcpserver import Context, MCPServer
from mcp.types import CallToolResult

from .common import LOCAL_TZ, READ_ONLY, BackendError, _parse_ts, api, fail, fmt_time, ok

CHANNEL_DE = {"mqtt": "MQTT", "push": "Push", "webhook": "Webhook", "mail": "Mail"}
KEYWORDS = {"today": 0, "heute": 0, "yesterday": 1, "gestern": 1, "vorgestern": 2}


def parse_day(value: Optional[str], today: _date) -> _date:
    v = (value or "today").strip().lower()
    if v in KEYWORDS:
        return today - timedelta(days=KEYWORDS[v])
    for fmt in ("%Y-%m-%d", "%d.%m.%Y", "%d.%m."):
        try:
            d = datetime.strptime(v, fmt).date()
        except ValueError:
            continue
        if fmt == "%d.%m.":
            d = d.replace(year=today.year)
            if d > today:
                d = d.replace(year=today.year - 1)
        return d
    raise ValueError(f"Unbekanntes Datum „{value}“ (erlaubt: YYYY-MM-DD, today, yesterday, heute, gestern)")


def _delivery_text(delivery: dict[str, Any]) -> str:
    if not delivery:
        return "nicht versendet"
    parts = []
    for k, d in delivery.items():
        name = CHANNEL_DE.get(k, k)
        parts.append(f"{name} ok" if d.get("ok") else f"{name} fehlgeschlagen ({d.get('error') or '?'})")
    return ", ".join(parts)


def _compact(r: dict[str, Any]) -> dict[str, Any]:
    facts = r.get("facts") or {}
    return {"id": r.get("id"), "kind": r.get("kind"), "period_start": r.get("period_start"),
            "period_end": r.get("period_end"), "created_at": r.get("created_at"), "generator": r.get("generator"),
            "text": r.get("text"), "delivery": r.get("delivery") or {}, "counts": facts.get("counts") or {},
            "sections": facts.get("sections") or [],
            "inventory_changes": [{"type": c.get("type"), "at": c.get("at"), "text": c.get("text"),
                                   "crop_url": (c.get("after") or {}).get("crop_url")
                                   or (c.get("before") or {}).get("crop_url")}
                                  for c in ((facts.get("inventory") or {}).get("changes") or [])][:10]}


def register(mcp: MCPServer) -> None:
    @mcp.tool(annotations=READ_ONLY)
    async def get_daily_report(ctx: Context, date: Optional[str] = None) -> CallToolResult:
        """Tagesbericht von VisionGuard: kurze deutsche Zusammenfassung eines Tages (Personen mit Namen und
        Uhrzeiten, Fahrzeuge/Kennzeichen, Tiere und Näpfe, veränderte Gegenstände, ausgelöste Regeln, Hinweise) und
        der Zustellstatus (MQTT, Push, Webhook, Mail). `date`: „today“ (Standard), „yesterday“ oder YYYY-MM-DD.
        Gibt es für den Tag keinen gespeicherten Tagesbericht, wird eine Vorschau für den Tag (00:00 bis jetzt bzw.
        bis Mitternacht) berechnet und nicht gespeichert."""
        try:
            now = datetime.now(LOCAL_TZ)
            try:
                day = parse_day(date, now.date())
            except ValueError as e:
                return fail(str(e))
            if day > now.date():
                return fail("Für zukünftige Tage gibt es keinen Bericht.")
            data = await api(ctx, "/reports", {"limit": 100})
            found = None
            for r in data.get("items") or []:
                end = _parse_ts(r.get("period_end"))
                # 1 s zurück: ein Bericht bis genau Mitternacht gehört zum Vortag (wie die Überschrift im Backend)
                if r.get("kind") == "daily" and end and (end - timedelta(seconds=1)).astimezone(LOCAL_TZ).date() == day:
                    found = r
                    break
            if found:
                full = await api(ctx, f"/reports/{found['id']}", {"signed": 1}, not_found="Bericht nicht gefunden.")
                rep = _compact(full)
                text = (f"{rep['text']}\n\n(Tagesbericht vom {fmt_time(rep['created_at'])}; Text: "
                        f"{rep['generator']}; Versand: {_delivery_text(rep['delivery'])})")
                return ok(text, {"date": day.isoformat(), "source": "stored", "report": rep})
            start = datetime.combine(day, datetime.min.time())
            until = None if day == now.date() else datetime.combine(day + timedelta(days=1), datetime.min.time())
            prev = await api(ctx, "/reports/preview", {"since": start.isoformat(),
                                                       "until": until.isoformat() if until else None, "signed": 1,
                                                       # R1: nie ein LLM – sonst > 20-s-Zeitlimit des MCP
                                                       # und ggf. bezahlte Wiederholungen
                                                       "template": 1})
            rep = _compact(prev)
            text = f"{rep['text']}\n\n(Vorschau – für diesen Tag ist kein Tagesbericht gespeichert; Text: {rep['generator']})"
            return ok(text, {"date": day.isoformat(), "source": "preview", "report": rep})
        except BackendError as e:
            return fail(str(e))
