"""Track-/Event-Verarbeitung: MQTT-Track-Nachrichten → Events, Regeln, Aktionen, Zähler."""
from __future__ import annotations

import asyncio
import json
import logging
import os
import threading
import time
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from . import go2rtc, media_sign, registry, rules, settings_store, state
from .db import SessionLocal
from .models import Event, Identity, utcnow
from .rules import TrackState
from .serializers import event_json
from .util import from_unix, local_date_str, media_path
from .ws import hub

log = logging.getLogger("vg.events")

SNAPSHOT_FALLBACK_S = 5.0
PENDING_TTL_S = 900.0
WORKER_GONE_S = 60.0


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
            return

        ev = self._apply_msg(s, tr, msg, now)
        if ev is not None:
            s.commit()
            self._emit(s, ev, "update")
        self._evaluate(s, tr, msg, now, phase="update")

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

    # ---------------------------------------------------------- Regeln

    def _evaluate(self, s: Session, tr: TrackState, msg: dict[str, Any], now: float, phase: str) -> None:
        lookup = self._name_lookup(s)
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

    def _fire(self, s: Session, rule: dict[str, Any], tr: TrackState, zones: list[str], now: float) -> None:
        ck = (int(rule["id"]), tr.camera_id)
        cd = float(rule.get("cooldown_s") or 0)
        last = self.cooldowns.get(ck)
        if last is not None and now - last < cd:
            log.info("Regel '%s' (Kamera %s): Cooldown aktiv", rule["name"], tr.camera_id)
            return
        self.cooldowns[ck] = now
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
        }
        event_data = media_sign.sign_urls(event_data, ttl)   # Webhook-/MQTT-Payloads gehen nach außen
        for action in rule.get("actions") or []:
            try:
                self._run_action(action, rule, tr, ev, event_data, ctx, date)
            except Exception:
                log.exception("Regel '%s': Aktion %s fehlgeschlagen", rule["name"], action.get("type"))

    def _run_action(self, action: dict[str, Any], rule: dict[str, Any], tr: TrackState, ev: Event,
                    event_data: dict[str, Any], ctx: dict[str, Any], date: str) -> None:
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
        else:
            log.warning("Unbekannte Aktion: %s", typ)

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
            except Exception:
                log.exception("Event-Aktualisierung fehlgeschlagen")

    # ---------------------------------------------------------- Periodischer Tick (1 s)

    def tick(self) -> None:
        now = time.time()
        with self.lock:
            try:
                dwell_rules = [r for r in rules.all_rules() if r.get("enabled") and r.get("trigger") == "dwell"]
                if dwell_rules and self.tracks:
                    with SessionLocal() as s:
                        lookup = self._name_lookup(s)
                        for tr in list(self.tracks.values()):
                            for rule in dwell_rules:
                                try:
                                    self._check_dwell(s, rule, tr, now, lookup)
                                except Exception:
                                    log.exception("Dwell-Prüfung fehlgeschlagen")
                # abgelaufene Aufträge
                for rid in [k for k, v in self.pending.items() if now - v["created"] > PENDING_TTL_S]:
                    self.pending.pop(rid, None)
                if now - self._last_status_check >= 5:
                    self._last_status_check = now
                    self._check_status(now)
            except Exception:
                log.exception("Tick fehlgeschlagen")

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


processor = Processor()


def notify_event_changed(event_id: str) -> None:
    processor.notify_event_changed(event_id)


def close_stale_events() -> None:
    """Beim Start: noch aktive Events aus früheren Läufen beenden."""
    with SessionLocal() as s:
        s.execute(update(Event).where(Event.active.is_(True)).values(active=False, end_ts=Event.last_seen))
        s.commit()
