"""Kamera-Gesundheit im Backend (C5), Vertrag: CONTRACT.md „Nachträge: Welle 1“, W1.F.

Der Worker meldet über `vg/internal/health/{camera_id}` (retain, alle 30 s und bei Änderung) die Rohbefunde
(`problems:[{type, since, value}]`, bereits über zwei Prüfungen entprellt). Hier passiert die Hysterese:

- Ein Befund wird zur **Warnung**, wenn er seit `health_alert_after_s` (Setting, Default 60 s) ununterbrochen
  gemeldet wird. Dann: Eintrag in `camera_health_log`, Notification `source="health"` (Ziele aus
  `notify_routes.health`, `dedup_key="health:<id>:<type>"`), WS `camera_status`, MQTT extern
  `{prefix}/health/{camera_slug}` (retain).
- Eine Warnung endet, wenn der Befund **60 s** lang nicht mehr gemeldet wurde (Log-Eintrag bekommt `end_ts`,
  Hinweis „wieder in Ordnung“).
- Meldet der Worker länger als `STALE_S` nichts (Kamera offline, Worker weg), wird der Zustand `unknown`;
  laufende Warnungen bleiben stehen und werden weder bestätigt noch beendet.

Laufzeitstatus: `state.set_camera_health()` → `status.health` in `/cameras`, `/system`, WS `camera_status`.
Ein eigener Thread (`startup()`) prüft alle 5 s die Zeitschwellen, damit eine Warnung nicht erst mit der
nächsten Worker-Nachricht entsteht.
"""
from __future__ import annotations

import logging
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from sqlalchemy import select

from . import i18n, registry, settings_store, state
from .config import config
from .db import SessionLocal
from .models import CameraHealthLog
from .ws import hub

log = logging.getLogger("vg.health")

CLEAR_S = 60.0          # Entwarnung nach so vielen Sekunden ohne Befund (Vertrag)
STALE_S = 120.0         # ohne Worker-Meldung so lange → Zustand „unknown“
TICK_S = 5.0
NOTIFY_DEDUP_S = 3600   # je Kamera und Problemart höchstens eine Warnung pro Stunde (flatternde Befunde)
TYPES = ("dark", "blur", "covered", "frozen", "moved")



def _tr(group: str, ptype: str) -> str:
    """Text je Problemart (`health.label|text|hint.<type>`), unbekannte Art → die Art selbst."""
    if ptype not in TYPES:
        return "" if group == "hint" else ptype
    return i18n.t(f"health.{group}.{ptype}")

_lock = threading.RLock()
_cams: dict[int, dict[str, Any]] = {}       # camera_id -> {msg, rx, problems:{type: {...}}, health}
_pending: dict[str, dict[str, Any]] = {}    # request_id -> {event, result}
_started = False
# Log, Hinweis und Kamerabild (go2rtc, bis 2 × 15 s) nie im Track-Thread: ein Thread, Reihenfolge bleibt erhalten
_effects = ThreadPoolExecutor(max_workers=1, thread_name_prefix="vg-health-fx")


def _now() -> float:
    return time.time()


def _iso(ts: Optional[float]) -> Optional[str]:
    if not ts:
        return None
    return datetime.fromtimestamp(float(ts), timezone.utc).isoformat().replace("+00:00", "Z")


def _since_text(ts: float, now: Optional[float] = None) -> str:
    dt = datetime.fromtimestamp(ts, timezone.utc).astimezone(config.tz)
    today = datetime.fromtimestamp(now or _now(), timezone.utc).astimezone(config.tz).date()
    if dt.date() == today:
        return i18n.t("health.since_today", time=f"{dt:%H:%M}")
    return i18n.t("health.since_other", date=dt.strftime(i18n.t("health.date_short")), time=f"{dt:%H:%M}")


def _direction(sx: Optional[float], sy: Optional[float]) -> str:
    """Richtung, in die sich der Bildinhalt gegenüber der Referenz verschoben hat."""
    if sx is None or sy is None:
        return ""
    sx, sy = float(sx), float(sy)
    amount = (sx * sx + sy * sy) ** 0.5
    if amount < 0.02:
        return ""
    parts = []
    if abs(sx) >= 0.4 * amount:
        parts.append(i18n.t("health.dir.right" if sx > 0 else "health.dir.left"))
    if abs(sy) >= 0.4 * amount:
        parts.append(i18n.t("health.dir.down" if sy > 0 else "health.dir.up"))
    return i18n.t("health.shift", pct=round(amount * 100), dir="/".join(parts))


def problem_text(ptype: str, since: float, extra: Optional[dict[str, Any]] = None, now: Optional[float] = None) -> str:
    """Kurztext in der aktuellen Sprache, z. B. "Covered since 14:02" / „Verdeckt seit 14:02“."""
    base = _tr("text", ptype)
    if ptype == "moved" and extra:
        d = _direction(extra.get("shift_x"), extra.get("shift_y"))
        base = i18n.t("health.moved_dir", dir=d) if d else i18n.t("health.moved_unknown")
    return f"{base} {_since_text(since, now)}"


# ------------------------------------------------------------------ Worker-Nachricht

def handle(camera_id: int, msg: dict[str, Any]) -> None:
    """Worker-Nachricht `vg/internal/health/{camera_id}` verarbeiten (Track-Thread)."""
    cam = registry.get(camera_id)
    if cam is None:
        return
    now = _now()
    try:
        ts = float(msg.get("ts") or now)
    except (TypeError, ValueError):
        ts = now
    with _lock:
        c = _cams.setdefault(camera_id, {"msg": None, "problems": {}, "health": None})
        c["msg"] = msg
        fresh = now - ts <= STALE_S
        if fresh and msg.get("enabled", True) is not False:
            reported = {}
            for p in msg.get("problems") or []:
                t = p.get("type")
                if t not in TYPES:
                    continue
                try:
                    since = float(p.get("since") or ts)
                except (TypeError, ValueError):
                    since = ts
                reported[t] = (min(since, now), p)
            for t, (since, p) in reported.items():
                e = c["problems"].get(t)
                if e is None:
                    e = c["problems"][t] = {"since": since, "active": False, "log_id": None, "notified": False}
                e["last_bad"] = now
                e["value"] = p.get("value")
                e["extra"] = {k: p.get(k) for k in ("shift_x", "shift_y") if k in p}
            c["reported"] = set(reported)
    _evaluate(camera_id)


def _cfg_enabled(cam: Optional[dict[str, Any]], c: dict[str, Any]) -> bool:
    if cam is None or not cam.get("enabled", True) or cam.get("health_enabled", True) is False:
        return False
    msg = c.get("msg") or {}
    return msg.get("enabled", True) is not False


def _evaluate(camera_id: int, now: Optional[float] = None) -> None:
    """Zeitschwellen prüfen, Log/Notification/Status aktualisieren."""
    now = now or _now()
    cam = registry.get(camera_id)
    after_s = float(settings_store.get().get("health_alert_after_s") or 60)
    started: list[tuple[str, dict]] = []
    ended: list[tuple[str, dict]] = []
    with _lock:
        c = _cams.get(camera_id)
        if c is None:
            return
        if not _cfg_enabled(cam, c):
            # Prüfung abgeschaltet oder Kamera deaktiviert/gelöscht: offene Episoden schließen, Status leeren
            for t, e in list(c["problems"].items()):
                if e.get("active"):
                    ended.append((t, e))
            c["problems"].clear()
            c["reported"] = set()
            health = None
        else:
            msg = c.get("msg") or {}
            try:
                msg_ts = float(msg.get("ts") or 0)
            except (TypeError, ValueError):
                msg_ts = 0.0
            stale = not msg or now - msg_ts > STALE_S
            reported = c.get("reported") or set()
            if not stale:
                for t, e in list(c["problems"].items()):
                    if t in reported:
                        if not e["active"] and now - e["since"] >= after_s:
                            e["active"] = True
                            started.append((t, e))
                    elif not e["active"]:
                        c["problems"].pop(t)                    # vor der Warnschwelle verschwunden
                    elif now - e.get("last_bad", now) >= CLEAR_S:
                        c["problems"].pop(t)
                        ended.append((t, e))
            active = sorted(((t, e) for t, e in c["problems"].items() if e["active"]), key=lambda x: x[1]["since"])
            with i18n.use_lang(i18n.system_lang()):   # Status-Cache/WS/MQTT immer in der Systemsprache
                health = {"state": "unknown" if stale else ("warn" if active else "ok"),
                          "problems": [{"type": t, "since": _iso(e["since"]),
                                        "text": problem_text(t, e["since"], e.get("extra"), now)} for t, e in active]}
        changed = health != c.get("health")
        c["health"] = health
    for t, e in started:
        _effects.submit(_start_episode, camera_id, cam, t, e)
    for t, e in ended:
        _effects.submit(_end_episode, camera_id, cam, t, e, now)
    if changed:
        state.set_camera_health(camera_id, health)
        if cam is not None:
            hub.broadcast({"type": "camera_status", "camera_id": camera_id,
                           "status": state.status_for(camera_id, cam.get("enabled", True))})
            _publish_external(cam, health)


# ------------------------------------------------------------------ Episoden, Meldungen

def _start_episode(camera_id: int, cam: Optional[dict[str, Any]], t: str, e: dict[str, Any]) -> None:
    try:
        with SessionLocal() as s:
            row = CameraHealthLog(camera_id=camera_id, type=t,
                                  start_ts=datetime.fromtimestamp(e["since"], timezone.utc),
                                  value=_float_or_none(e.get("value")))
            s.add(row)
            s.commit()
            e["log_id"] = row.id
    except Exception:  # noqa: BLE001
        log.exception("Gesundheits-Log Kamera %s nicht gespeichert", camera_id)
    name = (cam or {}).get("name") or i18n.t("health.camera", id=camera_id)
    text = problem_text(t, e["since"], e.get("extra"))
    log.warning("Kamera %s (%s): %s", camera_id, name, text)
    try:
        from . import notifications
        image = _grab_frame(camera_id)
        n = notifications.create(
            source="health", level="warning", title=i18n.t("health.title_warn", name=name, label=_tr("label", t)),
            message=f"{text}. {_tr('hint', t)}".strip(), camera_id=camera_id, image_path=image,
            data={"type": t, "value": e.get("value"), "since": _iso(e["since"]), "state": "warn"},
            dedup_key=f"health:{camera_id}:{t}", dedup_s=NOTIFY_DEDUP_S)
        e["notified"] = n is not None
    except Exception:  # noqa: BLE001
        log.exception("Hinweis Kamera-Gesundheit nicht erzeugt")


def _end_episode(camera_id: int, cam: Optional[dict[str, Any]], t: str, e: dict[str, Any], now: float) -> None:
    end = datetime.fromtimestamp(now, timezone.utc)
    try:
        with SessionLocal() as s:
            if e.get("log_id"):
                row = s.get(CameraHealthLog, e["log_id"])
                rows = [row] if row is not None else []
            else:
                rows = list(s.scalars(select(CameraHealthLog).where(
                    CameraHealthLog.camera_id == camera_id, CameraHealthLog.type == t,
                    CameraHealthLog.end_ts.is_(None))).all())
            for row in rows:
                if row.end_ts is None:
                    row.end_ts = end
            s.commit()
    except Exception:  # noqa: BLE001
        log.exception("Gesundheits-Log Kamera %s nicht abgeschlossen", camera_id)
    name = (cam or {}).get("name") or i18n.t("health.camera", id=camera_id)
    log.info("Kamera %s (%s): %s behoben", camera_id, name, t)
    if not e.get("notified") or cam is None:
        return
    try:
        from . import notifications
        mins = max(1, round((now - e["since"]) / 60))
        notifications.create(
            source="health", level="info", title=i18n.t("health.title_ok", name=name),
            message=i18n.t("health.msg_ok", text=_tr("text", t), mins=mins), camera_id=camera_id,
            data={"type": t, "state": "ok", "since": _iso(e["since"]), "end": _iso(now)}, priority="low",
            dedup_key=f"health:{camera_id}:{t}:ok", dedup_s=NOTIFY_DEDUP_S)
    except Exception:  # noqa: BLE001
        log.exception("Entwarnung Kamera-Gesundheit nicht erzeugt")


def _float_or_none(v: Any) -> Optional[float]:
    try:
        return None if v is None else float(v)
    except (TypeError, ValueError):
        return None


def _grab_frame(camera_id: int) -> Optional[str]:
    """Aktuelles Bild der Kamera (go2rtc) als Snapshot für den Hinweis; None bei Fehler."""
    try:
        import httpx

        from . import go2rtc
        from .util import local_date_str, media_path
        r = None
        for attempt in range(2):           # go2rtc liefert direkt nach einem Quellwechsel gelegentlich HTTP 500
            try:
                r = httpx.get(f"{config.go2rtc_url}/api/frame.jpeg",
                              params={"src": go2rtc.stream_name(camera_id)}, timeout=15)
            except httpx.HTTPError as e:   # erster Abruf startet in go2rtc ggf. erst einen Transcoder
                log.debug("frame.jpeg Kamera %s: %s", camera_id, e)
                r = None
                continue
            if r.status_code == 200 and r.content.startswith(b"\xff\xd8"):
                break
            time.sleep(1.0)
        else:
            log.info("Bild für Gesundheits-Hinweis Kamera %s nicht verfügbar (%s)", camera_id,
                     f"HTTP {r.status_code}" if r is not None else "Zeitüberschreitung")
            return None
        rel = f"snapshots/{local_date_str()}/health_cam{camera_id}_{int(_now())}.jpg"
        full = media_path(rel)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        tmp = full + ".part"
        with open(tmp, "wb") as f:
            f.write(r.content)
        os.replace(tmp, full)
        return rel
    except Exception as e:  # noqa: BLE001
        log.info("Bild für Gesundheits-Hinweis Kamera %s nicht verfügbar: %s", camera_id, e)
        return None


def _publish_external(cam: dict[str, Any], health: Optional[dict[str, Any]]) -> None:
    """MQTT extern `{prefix}/health/{camera_slug}` (retain): {state:"ok|warn", problems:[…]}."""
    try:
        from .mqtt_bus import bus
        prefix = settings_store.get()["mqtt_prefix"]
        topic = f"{prefix}/health/{cam.get('slug')}"
        if health is None:
            bus.publish(topic, "", retain=True, qos=1)           # Prüfung aus → Topic räumen
            return
        if health["state"] == "unknown":
            return                                               # letzten bekannten Stand stehen lassen
        bus.publish(topic, {"state": health["state"], "problems": health["problems"], "camera_id": cam["id"],
                            "camera": cam.get("name"), "ts": _iso(_now())}, retain=True, qos=1)
    except Exception as e:  # noqa: BLE001
        log.debug("MQTT extern health: %s", e)


# ------------------------------------------------------------------ Lebenszyklus

def startup() -> None:
    """Offene Episoden aus der DB übernehmen (keine erneute Meldung) und Zeitschwellen-Thread starten."""
    global _started
    try:
        with SessionLocal() as s:
            rows = s.scalars(select(CameraHealthLog).where(CameraHealthLog.end_ts.is_(None))).all()
            now = _now()
            with _lock:
                for r in rows:
                    c = _cams.setdefault(r.camera_id, {"msg": None, "problems": {}, "health": None})
                    since = r.start_ts.timestamp() if r.start_ts else now
                    c["problems"][r.type] = {"since": since, "active": True, "log_id": r.id, "notified": True,
                                             "last_bad": now, "value": r.value, "extra": {}}
                    c.setdefault("reported", set()).add(r.type)
        if rows:
            log.info("Kamera-Gesundheit: %d offene Warnung(en) übernommen", len(rows))
    except Exception:  # noqa: BLE001
        log.exception("Kamera-Gesundheit: Start fehlgeschlagen")
    if not _started:
        _started = True
        threading.Thread(target=_tick_loop, name="vg-health", daemon=True).start()


def _tick_loop() -> None:
    while True:
        time.sleep(TICK_S)
        try:
            tick()
        except Exception:  # noqa: BLE001
            log.exception("Kamera-Gesundheit: Prüfung fehlgeschlagen")


def tick() -> None:
    with _lock:
        ids = list(_cams)
    for cid in ids:
        _evaluate(cid)


def drop_camera(camera_id: int) -> None:
    """Kamera gelöscht: Zustand, Log und Referenzbilder entfernen, externes Topic räumen."""
    cam = registry.get(camera_id)
    with _lock:
        _cams.pop(camera_id, None)
    state.set_camera_health(camera_id, None)
    try:
        with SessionLocal() as s:
            for row in s.scalars(select(CameraHealthLog).where(CameraHealthLog.camera_id == camera_id)).all():
                s.delete(row)
            s.commit()
    except Exception:  # noqa: BLE001
        log.exception("Gesundheits-Log Kamera %s nicht gelöscht", camera_id)
    from .util import safe_remove
    for mode in ("day", "night"):
        safe_remove(f"health/cam{camera_id}_{mode}.jpg")
    if cam:
        _publish_external(cam, None)


# ------------------------------------------------------------------ REST-Hilfen

def reference_urls(camera_id: int) -> dict[str, Optional[str]]:
    """URLs der Referenzbilder (mit Änderungszeit als Cache-Brecher; `/media/*` wird 10 min gecacht)."""
    from .util import media_path
    out: dict[str, Optional[str]] = {}
    with _lock:
        ref = dict(((_cams.get(camera_id) or {}).get("msg") or {}).get("reference") or {})
    for mode in ("day", "night"):
        rel = ref.get(mode) or f"health/cam{camera_id}_{mode}.jpg"
        try:
            full = media_path(rel)
            out[mode] = f"/media/{rel}?v={int(os.path.getmtime(full))}" if os.path.exists(full) else None
        except Exception:  # noqa: BLE001
            out[mode] = None
    return out


def current(camera_id: int) -> Optional[dict[str, Any]]:
    """`current` für GET /cameras/{id}/health: {state, problems, metrics, ts} oder None."""
    _evaluate(camera_id)
    with _lock:
        c = _cams.get(camera_id)
        if c is None or c.get("health") is None:
            return None
        msg = c.get("msg") or {}
        h = c["health"]
        pending = sorted(({"type": t, "since": _iso(e["since"]),
                           "text": problem_text(t, e["since"], e.get("extra"))}
                          for t, e in c["problems"].items() if not e["active"]), key=lambda p: p["since"] or "")
        # Texte in der Sprache der Anfrage neu berechnen (der Status-Cache steht in der Systemsprache)
        problems = [{**p, "text": problem_text(p["type"], c["problems"][p["type"]]["since"],
                                               c["problems"][p["type"]].get("extra"))}
                    if p.get("type") in c["problems"] else dict(p) for p in h["problems"]]
        return {"state": h["state"], "problems": problems, "pending": pending,
                "metrics": dict(msg.get("metrics") or {}), "ts": _iso(msg.get("ts"))}


def log_entries(camera_id: int, hours: int) -> list[dict[str, Any]]:
    since = datetime.now(timezone.utc) - timedelta(hours=hours)
    with SessionLocal() as s:
        rows = s.scalars(select(CameraHealthLog).where(
            CameraHealthLog.camera_id == camera_id,
            (CameraHealthLog.end_ts.is_(None)) | (CameraHealthLog.end_ts >= since))
            .order_by(CameraHealthLog.start_ts.desc()).limit(500)).all()
        return [{"type": r.type, "start_ts": _iso(r.start_ts.timestamp()) if r.start_ts else None,
                 "end_ts": _iso(r.end_ts.timestamp()) if r.end_ts else None, "value": r.value} for r in rows]


# ------------------------------------------------------------------ Kommando health_reference

def request_reference(camera_id: int, mode: str, wait_s: float = 8.0) -> dict[str, Any]:
    """Kommando an den Worker; wartet kurz auf `cmd_result`. → {request_id, ok, error, path} (ok None = offen)."""
    from .mqtt_bus import bus
    from .rules import new_request_id
    rid = new_request_id()
    ev = threading.Event()
    with _lock:
        _pending[rid] = {"event": ev, "result": None, "created": _now()}
        for k in [k for k, v in _pending.items() if _now() - v["created"] > 120]:
            _pending.pop(k, None)
    bus.publish(f"vg/internal/cmd/{camera_id}", {"action": "health_reference", "request_id": rid, "mode": mode},
                qos=1)
    ev.wait(wait_s)
    with _lock:
        p = _pending.pop(rid, None) or {}
    res = p.get("result")
    if res is None:
        return {"request_id": rid, "ok": None, "error": None, "path": None}
    return {"request_id": rid, "ok": bool(res.get("ok")), "error": res.get("error"),
            "path": res.get("path") if res.get("ok") else None}


def handle_cmd_result(msg: dict[str, Any]) -> bool:
    """`cmd_result` einer `health_reference`-Anfrage übernehmen. → True, wenn sie hierher gehörte."""
    rid = str(msg.get("request_id") or "")
    with _lock:
        p = _pending.get(rid)
        if p is None:
            return False
        p["result"] = msg
        p["event"].set()
    return True
