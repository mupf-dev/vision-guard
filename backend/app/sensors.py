"""Virtuelle Sensoren (A3) und Vorräte (C4) im Backend. Vertrag: CONTRACT.md „Nachträge: Welle 3“, W3.F.

Ein Sensor beobachtet einen Bildbereich (Polygon) einer Kamera und hat einen Zustand:
`binary` (on|off|unknown) oder `level` (ok|low|out|unknown, Vorräte).

Wer misst was:
- **Worker** (`vg/internal/sensor/{camera_id}`, je Messung): `brightness`, `blink`, `reference`, `fill` → Wert
  (Helligkeit, Blinken, P(an), Füllstand), `change` (Änderungsmaß zum Ankerbild, siehe unten), `occluded`
  (Person im Bereich). Für `vlm`-Sensoren liefert der Worker nur `change`/`occluded` (Wert `null`).
- **Backend** (`tick()`): `inventory_count` (Gegenstände `present` mit Mittelpunkt im Bereich) und `vlm`
  (Frage an das Bildmodell über `vlm.ask(..., source="sensor")`, Schnittstelle von A2).

Zustandsautomat (`_observe`): Ein Rohzustand, der vom aktuellen abweicht, muss `confirm_n`-mal in Folge gemessen
werden (Hysterese). Verdeckte Messungen zählen nicht. `verify_vlm` (nur billige binäre Arten mit `params.question`):
nach der Hysterese bestätigt das Bildmodell den Wechsel; widerspricht es, bleibt der Zustand, und erst eine
sichtbare Änderung (`change`) oder `vlm_max_age_s` erlaubt die nächste Rückfrage. Ist das Bildmodell nicht
verfügbar, gilt der billige Befund (`last_engine: "cheap"`).

`change` (Worker): mittlere Absolutdifferenz (0..1) des Bereichs zu einem **Ankerbild**; erreicht sie
`change_threshold`, meldet der Worker sie einmal und setzt den Anker neu. Das Backend merkt sich daraus „seit der
letzten VLM-Frage sichtbar verändert“. Ein `vlm`-Sensor fragt das Modell deshalb nur, wenn sich der Bereich
verändert hat, ein Wechsel noch bestätigt werden muss oder der letzte Befund älter als `vlm_max_age_s` ist.

Bestätigter Wechsel (`_commit`, eigener Thread, Reihenfolge bleibt erhalten): DB (`virtual_sensors`,
`sensor_history`), Standbild (go2rtc, höchstens 1×/min je Sensor), WS `sensor`, MQTT extern
`{prefix}/sensor/{slug}` (+ `/attributes`, retain), Regeln (`events.processor.sensor_changed`, nur zwischen
bekannten Zuständen), Vorräte → `shopping.on_supply_state` + Hinweis (`source:"sensor"`).

`unknown`: Sensor deaktiviert, Kamera aus, keine Messwerte seit `max(5 × interval_s, 120 s)`, Referenzen fehlen.
Wechsel nach/aus `unknown` starten keine Regeln (Worker-Neustart soll keine „Fenster offen“-Meldung erzeugen).
"""
from __future__ import annotations

import logging
import threading
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

log = logging.getLogger("vg.sensors")

# Arten, die der Worker misst (CPU bzw. geteiltes DINOv2); inventory_count und vlm rechnet das Backend.
# Für vlm liefert der Worker zusätzlich das Änderungsmaß (CONTRACT W3.F, Nachtrag A3).
WORKER_KINDS = ("brightness", "blink", "reference", "fill")
WORKER_CHANGE_KINDS = WORKER_KINDS + ("vlm",)
KINDS = ("brightness", "blink", "reference", "inventory_count", "fill", "vlm")
BINARY_STATES = ("on", "off", "unknown")
LEVEL_STATES = ("ok", "low", "out", "unknown")
VERIFY_KINDS = ("brightness", "blink", "reference")
REF_STATES = {"reference": ("on", "off"), "fill": ("full", "half", "empty")}
MAX_REFS_PER_STATE = 5

STATE_DE = {"on": "an", "off": "aus", "ok": "ok", "low": "knapp", "out": "leer", "unknown": "unklar"}
KIND_DE = {"brightness": "Helligkeit", "blink": "Blinken", "reference": "Referenzbilder",
           "inventory_count": "Gegenstände zählen", "fill": "Füllstand", "vlm": "Bildmodell"}

STALE_MIN_S = 120.0         # ohne Messwerte so lange (mind. 5 × Intervall) → unknown
SNAPSHOT_MIN_GAP_S = 60.0   # Standbild je Sensor höchstens so oft
HISTORY_SAMPLE_S = 900.0    # Verlauf: zusätzlich zu Wechseln höchstens alle 15 min ein Messwert
FLUSH_S = 30.0              # value/last_eval gedrosselt in die DB
SHOPPING_DONE_KEEP_D = 30   # erledigte Einkaufseinträge

_lock = threading.RLock()
_cfg: dict[int, dict[str, Any]] = {}      # sensor_id → Konfiguration + bestätigter Zustand (Abbild der DB)
_mem: dict[int, dict[str, Any]] = {}      # sensor_id → Laufzeit (Kandidat, letzte Messung, …)
_slugs: dict[int, str] = {}               # sensor_id → externer Slug
_loaded = False
_last = {"stale": 0.0, "flush": 0.0, "orphans": 0.0}
_stats = {"vlm_calls": 0, "vlm_skipped": 0, "commits": 0}
# DB, Standbild, MQTT, Regeln nie im Track-Thread; ein Thread → Reihenfolge der Wechsel bleibt erhalten
_effects = ThreadPoolExecutor(max_workers=1, thread_name_prefix="vg-sensor-fx")
# Bildmodell (blockiert bis ~20 s): eigener Pool, damit Wechsel anderer Sensoren nicht warten
_vlm_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="vg-sensor-vlm")


def _now() -> float:
    return time.time()


def _iso(ts: Optional[float]) -> Optional[str]:
    if not ts:
        return None
    return datetime.fromtimestamp(float(ts), timezone.utc).isoformat().replace("+00:00", "Z")


def _ts(dt: Optional[datetime]) -> Optional[float]:
    return dt.timestamp() if dt else None


def _dt(ts: Optional[float]) -> Optional[datetime]:
    return datetime.fromtimestamp(float(ts), timezone.utc) if ts else None


# ------------------------------------------------------------------ Geometrie, Bewertung

def point_in_polygon(x: float, y: float, poly: list) -> bool:
    inside = False
    n = len(poly)
    if n < 3:
        return False
    j = n - 1
    for i in range(n):
        xi, yi = float(poly[i][0]), float(poly[i][1])
        xj, yj = float(poly[j][0]), float(poly[j][1])
        if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / ((yj - yi) or 1e-12) + xi:
            inside = not inside
        j = i
    return inside


def region_bbox(poly: list) -> list[float]:
    xs = [float(p[0]) for p in poly] or [0.0, 1.0]
    ys = [float(p[1]) for p in poly] or [0.0, 1.0]
    return [round(max(0.0, min(xs)), 4), round(max(0.0, min(ys)), 4),
            round(min(1.0, max(xs)), 4), round(min(1.0, max(ys)), 4)]


def default_state_set(kind: str, requested: Optional[str] = None) -> str:
    if kind == "fill":
        return "level"
    if kind == "inventory_count":
        return requested if requested in ("binary", "level") else "binary"
    return "binary"


def raw_state(cfg: dict[str, Any], value: Optional[float]) -> Optional[str]:
    """Rohzustand einer Messung (ohne Hysterese) oder None (keine Aussage)."""
    if value is None:
        return None
    p = cfg.get("params") or {}
    kind = cfg["kind"]
    v = float(value)
    if kind == "brightness":
        thr = float(p.get("threshold", 0.5))
        on = v <= thr if p.get("invert") else v >= thr
        return "on" if on else "off"
    if kind == "blink":
        return "on" if v >= float(p.get("min_std", 0.05)) else "off"
    if kind in ("reference", "vlm"):                 # vlm: 1 = ja, 0 = nein
        return "on" if v >= 0.5 else "off"
    if kind == "fill":
        if v < float(p.get("out_below", 0.15)):
            return "out"
        return "low" if v < float(p.get("low_below", 0.4)) else "ok"
    if kind == "inventory_count":
        mn = int(p.get("min_count", 1))
        if cfg.get("state_set") == "level":
            return "ok" if v >= mn else ("out" if v <= 0 else "low")
        return "on" if v >= mn else "off"
    return None


def _since_text(ts: float) -> str:
    from .config import config
    dt = datetime.fromtimestamp(ts, timezone.utc).astimezone(config.tz)
    today = datetime.now(timezone.utc).astimezone(config.tz).date()
    return f"seit {dt:%H:%M}" if dt.date() == today else f"seit {dt:%d.%m.} {dt:%H:%M}"


def state_text(cfg: dict[str, Any], m: Optional[dict[str, Any]] = None) -> str:
    """Deutscher Kurztext, z. B. „an seit 14:02“, „knapp seit 24.10. 08:15“, „unklar – Referenzen fehlen“."""
    m = m or {}
    st = cfg.get("state") or "unknown"
    if not cfg.get("enabled", True):
        return "deaktiviert"
    if st == "unknown":
        reason = m.get("reason") or ("noch keine Messung" if not m.get("last_rx") else "")
        txt = "unklar" + (f" – {reason}" if reason else "")
    else:
        txt = STATE_DE.get(st, st)
        if cfg.get("since"):
            txt += " " + _since_text(cfg["since"])
    if m.get("occluded") and _now() - float(m.get("occluded_ts") or 0) < max(30.0, 3 * float(cfg["interval_s"])):
        txt += " (gerade verdeckt)"
    return txt


# ------------------------------------------------------------------ Laden / Serialisieren

def _row_cfg(r) -> dict[str, Any]:
    return {"id": r.id, "name": r.name, "camera_id": r.camera_id, "enabled": bool(r.enabled), "kind": r.kind,
            "region": [list(map(float, p)) for p in (r.region or [])], "params": dict(r.params or {}),
            "state_set": r.state_set or default_state_set(r.kind), "supply": bool(r.supply),
            "verify_vlm": bool(r.verify_vlm), "interval_s": float(r.interval_s or 10),
            "vlm_max_age_s": float(r.vlm_max_age_s or 900), "change_threshold": float(r.change_threshold or 0.08),
            "confirm_n": int(r.confirm_n or 2), "state": r.state or "unknown", "value": r.value,
            "since": _ts(r.since), "last_eval": _ts(r.last_eval), "last_engine": r.last_engine,
            "last_vlm_ts": _ts(r.last_vlm_ts), "snapshot_path": r.snapshot_path,
            "created_at": _ts(r.created_at), "updated_at": _ts(r.updated_at)}


def _new_mem(cfg: dict[str, Any]) -> dict[str, Any]:
    return {"cand": None, "count": 0, "value": cfg.get("value"), "last_rx": 0.0, "last_eval": cfg.get("last_eval"),
            "occluded": False, "occluded_ts": 0.0, "changed": False, "scores": None, "reason": None,
            "next_due": 0.0, "vlm_busy": False, "verify_block": None, "dirty": False, "last_sample": 0.0,
            "last_snapshot": 0.0, "created": _now(),
            "prev_known": cfg.get("state") if cfg.get("state") not in (None, "unknown") else None}


# Zustandsfelder: im Speicher maßgeblich (DB wird im Effekt-Thread nachgezogen), bei reload() nicht überschreiben
_RUNTIME_KEYS = ("state", "since", "value", "last_eval", "last_engine", "last_vlm_ts", "snapshot_path")


def reload() -> None:
    """Konfiguration aus der DB neu lesen (nach REST-Änderungen); Laufzeitdaten bleiben erhalten."""
    global _loaded
    from .db import SessionLocal
    from .models import VirtualSensor
    with SessionLocal() as s:
        rows = s.scalars(select(VirtualSensor).order_by(VirtualSensor.id)).all()
        data = {r.id: _row_cfg(r) for r in rows}
    with _lock:
        for sid in list(_cfg):
            if sid not in data:
                _cfg.pop(sid, None)
                _mem.pop(sid, None)
        for sid, c in data.items():
            old = _cfg.get(sid)
            if old is not None:
                c.update({k: old.get(k) for k in _RUNTIME_KEYS})
            _cfg[sid] = c
            m = _mem.get(sid)
            if m is None:
                _mem[sid] = _new_mem(c)
        _recompute_slugs()
        _loaded = True


def _ensure_loaded() -> None:
    if not _loaded:
        try:
            reload()
        except Exception:  # noqa: BLE001
            log.exception("Sensoren: Laden fehlgeschlagen")


def _recompute_slugs() -> None:
    from .util import slugify
    base: dict[int, str] = {sid: slugify(c["name"]) for sid, c in _cfg.items()}
    seen: dict[str, int] = {}
    _slugs.clear()
    for sid in sorted(base):
        b = base[sid]
        if b == "shopping_list" or b in seen:
            _slugs[sid] = f"{b}_{sid}"
        else:
            _slugs[sid] = b
        seen.setdefault(b, sid)


def slug(sensor_id: int) -> str:
    with _lock:
        return _slugs.get(sensor_id) or f"sensor_{sensor_id}"


def to_json(cfg: dict[str, Any], m: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    """VirtualSensor-JSON (W3.F)."""
    from . import registry
    cam = registry.get(cfg["camera_id"]) or {}
    m = m or {}
    value = m.get("value", cfg.get("value"))
    last_eval = m.get("last_eval") or cfg.get("last_eval")
    snap = cfg.get("snapshot_path")
    return {"id": cfg["id"], "name": cfg["name"], "camera_id": cfg["camera_id"],
            "camera_name": cam.get("name") or f"Kamera {cfg['camera_id']}", "room": cam.get("room"),
            "enabled": cfg["enabled"], "kind": cfg["kind"], "region": cfg["region"], "params": cfg["params"],
            "state_set": cfg["state_set"], "supply": cfg["supply"], "verify_vlm": cfg["verify_vlm"],
            "interval_s": cfg["interval_s"], "vlm_max_age_s": cfg["vlm_max_age_s"],
            "change_threshold": cfg["change_threshold"], "confirm_n": cfg["confirm_n"], "state": cfg["state"],
            "value": None if value is None else round(float(value), 4), "since": _iso(cfg.get("since")),
            "last_eval": _iso(last_eval), "last_engine": cfg.get("last_engine"),
            "snapshot_url": f"/media/{snap}" if snap else None, "state_text": state_text(cfg, m)}


def get_json(sensor_id: int) -> Optional[dict[str, Any]]:
    _ensure_loaded()
    with _lock:
        c = _cfg.get(sensor_id)
        if c is None:
            return None
        return to_json(dict(c), dict(_mem.get(sensor_id) or {}))


def list_json(*, camera_id: Optional[int] = None, room: Optional[str] = None, kind: Optional[str] = None,
              supply: Optional[bool] = None) -> list[dict[str, Any]]:
    _ensure_loaded()
    from . import registry
    with _lock:
        items = [(dict(c), dict(_mem.get(sid) or {})) for sid, c in sorted(_cfg.items())]
    out = []
    for c, m in items:
        if camera_id is not None and c["camera_id"] != camera_id:
            continue
        if kind and c["kind"] != kind:
            continue
        if supply is not None and c["supply"] != supply:
            continue
        if room:
            r = (registry.get(c["camera_id"]) or {}).get("room") or ""
            if r.strip().lower() != room.strip().lower():
                continue
        out.append(to_json(c, m))
    return out


def current_state(sensor_id: int) -> Optional[str]:
    _ensure_loaded()
    with _lock:
        c = _cfg.get(sensor_id)
        return c["state"] if c else None


# ------------------------------------------------------------------ Worker → Messwerte

def internal_sensors(db: Session) -> dict[int, list[dict[str, Any]]]:
    """{camera_id: [{id, kind, region, params, interval_s, change_threshold, references:[{id, state, path}]}]} für
    `GET /api/internal/cameras` – aktive Sensoren der Worker-Arten (dazu `vlm` nur für das Änderungsmaß)."""
    from .models import SensorReference, VirtualSensor

    out: dict[int, list[dict[str, Any]]] = defaultdict(list)
    sensors = db.scalars(select(VirtualSensor).where(VirtualSensor.enabled.is_(True),
                                                     VirtualSensor.kind.in_(WORKER_CHANGE_KINDS))
                         .order_by(VirtualSensor.id)).all()
    if not sensors:
        return {}
    refs: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for r in db.scalars(select(SensorReference).where(SensorReference.sensor_id.in_([s.id for s in sensors]))
                        .order_by(SensorReference.id)).all():
        refs[r.sensor_id].append({"id": r.id, "state": r.state, "path": r.image_path})
    for s in sensors:
        out[s.camera_id].append({"id": s.id, "kind": s.kind, "region": list(s.region or []),
                                 "params": dict(s.params or {}), "interval_s": float(s.interval_s or 10),
                                 "change_threshold": float(s.change_threshold or 0.08),
                                 "references": refs.get(s.id, [])})
    return dict(out)


def handle_worker_values(camera_id: Optional[int], msg: dict[str, Any]) -> None:
    """`vg/internal/sensor/{camera_id}` (Track-Thread): Messwerte → Hysterese → Zustand."""
    _ensure_loaded()
    now = _now()
    try:
        ts = float(msg.get("ts") or now)
    except (TypeError, ValueError):
        ts = now
    if now - ts > 60:
        return                              # alte/retained Nachricht
    for v in msg.get("values") or []:
        try:
            sid = int(v.get("sensor_id"))
        except (TypeError, ValueError):
            continue
        with _lock:
            c = _cfg.get(sid)
            m = _mem.get(sid)
            if c is None or m is None or (camera_id is not None and c["camera_id"] != camera_id):
                continue
            m["last_rx"] = now
            occluded = bool(v.get("occluded"))
            if occluded:
                m["occluded"] = True
                m["occluded_ts"] = now
            else:
                m["occluded"] = False
            try:
                ch = float(v.get("change")) if v.get("change") is not None else None
            except (TypeError, ValueError):
                ch = None
            if ch is not None and ch >= c["change_threshold"]:
                m["changed"] = True
                m["verify_block"] = None
            if c["kind"] not in WORKER_KINDS:
                continue                    # vlm: nur Änderungsmaß/Verdeckung
            if occluded:
                continue
            value = v.get("value")
            m["scores"] = v.get("scores")
            if value is None:
                m["reason"] = "Referenzbilder fehlen" if c["kind"] in REF_STATES else "kein Messwert"
                m["last_eval"] = now
                if c["state"] != "unknown" and c["kind"] in REF_STATES:
                    _schedule_commit(sid, "unknown", None, "cheap")
                continue
            try:
                value = float(value)
            except (TypeError, ValueError):
                continue
        _observe(sid, value, engine="cheap", now=now)


def _observe(sid: int, value: Optional[float], *, engine: str, now: Optional[float] = None,
             direct: bool = False, snapshot_path: Optional[str] = None) -> Optional[str]:
    """Messung in den Zustandsautomaten geben. `direct` = ohne Hysterese (Jetzt prüfen, VLM-Bestätigung).
    → bestätigter neuer Zustand oder None."""
    now = now or _now()
    with _lock:
        c = _cfg.get(sid)
        m = _mem.get(sid)
        if c is None or m is None or not c["enabled"]:
            return None
        m["value"] = value
        m["last_eval"] = now
        m["dirty"] = True
        raw = raw_state(c, value)
        if raw is None:
            return None
        m["reason"] = None
        if now - m["last_sample"] >= HISTORY_SAMPLE_S and c["state"] == raw:
            m["last_sample"] = now
            _effects.submit(_add_sample, sid, c["state"], value, engine)
        if raw == c["state"]:
            m["cand"], m["count"] = None, 0
            return None
        if raw == m["cand"]:
            m["count"] += 1
        else:
            m["cand"], m["count"] = raw, 1
        if not direct and m["count"] < c["confirm_n"]:
            return None
        # verify_vlm nur zwischen bekannten Zuständen (der erste Zustand nach unknown startet ohnehin keine Regel)
        if (not direct and c["verify_vlm"] and c["kind"] in VERIFY_KINDS and c["state_set"] == "binary"
                and c["state"] != "unknown" and (c["params"].get("question") or "").strip()):
            blocked = m.get("verify_block")
            if blocked and blocked[0] == raw and now - blocked[1] < c["vlm_max_age_s"]:
                return None                 # Bildmodell hat diesen Wechsel schon verneint, Bild unverändert
            if not m["vlm_busy"]:
                m["vlm_busy"] = True
                _vlm_pool.submit(_verify, sid, raw, value)
            return None
        m["cand"], m["count"] = None, 0
    _schedule_commit(sid, raw, value, engine, snapshot_path=snapshot_path)
    return raw


# ------------------------------------------------------------------ Bildmodell (A2-Schnittstelle)

def _ask_vlm(c: dict[str, Any], question: str) -> tuple[Optional[str], str, Optional[str], Optional[str]]:
    """→ (state on|off|None, engine, snapshot_path, Fehlertext). Nutzt ausschließlich `vlm.ask` (A2)."""
    from . import vlm
    _stats["vlm_calls"] += 1
    try:
        res = vlm.ask(c["camera_id"], question, region=region_bbox(c["region"]), engine="auto", source="sensor")
    except vlm.AskError as e:
        return None, "local", None, e.message
    except Exception as e:  # noqa: BLE001
        log.warning("Sensor %s: Bildmodell-Frage fehlgeschlagen: %s", c["id"], e)
        return None, "local", None, "Bildmodell-Fehler"
    yn = (res or {}).get("yes_no")
    eng = (res or {}).get("engine") if (res or {}).get("engine") in ("local", "cloud") else "local"
    snap = (res or {}).get("snapshot_url") or None
    if snap and isinstance(snap, str) and snap.startswith("/media/"):
        snap = snap[len("/media/"):].split("?", 1)[0]
    else:
        snap = None
    return {"yes": "on", "no": "off"}.get(yn), eng, snap, None if yn in ("yes", "no") else "Antwort unklar"


def _verify(sid: int, candidate: str, value: Optional[float]) -> None:
    """verify_vlm: billiger Kandidat → Bildmodell bestätigt oder verneint."""
    try:
        with _lock:
            c = dict(_cfg.get(sid) or {})
        if not c:
            return
        state, engine, snap, err = _ask_vlm(c, c["params"].get("question") or "")
        now = _now()
        with _lock:
            m = _mem.get(sid)
            cur = _cfg.get(sid)
            if m is None or cur is None:
                return
            cur["last_vlm_ts"] = now
            m["changed"] = False
            m["cand"], m["count"] = None, 0
            if state is None:
                log.info("Sensor %s: Bildmodell ohne Aussage (%s) – billiger Befund gilt", sid, err)
                final, eng = candidate, "cheap"
            elif state != candidate:
                log.info("Sensor %s: Bildmodell verneint Wechsel nach %s", sid, candidate)
                m["verify_block"] = (candidate, now)
                _effects.submit(_persist_vlm_ts, sid, now)
                return
            else:
                final, eng = state, engine
        if final != c["state"]:
            _schedule_commit(sid, final, value, eng, snapshot_path=snap, vlm_ts=now)
    finally:
        with _lock:
            m = _mem.get(sid)
            if m is not None:
                m["vlm_busy"] = False


def _run_vlm_sensor(sid: int, force: bool = False) -> None:
    """vlm-Sensor: Frage stellen (im VLM-Pool), Ergebnis durch die Hysterese."""
    try:
        with _lock:
            c = dict(_cfg.get(sid) or {})
        if not c or not c["enabled"]:
            return
        state, engine, snap, err = _ask_vlm(c, c["params"].get("question") or "")
        now = _now()
        with _lock:
            cur = _cfg.get(sid)
            m = _mem.get(sid)
            if cur is None or m is None:
                return
            cur["last_vlm_ts"] = now
            m["changed"] = False
            m["last_rx"] = now
            m["last_eval"] = now
            if state is None:
                m["reason"] = err or "Bildmodell ohne Aussage"
                m["dirty"] = True
        _effects.submit(_persist_vlm_ts, sid, now)
        if state is not None:
            _observe(sid, 1.0 if state == "on" else 0.0, engine=engine, now=now, direct=force, snapshot_path=snap)
    finally:
        with _lock:
            m = _mem.get(sid)
            if m is not None:
                m["vlm_busy"] = False


def _persist_vlm_ts(sid: int, ts: float) -> None:
    try:
        from .db import SessionLocal
        from .models import VirtualSensor
        with SessionLocal() as s:
            s.execute(update(VirtualSensor).where(VirtualSensor.id == sid).values(last_vlm_ts=_dt(ts)))
            s.commit()
    except Exception:  # noqa: BLE001
        log.exception("Sensor %s: last_vlm_ts nicht gespeichert", sid)


# ------------------------------------------------------------------ Gegenstände zählen

def count_inventory(c: dict[str, Any]) -> tuple[Optional[int], Optional[str]]:
    """inventory_count: (Anzahl present-Gegenstände mit Mittelpunkt im Bereich, Grund bei None)."""
    from . import registry
    from .db import SessionLocal
    from .models import Camera, InventoryItem
    labels = [str(x) for x in (c["params"].get("labels") or [])]
    if not labels:
        return None, "keine Gegenstände gewählt"
    cam = registry.get(c["camera_id"])
    if cam is None or not cam.get("enabled", True):
        return None, "Kamera deaktiviert"
    with SessionLocal() as s:
        row = s.get(Camera, c["camera_id"])
        if row is None or not row.inventory_enabled:
            return None, "Gegenstandserkennung der Kamera ist aus"
        items = s.execute(select(InventoryItem.bbox, InventoryItem.state).where(
            InventoryItem.camera_id == c["camera_id"], InventoryItem.label.in_(labels),
            InventoryItem.state.in_(("present", "stale")))).all()
    n = 0
    for bbox, st in items:
        if not bbox or len(bbox) < 4:
            continue
        cx, cy = (float(bbox[0]) + float(bbox[2])) / 2, (float(bbox[1]) + float(bbox[3])) / 2
        if not point_in_polygon(cx, cy, c["region"]):
            continue
        if st == "stale":
            return None, "Gegenstandserkennung ruht (Stand unklar)"
        n += 1
    return n, None


def _run_inventory_sensor(sid: int, direct: bool = False) -> None:
    with _lock:
        c = dict(_cfg.get(sid) or {})
    if not c or not c["enabled"]:
        return
    try:
        n, reason = count_inventory(c)
    except Exception:  # noqa: BLE001
        log.exception("Sensor %s: Zählen fehlgeschlagen", sid)
        return
    now = _now()
    with _lock:
        m = _mem.get(sid)
        if m is None:
            return
        m["last_eval"] = now
        if n is None:
            m["reason"] = reason            # last_rx bleibt → nach der Stale-Frist „unklar“
            return
        m["last_rx"] = now
    _observe(sid, float(n), engine="cheap", now=now, direct=direct)


# ------------------------------------------------------------------ Bestätigter Wechsel

def _schedule_commit(sid: int, new_state: str, value: Optional[float], engine: str, *,
                     snapshot_path: Optional[str] = None, vlm_ts: Optional[float] = None,
                     reason: Optional[str] = None) -> None:
    now = _now()
    with _lock:
        c = _cfg.get(sid)
        m = _mem.get(sid)
        if c is None or m is None or c["state"] == new_state:
            return
        old = c["state"]
        prev_known = m.get("prev_known") if old == "unknown" else old
        c["state"] = new_state
        c["since"] = now
        c["value"] = value
        c["last_eval"] = now
        c["last_engine"] = engine
        if new_state != "unknown":
            m["prev_known"] = new_state
        elif old != "unknown":
            m["prev_known"] = old
        if reason:
            m["reason"] = reason
        want_snap = (new_state != "unknown" and snapshot_path is None
                     and now - m["last_snapshot"] >= SNAPSHOT_MIN_GAP_S)
        if want_snap or snapshot_path:
            m["last_snapshot"] = now
        m["last_sample"] = now              # Wechsel steht im Verlauf – nächster Messwert-Eintrag erst später
        if snapshot_path:
            c["snapshot_path"] = snapshot_path
        _stats["commits"] += 1
        snap_cfg = dict(c)
    _effects.submit(_commit, sid, old, prev_known, new_state, value, engine, snapshot_path, want_snap, vlm_ts, now,
                    snap_cfg)


def _commit(sid: int, old: str, prev_known: Optional[str], new: str, value: Optional[float], engine: str,
            snapshot_path: Optional[str], want_snap: bool, vlm_ts: Optional[float], now: float,
            c: dict[str, Any]) -> None:
    from .db import SessionLocal
    from .models import SensorHistory, VirtualSensor
    if want_snap:
        snapshot_path = grab_snapshot(c["camera_id"], f"sensors/{_local_day()}/sensor{sid}_{int(now)}.jpg")
        if snapshot_path:
            with _lock:
                if sid in _cfg:
                    _cfg[sid]["snapshot_path"] = snapshot_path
            c["snapshot_path"] = snapshot_path
    try:
        with SessionLocal() as s:
            vals: dict[str, Any] = {"state": new, "since": _dt(now), "value": value, "last_eval": _dt(now),
                                    "last_engine": engine, "updated_at": _dt(now)}
            if snapshot_path:
                vals["snapshot_path"] = snapshot_path
            if vlm_ts:
                vals["last_vlm_ts"] = _dt(vlm_ts)
            res = s.execute(update(VirtualSensor).where(VirtualSensor.id == sid).values(**vals))
            if not res.rowcount:
                return                      # inzwischen gelöscht
            s.add(SensorHistory(sensor_id=sid, ts=_dt(now), state=new, value=value, engine=engine,
                                snapshot_path=snapshot_path))
            s.commit()
    except Exception:  # noqa: BLE001
        log.exception("Sensor %s: Zustand nicht gespeichert", sid)
    log.info("Sensor %s (%s): %s → %s (%s, Wert %s)", sid, c["name"], old, new, engine,
             None if value is None else round(value, 3))
    with _lock:
        m = dict(_mem.get(sid) or {})
        cur = dict(_cfg.get(sid) or c)
    data = to_json(cur, m)
    _broadcast(data)
    _publish_external(sid, data)
    if new != "unknown" and prev_known is not None and prev_known != new:
        try:
            from .events import processor
            processor.sensor_changed({**data, "snapshot_path": snapshot_path}, prev_known, new)
        except Exception:  # noqa: BLE001
            log.exception("Sensor %s: Regeln nicht gestartet", sid)
    if cur.get("supply") and new in ("ok", "low", "out"):
        _supply_effects(cur, data, new, prev_known)


def _supply_effects(c: dict[str, Any], data: dict[str, Any], new: str, prev_known: Optional[str]) -> None:
    try:
        from . import shopping
        shopping.on_supply_state(data, new)
    except Exception:  # noqa: BLE001
        log.exception("Einkaufsliste für Sensor %s nicht aktualisiert", c["id"])
    if new not in ("low", "out") or prev_known == new:
        return
    try:
        from . import notifications
        day = _local_day()
        word = "knapp" if new == "low" else "leer"
        notifications.create(
            source="sensor", level="info", title=f"Vorrat {c['name']}: {word}",
            message=(f"{c['name']} ist {word} ({data.get('camera_name')}). "
                     + ("Steht auf der Einkaufsliste." if _shopping_auto() else "")).strip(),
            camera_id=c["camera_id"], image_path=c.get("snapshot_path"),
            data={"sensor_id": c["id"], "state": new, "kind": c["kind"], "value": data.get("value")},
            dedup_key=f"sensor:{c['id']}:{new}:{day}", dedup_s=86400, click_path="/vorraete")
    except Exception:  # noqa: BLE001
        log.exception("Hinweis für Vorrat %s nicht erzeugt", c["id"])


def _shopping_auto() -> bool:
    from . import settings_store
    return bool(settings_store.get().get("shopping_list_auto", True))


def _add_sample(sid: int, state: str, value: Optional[float], engine: str) -> None:
    try:
        from .db import SessionLocal
        from .models import SensorHistory
        with SessionLocal() as s:
            s.add(SensorHistory(sensor_id=sid, ts=_dt(_now()), state=state, value=value, engine=engine))
            s.commit()
    except Exception:  # noqa: BLE001
        log.debug("Sensor %s: Messwert nicht gespeichert", sid, exc_info=True)


def _local_day() -> str:
    from .util import local_date_str
    return local_date_str()


def _broadcast(data: dict[str, Any]) -> None:
    try:
        from .ws import hub
        hub.broadcast({"type": "sensor", "sensor": data})
    except Exception:  # noqa: BLE001
        pass


def _publish_external(sid: int, data: Optional[dict[str, Any]], old_slug: Optional[str] = None) -> None:
    """`{prefix}/sensor/{slug}` (Zustand) und `…/attributes` (VirtualSensor, signierte URLs), beide retain.
    `data=None` räumt die Topics (Sensor gelöscht)."""
    try:
        from . import media_sign, settings_store
        from .mqtt_bus import bus
        prefix = settings_store.get()["mqtt_prefix"]
        if old_slug:
            bus.publish(f"{prefix}/sensor/{old_slug}", "", retain=True, qos=1)
            bus.publish(f"{prefix}/sensor/{old_slug}/attributes", "", retain=True, qos=1)
        topic = f"{prefix}/sensor/{old_slug if data is None and old_slug else slug(sid)}"
        if data is None:
            if not old_slug:
                bus.publish(topic, "", retain=True, qos=1)
                bus.publish(f"{topic}/attributes", "", retain=True, qos=1)
            return
        bus.publish(topic, data["state"], retain=True, qos=1)
        bus.publish(f"{topic}/attributes", media_sign.sign_urls(dict(data)), retain=True, qos=1)
    except Exception as e:  # noqa: BLE001
        log.debug("MQTT extern sensor: %s", e)


def grab_jpeg(camera_id: int) -> Optional[bytes]:
    """Aktuelles Bild der Kamera über go2rtc (Hauptstream) als JPEG oder None."""
    try:
        import httpx

        from . import go2rtc
        from .config import config
        for _ in range(2):              # go2rtc liefert direkt nach einem Quellwechsel gelegentlich HTTP 500
            try:
                r = httpx.get(f"{config.go2rtc_url}/api/frame.jpeg",
                              params={"src": go2rtc.stream_name(camera_id)}, timeout=15)
            except httpx.HTTPError:
                continue
            if r.status_code == 200 and r.content.startswith(b"\xff\xd8"):
                return r.content
            time.sleep(1.0)
    except Exception as e:  # noqa: BLE001
        log.info("Kamerabild %s nicht verfügbar: %s", camera_id, e)
    return None


def grab_snapshot(camera_id: int, rel: str) -> Optional[str]:
    data = grab_jpeg(camera_id)
    if not data:
        return None
    try:
        store(rel, data)
        return rel
    except Exception:  # noqa: BLE001
        log.exception("Standbild %s nicht gespeichert", rel)
        return None


def store(rel: str, data: bytes) -> None:
    import os

    from .util import media_path
    full = media_path(rel)
    os.makedirs(os.path.dirname(full), exist_ok=True)
    tmp = full + ".part"
    with open(tmp, "wb") as f:
        f.write(data)
    os.replace(tmp, full)


# ------------------------------------------------------------------ REST-Hilfen

def after_change(sensor_id: int, *, deleted: bool = False, old_slug: Optional[str] = None,
                 reset: bool = False, notify_worker: bool = True) -> Optional[dict[str, Any]]:
    """Nach Anlegen/Ändern/Löschen über REST: Abbild neu laden, Worker informieren, WS/MQTT."""
    if deleted:
        with _lock:
            sl = _slugs.get(sensor_id)
        reload()
        _broadcast_deleted(sensor_id)
        _publish_external(sensor_id, None, old_slug=sl)
        if notify_worker:
            _refresh_worker()
        return None
    with _lock:
        prev = dict(_cfg.get(sensor_id) or {})
        prev_slug = _slugs.get(sensor_id)
    reload()
    with _lock:
        m = _mem.get(sensor_id)
        if m is not None:
            m["cand"], m["count"] = None, 0
            m["next_due"] = 0.0
            m["verify_block"] = None
            if reset:
                m.update(_new_mem(_cfg[sensor_id]))
        new_slug = _slugs.get(sensor_id)
    if notify_worker:
        _refresh_worker()
    data = get_json(sensor_id)
    if data:
        _broadcast(data)
        _publish_external(sensor_id, data, old_slug=prev_slug if prev and prev_slug != new_slug else None)
    return data


def _broadcast_deleted(sensor_id: int) -> None:
    try:
        from .ws import hub
        hub.broadcast({"type": "sensor", "sensor": {"id": sensor_id, "deleted": True}})
    except Exception:  # noqa: BLE001
        pass


def _refresh_worker() -> None:
    try:
        from .mqtt_bus import bus
        bus.publish_config_changed()
    except Exception:  # noqa: BLE001
        pass


def set_state_unknown(sensor_id: int, reason: str) -> None:
    """Zustand ohne Hysterese auf unknown (z. B. deaktiviert, Art geändert)."""
    _schedule_commit(sensor_id, "unknown", None, "cheap", reason=reason)


def evaluate_now(sensor_id: int) -> Optional[dict[str, Any]]:
    """`POST /sensors/{id}/evaluate`: sofort bewerten, **ohne** Hysterese. Worker-Arten nutzen die letzte Messung,
    inventory_count zählt neu, vlm (und verify_vlm) fragt das Bildmodell (blockiert bis ~20 s)."""
    _ensure_loaded()
    with _lock:
        c = dict(_cfg.get(sensor_id) or {})
        m = dict(_mem.get(sensor_id) or {})
    if not c:
        return None
    if not c["enabled"]:
        return get_json(sensor_id)
    if c["kind"] == "inventory_count":
        _run_inventory_sensor(sensor_id, direct=True)
    elif c["kind"] == "vlm" or (c["verify_vlm"] and (c["params"].get("question") or "").strip()
                                and c["kind"] in VERIFY_KINDS):
        if c["kind"] == "vlm":
            _run_vlm_sensor(sensor_id, force=True)
        else:
            state, engine, snap, err = _ask_vlm(c, c["params"]["question"])
            now = _now()
            with _lock:
                if sensor_id in _cfg:
                    _cfg[sensor_id]["last_vlm_ts"] = now
                if sensor_id in _mem:
                    _mem[sensor_id]["changed"] = False
            if state is not None:
                _observe(sensor_id, 1.0 if state == "on" else 0.0, engine=engine, direct=True, snapshot_path=snap)
                # value bleibt der billige Messwert
                with _lock:
                    if sensor_id in _mem:
                        _mem[sensor_id]["value"] = m.get("value")
            else:
                with _lock:
                    if sensor_id in _mem:
                        _mem[sensor_id]["reason"] = err
    elif m.get("value") is not None and _now() - float(m.get("last_rx") or 0) < max(60.0, 5 * c["interval_s"]):
        _observe(sensor_id, float(m["value"]), engine="cheap", direct=True)
    # Ergebnis sichtbar machen (Commit läuft im Effekt-Thread)
    try:
        _effects.submit(lambda: None).result(timeout=35)
    except Exception:  # noqa: BLE001
        pass
    return get_json(sensor_id)


def mark_reference_changed(sensor_id: int) -> None:
    _refresh_worker()
    with _lock:
        m = _mem.get(sensor_id)
        if m is not None:
            m["cand"], m["count"] = None, 0


def drop_camera(camera_id: int) -> int:
    """Sensoren einer gelöschten Kamera entfernen (samt Verlauf, Referenzen, Bildern)."""
    from .db import SessionLocal
    from .models import VirtualSensor
    with SessionLocal() as s:
        ids = [r for r in s.scalars(select(VirtualSensor.id).where(VirtualSensor.camera_id == camera_id)).all()]
    for sid in ids:
        delete_sensor(sid)
    return len(ids)


def delete_sensor(sensor_id: int) -> bool:
    from .db import SessionLocal
    from .models import SensorHistory, SensorReference, VirtualSensor
    from .util import safe_remove
    with SessionLocal() as s:
        row = s.get(VirtualSensor, sensor_id)
        if row is None:
            return False
        files = [row.snapshot_path]
        files += list(s.scalars(select(SensorReference.image_path).where(SensorReference.sensor_id == sensor_id)).all())
        files += list(s.scalars(select(SensorHistory.snapshot_path).where(
            SensorHistory.sensor_id == sensor_id, SensorHistory.snapshot_path.is_not(None))).all())
        s.execute(delete(SensorHistory).where(SensorHistory.sensor_id == sensor_id))
        s.execute(delete(SensorReference).where(SensorReference.sensor_id == sensor_id))
        s.delete(row)
        s.commit()
    for f in set(files):
        if f and f.startswith("sensors/"):     # Bilder von „Frag die Kamera“ (ask/) räumt A2 auf
            safe_remove(f)
    try:
        from . import shopping
        shopping.on_sensor_deleted(sensor_id)
    except Exception:  # noqa: BLE001
        log.exception("Einkaufsliste: Sensor %s nicht gelöst", sensor_id)
    after_change(sensor_id, deleted=True)
    return True


# ------------------------------------------------------------------ Lebenszyklus

def startup() -> None:
    """Beim Backend-Start: Abbild laden, Zustände extern (retain) veröffentlichen, Einkaufsliste veröffentlichen."""
    try:
        reload()
        now = _now()
        with _lock:
            for m in _mem.values():
                m["created"] = now             # Schonfrist für „keine Messwerte“
            items = [(sid, to_json(dict(c), dict(_mem.get(sid) or {}))) for sid, c in _cfg.items()]
        for sid, data in items:
            _publish_external(sid, data)
        if items:
            log.info("Sensoren: %d geladen", len(items))
    except Exception:  # noqa: BLE001
        log.exception("Sensoren: Start fehlgeschlagen")
    try:
        from . import shopping
        shopping.publish_external()
    except Exception:  # noqa: BLE001
        log.exception("Einkaufsliste: Veröffentlichen fehlgeschlagen")


def tick() -> None:
    """Jede Sekunde vom Backend-Ticker: fällige inventory_count/vlm-Sensoren, veraltete Messungen, DB-Flush."""
    _ensure_loaded()
    now = _now()
    due_inv: list[int] = []
    due_vlm: list[int] = []
    with _lock:
        for sid, c in _cfg.items():
            m = _mem.get(sid)
            if m is None or not c["enabled"]:
                continue
            if c["kind"] not in ("inventory_count", "vlm") or now < m["next_due"]:
                continue
            m["next_due"] = now + c["interval_s"]
            if c["kind"] == "inventory_count":
                due_inv.append(sid)
                continue
            if m["vlm_busy"]:
                continue
            last = c.get("last_vlm_ts") or 0.0
            occluded_recent = m["occluded"] and now - m["occluded_ts"] < max(30.0, 2 * c["interval_s"])
            need = (m["cand"] is not None or m["changed"] or c["state"] == "unknown"
                    or now - last >= c["vlm_max_age_s"])
            if occluded_recent or not need:
                _stats["vlm_skipped"] += 1
                continue
            m["vlm_busy"] = True
            due_vlm.append(sid)
    for sid in due_inv:
        _run_inventory_sensor(sid)
    for sid in due_vlm:
        _vlm_pool.submit(_run_vlm_sensor, sid)
    if now - _last["stale"] >= 5:
        _last["stale"] = now
        _check_stale(now)
    if now - _last["flush"] >= FLUSH_S:
        _last["flush"] = now
        _effects.submit(_flush)
    if now - _last["orphans"] >= 60:
        _last["orphans"] = now
        _effects.submit(_drop_orphans)


def _check_stale(now: float) -> None:
    from . import registry
    todo: list[tuple[int, str]] = []
    with _lock:
        for sid, c in _cfg.items():
            m = _mem.get(sid)
            if m is None:
                continue
            if not c["enabled"]:
                if c["state"] != "unknown":
                    todo.append((sid, "deaktiviert"))
                continue
            cam = registry.get(c["camera_id"])
            if cam is not None and not cam.get("enabled", True):
                if c["state"] != "unknown":
                    todo.append((sid, "Kamera deaktiviert"))
                continue
            if c["kind"] == "vlm":
                continue                    # vlm hat eigene Taktung; Fehler stehen in reason
            limit = max(STALE_MIN_S, 5 * c["interval_s"])
            last = max(float(m["last_rx"] or 0), float(m["created"] or 0))
            if c["state"] != "unknown" and now - last > limit:
                todo.append((sid, m.get("reason") or "keine Messwerte (Kamera offline oder Analyse ruht)"))
    for sid, reason in todo:
        set_state_unknown(sid, reason)


def _flush() -> None:
    from .db import SessionLocal
    from .models import VirtualSensor
    with _lock:
        rows = []
        for sid, m in _mem.items():
            if m.get("dirty"):
                m["dirty"] = False
                rows.append((sid, m.get("value"), m.get("last_eval")))
    if not rows:
        return
    try:
        with SessionLocal() as s:
            for sid, value, le in rows:
                s.execute(update(VirtualSensor).where(VirtualSensor.id == sid)
                          .values(value=value, last_eval=_dt(le)))
            s.commit()
    except Exception:  # noqa: BLE001
        log.exception("Sensoren: Messwerte nicht gespeichert")


def _drop_orphans() -> None:
    """Sensoren gelöschter Kameras entfernen (statt eines Hooks im Kamera-Router)."""
    from . import registry
    known = {c["id"] for c in registry.all()}
    if not known:
        return                              # Registry noch leer (Start) → nichts löschen
    with _lock:
        orphans = sorted({c["camera_id"] for c in _cfg.values() if c["camera_id"] not in known})
    for cid in orphans:
        n = drop_camera(cid)
        if n:
            log.info("Sensoren: %d Sensor(en) der gelöschten Kamera %s entfernt", n, cid)


def retention(now: datetime) -> int:
    """Stündlich aus retention.py: sensor_history älter als `retention_days_sensors` (samt Bildern), erledigte
    Einkaufseinträge älter als 30 Tage."""
    from . import settings_store
    from .db import SessionLocal
    from .models import SensorHistory, ShoppingItem, VirtualSensor
    from .util import safe_remove
    days = int(settings_store.get().get("retention_days_sensors") or 30)
    cutoff = now - timedelta(days=days)
    with SessionLocal() as s:
        keep = {p for p in s.scalars(select(VirtualSensor.snapshot_path).where(
            VirtualSensor.snapshot_path.is_not(None))).all()}
        old = s.execute(select(SensorHistory.id, SensorHistory.snapshot_path)
                        .where(SensorHistory.ts < cutoff)).all()
        if old:
            s.execute(delete(SensorHistory).where(SensorHistory.id.in_([r[0] for r in old])))
        done = s.execute(delete(ShoppingItem).where(
            ShoppingItem.done_at.is_not(None),
            ShoppingItem.done_at < now - timedelta(days=SHOPPING_DONE_KEEP_D))).rowcount or 0
        s.commit()
    for _, p in old:
        if p and p not in keep and p.startswith("sensors/"):
            safe_remove(p)
    if old or done:
        log.info("Aufräumen Sensoren: %d Verlaufseinträge, %d erledigte Einkaufseinträge", len(old), done)
    return len(old)


def stats() -> dict[str, Any]:
    """Zähler (Logs/Tests): Bildmodell-Aufrufe, übersprungene VLM-Takte, Zustandswechsel."""
    with _lock:
        return dict(_stats, sensors=len(_cfg))
