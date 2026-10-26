"""Haustier-Tagebuch (C3) – Vertrag: CONTRACT.md „Nachträge: Welle 2“, W2.F.

Erfassung (Track-Thread, über `events.on_track`):
- Ein Tier (Label aus `pet_diary_labels`) betritt eine Zone mit `purpose` (`food|water|litter`) → offener Besuch.
  Beginn = lokale Eintrittszeit (`TrackState.zone_since`), Ende = Verlassen der Zone bzw. Track-Ende.
- Gezählt (= in `pet_visits` gespeichert) wird ein Besuch, sobald er `pet_diary_min_visit_s[purpose]` dauert –
  schon während des Besuchs (Ticker), damit Seite/MQTT live mitzählen; beim Ende wird `end_ts/duration_s` ergänzt.
- Besuche desselben Tiers (Identität bzw. ohne Identität: Label) an derselben Kamera und mit gleichem Zweck, die
  höchstens `MERGE_GAP_S` (60 s) auseinanderliegen, werden zu einem Besuch zusammengefasst (auch kurze Aussetzer,
  Track-Abriss und neue Track-ID).
- Tier = Identität des Tracks (DINOv2). Kommt sie später, wird sie für alle Besuche des Tracks nachgetragen.
  Ohne Identität und mit `pet_diary_single_pet_fallback`: Gibt es genau eine benannte Tier-Identität, die zu
  diesem Label passt, wird der Besuch ihr zugeordnet (eine später erkannte echte Identität ersetzt das).

Auswertung (Ticker-Thread, `tick()`):
- Normalwert je Tier und Zweck aus den 14 Tagen vor dem Stichtag (mind. 7 Tage mit Daten): Tageszahl P10/Median/P90,
  dieselben Quantile für „bis zu dieser Uhrzeit“ (Hochrechnung) und die längste übliche Pause (P95 der Abstände).
- Stündlich: `too_few` (heute < P10 bis jetzt − 1), `too_many` (heute > P90 + 2), `long_gap` (aktuelle Pause
  > 1,5 × P95). Nach Mitternacht einmal der Abschluss des Vortags (`too_few`/`too_many` mit Ganztageswerten).
- Hinweis = Notification `source="pet"`, `level="warning"`, Ziele `notify_routes.pet`, je Tier/Zweck/Tag/Art
  höchstens einmal (`dedup_key`). `pet_diary_notify=false` → keine Hinweise (Erfassung läuft weiter).

Außerdem: WS `pet_visit`, MQTT extern `{prefix}/pets/{pet_slug}/{purpose}` (retain, `{today, last_ts}`),
Aufbewahrung `cleanup()` (aus `retention.run_once`, wie Events).
"""
from __future__ import annotations

import logging
import math
import threading
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any, Iterable, Optional

from sqlalchemy import delete, func, select, update

from . import i18n, registry, settings_store
from .config import config
from .db import SessionLocal
from .models import Event, Identity, IdentitySample, PetVisit
from .schemas import iso, media_url
from .util import from_unix, slugify
from .ws import hub

log = logging.getLogger("vg.pets")

PURPOSES = ("food", "water", "litter")

MERGE_GAP_S = 60.0          # Vertrag: Besuche innerhalb von 60 s zusammenfassen
HISTORY_DAYS = 14           # Normalwert aus 14 Tagen
MIN_HISTORY_DAYS = 7        # mindestens 7 Tage Daten
MIN_GAPS = 10               # long_gap erst ab so vielen beobachteten Pausen
GAP_FACTOR = 1.5
LOW_MARGIN = 1              # too_few: heute < P10 − 1
HIGH_MARGIN = 2             # too_many: heute > P90 + 2
CHECK_FIRST_DELAY_S = 30.0  # erste Prüfung kurz nach dem Start
NOTIFY_DEDUP_S = 3 * 86400  # Schlüssel enthält den Tag – nur gegen Doppelungen durch Neustarts
FALLBACK_CACHE_S = 60.0


# ------------------------------------------------------------------ Laufzeitzustand


@dataclass
class _Visit:
    camera_id: int
    track_id: int
    zone_id: str
    purpose: str
    label: str
    event_id: Optional[str]
    start: float                      # lokale Unix-s
    identity_id: Optional[int] = None
    fallback: bool = False            # Identität nur aus `single_pet_fallback`
    visit_id: Optional[int] = None    # DB-Zeile, sobald gezählt
    end: Optional[float] = None


@dataclass
class _State:
    open: dict[tuple[int, int, str], _Visit] = field(default_factory=dict)       # (cam, track, zone)
    recent: list[_Visit] = field(default_factory=list)                           # beendet, ≤ MERGE_GAP_S alt
    track_visits: dict[tuple[int, int], set[int]] = field(default_factory=dict)  # Besuchs-IDs je laufendem Track
    fallback_ids: set[int] = field(default_factory=set)                          # Besuche mit Fallback-Identität
    applied: dict[tuple[int, int], int] = field(default_factory=dict)            # zuletzt nachgetragene Identität
    started_at: float = 0.0
    last_persist: float = 0.0
    last_check_hour: Optional[str] = None
    closed_day: Optional[str] = None                                            # Vortag bereits abgeschlossen
    mqtt_day: Optional[str] = None
    fallback_cache: dict[str, tuple[float, Optional[int]]] = field(default_factory=dict)


_lock = threading.RLock()
_st = _State()
_unhook = None


# ------------------------------------------------------------------ Hilfen


def _settings() -> dict[str, Any]:
    return settings_store.get()


def _labels() -> set[str]:
    return {str(x) for x in (_settings().get("pet_diary_labels") or [])}


def _min_visit_s(purpose: str) -> float:
    m = _settings().get("pet_diary_min_visit_s") or {}
    try:
        return max(0.0, float(m.get(purpose, 0)))
    except (TypeError, ValueError):
        return 0.0


def _local(ts: float | datetime) -> datetime:
    if isinstance(ts, datetime):
        return ts.astimezone(config.tz)
    return datetime.fromtimestamp(ts, config.tz)


def _local_midnight(d: date) -> datetime:
    return datetime(d.year, d.month, d.day, tzinfo=config.tz)


def _identity_id(ident: Any) -> Optional[int]:
    try:
        return int((ident or {}).get("id"))
    except (TypeError, ValueError, AttributeError):
        return None


def _pet_key(identity_id: Optional[int], label: str) -> str:
    return f"id:{identity_id}" if identity_id else f"label:{label}"


def _quantile(values: list[float], q: float) -> float:
    """Lineare Interpolation (wie numpy.percentile, Methode linear)."""
    if not values:
        return 0.0
    xs = sorted(values)
    pos = (len(xs) - 1) * q
    lo, hi = math.floor(pos), math.ceil(pos)
    return xs[lo] + (xs[hi] - xs[lo]) * (pos - lo)


def _fmt_dur(sec: float) -> str:
    if sec < 3600:
        return i18n.t("pets.dur_min", n=max(1, round(sec / 60)))
    h = sec / 3600
    return i18n.t("pets.dur_h", n=i18n.fmt_num(h, 1) if h < 10 else round(h))


def _range_text(lo: int, hi: int) -> str:
    return f"{lo}" if lo == hi else f"{lo}–{hi}"


def pet_name(identity: Optional[Identity], label: str) -> str:
    if identity is not None:
        return identity.name or identity.display_name
    return i18n.t("pets.unknown", label=i18n.label(label))


def pet_slug(identity: Optional[Identity], label: str) -> str:
    if identity is not None:
        if identity.name:
            return slugify(identity.name)
        # MQTT-Topics müssen sprachunabhängig stabil bleiben: unbenannte Tiere immer mit der deutschen Form
        # („Tier #3“ → tier_3), wie vor der Mehrsprachigkeit
        from . import i18n
        with i18n.use_lang("de"):
            return slugify(identity.display_name or f"tier_{identity.id}")
    return slugify(label)


# ------------------------------------------------------------------ Single-Pet-Fallback


def _fallback_identity(label: str) -> Optional[int]:
    """Genau eine benannte Tier-Identität, die zu `label` passt (keine Events mit einem anderen Label)."""
    if not _settings().get("pet_diary_single_pet_fallback", True):
        return None
    now = time.time()
    hit = _st.fallback_cache.get(label)
    if hit and now - hit[0] < FALLBACK_CACHE_S:
        return hit[1]
    result: Optional[int] = None
    try:
        with SessionLocal() as s:
            named = list(s.scalars(select(Identity.id).where(Identity.kind == "pet", Identity.name.is_not(None))))
            if named:
                labels: dict[int, set[str]] = {i: set() for i in named}
                for iid, lbl in s.execute(select(Event.identity_id, Event.label).where(
                        Event.identity_id.in_(named)).group_by(Event.identity_id, Event.label)):
                    labels[iid].add(lbl)
                cands = [i for i in named if not labels[i] or label in labels[i]]
                if len(cands) == 1:
                    result = cands[0]
    except Exception:  # noqa: BLE001
        log.exception("Tier-Fallback nicht ermittelbar")
    _st.fallback_cache[label] = (now, result)
    return result


def invalidate_fallback() -> None:
    with _lock:
        _st.fallback_cache.clear()


# ------------------------------------------------------------------ JSON / Versand


def visit_json(s, v: PetVisit, *, ident: Any = "load", thumb: Any = "load") -> dict[str, Any]:
    cam = registry.get(v.camera_id) or {}
    if ident == "load":
        ident = s.get(Identity, v.identity_id) if v.identity_id else None
    if thumb == "load":
        thumb = s.scalar(select(Event.thumb_path).where(Event.id == v.event_id)) if v.event_id else None
    return {
        "id": v.id,
        "identity": ({"id": ident.id, "kind": ident.kind, "display_name": ident.display_name, "name": ident.name}
                     if ident is not None else None),
        "label": v.label,
        "camera_id": v.camera_id,
        "camera_name": cam.get("name") or i18n.t("pets.camera", id=v.camera_id),
        "zone_id": v.zone_id,
        "zone_name": (cam.get("zones") or {}).get(v.zone_id) or v.zone_id,
        "purpose": v.purpose,
        "event_id": v.event_id,
        "start_ts": iso(v.start_ts),
        "end_ts": iso(v.end_ts),
        "duration_s": round(float(v.duration_s), 1) if v.duration_s is not None else None,
        "thumb_url": media_url(thumb),
    }


def visits_json(s, rows: list[PetVisit]) -> list[dict[str, Any]]:
    """Batch-Variante (Identitäten und Vorschaubilder in je einer Abfrage)."""
    iids = {r.identity_id for r in rows if r.identity_id}
    eids = {r.event_id for r in rows if r.event_id}
    idents = {i.id: i for i in s.scalars(select(Identity).where(Identity.id.in_(iids)))} if iids else {}
    thumbs = dict(s.execute(select(Event.id, Event.thumb_path).where(Event.id.in_(eids))).all()) if eids else {}
    return [visit_json(s, r, ident=idents.get(r.identity_id), thumb=thumbs.get(r.event_id)) for r in rows]


def _today_bounds(now: Optional[float] = None) -> tuple[datetime, datetime]:
    d = _local(now or time.time()).date()
    start = _local_midnight(d)
    return start.astimezone(timezone.utc), (start + timedelta(days=1)).astimezone(timezone.utc)


def _publish_counter(s, identity_id: Optional[int], label: str, purpose: str) -> None:
    """MQTT extern `{prefix}/pets/{pet_slug}/{purpose}` (retain) `{today, last_ts}`."""
    try:
        from .mqtt_bus import bus   # zyklischen Import vermeiden

        a, b = _today_bounds()
        q = select(func.count(), func.max(func.coalesce(PetVisit.end_ts, PetVisit.start_ts))).where(
            PetVisit.purpose == purpose, PetVisit.start_ts >= a, PetVisit.start_ts < b)
        q = q.where(PetVisit.identity_id == identity_id) if identity_id else \
            q.where(PetVisit.identity_id.is_(None), PetVisit.label == label)
        n, last = s.execute(q).one()
        if last is None:   # heute noch nichts: letzter Besuch überhaupt
            q2 = select(func.max(func.coalesce(PetVisit.end_ts, PetVisit.start_ts))).where(PetVisit.purpose == purpose)
            q2 = q2.where(PetVisit.identity_id == identity_id) if identity_id else \
                q2.where(PetVisit.identity_id.is_(None), PetVisit.label == label)
            last = s.scalar(q2)
        ident = s.get(Identity, identity_id) if identity_id else None
        prefix = _settings()["mqtt_prefix"]
        bus.publish(f"{prefix}/pets/{pet_slug(ident, label)}/{purpose}",
                    {"today": int(n or 0), "last_ts": iso(last)}, retain=True, qos=1)
    except Exception as e:  # noqa: BLE001
        log.warning("MQTT Tier-Zähler fehlgeschlagen: %s", e)


def _emit(s, row: PetVisit, action: str) -> None:
    s.refresh(row)
    hub.broadcast({"type": "pet_visit", "action": action, "visit": visit_json(s, row)})
    _publish_counter(s, row.identity_id, row.label, row.purpose)


# ------------------------------------------------------------------ Erfassung (Track-Hook)


def _on_track(hook) -> None:
    if hook.label not in _labels():
        return
    cam = registry.get(hook.camera_id)
    purposes = (cam or {}).get("zone_purpose") or {}
    now = time.time()
    with _lock:
        tkey = (hook.camera_id, hook.track_id)
        iid = _identity_id(hook.track.identity) or _identity_id(hook.msg.get("identity"))
        if iid and _st.applied.get(tkey) != iid:
            _apply_identity(tkey, iid)
        if hook.phase == "end":
            end = _end_time(hook.msg, now)
            for k in [k for k in _st.open if k[:2] == tkey]:
                _close(_st.open.pop(k), end)
            _st.track_visits.pop(tkey, None)
            _st.applied.pop(tkey, None)
            return
        if not purposes:
            return
        inside = {z for z in hook.track.zones if z in purposes}
        # verlassene Zonen schließen
        for k in [k for k in _st.open if k[:2] == tkey and k[2] not in inside]:
            _close(_st.open.pop(k), now)
        # neue Zonen öffnen
        for z in inside:
            if (tkey + (z,)) in _st.open:
                continue
            start = float(hook.track.zone_since.get(z, now))
            _open(hook, z, purposes[z], start, iid)


def _end_time(msg: dict[str, Any], now: float) -> float:
    """Track-Ende: letzter Treffer laut Worker (end kommt ≥ 2 s später), auf plausible Werte begrenzt."""
    try:
        ls = float(msg.get("last_seen") or 0)
    except (TypeError, ValueError):
        ls = 0.0
    return ls if now - 30.0 <= ls <= now else now


def _open(hook, zone_id: str, purpose: str, start: float, iid: Optional[int]) -> None:
    fallback = False
    if not iid:
        iid = _fallback_identity(hook.label)
        fallback = iid is not None
    v = _Visit(camera_id=hook.camera_id, track_id=hook.track_id, zone_id=zone_id, purpose=purpose,
               label=hook.label, event_id=hook.event_id, start=start, identity_id=iid, fallback=fallback)
    # Zusammenfassen: gleicher Tier-Schlüssel, Kamera und Zweck, Ende ≤ 60 s vor dem neuen Beginn
    key = _pet_key(iid, hook.label)
    for prev in sorted(_st.recent, key=lambda r: -(r.end or 0)):
        if (prev.camera_id == v.camera_id and prev.purpose == purpose and _pet_key(prev.identity_id, prev.label) == key
                and prev.end is not None and start - prev.end <= MERGE_GAP_S):
            _st.recent.remove(prev)
            v.start = min(prev.start, start)
            v.visit_id = prev.visit_id
            if prev.visit_id is not None:
                v.event_id = prev.event_id   # Vorschaubild des ersten Abschnitts behalten
            break
    _st.open[(v.camera_id, v.track_id, zone_id)] = v
    if v.visit_id is not None:
        _st.track_visits.setdefault((v.camera_id, v.track_id), set()).add(v.visit_id)
        _persist(v, time.time(), final=False)   # Ende wieder offen (Besuch läuft weiter)


def _close(v: _Visit, end: float) -> None:
    v.end = max(end, v.start)
    _persist(v, v.end, final=True)
    _st.recent.append(v)


def _persist(v: _Visit, now: float, *, final: bool) -> None:
    """Zählt den Besuch (ab Mindestdauer) bzw. aktualisiert Ende/Dauer. Ohne Mindestdauer: nichts speichern."""
    dur = max(0.0, (v.end if final and v.end is not None else now) - v.start)
    try:
        with SessionLocal() as s:
            if v.visit_id is None:
                if dur < _min_visit_s(v.purpose):
                    return
                row = PetVisit(identity_id=v.identity_id, label=v.label, camera_id=v.camera_id, zone_id=v.zone_id,
                               purpose=v.purpose, event_id=v.event_id, start_ts=from_unix(v.start),
                               end_ts=from_unix(v.end) if final else None, duration_s=round(dur, 1) if final else None)
                s.add(row)
                s.commit()
                v.visit_id = row.id
                _st.track_visits.setdefault((v.camera_id, v.track_id), set()).add(row.id)
                if v.fallback:
                    _st.fallback_ids.add(row.id)
                log.info("Tierbesuch %s: %s %s (Kamera %s, Zone %s)", row.id, v.label, v.purpose,
                         v.camera_id, v.zone_id)
                _emit(s, row, "new")
                return
            row = s.get(PetVisit, v.visit_id)
            if row is None:
                v.visit_id = None
                return
            row.start_ts = from_unix(v.start)
            row.end_ts = from_unix(v.end) if final else None
            row.duration_s = round(dur, 1) if final else None
            if v.identity_id and row.identity_id != v.identity_id:
                row.identity_id = v.identity_id
            s.commit()
            _emit(s, row, "update")
    except Exception:  # noqa: BLE001
        log.exception("Tierbesuch nicht gespeichert")


def _apply_identity(tkey: tuple[int, int], iid: int) -> None:
    """Echte Identität für alle Besuche des Tracks nachtragen (ersetzt fehlende bzw. Fallback-Zuordnung)."""
    for k, v in _st.open.items():
        if k[:2] == tkey and v.identity_id != iid and (v.identity_id is None or v.fallback):
            v.identity_id, v.fallback = iid, False
    ids = [i for i in _st.track_visits.get(tkey, set())]
    if not ids:
        return
    _st.applied[tkey] = iid
    try:
        with SessionLocal() as s:
            rows = list(s.scalars(select(PetVisit).where(PetVisit.id.in_(ids), PetVisit.identity_id.is_distinct_from(iid))))
            changed = []
            for r in rows:
                if r.identity_id is None or r.id in _st.fallback_ids:
                    old = (r.identity_id, r.label)
                    r.identity_id = iid
                    _st.fallback_ids.discard(r.id)
                    changed.append((r, old))
            if not changed:
                return
            s.commit()
            for r, old in changed:
                _emit(s, r, "update")
                _publish_counter(s, old[0], old[1], r.purpose)   # alter Zähler (unbekannt/Fallback) sinkt
    except Exception:  # noqa: BLE001
        log.exception("Identität für Tierbesuche nicht nachgetragen")


def _tick_visits(now: float) -> None:
    """Laufende Besuche ab Mindestdauer zählen, verwaiste schließen, alte `recent` vergessen."""
    from .events import processor   # zyklischen Import vermeiden

    with processor.lock:   # gleiche Sperr-Reihenfolge wie im Track-Hook (processor.lock → _lock)
        alive = set(processor.tracks)
    with _lock:
        for k, v in list(_st.open.items()):
            if (v.camera_id, v.track_id) not in alive and now - v.start > 10:
                _close(_st.open.pop(k), now)   # Track ohne Ende-Meldung verschwunden
            elif v.visit_id is None and now - v.start >= _min_visit_s(v.purpose):
                _persist(v, now, final=False)
        _st.recent = [r for r in _st.recent if r.end is not None and now - r.end <= MERGE_GAP_S]
        for tkey in [t for t in _st.track_visits if t not in alive]:
            if not any(k[:2] == tkey for k in _st.open):
                _st.track_visits.pop(tkey, None)
                _st.applied.pop(tkey, None)
        if len(_st.fallback_ids) > 1000:
            _st.fallback_ids.clear()


# ------------------------------------------------------------------ Statistik / Normalwerte


@dataclass
class Stats:
    today: int
    last_ts: Optional[datetime]
    daily: list[dict[str, Any]]
    normal: Optional[list[int]]            # [lo, hi] ganzer Tag (P10/P90 gerundet)
    median: Optional[float]
    expected_now: Optional[list[int]]      # [lo, hi] bis zur aktuellen Uhrzeit
    gap_s: Optional[float]                 # aktuelle Pause seit dem letzten Besuch
    usual_gap_s: Optional[float]           # P95 der Pausen
    history_days: int
    status: str                            # ok|low|high|gap|unknown
    p10: float = 0.0
    p90: float = 0.0
    p10_now: float = 0.0


def _load_rows(s, since_utc: datetime, until_utc: Optional[datetime] = None) -> list[tuple]:
    q = select(PetVisit.identity_id, PetVisit.label, PetVisit.purpose, PetVisit.start_ts, PetVisit.end_ts) \
        .where(PetVisit.start_ts >= since_utc).order_by(PetVisit.start_ts)
    if until_utc is not None:
        q = q.where(PetVisit.start_ts < until_utc)
    return list(s.execute(q).all())


def _first_seen(s) -> dict[tuple[str, str], datetime]:
    """Erster Besuch je (Tier-Schlüssel, Zweck) – Beginn der Datenbasis für den Normalwert."""
    out: dict[tuple[str, str], datetime] = {}
    q = select(PetVisit.identity_id, PetVisit.label, PetVisit.purpose, func.min(PetVisit.start_ts)) \
        .group_by(PetVisit.identity_id, PetVisit.label, PetVisit.purpose)
    for iid, label, purpose, first in s.execute(q).all():
        k = (_pet_key(iid, label), purpose)
        out[k] = min(out.get(k, first), first)
    return out


def compute_stats(starts: list[tuple[datetime, Optional[datetime]]], first: Optional[datetime], day: date,
                  as_of: Optional[datetime], days: int = HISTORY_DAYS) -> Stats:
    """Kennzahlen eines Tiers/Zwecks für den Stichtag `day`.

    `starts` = alle Besuche [(start, end)] (UTC, mind. 14 Tage vor `day` bis `as_of`), `as_of` = Zeitpunkt der
    Auswertung (None = ganzer Tag, z. B. Abschluss des Vortags)."""
    tz = config.tz
    by_day: dict[date, list[tuple[datetime, Optional[datetime]]]] = {}
    for st_, en in starts:
        by_day.setdefault(st_.astimezone(tz).date(), []).append((st_, en))
    hist_days = [day - timedelta(days=i) for i in range(HISTORY_DAYS, 0, -1)]
    if first is not None:
        fd = first.astimezone(tz).date()
        hist_days = [d for d in hist_days if d >= fd]
    end_of_window = as_of or (_local_midnight(day) + timedelta(days=1))
    today_list = [x for x in by_day.get(day, []) if x[0] < end_of_window]
    today = len(today_list)
    last_ts = None
    if starts:
        relevant = [x for x in starts if x[0] < end_of_window]
        if relevant:
            last_ts = max((en or st_) for st_, en in relevant)
    daily = []
    for i in range(days - 1, -1, -1):
        d = day - timedelta(days=i)
        daily.append({"day": d.isoformat(), "count": today if d == day else len(by_day.get(d, []))})
    st = Stats(today=today, last_ts=last_ts, daily=daily, normal=None, median=None, expected_now=None, gap_s=None,
               usual_gap_s=None, history_days=len(hist_days), status="unknown")
    if as_of is not None and last_ts is not None:
        st.gap_s = max(0.0, (as_of - last_ts).total_seconds())
    if len(hist_days) < MIN_HISTORY_DAYS:
        return st
    counts = [len(by_day.get(d, [])) for d in hist_days]
    st.p10, st.p90 = _quantile(counts, 0.10), _quantile(counts, 0.90)
    st.median = round(_quantile(counts, 0.5), 1)
    st.normal = [int(round(st.p10)), int(round(st.p90))]
    if as_of is not None:
        tod = as_of.astimezone(tz) - _local_midnight(day)
        upto = [sum(1 for s_, _ in by_day.get(d, []) if s_ < _local_midnight(d) + tod) for d in hist_days]
        st.p10_now = _quantile(upto, 0.10)
        st.expected_now = [int(round(st.p10_now)), int(round(_quantile(upto, 0.90)))]
    else:
        st.p10_now = st.p10
        st.expected_now = list(st.normal)
    # übliche längste Pause: Abstände aufeinanderfolgender Besuche in den Vergleichstagen
    hist = sorted(x for d in hist_days for x in by_day.get(d, []))
    gaps = [(b[0] - (a[1] or a[0])).total_seconds() for a, b in zip(hist, hist[1:])]
    gaps = [g for g in gaps if g > 0]
    if len(gaps) >= MIN_GAPS:
        st.usual_gap_s = _quantile(gaps, 0.95)
    st.status = "ok"
    if today > st.p90 + HIGH_MARGIN:
        st.status = "high"
    elif today < st.p10_now - LOW_MARGIN:
        st.status = "low"
    elif st.gap_s is not None and st.usual_gap_s and st.gap_s > GAP_FACTOR * st.usual_gap_s:
        st.status = "gap"
    return st


def _groups(rows: Iterable[tuple]) -> dict[tuple[Optional[int], str], dict[str, list]]:
    """{(identity_id, label|""): {purpose: [(start, end)]}} – mit Identität ohne Label-Trennung."""
    out: dict[tuple[Optional[int], str], dict[str, list]] = {}
    for iid, label, purpose, st_, en in rows:
        key = (iid, "") if iid else (None, label)
        out.setdefault(key, {}).setdefault(purpose, []).append((st_, en))
    return out


def summary(days: int = 14) -> list[dict[str, Any]]:
    """REST `GET /pets/summary` – je Tier (bzw. unbekanntem Tier je Label) die Zwecke mit Besuchen."""
    now = datetime.now(timezone.utc)
    today = now.astimezone(config.tz).date()
    since = _local_midnight(today - timedelta(days=max(days, HISTORY_DAYS + 1))).astimezone(timezone.utc)
    with SessionLocal() as s:
        rows = _load_rows(s, since)
        firsts = _first_seen(s)
        groups = _groups(rows)
        iids = [k[0] for k in groups if k[0]]
        idents = {i.id: i for i in s.scalars(select(Identity).where(Identity.id.in_(iids)))} if iids else {}
        labels = {}
        for iid, label, *_ in rows:
            if iid:
                labels.setdefault(iid, label)
        out = []
        for (iid, lbl), purposes in groups.items():
            ident = idents.get(iid) if iid else None
            label = lbl or labels.get(iid, "")
            entry: dict[str, Any] = {
                "identity": ({"id": ident.id, "kind": ident.kind, "display_name": ident.display_name,
                              "name": ident.name} if ident else None),
                "label": label, "name": pet_name(ident, label), "purposes": {}}
            for purpose in PURPOSES:
                lst = purposes.get(purpose)
                if not lst:
                    continue
                st = compute_stats(lst, firsts.get((_pet_key(iid, label), purpose)), today, now, days=days)
                entry["purposes"][purpose] = {
                    "today": st.today, "normal": st.normal, "last_ts": iso(st.last_ts), "daily": st.daily,
                    "median": st.median, "expected_now": st.expected_now, "status": st.status,
                    "gap_s": round(st.gap_s) if st.gap_s is not None else None,
                    "usual_gap_s": round(st.usual_gap_s) if st.usual_gap_s else None,
                }
            out.append(entry)
    # benannte zuerst, dann nach Name
    out.sort(key=lambda e: (e["identity"] is None or not e["identity"].get("name"), e["name"].lower()))
    return out


# ------------------------------------------------------------------ Hinweise


def _cover_path(s, iid: Optional[int]) -> Optional[str]:
    if not iid:
        return None
    return s.scalar(select(IdentitySample.image_path).where(
        IdentitySample.identity_id == iid, IdentitySample.image_path.is_not(None))
        .order_by(IdentitySample.quality.desc(), IdentitySample.id.desc()).limit(1))


def _alert(s, *, kind: str, iid: Optional[int], label: str, purpose: str, day: date, value: float,
           expected: list[int], title: str, message: str) -> Optional[dict[str, Any]]:
    from . import notifications   # zyklischen Import vermeiden

    key = f"pet:{iid or label}:{purpose}:{day.isoformat()}:{kind}"
    return notifications.create(
        source="pet", level="warning", title=title, message=message, image_path=_cover_path(s, iid),
        data={"type": kind, "identity_id": iid, "label": label, "purpose": purpose, "day": day.isoformat(),
              "value": value, "expected": expected},
        dedup_key=key, dedup_s=NOTIFY_DEDUP_S)


def check(now_ts: Optional[float] = None, *, close_day: Optional[date] = None) -> list[dict[str, Any]]:
    """Prüft alle Tiere/Zwecke und legt Hinweise an. `close_day` = Abschluss eines vergangenen Tages
    (Ganztageswerte, nur too_few/too_many); sonst laufender Tag bis `now`. Liefert die neuen Notifications."""
    if not _settings().get("pet_diary_notify", True):
        return []
    now = datetime.fromtimestamp(now_ts or time.time(), timezone.utc)
    day = close_day or now.astimezone(config.tz).date()
    as_of = None if close_day else now
    since = _local_midnight(day - timedelta(days=HISTORY_DAYS)).astimezone(timezone.utc)
    until = (_local_midnight(day) + timedelta(days=1)).astimezone(timezone.utc) if close_day else None
    created = []
    with SessionLocal() as s:
        rows = _load_rows(s, since, until)
        firsts = _first_seen(s)
        groups = _groups(rows)
        labels = {}
        for iid, label, *_ in rows:
            if iid:
                labels.setdefault(iid, label)
        for (iid, lbl), purposes in groups.items():
            label = lbl or labels.get(iid, "")
            ident = s.get(Identity, iid) if iid else None
            if iid and ident is None:
                continue   # Identität gelöscht
            name = pet_name(ident, label)
            for purpose, lst in purposes.items():
                st = compute_stats(lst, firsts.get((_pet_key(iid, label), purpose)), day, as_of)
                if st.normal is None:
                    continue
                at = i18n.t(f"pets.at.{purpose}")
                if close_day and close_day == now.astimezone(config.tz).date() - timedelta(days=1):
                    when = i18n.t("pets.when_yesterday")
                elif close_day:
                    when = i18n.t("pets.when_day", date=day.strftime(i18n.t("pets.date_short")))
                else:
                    when = i18n.t("pets.when_today")
                normal = _range_text(*st.normal)
                if st.today > st.p90 + HIGH_MARGIN:
                    n = _alert(s, kind="too_many", iid=iid, label=label, purpose=purpose, day=day, value=st.today,
                               expected=st.normal,
                               title=i18n.t("pets.many_title", name=name, when=when, n=st.today, at=at),
                               message=i18n.t("pets.many_msg", name=name, when=when, n=st.today, at=at,
                                              normal=normal))
                    if n:
                        created.append(n)
                elif st.today < st.p10_now - LOW_MARGIN:
                    exp = st.expected_now or st.normal
                    if close_day or exp == st.normal:
                        msg = i18n.t("pets.few_msg", name=name, when=when, n=st.today, at=at, normal=normal)
                    else:
                        msg = i18n.t("pets.few_msg_so_far", name=name, time=f"{now.astimezone(config.tz):%H:%M}",
                                     n=st.today, at=at, expected=_range_text(*exp), normal=normal)
                    n = _alert(s, kind="too_few", iid=iid, label=label, purpose=purpose, day=day, value=st.today,
                               expected=exp, title=i18n.t("pets.few_title" if close_day else "pets.few_title_so_far",
                                                          name=name, when=when, n=st.today, at=at),
                               message=msg)
                    if n:
                        created.append(n)
                if not close_day and st.gap_s is not None and st.usual_gap_s and \
                        st.gap_s > GAP_FACTOR * st.usual_gap_s:
                    n = _alert(s, kind="long_gap", iid=iid, label=label, purpose=purpose, day=day,
                               value=round(st.gap_s), expected=[0, round(st.usual_gap_s)],
                               title=i18n.t("pets.gap_title", name=name, dur=_fmt_dur(st.gap_s), at=at),
                               message=i18n.t("pets.gap_msg", name=name, dur=_fmt_dur(st.gap_s), at=at,
                                              usual=_fmt_dur(st.usual_gap_s)))
                    if n:
                        created.append(n)
    return created


def _tick_checks(now: float) -> None:
    if now - _st.started_at < CHECK_FIRST_DELAY_S:
        return
    loc = _local(now)
    hour_key = loc.strftime("%Y-%m-%d %H")
    day_key = loc.strftime("%Y-%m-%d")
    if _st.mqtt_day != day_key:
        first = _st.mqtt_day is None
        _st.mqtt_day = day_key
        if not first:
            _republish_counters()   # neuer Tag: Zähler auf 0
    if _st.closed_day != day_key:
        _st.closed_day = day_key
        try:
            check(now, close_day=loc.date() - timedelta(days=1))
        except Exception:  # noqa: BLE001
            log.exception("Tages-Abschluss Haustier-Tagebuch fehlgeschlagen")
    if _st.last_check_hour != hour_key:
        _st.last_check_hour = hour_key
        try:
            n = check(now)
            if n:
                log.info("Haustier-Tagebuch: %d Hinweis(e)", len(n))
        except Exception:  # noqa: BLE001
            log.exception("Prüfung Haustier-Tagebuch fehlgeschlagen")


def _republish_counters() -> None:
    try:
        since = _local_midnight(_local(time.time()).date() - timedelta(days=HISTORY_DAYS)).astimezone(timezone.utc)
        with SessionLocal() as s:
            keys = set(s.execute(select(PetVisit.identity_id, PetVisit.label, PetVisit.purpose)
                                 .where(PetVisit.start_ts >= since).distinct()).all())
            for iid, label, purpose in keys:
                _publish_counter(s, iid, label, purpose)
    except Exception:  # noqa: BLE001
        log.exception("Tier-Zähler nicht neu veröffentlicht")


# ------------------------------------------------------------------ Lebenszyklus


def startup() -> None:
    """Beim Backend-Start (nach registry/rules): Track-Hook registrieren."""
    global _unhook
    from . import events   # zyklischen Import vermeiden

    with _lock:
        _st.started_at = time.time()
        if _unhook is None:
            _unhook = events.on_track(_on_track)
    _close_dangling()
    log.info("Haustier-Tagebuch aktiv (Labels: %s)", ", ".join(sorted(_labels())) or "–")


def _close_dangling() -> None:
    """Nach einem Neustart offene Besuche (ohne Ende) abschließen: Ende = Ende des Events, sonst Beginn."""
    try:
        with SessionLocal() as s:
            rows = list(s.scalars(select(PetVisit).where(PetVisit.end_ts.is_(None))))
            for r in rows:
                ev_end = s.scalar(select(Event.end_ts).where(Event.id == r.event_id)) if r.event_id else None
                r.end_ts = ev_end if ev_end and ev_end >= r.start_ts else r.start_ts
                r.duration_s = round((r.end_ts - r.start_ts).total_seconds(), 1)
            if rows:
                s.commit()
                log.info("Haustier-Tagebuch: %d offene Besuche nach Neustart abgeschlossen", len(rows))
    except Exception:  # noqa: BLE001
        log.exception("Offene Tierbesuche nicht abgeschlossen")


def tick() -> None:
    """Jede Sekunde vom Backend-Ticker: Besuche zählen (alle 2 s), stündlich prüfen, Vortag abschließen."""
    now = time.time()
    if now - _st.last_persist >= 2.0:
        _st.last_persist = now
        _tick_visits(now)
    _tick_checks(now)


def cleanup(now: datetime, days: int) -> int:
    """Aufbewahrung wie Events (`retention_days_events`)."""
    with SessionLocal() as s:
        n = s.execute(delete(PetVisit).where(PetVisit.start_ts < now - timedelta(days=days))).rowcount or 0
        s.commit()
    return n


def on_identity_merged(s, src_id: int, dst_id: int) -> None:
    """Klassischer Merge: Besuche wandern mit (läuft in der Merge-Transaktion, ohne Commit)."""
    s.execute(update(PetVisit).where(PetVisit.identity_id == src_id).values(identity_id=dst_id))
    invalidate_fallback()


def on_identity_deleted(s, iid: int) -> None:
    """Identität gelöscht: Besuche bleiben als „unbekanntes Tier“ erhalten (ohne Commit)."""
    s.execute(update(PetVisit).where(PetVisit.identity_id == iid).values(identity_id=None))
    invalidate_fallback()
