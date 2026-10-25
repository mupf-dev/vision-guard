"""Auswertung der Regel-Bedingungen (`rules.conditions`) – Umsetzung A1 (CONTRACT.md W1.B).

Die Bedingungs-*Typen* und ihre Schemas sind in `rules.py` registriert (`rules.CONDITIONS`); hier werden die
Evaluatoren nachgereicht. Signatur: `evaluate(cond: dict, ctx: rules.CondContext) -> rules.CondResult`.
Jede Auswertung öffnet eine eigene, kurze DB-Session. Fehler/Unklarheiten → nicht erfüllt.

Bedingungen:
- `inventory`: Gegenstand mit Label aus `labels` ist `present` (bzw. `absent` = kein passender `present`, auch
  `missing`/`stale`/nie gesehen) – gefiltert nach `camera_ids`, `room` (Raum der Kamera, ohne Groß-/Kleinschreibung)
  und `zone_ids` (Zonen des Gegenstands).
- `inventory_untouched`: Gegenstand liegt seit `since` (lokal, heute; liegt die Uhrzeit in der Zukunft: gestern)
  unverändert da. Gezählt werden nur Gegenstände, die zu `since` schon da waren. „Berührt“ = Historie zeigt seitdem
  eine Verschiebung > 0,1 oder eine `missing`-Phase, die länger als `FLICKER_TOLERANCE_S` dauerte bzw. an anderer
  Stelle endete. Kurze `missing`-Phasen am selben Ort gelten als Erkennungs-Flackern (Messung W0: bis 300
  Wechsel/h nachts), `stale`-Phasen (Kamera ohne Analyse) zählen nicht, solange der Gegenstand danach am selben Ort
  liegt. `zone_ids` (optional): zusätzlich darf seit `since` kein Personen-Track in diesen Zonen gewesen sein (die
  Zonen beschreiben den Bereich *vor* dem Gegenstand, nicht den Gegenstand selbst).
- `weather`: Regenwahrscheinlichkeit ≥ `min` in den nächsten `hours` Stunden (Open-Meteo, `weather.py`).
- `track_came_from`: Der auslösende Track war vor der Auslösung in einer der `zone_ids` (Richtung „hinaus“).
  Betrat er Herkunfts- und Auslösezone gleichzeitig (Track-Start), ist die Richtung unklar → nicht erfüllt.

Platzhalter (`CondResult.ctx`): `items` (deutsche Namen), `item_place` („Flur, Schlüsselbrett“), `weather`
(„70 % Regen ab 15 Uhr“).
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from sqlalchemy import or_, select

from . import inventory_names as names
from . import registry, rules, settings_store, weather
from .config import config
from .db import SessionLocal
from .models import Event, InventoryHistory, InventoryItem
from .schemas import CondInventory, CondInventoryUntouched, CondTrackCameFrom, CondWeather

log = logging.getLogger("vg.rule_conditions")

MOVED_DIST = 0.10            # wie inventory.MOVED_HISTORY: Mittelpunktverschiebung = „verschoben“
FLICKER_TOLERANCE_S = 120.0  # kürzere missing-Phasen am selben Ort = Erkennungs-Flackern (messungen.md §7)


# ------------------------------------------------------------------ Hilfen


def _utc(dt: Optional[datetime]) -> Optional[datetime]:
    if dt is not None and dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _center(b: Any) -> Optional[tuple[float, float]]:
    try:
        x1, y1, x2, y2 = (float(v) for v in b)
    except (TypeError, ValueError):
        return None
    return (x1 + x2) / 2, (y1 + y2) / 2


def _dist(a: Any, b: Any) -> float:
    ca, cb = _center(a), _center(b)
    if ca is None or cb is None:
        return 0.0
    return ((ca[0] - cb[0]) ** 2 + (ca[1] - cb[1]) ** 2) ** 0.5


def _vocab() -> dict[str, dict[str, Any]]:
    return names.vocab_map(settings_store.get().get("inventory_vocabulary") or [])


def _join_de(xs: list[str]) -> str:
    xs = list(dict.fromkeys(x for x in xs if x))
    if len(xs) <= 1:
        return "".join(xs)
    return f"{', '.join(xs[:-1])} und {xs[-1]}"


def _cam_ok(camera_id: int, camera_ids: list[int], room: Optional[str]) -> bool:
    if camera_ids and camera_id not in camera_ids:
        return False
    if room:
        cam = registry.get(camera_id) or {}
        if (cam.get("room") or "").strip().casefold() != room.strip().casefold():
            return False
    return True


def item_place(it: InventoryItem) -> str:
    """„Flur, Schlüsselbrett“ (Raum bzw. Kamera, Zonen) oder „Wohnzimmer, vermutlich auf dem Tisch“."""
    cam = registry.get(it.camera_id) or {}
    zmap = cam.get("zones") or {}
    parts = [cam.get("room") or cam.get("name") or f"Kamera {it.camera_id}"]
    zone_names = [zmap.get(z) or z for z in (it.zones or [])]
    if zone_names:
        parts += zone_names
    elif (it.relation or {}).get("text"):
        parts.append(str(it.relation["text"]))
    return ", ".join(parts)


def _since_dt(since: str, now: datetime) -> datetime:
    loc = now.astimezone(config.tz)
    h, m = (int(x) for x in since.split(":"))
    dt = loc.replace(hour=h, minute=m, second=0, microsecond=0)
    if dt > loc:
        dt -= timedelta(days=1)
    return dt.astimezone(timezone.utc)


def _items_ctx(items: list[InventoryItem], vocab: dict[str, dict[str, Any]]) -> dict[str, Any]:
    return {"items": _join_de([names.display_name(i.label, vocab) for i in items]),
            "item_place": _join_de([item_place(i) for i in items[:3]]) if items else ""}


# ------------------------------------------------------------------ inventory


def eval_inventory(cond: dict[str, Any], ctx: rules.CondContext) -> rules.CondResult:
    c = CondInventory(**cond)
    vocab = _vocab()
    with SessionLocal() as s:
        rows = [r for r in s.scalars(select(InventoryItem).where(InventoryItem.label.in_(c.labels))
                                     .order_by(InventoryItem.last_seen.desc())).all()
                if _cam_ok(r.camera_id, c.camera_ids, c.room)
                and (not c.zone_ids or set(c.zone_ids) & set(r.zones or []))]
        present = [r for r in rows if r.state == "present"]
        label_names = _join_de([names.display_name(lbl, vocab) for lbl in c.labels])
        if c.state == "present":
            if not present:
                return rules.CondResult(ok=False, reason=f"{label_names}: nicht sichtbar")
            present.sort(key=lambda r: -float(r.score or 0))
            top = present[0]
            return rules.CondResult(
                ok=True, ctx=_items_ctx(present, vocab), item_ids=[r.id for r in present],
                image_path=top.crop_path, snapshot_path=top.snapshot_path, camera_id=top.camera_id,
                reason=f"{label_names}: sichtbar ({item_place(top)}, Score {float(top.score or 0):.2f})")
        # absent
        if present:
            return rules.CondResult(ok=False, reason=f"{label_names}: noch sichtbar ({item_place(present[0])})")
        last = rows[0] if rows else None
        out = rules.CondResult(ok=True, ctx={"items": label_names,
                                             "item_place": item_place(last) if last else ""},
                               item_ids=[r.id for r in rows[:5]],
                               image_path=last.crop_path if last else None,
                               snapshot_path=last.snapshot_path if last else None,
                               camera_id=last.camera_id if last else None)
        out.reason = (f"{label_names}: nicht sichtbar (zuletzt {item_place(last)})" if last
                      else f"{label_names}: nie gesehen")
        return out


# ------------------------------------------------------------------ inventory_untouched


def untouched_since(s, it: InventoryItem, since_dt: datetime, now: datetime) -> tuple[bool, str]:
    """Liegt der Gegenstand seit `since_dt` unberührt da? → (ok, Grund)."""
    if it.state != "present":
        return False, "nicht sichtbar"
    first = _utc(it.first_seen)
    before = s.scalar(select(InventoryHistory).where(InventoryHistory.item_id == it.id,
                                                     InventoryHistory.ts <= since_dt)
                      .order_by(InventoryHistory.ts.desc()).limit(1))
    after = list(s.scalars(select(InventoryHistory).where(InventoryHistory.item_id == it.id,
                                                          InventoryHistory.ts > since_dt)
                           .order_by(InventoryHistory.ts)).all())
    if before is None and (first is None or first > since_dt):
        return False, "erst nach dem Stichzeitpunkt aufgetaucht"
    entries = ([before] if before is not None else []) + after
    ref = None
    gap_start: Optional[datetime] = None
    gap_state = ""
    for h in entries:
        ts = _utc(h.ts)
        if h.state == "present":
            if ref is None:
                ref = list(h.bbox or [])
            if gap_start is not None:
                dur = (ts - max(gap_start, since_dt)).total_seconds()
                if _dist(ref, h.bbox) > MOVED_DIST:
                    return False, "nach einer Lücke an anderer Stelle"
                if gap_state == "missing" and dur > FLICKER_TOLERANCE_S:
                    return False, f"zwischendurch {int(dur // 60)} min weg"
                gap_start = None
            elif ts >= since_dt and _dist(ref, h.bbox) > MOVED_DIST:
                return False, "verschoben"
        else:
            if gap_start is None:
                gap_start, gap_state = ts, h.state
            elif h.state == "missing":
                gap_state = "missing"
    if ref is None:
        ref = list(it.bbox or [])
    if _dist(ref, it.bbox) > MOVED_DIST:
        return False, "verschoben"
    if gap_start is not None:
        # Historie endet mit einer Lücke, aktuell aber present (Historie wird je 5 min geschrieben) – nur kurze Lücken
        dur = (now - max(gap_start, since_dt)).total_seconds()
        if gap_state == "missing" and dur > FLICKER_TOLERANCE_S:
            return False, f"zwischendurch {int(dur // 60)} min weg"
    return True, "unberührt"


def eval_inventory_untouched(cond: dict[str, Any], ctx: rules.CondContext) -> rules.CondResult:
    c = CondInventoryUntouched(**cond)
    vocab = _vocab()
    now = ctx.now
    since_dt = _since_dt(c.since, now)
    label_names = _join_de([names.display_name(lbl, vocab) for lbl in c.labels])
    since_txt = since_dt.astimezone(config.tz).strftime("%H:%M")
    with SessionLocal() as s:
        rows = [r for r in s.scalars(select(InventoryItem).where(InventoryItem.label.in_(c.labels),
                                                                  InventoryItem.state == "present")).all()
                if _cam_ok(r.camera_id, c.camera_ids, None)]
        if not rows:
            return rules.CondResult(ok=False, reason=f"{label_names}: nicht sichtbar")
        # nur Gegenstände, die zu `since` schon da waren (später aufgetauchte sind neu bzw. Dubletten)
        old = [r for r in rows if (_utc(r.first_seen) or now) <= since_dt]
        if not old:
            return rules.CondResult(ok=False, reason=f"{label_names}: erst nach {since_txt} aufgetaucht")
        for r in old:
            ok, why = untouched_since(s, r, since_dt, now)
            if not ok:
                return rules.CondResult(ok=False, reason=f"{label_names}: seit {since_txt} berührt ({why})")
        if c.zone_ids:
            cams = sorted({r.camera_id for r in old} | set(c.camera_ids))
            evs = s.scalars(select(Event).where(Event.label == "person", Event.camera_id.in_(cams),
                                                Event.start_ts <= now,
                                                or_(Event.end_ts.is_(None), Event.end_ts >= since_dt,
                                                    Event.active.is_(True)))).all()
            zones = set(c.zone_ids)
            hit = next((e for e in evs if zones & set(e.zones or [])), None)
            if hit is not None:
                when = _utc(hit.start_ts).astimezone(config.tz).strftime("%H:%M")
                return rules.CondResult(ok=False, reason=f"{label_names}: Person um {when} in der Zone")
        top = max(old, key=lambda r: float(r.score or 0))
        return rules.CondResult(ok=True, ctx=_items_ctx(old, vocab), item_ids=[r.id for r in old],
                                image_path=top.crop_path, snapshot_path=top.snapshot_path, camera_id=top.camera_id,
                                reason=f"{label_names}: seit {since_txt} unberührt ({item_place(top)})")


# ------------------------------------------------------------------ weather


def eval_weather(cond: dict[str, Any], ctx: rules.CondContext) -> rules.CondResult:
    c = CondWeather(**cond)
    if weather.location() is None:
        return rules.CondResult(ok=False, reason="Wetter: kein Standort eingestellt")
    o = weather.rain_outlook(c.hours, c.min)
    if o is None:
        err = weather.last_error()
        return rules.CondResult(ok=False, reason=f"Wetter unbekannt{f' ({err})' if err else ''}")
    text = o.text(c.min)
    ok = o.max_probability >= c.min
    return rules.CondResult(ok=ok, ctx={"weather": text},
                            reason=f"Wetter: {text} (Schwelle {int(c.min)} % in {c.hours} h)")


# ------------------------------------------------------------------ track_came_from


def eval_track_came_from(cond: dict[str, Any], ctx: rules.CondContext) -> rules.CondResult:
    c = CondTrackCameFrom(**cond)
    tr = ctx.track
    if tr is None:
        return rules.CondResult(ok=False, reason="kam aus Zone: kein Track")
    want = {str(z) for z in c.zone_ids}
    trig_ts = ctx.trigger_ts if ctx.trigger_ts is not None else ctx.now.timestamp()
    entries = list(getattr(tr, "zone_entries", []) or [])
    trig = set(ctx.trigger_zones or [])
    # Eintritt in die Auslösezone: letzter Eintritt bis zur Auslösung (ohne Zonen der Regel = Auslösezeitpunkt)
    trig_entry = max((t for z, t in entries if z in trig and t <= trig_ts + 0.5), default=trig_ts)
    came = [z for z, t in entries if z in want and z not in trig and t < trig_entry - 0.05]
    cam = registry.get(tr.camera_id) or {}
    zmap = cam.get("zones") or {}
    zn = _join_de([zmap.get(z) or z for z in c.zone_ids])
    if came:
        return rules.CondResult(ok=True, reason=f"kam aus {zn}")
    return rules.CondResult(ok=False, reason=f"kam nicht aus {zn}")


rules.register_condition("inventory", CondInventory, evaluate=eval_inventory, label_de="Gegenstand da/weg")
rules.register_condition("inventory_untouched", CondInventoryUntouched, evaluate=eval_inventory_untouched,
                         label_de="Gegenstand unberührt")
rules.register_condition("weather", CondWeather, evaluate=eval_weather, label_de="Wetter")
rules.register_condition("track_came_from", CondTrackCameFrom, evaluate=eval_track_came_from, track_only=True,
                         label_de="kam aus Zone")


def uses_weather() -> bool:
    return any(c.get("type") == "weather" for r in rules.all_rules() if r.get("enabled")
               for c in r.get("conditions") or [])
