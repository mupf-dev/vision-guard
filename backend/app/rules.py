"""Regel-Engine: Bedingungen prüfen (rein funktional) und Aktionen ausführen."""
from __future__ import annotations

import asyncio
import json
import logging
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Optional
from urllib.parse import quote

import httpx
from pydantic import BaseModel, ValidationError
from sqlalchemy import select

from .config import config
from .db import SessionLocal
from .models import Rule
from .schemas import (CondInventory, CondInventoryUntouched, CondTrackCameFrom, CondWeather, TimeTriggerOptions,
                      TriggerOptionsNone)

log = logging.getLogger("vg.rules")

# ------------------------------------------------------------------ Regel-Cache

_lock = threading.Lock()
_rules: list[dict[str, Any]] = []


def reload() -> None:
    from .serializers import rule_json

    with SessionLocal() as s:
        data = [rule_json(r) for r in s.scalars(select(Rule).order_by(Rule.id)).all()]
    with _lock:
        _rules[:] = data


def all_rules() -> list[dict[str, Any]]:
    with _lock:
        return list(_rules)


# ------------------------------------------------------------------ Registry (Regel-Engine v2)
#
# Trigger und Bedingungen werden hier registriert statt fest verdrahtet. Fach-Module registrieren beim Import
# eigene Typen (W2: Trigger posture/gesture, W3: Trigger/Bedingung sensor) bzw. reichen den Evaluator einer hier
# angelegten Bedingung nach (A1: rule_conditions.py). Erneutes Registrieren desselben Namens überschreibt.


@dataclass(frozen=True)
class TriggerSpec:
    name: str
    track_based: bool                    # True: aus Track-Nachrichten (Event-Bezug); False: Regel ohne Event → Notification
    options_model: type[BaseModel]       # validiert rules.trigger_options
    label_de: str = ""


@dataclass
class CondContext:
    """Kontext für die Auswertung einer Bedingung (zum Zeitpunkt der Prüfung, ggf. nach condition_delay_s)."""
    rule: dict[str, Any]
    now: datetime                                 # UTC
    camera_id: Optional[int] = None               # auslösende Kamera (None bei trigger=time)
    track: Optional["TrackState"] = None          # nur bei Track-Triggern
    event_id: Optional[str] = None


@dataclass
class CondResult:
    ok: bool
    ctx: dict[str, Any] = field(default_factory=dict)     # zusätzliche Platzhalter, z. B. items/item_place/weather
    item_ids: list[int] = field(default_factory=list)      # betroffene Gegenstände (→ Notification.item_ids)
    image_path: Optional[str] = None                      # passendes Bild (z. B. Crop des Gegenstands)
    reason: str = ""                                      # Klartext fürs Log, warum (nicht) erfüllt


ConditionEvaluator = Callable[[dict[str, Any], CondContext], CondResult]


@dataclass
class ConditionSpec:
    name: str
    model: type[BaseModel]               # validiert einen Eintrag in rules.conditions (Feld `type` = name)
    track_only: bool = False             # nur bei Track-Triggern sinnvoll (z. B. track_came_from)
    evaluate: Optional[ConditionEvaluator] = None
    label_de: str = ""


TRIGGERS: dict[str, TriggerSpec] = {}
CONDITIONS: dict[str, ConditionSpec] = {}


def register_trigger(name: str, *, track_based: bool, options_model: type[BaseModel] = TriggerOptionsNone,
                     label_de: str = "") -> TriggerSpec:
    spec = TriggerSpec(name=name, track_based=track_based, options_model=options_model, label_de=label_de or name)
    TRIGGERS[name] = spec
    return spec


def register_condition(name: str, model: type[BaseModel], *, evaluate: Optional[ConditionEvaluator] = None,
                       track_only: bool = False, label_de: str = "") -> ConditionSpec:
    prev = CONDITIONS.get(name)
    spec = ConditionSpec(name=name, model=model, track_only=track_only,
                         evaluate=evaluate or (prev.evaluate if prev else None), label_de=label_de or name)
    CONDITIONS[name] = spec
    return spec


def is_track_trigger(name: Optional[str]) -> bool:
    spec = TRIGGERS.get(name or "enter")
    return spec.track_based if spec else True


def validate_rule_parts(trigger: str, options: dict[str, Any], conditions: list[dict[str, Any]]
                        ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Prüft trigger/trigger_options/conditions gegen die Registry (für schemas.RuleIn). ValueError → 422."""
    spec = TRIGGERS.get(trigger)
    if spec is None:
        raise ValueError(f"Unbekannter Trigger: {trigger} (erlaubt: {', '.join(TRIGGERS)})")
    try:
        opts = spec.options_model(**(options or {})).model_dump(mode="json")
    except ValidationError as e:
        raise ValueError(f"trigger_options für {trigger}: {_first_error(e)}") from None
    out: list[dict[str, Any]] = []
    for i, c in enumerate(conditions or []):
        typ = (c or {}).get("type") if isinstance(c, dict) else None
        cs = CONDITIONS.get(str(typ))
        if cs is None:
            raise ValueError(f"Bedingung {i + 1}: unbekannter Typ {typ!r} (erlaubt: {', '.join(CONDITIONS)})")
        if cs.track_only and not spec.track_based:
            raise ValueError(f"Bedingung {i + 1} ({typ}) geht nur mit Track-Triggern, nicht mit {trigger}")
        try:
            out.append(cs.model(**c).model_dump(mode="json"))
        except ValidationError as e:
            raise ValueError(f"Bedingung {i + 1} ({typ}): {_first_error(e)}") from None
    return opts, out


def _first_error(e: ValidationError) -> str:
    err = e.errors()[0] if e.errors() else {}
    loc = ".".join(str(x) for x in err.get("loc") or [])
    return f"{loc}: {err.get('msg')}" if loc else str(err.get("msg") or e)


def evaluate_conditions(rule: dict[str, Any], ctx: CondContext) -> CondResult:
    """UND-Verknüpfung aller Bedingungen der Regel. Fehlt ein Evaluator oder wirft er, gilt die Bedingung als
    NICHT erfüllt (lieber kein Hinweis als ein falscher). Platzhalter/Item-IDs der Einzelergebnisse werden vereint."""
    total = CondResult(ok=True)
    for c in rule.get("conditions") or []:
        spec = CONDITIONS.get(str(c.get("type")))
        if spec is None or spec.evaluate is None:
            log.warning("Regel '%s': Bedingung %s ohne Auswertung – gilt als nicht erfüllt",
                        rule.get("name"), c.get("type"))
            return CondResult(ok=False, reason=f"{c.get('type')}: nicht verfügbar")
        try:
            r = spec.evaluate(c, ctx)
        except Exception:
            log.exception("Regel '%s': Bedingung %s fehlgeschlagen", rule.get("name"), c.get("type"))
            return CondResult(ok=False, reason=f"{c.get('type')}: Fehler")
        if not r.ok:
            return r
        total.ctx.update(r.ctx)
        total.item_ids += [i for i in r.item_ids if i not in total.item_ids]
        total.image_path = total.image_path or r.image_path
    return total


# Eingebaute Trigger
register_trigger("enter", track_based=True, label_de="betritt")
register_trigger("leave", track_based=True, label_de="verlässt")
register_trigger("dwell", track_based=True, label_de="verweilt")
register_trigger("time", track_based=False, options_model=TimeTriggerOptions, label_de="zu einer Uhrzeit")

# W1-Bedingungen (Auswertung: A1 in rule_conditions.py per register_condition(..., evaluate=…))
register_condition("inventory", CondInventory, label_de="Gegenstand da/weg")
register_condition("inventory_untouched", CondInventoryUntouched, label_de="Gegenstand unberührt")
register_condition("weather", CondWeather, label_de="Wetter")
register_condition("track_came_from", CondTrackCameFrom, track_only=True, label_de="kam aus Zone")


# ------------------------------------------------------------------ Track-Zustand


@dataclass
class TrackState:
    camera_id: int
    track_id: int
    event_id: str
    first_seen: float
    label: str
    score: float = 0.0
    best_score: float = 0.0
    zones: list[str] = field(default_factory=list)
    identity: Optional[dict[str, Any]] = None
    attributes: dict[str, Any] = field(default_factory=dict)
    started_rx: float = field(default_factory=time.time)          # lokale Empfangszeit Trackstart
    zone_since: dict[str, float] = field(default_factory=dict)     # zone_id -> lokale Eintrittszeit
    last_rx: float = field(default_factory=time.time)
    fired_enter: set[int] = field(default_factory=set)
    fired_dwell: set[int] = field(default_factory=set)
    fired_leave: set[int] = field(default_factory=set)
    clip_requested: bool = False

    def set_zones(self, zones: list[str], now: float) -> None:
        zs = [str(z) for z in (zones or [])]
        for z in zs:
            self.zone_since.setdefault(z, now)
        for z in list(self.zone_since):
            if z not in zs:
                del self.zone_since[z]
        self.zones = zs


# ------------------------------------------------------------------ Bedingungen


def _hm(s: str) -> int:
    h, m = s.split(":")
    return int(h) * 60 + int(m)


def schedule_active(schedule: Optional[dict[str, Any]], now: Optional[datetime] = None) -> bool:
    """Zeitplan in lokaler Zeitzone; Bereiche über Mitternacht (start > end) gehören zum Starttag."""
    if not schedule:
        return True
    now = (now or datetime.now(timezone.utc)).astimezone(config.tz)
    days = schedule.get("days")
    if days is None:
        days = list(range(7))
    try:
        start, end = _hm(schedule.get("start") or "00:00"), _hm(schedule.get("end") or "23:59")
    except Exception:
        return True
    t = now.hour * 60 + now.minute
    wd = now.weekday()  # 0 = Montag
    if start == end:
        return wd in days
    if start < end:
        return wd in days and start <= t < end
    # über Mitternacht
    if t >= start:
        return wd in days
    if t < end:
        return ((wd - 1) % 7) in days
    return False


def identity_matches(rule: dict[str, Any], ident: Optional[dict[str, Any]],
                     name_lookup: Callable[[int], Optional[str]]) -> bool:
    cond = rule.get("identity") or {}
    mode = cond.get("mode") or "any"
    if mode == "any":
        return True
    iid = (ident or {}).get("id")
    if mode == "specific":
        return iid is not None and int(iid) in {int(x) for x in cond.get("identity_ids") or []}
    named = bool(iid is not None and name_lookup(int(iid)))
    if mode == "known":
        return named
    if mode == "unknown":
        return not named
    return True


def static_match(rule: dict[str, Any], tr: TrackState, name_lookup: Callable[[int], Optional[str]]) -> bool:
    """Alles außer Zone/Trigger/Cooldown."""
    if not rule.get("enabled"):
        return False
    cams = rule.get("camera_ids") or []
    if cams and tr.camera_id not in cams:
        return False
    labels = rule.get("labels") or []
    if labels and tr.label not in labels:
        return False
    score = max(tr.score or 0, tr.best_score or 0)
    if score < float(rule.get("min_score") or 0):
        return False
    if not identity_matches(rule, tr.identity, name_lookup):
        return False
    if not schedule_active(rule.get("schedule")):
        return False
    return True


def rule_zones_hit(rule: dict[str, Any], zones: list[str]) -> list[str]:
    rz = [str(z) for z in rule.get("zone_ids") or []]
    return [z for z in zones if z in rz]


def dwell_seconds(rule: dict[str, Any], tr: TrackState, now: float) -> float:
    rz = rule.get("zone_ids") or []
    if not rz:
        return now - tr.started_rx
    times = [now - t for z, t in tr.zone_since.items() if z in rz]
    return max(times) if times else 0.0


# ------------------------------------------------------------------ Templates


def render(template: str, ctx: dict[str, Any], url_encode: bool = False) -> str:
    out = template
    for k, v in ctx.items():
        val = "" if v is None else str(v)
        if url_encode:
            val = quote(val, safe="")
        out = out.replace("{" + k + "}", val)
    return out


# ------------------------------------------------------------------ Webhooks


async def run_webhook(action: dict[str, Any], ctx: dict[str, Any], event: dict[str, Any]) -> None:
    url = render(action.get("url") or "", ctx, url_encode=True)
    method = (action.get("method") or "POST").upper()
    headers = {str(k): render(str(v), ctx) for k, v in (action.get("headers") or {}).items()}
    body = action.get("body")
    try:
        async with httpx.AsyncClient(timeout=10, follow_redirects=True) as client:
            if method in ("GET", "DELETE") and body is None:
                r = await client.request(method, url, headers=headers)
            elif body is None:
                headers.setdefault("Content-Type", "application/json")
                r = await client.request(method, url, headers=headers, content=json.dumps(event, default=str))
            else:
                r = await client.request(method, url, headers=headers, content=render(body, ctx).encode())
        if r.status_code >= 400:
            log.warning("Webhook %s %s → HTTP %s", method, url, r.status_code)
        else:
            log.info("Webhook %s %s → HTTP %s", method, url, r.status_code)
    except Exception as e:  # noqa: BLE001
        log.warning("Webhook %s %s fehlgeschlagen: %s", method, url, e)


def new_request_id() -> str:
    return uuid.uuid4().hex


def submit_async(loop: Optional[asyncio.AbstractEventLoop], coro) -> None:
    if loop is None or loop.is_closed():
        coro.close()
        return
    fut = asyncio.run_coroutine_threadsafe(coro, loop)

    def _done(f):
        exc = f.exception()
        if exc:
            log.warning("Async-Aktion fehlgeschlagen: %s", exc)
    fut.add_done_callback(_done)
