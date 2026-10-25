"""Wetter für Regel-Bedingungen (A1): Regenwahrscheinlichkeit aus Open-Meteo (ohne Key).

- Standort aus Setting `weather_location` ({lat, lon}, auf 2 Nachkommastellen gerundet); `null` = Wetter inaktiv,
  die Bedingung `weather` ist dann nie erfüllt.
- Abfrage höchstens alle 30 min (Cache im Speicher, je Standort). Bei Fehlern wird ein höchstens 3 h alter Cache
  weiterverwendet, danach gilt das Wetter als unbekannt (→ Bedingung nicht erfüllt). Nach einem Fehler wird frühestens
  nach 5 min erneut gefragt, damit Regelprüfungen nicht ständig auf das Netz warten.
- An Open-Meteo gehen nur die gerundeten Koordinaten. Basis-URL über Env `VG_WEATHER_URL` änderbar (Tests mit einem
  lokalen Mock-Server; Default `https://api.open-meteo.com/v1/forecast`).
"""
from __future__ import annotations

import logging
import os
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import httpx

from . import settings_store
from .config import config

log = logging.getLogger("vg.weather")

WEATHER_URL = (os.getenv("VG_WEATHER_URL") or "https://api.open-meteo.com/v1/forecast").rstrip("/")
REFRESH_S = 30 * 60          # reguläre Aktualisierung
MAX_AGE_S = 3 * 3600         # so alt darf ein Cache bei Fehlern höchstens werden
RETRY_AFTER_ERROR_S = 5 * 60
TIMEOUT_S = 8.0

_lock = threading.Lock()
_fetch_lock = threading.Lock()


@dataclass
class _Cache:
    key: tuple[float, float]
    fetched: float                                # time.time() der erfolgreichen Abfrage
    hours: list[tuple[float, float, float]]       # (unix_ts Stundenbeginn, Regenwahrscheinlichkeit %, Niederschlag mm)


_cache: Optional[_Cache] = None
_last_error: tuple[float, str] = (0.0, "")       # (Zeitpunkt, Text)


@dataclass
class RainOutlook:
    max_probability: float              # höchste Regenwahrscheinlichkeit im Zeitraum (%)
    start_ts: Optional[float]           # erste Stunde mit ≥ Schwelle (Unix-s) bzw. None
    precipitation_mm: float             # Summe Niederschlag im Zeitraum
    hours: int
    fetched: float

    def text(self, threshold: float) -> str:
        """„70 % Regen ab 15 Uhr“ / „70 % Regen ab morgen 6 Uhr“ / „höchstens 10 % Regen“ / „kein Regen erwartet“."""
        p = int(round(self.max_probability))
        if p <= 0:
            return "kein Regen erwartet"
        if threshold > 0 and self.max_probability < threshold:
            return f"höchstens {p} % Regen"
        if self.start_ts is None:
            return f"{p} % Regen in den nächsten {self.hours} h"
        now = datetime.now(timezone.utc).astimezone(config.tz)
        start = datetime.fromtimestamp(self.start_ts, timezone.utc).astimezone(config.tz)
        if start <= now:
            return f"{p} % Regen, schon jetzt"
        day = "" if start.date() == now.date() else ("morgen " if start.date() == (now + timedelta(days=1)).date()
                                                     else start.strftime("%d.%m. "))
        return f"{p} % Regen ab {day}{start.hour} Uhr"


def location() -> Optional[tuple[float, float]]:
    loc = settings_store.get().get("weather_location")
    if not isinstance(loc, dict):
        return None
    try:
        return round(float(loc["lat"]), 2), round(float(loc["lon"]), 2)
    except (KeyError, TypeError, ValueError):
        return None


def last_error() -> Optional[str]:
    with _lock:
        t, txt = _last_error
    return txt if t and time.time() - t < MAX_AGE_S else None


def _fetch(key: tuple[float, float]) -> _Cache:
    params = {"latitude": f"{key[0]:.2f}", "longitude": f"{key[1]:.2f}",
              "hourly": "precipitation_probability,precipitation", "timeformat": "unixtime",
              "timezone": "UTC", "forecast_days": 3}
    with httpx.Client(timeout=TIMEOUT_S) as client:
        r = client.get(WEATHER_URL, params=params)
    if r.status_code != 200:
        raise RuntimeError(f"HTTP {r.status_code}")
    data = r.json()
    hourly = data.get("hourly") or {}
    times = hourly.get("time") or []
    probs = hourly.get("precipitation_probability") or []
    mm = hourly.get("precipitation") or []
    hours: list[tuple[float, float, float]] = []
    for i, t in enumerate(times):
        try:
            p = probs[i] if i < len(probs) else None
            if p is None:
                continue
            hours.append((float(t), float(p), float(mm[i] or 0) if i < len(mm) and mm[i] is not None else 0.0))
        except (TypeError, ValueError):
            continue
    if not hours:
        raise RuntimeError("Antwort ohne Regenwahrscheinlichkeit")
    return _Cache(key=key, fetched=time.time(), hours=hours)


def _get_cache(force: bool = False) -> Optional[_Cache]:
    """Aktueller Cache für den eingestellten Standort; fragt bei Bedarf Open-Meteo (synchron, ≤ 8 s)."""
    global _cache, _last_error
    key = location()
    if key is None:
        return None
    now = time.time()
    with _lock:
        c = _cache if _cache is not None and _cache.key == key else None
        err_t = _last_error[0]
    if c is not None and not force and now - c.fetched < REFRESH_S:
        return c
    if not force and err_t and now - err_t < RETRY_AFTER_ERROR_S:
        return c if c is not None and now - c.fetched < MAX_AGE_S else None
    with _fetch_lock:   # nur eine Abfrage gleichzeitig
        with _lock:
            c2 = _cache if _cache is not None and _cache.key == key else None
        if c2 is not None and not force and time.time() - c2.fetched < REFRESH_S:
            return c2
        try:
            fresh = _fetch(key)
        except Exception as e:  # noqa: BLE001
            msg = str(e) if isinstance(e, RuntimeError) else type(e).__name__
            log.warning("Wetter (Open-Meteo) nicht abrufbar: %s", msg)
            with _lock:
                _last_error = (time.time(), msg)
            c = c2 or c
            return c if c is not None and time.time() - c.fetched < MAX_AGE_S else None
        with _lock:
            _cache = fresh
            _last_error = (0.0, "")
        log.info("Wetter aktualisiert (%d Stunden Vorhersage)", len(fresh.hours))
        return fresh


def rain_outlook(hours: int, threshold: float = 0.0) -> Optional[RainOutlook]:
    """Regen in den nächsten `hours` Stunden (inkl. der laufenden Stunde) oder None (unbekannt/inaktiv)."""
    c = _get_cache()
    if c is None:
        return None
    now = time.time()
    hour_start = now - (now % 3600)
    end = hour_start + hours * 3600
    window = [h for h in c.hours if hour_start <= h[0] < end]
    if not window:
        return None
    mx = max(h[1] for h in window)
    # „ab …“ = erste Stunde über der Schwelle; ohne Schwelle die erste Stunde mit dem Höchstwert
    thr = threshold if threshold > 0 else max(mx, 1.0)
    start = next((h[0] for h in window if h[1] >= thr), None)
    return RainOutlook(max_probability=mx, start_ts=start, precipitation_mm=round(sum(h[2] for h in window), 1),
                       hours=hours, fetched=c.fetched)


def prefetch() -> None:
    """Hält den Cache warm (vom Regel-Takt aufgerufen, wenn Regeln mit Wetter-Bedingung existieren)."""
    try:
        _get_cache()
    except Exception:  # noqa: BLE001
        log.debug("Wetter-Vorabruf fehlgeschlagen", exc_info=True)


def status() -> dict[str, Any]:
    """Für die Regel-Prüfung im UI: Standort gesetzt? Letzter Abruf? Fehler?"""
    with _lock:
        c = _cache
    return {"location": location(), "fetched": c.fetched if c else None, "error": last_error()}
