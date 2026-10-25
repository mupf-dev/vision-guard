"""B3 Tagesbericht – Zeitsteuerung (vom Backend-Ticker jede Sekunde aufgerufen, selbst gedrosselt).

- Genau ein `daily` je lokalem Tag: `reports.period_key = "daily:YYYY-MM-DD"` ist eindeutig; ein Backend-Neustart
  erzeugt keinen zweiten Bericht.
- Fällig ab `report_time` (Ortszeit, `zoneinfo`, Sommer-/Winterzeit korrekt) an den Wochentagen `report_days`
  (0 = Montag); ein verpasster Lauf wird bis 6 h danach nachgeholt (auch über Mitternacht).
- Zeitraum eines Tagesberichts: vom Berichtszeitpunkt des Vortags bis zum Berichtszeitpunkt (24 h, am Tag der
  Zeitumstellung 23 bzw. 25 h) – so fällt kein Ereignis zwischen zwei Berichte.
- Erzeugen und Versand laufen in einem eigenen Thread (LLM bis `llm_timeout_s`), der Ticker blockiert nie.
- Aufbewahrung: Berichte älter als `retention_days_events` werden stündlich gelöscht.
"""
from __future__ import annotations

import logging
import threading
import time as _time
from datetime import date as _date, datetime, time, timedelta, timezone
from typing import Optional

from sqlalchemy import delete, select

from .. import settings_store, state
from ..config import config
from ..db import SessionLocal
from ..models import Report

log = logging.getLogger("vg.report")

CHECK_S = 20             # so oft wird geprüft, ob ein Bericht fällig ist
CATCH_UP = timedelta(hours=6)
RETENTION_S = 3600
STARTUP_GRACE_S = 30     # nach dem Start kurz warten (MQTT-Verbindung), außer MQTT ist schon verbunden

_started = _time.monotonic()
_last_check = 0.0
_last_retention = 0.0
_busy = threading.Lock()


def _parse_hm(v: str) -> time:
    try:
        h, m = (int(x) for x in (v or "21:00").split(":", 1))
        return time(h % 24, m % 60)
    except ValueError:
        return time(21, 0)


def local_at(d: _date, t: time) -> datetime:
    """Lokaler Zeitpunkt `d t` als UTC (zoneinfo; nicht existierende Zeiten springen vorwärts)."""
    loc = datetime.combine(d, t, config.tz)
    # Hin- und Rückrechnung normalisiert z. B. 02:30 am Tag der Umstellung auf Sommerzeit
    return loc.astimezone(timezone.utc)


def period_for(d: _date, st: Optional[dict] = None) -> tuple[datetime, datetime]:
    st = st or settings_store.get()
    t = _parse_hm(st.get("report_time") or "21:00")
    return local_at(d - timedelta(days=1), t), local_at(d, t)


def due_days(now: datetime, st: dict) -> list[_date]:
    """Lokale Tage, deren Bericht jetzt fällig ist (älteste zuerst) – ohne DB-Prüfung."""
    t = _parse_hm(st.get("report_time") or "21:00")
    days = {int(x) for x in st.get("report_days") or []}
    loc_today = now.astimezone(config.tz).date()
    out = []
    for d in (loc_today - timedelta(days=1), loc_today):
        if d.weekday() not in days:
            continue
        at = local_at(d, t)
        if at <= now < at + CATCH_UP:
            out.append(d)
    return out


def period_key(d: _date) -> str:
    return f"daily:{d.isoformat()}"


def _run_due(days: list[_date]) -> None:
    from .service import generate

    try:
        for d in days:
            start, end = period_for(d)
            try:
                r = generate(start, end, kind="daily", period_key=period_key(d), deliver=True)
            except Exception:
                log.exception("Tagesbericht %s fehlgeschlagen", d)
                continue
            if r:
                log.info("Tagesbericht %s erzeugt (id %s, %s)", d, r["id"], r["generator"])
    finally:
        _busy.release()


def _retention() -> None:
    days = int(settings_store.get().get("retention_days_events") or 30)
    with SessionLocal() as s:
        n = s.execute(delete(Report).where(
            Report.created_at < datetime.now(timezone.utc) - timedelta(days=days))).rowcount or 0
        s.commit()
    if n:
        log.info("Aufräumen: %d Berichte entfernt", n)


def tick() -> None:
    global _last_check, _last_retention
    mono = _time.monotonic()
    if mono - _last_retention >= RETENTION_S and mono - _started > 120:
        _last_retention = mono
        try:
            _retention()
        except Exception:
            log.exception("Aufräumen der Berichte fehlgeschlagen")
    if mono - _last_check < CHECK_S:
        return
    _last_check = mono
    st = settings_store.get()
    if not st.get("report_enabled"):
        return
    if mono - _started < STARTUP_GRACE_S and not state.mqtt_connected:
        return
    now = datetime.now(timezone.utc)
    days = due_days(now, st)
    if not days:
        return
    with SessionLocal() as s:
        have = set(s.scalars(select(Report.period_key).where(
            Report.period_key.in_([period_key(d) for d in days]))).all())
    todo = [d for d in days if period_key(d) not in have]
    if not todo or not _busy.acquire(blocking=False):
        return
    threading.Thread(target=_run_due, args=(todo,), name="vg-report", daemon=True).start()
