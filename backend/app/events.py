"""Track-/Event-Verarbeitung: MQTT-Track-Nachrichten → Events, Regeln, Aktionen, Zähler."""
from __future__ import annotations

import asyncio
import json
import logging
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Optional

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from . import go2rtc, media_sign, notifications, registry, rules, settings_store, state
from .db import SessionLocal
from .models import KV, Event, Identity, utcnow
from .rules import TrackState
from .serializers import event_json
from .util import from_unix, local_date_str, media_path
from .ws import hub

log = logging.getLogger("vg.events")

SNAPSHOT_FALLBACK_S = 5.0
PENDING_TTL_S = 900.0
WORKER_GONE_S = 60.0
TIME_CHECK_EVERY_S = 20.0          # Zeit-Regeln: Prüftakt (fällig ab `at`, 15 min Nachholfenster)
WEATHER_PREFETCH_EVERY_S = 300.0   # Wetter-Cache warm halten, solange Regeln mit Wetter-Bedingung existieren
TIME_RUNS_KEY = "rule_time_runs"   # KV: {rule_id: "YYYY-MM-DD HH:MM"} – letzter Lauf je Zeit-Regel

# Bedingungsprüfungen (A1) laufen außerhalb des Track-Threads (DB-Abfragen, ggf. Wetter-Abruf)
_cond_pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="vg-cond")


# ------------------------------------------------------------------ Hook-API (W1, genutzt von C3/C1/C2)


@dataclass(frozen=True)
class TrackHook:
    """Ein verarbeiteter Track-Schritt. `msg` ist die Worker-Nachricht (zones, entered_zones, left_zones, identity,
    attributes, ts, …; bei nachträglich aufgelöster Identität nur `{"identity": …}`), `track` der aktuelle
    Zustand – beides nur lesen."""
    phase: str                  # "start" | "update" | "end"
    camera_id: int
    track_id: int
    event_id: str
    label: str
    msg: dict[str, Any]
    track: TrackState


TrackCallback = Callable[[TrackHook], None]
_track_hooks: list[TrackCallback] = []


def on_track(callback: TrackCallback) -> Callable[[], None]:
    """Registriert einen Callback für jeden Track-Schritt (nach Event-Speicherung und Regelauswertung).

    Läuft im Track-Thread unter `processor.lock`: schnell bleiben, eigene DB-Session öffnen, nichts blockieren
    (Längeres per Thread/`rules.submit_async`). Ausnahmen werden geloggt und stoppen nichts. Liefert eine
    Funktion zum Abmelden."""
    _track_hooks.append(callback)

    def off() -> None:
        if callback in _track_hooks:
            _track_hooks.remove(callback)
    return off


def _run_track_hooks(phase: str, tr: TrackState, msg: dict[str, Any]) -> None:
    if not _track_hooks:
        return
    hook = TrackHook(phase=phase, camera_id=tr.camera_id, track_id=tr.track_id, event_id=tr.event_id,
                     label=tr.label, msg=msg or {}, track=tr)
    for cb in list(_track_hooks):
        try:
            cb(hook)
        except Exception:
            log.exception("Track-Hook %s fehlgeschlagen", getattr(cb, "__name__", cb))


class Processor:
    def __init__(self) -> None:
        self.lock = threading.RLock()
        self.tracks: dict[tuple[int, int], TrackState] = {}
        self.cooldowns: dict[tuple[int, int], float] = {}
        self.pending: dict[str, dict[str, Any]] = {}      # request_id -> {event_id, action, path, created}
        self.counts: dict[tuple[int, str], int] = {}       # (camera_id, label) -> sichtbare Objekte (veröffentlicht)
        self.loop: Optional[asyncio.AbstractEventLoop] = None
        self.publish = None   # callable(topic, payload, retain=False, qos=0)
        self._last_status_check = 0.0
        self._status_seen: dict[int, str] = {}
        # A1: laufende/geplante Bedingungsprüfungen je (Regel, Kamera) und Zeit-Regeln
        self.cond_pending: dict[tuple[int, int], float] = {}
        self._last_time_check = 0.0
        self._last_weather_prefetch = 0.0
        self._time_runs: Optional[dict[str, str]] = None

    # ---------------------------------------------------------- Hilfen

    def _pub(self, topic: str, payload: Any, retain: bool = False, qos: int = 0) -> None:
        if self.publish is None:
            return
        try:
            if not isinstance(payload, (str, bytes)):
                payload = json.dumps(payload, default=str, separators=(",", ":"))
            self.publish(topic, payload, retain=retain, qos=qos)
        except Exception as e:  # noqa: BLE001
            log.warning("MQTT-Publish %s fehlgeschlagen: %s", topic, e)

    def _emit(self, s: Session, ev: Event, action: str) -> dict[str, Any]:
        s.refresh(ev)
        data = event_json(ev)
        msg = {"type": "event", "action": action, "event": data}
        hub.broadcast(msg)
        st = settings_store.get()
        if st.get("publish_all_events"):
            # extern: Medien-Links signiert (TTL media_link_ttl_h)
            self._pub(f"{st['mqtt_prefix']}/events", {"action": action, "event": media_sign.sign_urls(data)}, qos=1)
        return data

    @staticmethod
    def _name_lookup(s: Session):
        cache: dict[int, Optional[str]] = {}

        def f(iid: int) -> Optional[str]:
            if iid not in cache:
                cache[iid] = s.scalar(select(Identity.name).where(Identity.id == iid))
            return cache[iid]
        return f

    @staticmethod
    def _valid_identity_id(s: Session, ident: Optional[dict[str, Any]]) -> Optional[int]:
        try:
            iid = int((ident or {}).get("id"))
        except (TypeError, ValueError):
            return None
        return iid if s.get(Identity, iid) is not None else None

    # ---------------------------------------------------------- Zähler (extern, retained)

    def _publish_counts(self, camera_id: int) -> None:
        cam = registry.get(camera_id)
        if not cam:
            return
        prefix = settings_store.get()["mqtt_prefix"]
        now_counts: dict[str, int] = {}
        for tr in self.tracks.values():
            if tr.camera_id == camera_id:
                now_counts[tr.label] = now_counts.get(tr.label, 0) + 1
        labels = set(now_counts) | {lbl for (cid, lbl) in self.counts if cid == camera_id}
        for lbl in labels:
            n = now_counts.get(lbl, 0)
            if self.counts.get((camera_id, lbl)) != n:
                self.counts[(camera_id, lbl)] = n
                self._pub(f"{prefix}/{cam['slug']}/{lbl}", str(n), retain=True)

    def reset_counts(self) -> None:
        """Beim Start / nach Kamera-Änderungen: alle Zähler (Kamera-Labels) neu veröffentlichen."""
        with self.lock:
            prefix = settings_store.get()["mqtt_prefix"]
            self.counts.clear()
            for cam in registry.all():
                for lbl in cam["labels"]:
                    n = sum(1 for t in self.tracks.values() if t.camera_id == cam["id"] and t.label == lbl)
                    self.counts[(cam["id"], lbl)] = n
                    self._pub(f"{prefix}/{cam['slug']}/{lbl}", str(n), retain=True)
            for (cid, tid), tr in self.tracks.items():
                self._publish_counts(cid)

    # ---------------------------------------------------------- Track-Nachrichten

    def handle_track(self, camera_id: int, msg: dict[str, Any]) -> None:
        with self.lock:
            try:
                with SessionLocal() as s:
                    self._handle_track(s, camera_id, msg)
            except Exception:
                log.exception("Fehler bei Track-Nachricht (Kamera %s)", camera_id)

    def _handle_track(self, s: Session, camera_id: int, msg: dict[str, Any]) -> None:
        typ = msg.get("type")
        try:
            track_id = int(msg.get("track_id"))
        except (TypeError, ValueError):
            log.warning("Track-Nachricht ohne track_id: %s", msg)
            return
        key = (camera_id, track_id)
        now = time.time()
        tr = self.tracks.get(key)
        first_seen = msg.get("first_seen") or msg.get("ts") or now

        if tr is not None and typ == "start" and abs(float(first_seen) - tr.first_seen) > 1.0:
            # Track-ID wurde vom Worker neu vergeben → alten Track beenden
            self._end_track(s, tr, {"ts": tr.first_seen}, now)
            tr = None

        if typ == "end":
            if tr is None:
                ev = s.scalar(select(Event).where(Event.camera_id == camera_id, Event.track_id == track_id,
                                                  Event.active.is_(True)).order_by(Event.start_ts.desc()).limit(1))
                if ev is None:
                    return
                tr = TrackState(camera_id=camera_id, track_id=track_id, event_id=ev.id,
                                first_seen=float(first_seen), label=ev.label, zones=list(msg.get("zones") or []))
                tr.started_rx = ev.start_ts.timestamp()
            self._apply_msg(s, tr, msg, now)
            self._end_track(s, tr, msg, now)
            return

        if tr is None:
            tr = self._start_track(s, camera_id, track_id, msg, now)
            if tr is None:
                return
            self._evaluate(s, tr, msg, now, phase="start")
            _run_track_hooks("start", tr, msg)
            return

        ev = self._apply_msg(s, tr, msg, now)
        if ev is not None:
            s.commit()
            self._emit(s, ev, "update")
        self._evaluate(s, tr, msg, now, phase="update")
        _run_track_hooks("update", tr, msg)

    def _start_track(self, s: Session, camera_id: int, track_id: int, msg: dict[str, Any],
                     now: float) -> Optional[TrackState]:
        cam = registry.get(camera_id)
        if cam is None:
            registry.reload()
            cam = registry.get(camera_id)
        if cam is None:
            log.warning("Track für unbekannte Kamera %s ignoriert", camera_id)
            return None
        first_seen = float(msg.get("first_seen") or msg.get("ts") or now)
        label = str(msg.get("label") or "object")
        score = float(msg.get("best_score") or msg.get("score") or 0)
        zones = [str(z) for z in msg.get("zones") or []]
        ev = Event(
            camera_id=camera_id, camera_name=cam["name"], track_id=track_id, label=label, score=score,
            start_ts=from_unix(first_seen) or utcnow(), last_seen=from_unix(msg.get("last_seen") or msg.get("ts")) or utcnow(),
            zones=zones, identity_id=self._valid_identity_id(s, msg.get("identity")),
            attributes=dict(msg.get("attributes") or {}), thumb_path=msg.get("crop_path") or None,
            rules=[], active=True,
        )
        s.add(ev)
        s.commit()
        # Lokale Startzeit möglichst nah an first_seen (Uhren können abweichen → begrenzen)
        started_rx = now - max(0.0, min(now - first_seen, 30.0))
        tr = TrackState(camera_id=camera_id, track_id=track_id, event_id=ev.id, first_seen=first_seen,
                        label=label, score=float(msg.get("score") or 0), best_score=score,
                        identity=msg.get("identity"), attributes=dict(msg.get("attributes") or {}),
                        started_rx=started_rx, last_rx=now)
        tr.set_zones(zones, started_rx)
        self.tracks[(camera_id, track_id)] = tr
        self._emit(s, ev, "new")
        self._publish_counts(camera_id)
        return tr

    def _apply_msg(self, s: Session, tr: TrackState, msg: dict[str, Any], now: float) -> Optional[Event]:
        """Übernimmt Felder aus update/end in Track-Zustand und Event-Zeile (ohne Commit)."""
        tr.last_rx = now
        if msg.get("score") is not None:
            tr.score = float(msg["score"])
        tr.best_score = max(tr.best_score, float(msg.get("best_score") or 0), tr.score)
        if "zones" in msg and msg.get("type") != "end":
            tr.set_zones(list(msg.get("zones") or []), now)
        if msg.get("identity"):
            tr.identity = msg["identity"]
        if msg.get("attributes"):
            tr.attributes.update(msg["attributes"])
        ev = s.get(Event, tr.event_id)
        if ev is None:
            return None
        ev.score = max(float(ev.score or 0), tr.best_score)
        seen = from_unix(msg.get("last_seen") or msg.get("ts"))
        if seen:
            ev.last_seen = seen
        zones = list(ev.zones or [])
        for z in list(msg.get("zones") or []) + list(msg.get("entered_zones") or []):
            if str(z) not in zones:
                zones.append(str(z))
        ev.zones = zones
        iid = self._valid_identity_id(s, msg.get("identity"))
        if iid is not None:
            ev.identity_id = iid
        if msg.get("attributes"):
            ev.attributes = {**(ev.attributes or {}), **msg["attributes"]}
        if msg.get("crop_path"):
            ev.thumb_path = msg["crop_path"]
        return ev

    def _end_track(self, s: Session, tr: TrackState, msg: dict[str, Any], now: float) -> None:
        self._evaluate(s, tr, msg, now, phase="end")
        self.tracks.pop((tr.camera_id, tr.track_id), None)
        ev = s.get(Event, tr.event_id)
        if ev is not None:
            ev.active = False
            ev.end_ts = from_unix(msg.get("last_seen") or msg.get("ts")) or utcnow()
            if ev.end_ts < ev.start_ts:
                ev.end_ts = ev.start_ts
            s.commit()
            self._emit(s, ev, "end")
        self._publish_counts(tr.camera_id)
        _run_track_hooks("end", tr, msg)

    # ---------------------------------------------------------- Regeln

    def _evaluate(self, s: Session, tr: TrackState, msg: dict[str, Any], now: float, phase: str,
                  only_custom: bool = False) -> None:
        """Regeln für einen Track-Schritt auswerten. enter/leave/dwell nur in den Track-Phasen start/update/end;
        Nicht-Standard-Trigger (W2: posture, gesture) über ihren Evaluator in jeder Phase (auch tick/gesture)."""
        lookup = self._name_lookup(s)
        if only_custom or phase not in ("start", "update", "end"):
            self._evaluate_custom(s, tr, msg, now, phase, lookup)
            return
        entered = [str(z) for z in msg.get("entered_zones") or []]
        left = [str(z) for z in msg.get("left_zones") or []]
        if phase == "start":
            entered = list(dict.fromkeys(entered + tr.zones))
        end_zones = [str(z) for z in (msg.get("zones") if msg.get("zones") is not None else tr.zones)]
        for rule in rules.all_rules():
            try:
                rid = int(rule["id"])
                trig = rule.get("trigger") or "enter"
                rz = rule.get("zone_ids") or []
                fire_zones: Optional[list[str]] = None
                if trig == "enter" and rid not in tr.fired_enter and phase != "end":
                    if not rz:
                        fire_zones = list(tr.zones)
                    else:
                        hit = rules.rule_zones_hit(rule, entered) or rules.rule_zones_hit(rule, tr.zones)
                        if hit:
                            fire_zones = hit
                    if fire_zones is not None and rules.static_match(rule, tr, lookup):
                        tr.fired_enter.add(rid)
                        self._fire(s, rule, tr, fire_zones, now)
                elif trig == "leave" and rid not in tr.fired_leave:
                    if rz:
                        hit = rules.rule_zones_hit(rule, left)
                        if not hit and phase == "end":
                            hit = rules.rule_zones_hit(rule, end_zones)
                        if hit:
                            fire_zones = hit
                    elif phase == "end":
                        fire_zones = end_zones
                    if fire_zones is not None and rules.static_match(rule, tr, lookup):
                        tr.fired_leave.add(rid)
                        self._fire(s, rule, tr, fire_zones, now)
                elif trig == "dwell" and phase != "end":
                    self._check_dwell(s, rule, tr, now, lookup)
            except Exception:
                log.exception("Regel %s: Auswertung fehlgeschlagen", rule.get("name"))
        self._evaluate_custom(s, tr, msg, now, phase, lookup)

    def _evaluate_custom(self, s: Session, tr: TrackState, msg: dict[str, Any], now: float, phase: str,
                         lookup) -> None:
        """W2: Track-Trigger mit Evaluator (rules.register_trigger(..., evaluate=…)). Der Evaluator entscheidet über
        Haltezeiten/Einmaligkeit (eigener `state`); Cooldown, static_match und Bedingungen prüft wie immer _fire."""
        for rule in rules.all_rules():
            ev_fn = rules.track_evaluator(rule.get("trigger"))
            if ev_fn is None or not rule.get("enabled"):
                continue
            try:
                rid = int(rule["id"])
                st = tr.trigger_state.setdefault(rid, {})
                zones = ev_fn(rules.TriggerContext(rule=rule, track=tr, msg=msg or {}, now=now, phase=phase, state=st))
                if zones is not None and rules.static_match(rule, tr, lookup):
                    self._fire(s, rule, tr, list(zones), now)
            except Exception:
                log.exception("Regel %s: Trigger %s fehlgeschlagen", rule.get("name"), rule.get("trigger"))

    def dispatch_track_phase(self, camera_id: int, track_id: int, phase: str, msg: dict[str, Any]) -> bool:
        """W2: Nur Evaluator-Trigger für einen laufenden Track auswerten (z. B. phase="gesture" aus
        vg/internal/gesture). False, wenn der Track (noch) nicht bekannt ist."""
        with self.lock:
            tr = self.tracks.get((camera_id, int(track_id)))
            if tr is None:
                return False
            try:
                with SessionLocal() as s:
                    self._evaluate(s, tr, msg, time.time(), phase=phase, only_custom=True)
            except Exception:
                log.exception("Track-Phase %s (Kamera %s, Track %s) fehlgeschlagen", phase, camera_id, track_id)
            return True

    def _check_dwell(self, s: Session, rule: dict[str, Any], tr: TrackState, now: float, lookup) -> None:
        rid = int(rule["id"])
        if rid in tr.fired_dwell:
            return
        rz = rule.get("zone_ids") or []
        if rz and not rules.rule_zones_hit(rule, tr.zones):
            return
        if rules.dwell_seconds(rule, tr, now) < float(rule.get("min_dwell_s") or 0):
            return
        if not rules.static_match(rule, tr, lookup):
            return
        tr.fired_dwell.add(rid)
        self._fire(s, rule, tr, rules.rule_zones_hit(rule, tr.zones) if rz else list(tr.zones), now)

    def _cooldown_active(self, ck: tuple[int, int], rule: dict[str, Any], now: float) -> bool:
        last = self.cooldowns.get(ck)
        return last is not None and now - last < float(rule.get("cooldown_s") or 0)

    def _fire(self, s: Session, rule: dict[str, Any], tr: TrackState, zones: list[str], now: float) -> None:
        ck = (int(rule["id"]), tr.camera_id)
        if self._cooldown_active(ck, rule, now):
            log.info("Regel '%s' (Kamera %s): Cooldown aktiv", rule["name"], tr.camera_id)
            return
        if rule.get("conditions"):
            # A1: Bedingungen erst nach condition_delay_s prüfen (Gegenstand braucht Zeit, um „missing“ zu werden);
            # je Regel und Kamera höchstens eine offene Prüfung, Cooldown erst, wenn die Aktionen wirklich laufen
            if ck in self.cond_pending:
                log.debug("Regel '%s' (Kamera %s): Prüfung läuft bereits", rule["name"], tr.camera_id)
                return
            self.cond_pending[ck] = now
            delay = float(rule.get("condition_delay_s") or 0)
            log.info("Regel '%s' ausgelöst (Kamera %s, Track %s) – Bedingungen werden in %.0f s geprüft",
                     rule["name"], tr.camera_id, tr.track_id, delay)
            rid, zs = int(rule["id"]), list(zones)

            def job() -> None:
                _cond_pool.submit(self._conditional_fire, rid, tr, zs, now)
            if delay > 0:
                t = threading.Timer(delay, job)
                t.daemon = True
                t.start()
            else:
                job()
            return
        self.cooldowns[ck] = now
        self._do_fire(s, rule, tr, zones, now)

    def _conditional_fire(self, rule_id: int, tr: TrackState, zones: list[str], trigger_ts: float) -> None:
        """Läuft im Pool: Bedingungen prüfen (ohne Lock), bei Erfolg Aktionen unter dem Lock ausführen."""
        ck = (rule_id, tr.camera_id)
        try:
            rule = next((r for r in rules.all_rules() if int(r["id"]) == rule_id), None)
            if rule is None or not rule.get("enabled"):
                log.info("Regel %s inzwischen gelöscht/deaktiviert – Prüfung entfällt", rule_id)
                return
            ctx = rules.CondContext(rule=rule, now=datetime.now(timezone.utc), camera_id=tr.camera_id, track=tr,
                                    event_id=tr.event_id, trigger_ts=trigger_ts, trigger_zones=list(zones))
            res = rules.evaluate_conditions(rule, ctx)
            if not res.ok:
                log.info("Regel '%s' (Kamera %s): Bedingungen nicht erfüllt – %s", rule["name"], tr.camera_id,
                         res.reason or "?")
                return
            with self.lock:
                now = time.time()
                if self._cooldown_active(ck, rule, now):
                    log.info("Regel '%s' (Kamera %s): Cooldown aktiv", rule["name"], tr.camera_id)
                    return
                self.cooldowns[ck] = now
                log.info("Regel '%s' (Kamera %s): Bedingungen erfüllt – %s", rule["name"], tr.camera_id, res.reason)
                with SessionLocal() as s:
                    self._do_fire(s, rule, tr, zones, now, cond=res)
        except Exception:
            log.exception("Regel %s: verzögerte Prüfung fehlgeschlagen", rule_id)
        finally:
            with self.lock:
                self.cond_pending.pop(ck, None)

    def _do_fire(self, s: Session, rule: dict[str, Any], tr: TrackState, zones: list[str], now: float,
                 cond: Optional[rules.CondResult] = None) -> None:
        ev = s.get(Event, tr.event_id)
        if ev is None:
            return
        log.info("Regel '%s' ausgelöst (Kamera %s, Track %s, Event %s)", rule["name"], tr.camera_id, tr.track_id, ev.id)
        names = list(ev.rules or [])
        if rule["name"] not in names:
            names.append(rule["name"])
            ev.rules = names
        s.commit()
        event_data = self._emit(s, ev, "update")
        cam = registry.get(tr.camera_id) or {"name": ev.camera_name, "zones": {}}
        zone_names = [cam["zones"].get(z, z) for z in zones]
        ident = event_data.get("identity") or {}
        date = local_date_str()
        ttl = media_sign.default_ttl_s()
        act_types = {a.get("type") for a in rule.get("actions") or []}
        # Snapshot/Clip dieser Regel entstehen erst nach der Auslösung – Pfad ist aber schon bekannt
        snap_path = ev.snapshot_path or (f"snapshots/{date}/{ev.id}_{rule['id']}.jpg"
                                         if "snapshot" in act_types else None)
        clip_path = ev.clip_path or (f"clips/{date}/{ev.id}.mp4" if "clip" in act_types else None)
        ctx = {
            "camera": cam["name"], "camera_id": tr.camera_id, "label": tr.label,
            "score": f"{max(tr.best_score, tr.score):.2f}", "identity": ident.get("display_name") or "",
            "zone": ", ".join(zone_names), "event_id": ev.id, "rule": rule["name"],
            "time": datetime.now(timezone.utc).astimezone(rules.config.tz).isoformat(timespec="seconds"),
            "thumb_url": media_sign.sign_media_path(ev.thumb_path, ttl) if ev.thumb_path else "",
            "snapshot_url": media_sign.sign_media_path(snap_path, ttl) if snap_path else "",
            "clip_url": media_sign.sign_media_path(clip_path, ttl) if clip_path else "",
            # A1: Platzhalter aus den Bedingungen (leer ohne passende Bedingung)
            "items": "", "item_place": "", "weather": "",
        }
        if cond is not None:
            ctx.update({k: v for k, v in cond.ctx.items() if k in ("items", "item_place", "weather")})
        event_data = media_sign.sign_urls(event_data, ttl)   # Webhook-/MQTT-Payloads gehen nach außen
        for action in rule.get("actions") or []:
            try:
                self._run_action(action, rule, tr, ev, event_data, ctx, date, cond)
            except Exception:
                log.exception("Regel '%s': Aktion %s fehlgeschlagen", rule["name"], action.get("type"))
        if cond is not None and "push" not in act_types:
            # Regeln mit Bedingungen, aber ohne Push: Hinweis nur anzeigen (CONTRACT W1.B)
            notifications.create(
                source="rule", title=rule["name"], message=_cond_message(ctx), rule_id=int(rule["id"]),
                rule_name=rule.get("name"), camera_id=tr.camera_id, event_id=ev.id, item_ids=list(cond.item_ids),
                image_path=snap_path or cond.image_path or ev.thumb_path, push_enabled=False)

    def _run_action(self, action: dict[str, Any], rule: dict[str, Any], tr: TrackState, ev: Event,
                    event_data: dict[str, Any], ctx: dict[str, Any], date: str,
                    cond: Optional[rules.CondResult] = None) -> None:
        typ = action.get("type")
        if typ == "snapshot":
            rid = rules.new_request_id()
            path = f"snapshots/{date}/{ev.id}_{rule['id']}.jpg"
            self.pending[rid] = {"event_id": ev.id, "action": "snapshot", "path": path, "created": time.time(),
                                 "camera_id": tr.camera_id}
            self._pub(f"vg/internal/cmd/{tr.camera_id}", {"action": "snapshot", "request_id": rid, "path": path}, qos=1)
            rules.submit_async(self.loop, self._snapshot_fallback(rid))
        elif typ == "clip":
            if tr.clip_requested:
                return
            tr.clip_requested = True
            rid = rules.new_request_id()
            path = f"clips/{date}/{ev.id}.mp4"
            pre_s = float(action.get("pre_s", 5) or 0)
            post_s = float(action.get("post_s", 10) or 0)
            self.pending[rid] = {"event_id": ev.id, "action": "clip", "path": path, "created": time.time(),
                                 "camera_id": tr.camera_id}
            self._pub(f"vg/internal/cmd/{tr.camera_id}",
                      {"action": "clip", "request_id": rid, "path": path, "pre_s": pre_s, "post_s": post_s}, qos=1)
        elif typ == "mqtt":
            topic = rules.render(action.get("topic") or "", ctx)
            if not topic or "#" in topic or "+" in topic:
                log.warning("Regel '%s': ungültiges MQTT-Topic '%s'", rule["name"], topic)
                return
            payload = action.get("payload")
            body = rules.render(payload, ctx) if payload else json.dumps(event_data, default=str)
            self._pub(topic, body, retain=bool(action.get("retain")), qos=1)
        elif typ == "webhook":
            rules.submit_async(self.loop, rules.run_webhook(action, ctx, event_data))
        elif typ == "push":
            self._run_push(action, rule, tr, ev, ctx, date, cond)
        else:
            log.warning("Unbekannte Aktion: %s", typ)

    def _run_push(self, action: dict[str, Any], rule: dict[str, Any], tr: TrackState, ev: Event,
                  ctx: dict[str, Any], date: str, cond: Optional[rules.CondResult] = None) -> Optional[dict]:
        """Aktion `push`: je Aktion eine Notification (source "rule") mit Push an `target` (None = alle Ziele).
        `cond` (A1): Ergebnis der Bedingungen – liefert item_ids und ggf. das Bild des Gegenstands."""
        mode = action.get("image") or "snapshot"
        has_snap = any(a.get("type") == "snapshot" for a in rule.get("actions") or [])
        if mode == "snapshot":
            image = ev.snapshot_path or (f"snapshots/{date}/{ev.id}_{rule['id']}.jpg" if has_snap else ev.thumb_path)
        elif mode == "crop":
            image = (cond.image_path if cond else None) or ev.thumb_path
        else:
            image = None
        priority = action.get("priority") or "default"
        target = action.get("target")
        return notifications.create(
            source="rule", level="alarm" if priority == "high" else "info",
            title=rules.render(action.get("title") or "{rule}", ctx),
            message=rules.render(action.get("message") or "", ctx),
            rule_id=int(rule["id"]), rule_name=rule.get("name"), camera_id=tr.camera_id, event_id=ev.id,
            item_ids=list(cond.item_ids) if cond else [], image_path=image,
            targets=[target] if target else None, priority=priority)

    # ---------------------------------------------------------- Snapshot/Clip-Ergebnisse

    async def _snapshot_fallback(self, request_id: str) -> None:
        await asyncio.sleep(SNAPSHOT_FALLBACK_S)
        with self.lock:
            p = self.pending.pop(request_id, None)
        if p is None:
            return
        log.info("Worker-Snapshot ausgeblieben → Fallback über go2rtc (Event %s)", p["event_id"])
        try:
            data = await go2rtc.fetch_frame(go2rtc.stream_name(p["camera_id"]), timeout=10)
            await asyncio.to_thread(self._write_file, p["path"], data)
            await asyncio.to_thread(self._attach, p["event_id"], "snapshot", p["path"])
        except Exception as e:  # noqa: BLE001
            log.warning("Fallback-Snapshot fehlgeschlagen (Event %s): %s", p["event_id"], e)

    @staticmethod
    def _write_file(rel: str, data: bytes) -> None:
        full = media_path(rel)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        tmp = full + ".tmp"
        with open(tmp, "wb") as f:
            f.write(data)
        os.replace(tmp, full)

    def _attach(self, event_id: str, action: str, path: str) -> None:
        with self.lock:
            with SessionLocal() as s:
                ev = s.get(Event, event_id)
                if ev is None:
                    return
                if action == "snapshot":
                    ev.snapshot_path = path
                elif action == "clip":
                    ev.clip_path = path
                else:
                    return
                s.commit()
                self._emit(s, ev, "update")

    def handle_cmd_result(self, msg: dict[str, Any]) -> None:
        rid = str(msg.get("request_id") or "")
        with self.lock:
            p = self.pending.pop(rid, None)
        if p is None:
            return
        if not msg.get("ok"):
            log.warning("Worker-%s fehlgeschlagen (Event %s): %s", p["action"], p["event_id"], msg.get("error"))
            if p["action"] == "snapshot":
                # Auftrag bleibt offen → Fallback über go2rtc nach Timeout
                with self.lock:
                    self.pending[rid] = p
            return
        try:
            self._attach(p["event_id"], msg.get("action") or p["action"], msg.get("path") or p["path"])
        except Exception:
            log.exception("Ergebnis konnte nicht übernommen werden")

    # ---------------------------------------------------------- Identität (aus identify)

    def notify_event_changed(self, event_id: str) -> None:
        with self.lock:
            try:
                with SessionLocal() as s:
                    ev = s.get(Event, event_id)
                    if ev is None:
                        return
                    data = self._emit(s, ev, "update")
                    tr = self.tracks.get((ev.camera_id, ev.track_id))
                    if tr is not None and tr.event_id == ev.id and data.get("identity"):
                        tr.identity = data["identity"]
                        self._evaluate(s, tr, {}, time.time(), phase="update")
                        _run_track_hooks("update", tr, {"identity": data["identity"]})
            except Exception:
                log.exception("Event-Aktualisierung fehlgeschlagen")

    # ---------------------------------------------------------- Periodischer Tick (1 s)

    def tick(self) -> None:
        now = time.time()
        with self.lock:
            try:
                dwell_rules = [r for r in rules.all_rules() if r.get("enabled") and r.get("trigger") == "dwell"]
                custom = any(r.get("enabled") and rules.track_evaluator(r.get("trigger")) for r in rules.all_rules())
                if (dwell_rules or custom) and self.tracks:
                    with SessionLocal() as s:
                        lookup = self._name_lookup(s)
                        for tr in list(self.tracks.values()):
                            for rule in dwell_rules:
                                try:
                                    self._check_dwell(s, rule, tr, now, lookup)
                                except Exception:
                                    log.exception("Dwell-Prüfung fehlgeschlagen")
                            if custom:   # W2: Evaluator-Trigger (posture: Haltedauer) jede Sekunde
                                self._evaluate_custom(s, tr, {}, now, "tick", lookup)
                # abgelaufene Aufträge
                for rid in [k for k, v in self.pending.items() if now - v["created"] > PENDING_TTL_S]:
                    self.pending.pop(rid, None)
                if now - self._last_status_check >= 5:
                    self._last_status_check = now
                    self._check_status(now)
            except Exception:
                log.exception("Tick fehlgeschlagen")
        if now - self._last_time_check >= TIME_CHECK_EVERY_S:
            self._last_time_check = now
            try:
                self._check_time_rules()
            except Exception:
                log.exception("Zeit-Regeln: Prüfung fehlgeschlagen")

    # ---------------------------------------------------------- Zeit-Regeln (A1, trigger=time)

    def _load_time_runs(self) -> dict[str, str]:
        if self._time_runs is None:
            with SessionLocal() as s:
                row = s.get(KV, TIME_RUNS_KEY)
                self._time_runs = dict(row.value) if row and isinstance(row.value, dict) else {}
        return self._time_runs

    def _save_time_run(self, rule_id: int, key: str) -> None:
        runs = self._load_time_runs()
        runs[str(rule_id)] = key
        valid = {str(r["id"]) for r in rules.all_rules()}
        for k in [k for k in runs if k not in valid]:
            runs.pop(k, None)
        with SessionLocal() as s:
            row = s.get(KV, TIME_RUNS_KEY)
            if row is None:
                s.add(KV(key=TIME_RUNS_KEY, value=dict(runs)))
            else:
                row.value = dict(runs)
            s.commit()

    def _check_time_rules(self) -> None:
        """Fällige Zeit-Regeln starten (Planung zustandslos aus Regeln + KV `rule_time_runs`)."""
        now_dt = datetime.now(timezone.utc)
        time_rules = [r for r in rules.all_rules() if r.get("enabled") and r.get("trigger") == "time"]
        if time_rules:
            runs = self._load_time_runs()
            for rule in time_rules:
                key = rules.time_rule_due(rule, now_dt, runs.get(str(rule["id"])))
                if key is None:
                    continue
                self._save_time_run(int(rule["id"]), key)   # vor dem Lauf merken: höchstens 1× je Tag und `at`
                log.info("Zeit-Regel '%s' fällig (%s)", rule["name"], key)
                _cond_pool.submit(self._run_time_rule_safe, int(rule["id"]))
        if time.time() - self._last_weather_prefetch >= WEATHER_PREFETCH_EVERY_S:
            from . import rule_conditions, weather
            if rule_conditions.uses_weather() and weather.location() is not None:
                self._last_weather_prefetch = time.time()
                _cond_pool.submit(weather.prefetch)

    def _run_time_rule_safe(self, rule_id: int) -> None:
        try:
            self.run_time_rule(rule_id)
        except Exception:
            log.exception("Zeit-Regel %s fehlgeschlagen", rule_id)

    def run_time_rule(self, rule_id: int, *, force: bool = False) -> Optional[dict[str, Any]]:
        """Zeit-Regel ausführen: Bedingungen prüfen, bei Erfolg Aktionen ohne Event (Notification statt Event).
        Liefert {ok, reason, notifications} oder None (Regel unbekannt). `force` ignoriert den Cooldown."""
        rule = next((r for r in rules.all_rules() if int(r["id"]) == rule_id), None)
        if rule is None:
            return None
        now_dt = datetime.now(timezone.utc)
        res = rules.evaluate_conditions(rule, rules.CondContext(rule=rule, now=now_dt))
        if not res.ok:
            log.info("Zeit-Regel '%s': Bedingungen nicht erfüllt – %s", rule["name"], res.reason or "?")
            return {"ok": False, "reason": res.reason, "notifications": []}
        ck = (rule_id, 0)
        with self.lock:
            if not force and self._cooldown_active(ck, rule, time.time()):
                log.info("Zeit-Regel '%s': Cooldown aktiv", rule["name"])
                return {"ok": False, "reason": "Cooldown aktiv", "notifications": []}
            self.cooldowns[ck] = time.time()
        log.info("Zeit-Regel '%s' ausgelöst – %s", rule["name"], res.reason or "ohne Bedingungen")
        cam = registry.get(res.camera_id) if res.camera_id is not None else None
        ttl = media_sign.default_ttl_s()
        ctx: dict[str, Any] = {
            "camera": (cam or {}).get("name") or "", "camera_id": res.camera_id if res.camera_id is not None else "",
            "label": "", "score": "", "identity": "", "zone": "", "event_id": "", "rule": rule["name"],
            "time": now_dt.astimezone(rules.config.tz).isoformat(timespec="seconds"),
            "thumb_url": media_sign.sign_media_path(res.image_path, ttl) if res.image_path else "",
            "snapshot_url": media_sign.sign_media_path(res.snapshot_path, ttl) if res.snapshot_path else "",
            "clip_url": "", "items": "", "item_place": "", "weather": "",
        }
        ctx.update({k: v for k, v in res.ctx.items() if k in ("items", "item_place", "weather")})
        created: list[dict[str, Any]] = []
        actions = rule.get("actions") or []
        for a in actions:
            if a.get("type") != "push":
                continue
            mode = a.get("image") or "snapshot"
            if mode == "snapshot":
                image = res.snapshot_path or res.image_path
            elif mode == "crop":
                image = res.image_path or res.snapshot_path
            else:
                image = None
            priority = a.get("priority") or "default"
            n = notifications.create(
                source="rule", level="alarm" if priority == "high" else "info",
                title=rules.render(a.get("title") or "{rule}", ctx), message=rules.render(a.get("message") or "", ctx),
                rule_id=rule_id, rule_name=rule.get("name"), camera_id=res.camera_id, item_ids=list(res.item_ids),
                image_path=image, targets=[a["target"]] if a.get("target") else None, priority=priority,
                data={"trigger": "time"})
            if n:
                created.append(n)
        if not any(a.get("type") == "push" for a in actions):
            n = notifications.create(
                source="rule", title=rule["name"], message=_cond_message(ctx) or rule["name"], rule_id=rule_id,
                rule_name=rule.get("name"), camera_id=res.camera_id, item_ids=list(res.item_ids),
                image_path=res.snapshot_path or res.image_path, push_enabled=False, data={"trigger": "time"})
            if n:
                created.append(n)
        # MQTT/Webhook ohne eigenen Payload: Notification-JSON (signierte URLs), sonst die Platzhalter
        payload = media_sign.sign_urls(dict(created[0])) if created else dict(ctx)
        for a in actions:
            typ = a.get("type")
            try:
                if typ == "mqtt":
                    topic = rules.render(a.get("topic") or "", ctx)
                    if not topic or "#" in topic or "+" in topic:
                        log.warning("Zeit-Regel '%s': ungültiges MQTT-Topic '%s'", rule["name"], topic)
                        continue
                    body = rules.render(a["payload"], ctx) if a.get("payload") else json.dumps(payload, default=str)
                    self._pub(topic, body, retain=bool(a.get("retain")), qos=1)
                elif typ == "webhook":
                    rules.submit_async(self.loop, rules.run_webhook(a, ctx, payload))
                elif typ in ("snapshot", "clip"):
                    log.info("Zeit-Regel '%s': Aktion %s braucht ein Ereignis – übersprungen", rule["name"], typ)
            except Exception:
                log.exception("Zeit-Regel '%s': Aktion %s fehlgeschlagen", rule["name"], typ)
        return {"ok": True, "reason": res.reason, "notifications": created}

    def _check_status(self, now: float) -> None:
        """Statuswechsel (z. B. online → offline durch Timeout) melden; verwaiste Tracks beenden."""
        for cam in registry.all():
            st = state.status_for(cam["id"], cam["enabled"])
            if self._status_seen.get(cam["id"]) not in (None, st["state"]):
                hub.broadcast({"type": "camera_status", "camera_id": cam["id"], "status": st})
            self._status_seen[cam["id"]] = st["state"]
        stale = []
        for key, tr in self.tracks.items():
            with state._lock:
                rx = (state.camera_status.get(tr.camera_id) or {}).get("_rx", 0)
            if now - max(rx, tr.last_rx) > WORKER_GONE_S:
                stale.append(tr)
        if stale:
            with SessionLocal() as s:
                for tr in stale:
                    log.info("Track %s/%s verwaist → beendet", tr.camera_id, tr.track_id)
                    self._end_track(s, tr, {"ts": tr.last_rx}, now)

    def drop_camera(self, camera_id: int) -> None:
        with self.lock:
            for key in [k for k in self.tracks if k[0] == camera_id]:
                self.tracks.pop(key, None)
            for key in [k for k in self.counts if k[0] == camera_id]:
                self.counts.pop(key, None)


def _cond_message(ctx: dict[str, Any]) -> str:
    """Hinweistext für Regeln mit Bedingungen ohne eigene Push-Aktion."""
    parts = []
    if ctx.get("items"):
        parts.append(f"{ctx['items']}: {ctx['item_place']}" if ctx.get("item_place") else str(ctx["items"]))
    if ctx.get("weather"):
        parts.append(str(ctx["weather"]))
    if not parts and ctx.get("label"):
        parts.append(f"{ctx['label']} an {ctx.get('camera') or 'Kamera'}")
    return " · ".join(parts)


processor = Processor()


def notify_event_changed(event_id: str) -> None:
    processor.notify_event_changed(event_id)


def close_stale_events() -> None:
    """Beim Start: noch aktive Events aus früheren Läufen beenden."""
    with SessionLocal() as s:
        s.execute(update(Event).where(Event.active.is_(True)).values(active=False, end_ts=Event.last_seen))
        s.commit()
