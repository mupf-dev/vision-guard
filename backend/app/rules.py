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
from sqlalchemy import select

from .config import config
from .db import SessionLocal
from .models import Rule

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
