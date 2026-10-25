"""PTZ – Schwenken/Neigen/Zoomen über ONVIF (CONTRACT „Nachträge: PTZ“).

Je Kamera mit `ptz_enabled` ein `Controller` mit eigenem Takt-Thread und genau einem ONVIF-Ausführungsthread
(Befehle an die Kamera laufen nacheinander). Betriebsarten mit Vorrang manual > rule > follow > patrol > idle.
`view` sagt Worker und Oberfläche, ob die Kamera ruhig in der Grundposition steht – nur dann gelten Zonen,
Gegenstände, Sensoren und Gesundheitsprüfung (`vg/internal/ptz/{id}`, retain).
"""
from __future__ import annotations

import copy
import logging
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Any, Optional
from urllib.parse import unquote, urlsplit

from sqlalchemy import select

from .db import SessionLocal
from .models import Camera
from .onvif import OnvifError, OnvifPtz

log = logging.getLogger("vg.ptz")

DEFAULT_CONFIG: dict[str, Any] = {
    "onvif_port": None, "onvif_user": None, "onvif_password": None, "profile_token": None,
    "home_preset": None, "return_s": 60, "home_on_start": True, "speed": 0.5,
    "invert_pan": False, "invert_tilt": False,
    "patrol": {"enabled": False, "steps": [], "schedule": None, "pause_s": 0},
    "follow": {"enabled": False, "labels": ["person"], "schedule": None, "lost_s": 8, "max_s": 300, "speed": 0.5,
               "latency_s": 1.4, "tilt": True},
}
CONNECT_RETRY_S = 60.0
SETTLE_S = 2.5                 # nach Ende einer Fahrt: Bild beruhigt sich, Video-Verzögerung (go2rtc → Worker)
GOTO_TIMEOUT_S = 20.0          # GotoPreset blockiert bei manchen Geräten bis zum Ziel (Reolink ~4–5 s)
STATUS_MAX_S = 20.0
FOLLOW_ENGAGE_S = 1.0          # Ziel muss so lange im Bild sein, bevor die Verfolgung startet
FOLLOW_COOLDOWN_S = 60.0       # Ruhe nach max_s
# Verfolgen – gemessen an Reolink E1 Pro (CONTRACT PTZ.B, AGENTS.md „PTZ“): die Geschwindigkeit wirkt nicht
# (0,1 … 1 fahren gleich schnell), 0,5 s ≈ ⅓ Bildbreite, Bewegung erst ≈ 1,1–1,2 s nach dem Befehl im Detect-Bild,
# Impulse < 0,25 s werden teils verschluckt. Daher: kurze Impulse auf einer Achse, absichtlich zu wenig (Anteil
# FOLLOW_FRACTION), erst wieder nachsteuern, wenn ein Bild nach dem Stillstand ausgewertet ist; Schrittweite und
# Richtung lernt der Regler aus der beobachteten Verschiebung des Ziels nach (nur im Speicher).
FOLLOW_DEADBAND_X = 0.12       # Totzone um die Bildmitte (normiert, Abweichung vom Zentrum)
FOLLOW_DEADBAND_Y = 0.15
FOLLOW_LATENCY_S = 1.4         # Default follow.latency_s: Bildverzögerung Kamera → Detektion nach dem Stopp
FOLLOW_GAIN = 0.6              # Startwert: Bildanteil je Sekunde Impuls (lernt nach)
FOLLOW_GAIN_MIN, FOLLOW_GAIN_MAX = 0.15, 3.0
FOLLOW_FRACTION = 0.6          # Anteil der Abweichung, den ein Impuls ausgleichen soll
FOLLOW_PULSE_MIN_S = 0.25
FOLLOW_PULSE_MAX_S = 0.6
HEALTH_REHOME_S = 600.0


def merged_config(raw: Optional[dict]) -> dict[str, Any]:
    cfg = copy.deepcopy(DEFAULT_CONFIG)
    for k, v in (raw or {}).items():
        if k in ("patrol", "follow") and isinstance(v, dict):
            cfg[k].update(v)
        elif k in cfg:
            cfg[k] = v
    return cfg


def public_config(raw: Optional[dict]) -> dict[str, Any]:
    """Konfiguration für REST-Antworten: Passwort maskiert."""
    cfg = merged_config(raw)
    if cfg.get("onvif_password"):
        cfg["onvif_password"] = "***"
    return cfg


def _iso(ts: Optional[float]) -> Optional[str]:
    return datetime.fromtimestamp(ts, timezone.utc).isoformat() if ts else None


def credentials(url: str, cfg: dict[str, Any]) -> tuple[str, Optional[int], str, str]:
    """(host, port, user, password) – Benutzer/Passwort aus der PTZ-Konfiguration oder der Kamera-URL."""
    u = urlsplit(url or "")
    host = u.hostname or ""
    user = cfg.get("onvif_user") or unquote(u.username or "")
    pw = cfg.get("onvif_password") if cfg.get("onvif_user") or cfg.get("onvif_password") else None
    if pw is None:
        pw = unquote(u.password or "")
    port = cfg.get("onvif_port")
    return host, int(port) if port else None, user, pw or ""


class PtzUnavailable(Exception):
    """Kamera (noch) nicht verbunden – Meldung deutsch."""


class Controller:
    def __init__(self, cam: dict[str, Any], publish):
        self.id = int(cam["id"])
        self.cam = cam
        self.cfg = merged_config(cam.get("ptz"))
        self.publish = publish
        self.lock = threading.RLock()
        self.ex = ThreadPoolExecutor(max_workers=1, thread_name_prefix=f"ptz-{self.id}")
        self.client: Optional[OnvifPtz] = None
        self.connected = False
        self.error: Optional[str] = None
        self.next_connect = 0.0
        self.started_home = False
        self._presets: tuple[float, list[dict[str, str]]] = (0.0, [])
        # Zustand
        self.mode = "idle"
        self.view = "home" if not self.cfg.get("home_preset") else "unknown"
        self.moving = False
        self.preset: Optional[str] = None
        self.until: Optional[float] = None
        self.hold_on_arrival: Optional[float] = None   # Haltezeit zählt ab Ankunft (manual/rule mit Position)
        self.paused_until: Optional[float] = None
        self.gen = 0                       # jede Bewegung erhöht; veraltete Abschlüsse werden verworfen
        self.stop_at: Optional[float] = None
        # Patrouille
        self.patrol_step: Optional[int] = None
        self.step_until: Optional[float] = None
        self.next_patrol_at = 0.0
        # Verfolgung
        self.dets: Optional[dict[str, Any]] = None
        self.seen_since: dict[int, float] = {}
        self.target: Optional[dict[str, Any]] = None
        self.follow_started = 0.0
        self.target_seen = 0.0
        self.follow_cooldown_until = 0.0
        self.pulse_end = 0.0
        # Lernwerte der Verfolgung (je Achse): Schrittweite, Richtung, Fehlrichtungs-Zähler, letzter Impuls
        self.gain = {"pan": FOLLOW_GAIN, "tilt": FOLLOW_GAIN}
        self.axis_sign = {"pan": 1, "tilt": 1}
        self.wrong_dir = {"pan": 0, "tilt": 0}
        self.last_pulse: Optional[dict[str, Any]] = None
        self.health_rehome_at = 0.0
        self._last_pub: Optional[tuple] = None
        self._stop = threading.Event()
        self.thread = threading.Thread(target=self._run, name=f"ptz-loop-{self.id}", daemon=True)
        self.thread.start()

    # ------------------------------------------------------------------ Lebenszyklus
    def close(self) -> None:
        self._stop.set()
        self.ex.shutdown(wait=False, cancel_futures=True)
        if self.client:
            self.client.close()

    def update(self, cam: dict[str, Any]) -> None:
        new = merged_config(cam.get("ptz"))
        conn_keys = ("onvif_port", "onvif_user", "onvif_password", "profile_token")
        with self.lock:
            reconnect = cam.get("url") != self.cam.get("url") or any(new.get(k) != self.cfg.get(k) for k in conn_keys)
            home_changed = new.get("home_preset") != self.cfg.get("home_preset")
            self.cam, self.cfg = cam, new
            if reconnect:
                self.connected = False
                self.next_connect = 0.0
                self.started_home = False
            if home_changed:
                self.view = "home" if self.preset and self.preset == new.get("home_preset") and not self.moving \
                    else ("unknown" if new.get("home_preset") else self.view)
            if not new["patrol"].get("enabled") and self.mode == "patrol":
                self._go_home_locked()
            if not new["follow"].get("enabled") and self.mode == "follow":
                self._go_home_locked()
        self._publish_state()

    # ------------------------------------------------------------------ ONVIF-Ausführung
    def _connect(self) -> None:
        host, port, user, pw = credentials(self.cam.get("url") or "", self.cfg)
        if not host:
            raise OnvifError("Kamera-URL enthält keinen Host – ONVIF nicht möglich")
        c = OnvifPtz(host, port, user, pw, self.cfg.get("profile_token"))
        try:
            c.connect()
        except Exception:
            c.close()
            raise
        old, self.client = self.client, c
        if old:
            old.close()

    def _submit(self, fn, *a) -> Future:
        return self.ex.submit(fn, *a)

    def _require(self) -> OnvifPtz:
        if not self.connected or self.client is None:
            raise PtzUnavailable(self.error or "PTZ-Kamera noch nicht verbunden")
        return self.client

    def _wait(self, fut: Future, timeout: float = 8.0):
        try:
            return fut.result(timeout=timeout)
        except OnvifError as e:
            with self.lock:
                self.error = str(e)
            self._publish_state()
            raise
        except TimeoutError as e:
            raise OnvifError("Kamera antwortet nicht (Zeitüberschreitung)") from e

    # ------------------------------------------------------------------ öffentliche Befehle
    def presets(self, max_age: float = 30.0) -> list[dict[str, str]]:
        ts, data = self._presets
        if self.connected and time.time() - ts > max_age:
            try:
                data = self._wait(self._submit(lambda: self._require().presets()))
                self._presets = (time.time(), data)
            except (OnvifError, PtzUnavailable):
                pass
        return list(data)

    def preset_name(self, token: Optional[str]) -> Optional[str]:
        if token is None:
            return None
        return next((p["name"] for p in self._presets[1] if p["token"] == token), None)

    def move(self, pan: float, tilt: float, zoom: float, duration_ms: int) -> None:
        self._require()
        cfg = self.cfg
        sp = float(cfg.get("speed") or 0.5)
        p = max(-1.0, min(1.0, pan)) * sp * (-1 if cfg.get("invert_pan") else 1)
        t = max(-1.0, min(1.0, tilt)) * sp * (-1 if cfg.get("invert_tilt") else 1)
        z = max(-1.0, min(1.0, zoom)) * sp
        dur = max(0.05, min(3.0, duration_ms / 1000.0))
        with self.lock:
            self._enter_manual_locked()
            self.gen += 1
            self.moving = True
            self.view = "away"
            self.preset = None
            self.stop_at = time.time() + dur
        self._publish_state()
        self._wait(self._submit(lambda: self._require().continuous_move(p, t, z, timeout_s=dur + 0.2)))

    def stop(self) -> None:
        self._require()
        with self.lock:
            self.stop_at = None
            self.gen += 1
            gen = self.gen
        self._wait(self._submit(lambda: self._require().stop()))
        self._settle_later(gen, preset=None)

    def goto(self, preset: Optional[str], hold_s: Optional[float] = None, mode: str = "manual") -> None:
        self._require()
        token = preset if preset is not None else self.cfg.get("home_preset")
        if not token:
            raise ValueError("Keine Grundposition festgelegt")
        if self._presets[1] and token not in {p["token"] for p in self.presets(max_age=300)}:
            raise ValueError(f"Position {token} gibt es auf der Kamera nicht")
        with self.lock:
            if mode == "manual" and token == self.cfg.get("home_preset") and not hold_s:
                # „Grundposition“ = Automatik fortsetzen (Patrouille/Verfolgung dürfen wieder)
                self.target = None
                self._go_home_locked()
                if not self.moving:
                    self._goto_locked(token)
                self._publish_state()
                return
            if mode == "manual":
                self._enter_manual_locked(hold_s)
                span = hold_s or float(self.cfg.get("return_s") or 0)
                self.hold_on_arrival = span if span > 0 and token != self.cfg.get("home_preset") else None
            self._goto_locked(token)
        self._publish_state()

    def save_preset(self, name: str) -> dict[str, str]:
        self._require()
        token = self._wait(self._submit(lambda: self._require().set_preset(name)))
        self._presets = (0.0, self._presets[1])
        self.presets(max_age=0)
        return {"token": token, "name": name}

    def remove_preset(self, token: str) -> None:
        self._require()
        self._wait(self._submit(lambda: self._require().remove_preset(token)))
        self._presets = (0.0, [p for p in self._presets[1] if p["token"] != token])

    def resume(self) -> None:
        self._require()
        with self.lock:
            self.target = None
            self.paused_until = None
            self.next_patrol_at = 0.0
            self._go_home_locked()
        self._publish_state()

    def pause(self, minutes: int) -> None:
        with self.lock:
            self.paused_until = time.time() + minutes * 60 if minutes > 0 else None
            if self.paused_until and self.mode in ("patrol", "follow"):
                self.target = None
                self._go_home_locked()
        self._publish_state()

    def rule_goto(self, preset: Optional[str], hold_s: float) -> bool:
        """Regel-Aktion `ptz`. False = ignoriert (Handsteuerung aktiv / nicht verbunden)."""
        if not self.connected:
            log.info("PTZ Kamera %s: Regel-Aktion ignoriert – nicht verbunden", self.id)
            return False
        token = preset or self.cfg.get("home_preset")
        if not token:
            return False
        with self.lock:
            if self.mode == "manual":
                return False
            self.mode = "rule"
            self.target = None
            self.patrol_step = None
            self.until = time.time() + max(5.0, float(hold_s)) + GOTO_TIMEOUT_S   # vorläufig, gilt ab Ankunft
            self.hold_on_arrival = max(5.0, float(hold_s))
            self._goto_locked(token)
        self._publish_state()
        return True

    def on_detections(self, msg: dict[str, Any]) -> None:
        self.dets = msg

    def on_health(self, msg: dict[str, Any]) -> None:
        """Gesundheitsprüfung meldet „verschoben“, obwohl wir in der Grundposition stehen → neu anfahren."""
        if not any(p.get("type") == "moved" for p in msg.get("problems") or []):
            return
        now = time.time()
        with self.lock:
            if self.view != "home" or self.mode != "idle" or now < self.health_rehome_at or not self.connected:
                return
            self.health_rehome_at = now + HEALTH_REHOME_S
            log.info("PTZ Kamera %s: Bild verschoben gemeldet – fahre Grundposition erneut an", self.id)
            self._go_home_locked()
        self._publish_state()

    # ------------------------------------------------------------------ interne Bewegungen (Lock gehalten)
    def _enter_manual_locked(self, hold_s: Optional[float] = None) -> None:
        self.hold_on_arrival = None
        self.mode = "manual"
        self.target = None
        self.patrol_step = None
        ret = float(self.cfg.get("return_s") or 0)
        span = hold_s if hold_s else ret
        self.until = time.time() + span if span > 0 else None

    def _goto_locked(self, token: str) -> None:
        self.gen += 1
        gen = self.gen
        self.moving = True
        self.view = "away"
        self.stop_at = None

        def run():
            c = self._require()
            c.http.timeout = GOTO_TIMEOUT_S
            try:
                c.goto_preset(token)
            finally:
                c.http.timeout = c.timeout
            # Geräte mit verlässlichem MoveStatus: warten, bis die Fahrt endet
            t0 = time.time()
            while c.caps.get("status") and time.time() - t0 < STATUS_MAX_S:
                if gen != self.gen:
                    return
                try:
                    if not c.status():
                        break
                except OnvifError:
                    break
                time.sleep(0.4)

        fut = self._submit(run)
        fut.add_done_callback(lambda f: self._after_goto(f, gen, token))

    def _after_goto(self, fut: Future, gen: int, token: str) -> None:
        err = fut.exception() if not fut.cancelled() else None
        if err is not None:
            log.warning("PTZ Kamera %s: Fahrt zu %s fehlgeschlagen: %s", self.id, token, err)
            with self.lock:
                self.error = str(err)
                if gen == self.gen:
                    self.moving = False
            self._publish_state()
            return
        with self.lock:
            self.error = None
        self._settle_later(gen, preset=token)

    def _settle_later(self, gen: int, preset: Optional[str]) -> None:
        def done():
            if self._stop.is_set():
                return
            with self.lock:
                if gen != self.gen:
                    return
                self.moving = False
                self.preset = preset
                home = self.cfg.get("home_preset")
                self.view = "home" if preset is not None and preset == home else "away"
                if self.hold_on_arrival and self.mode in ("manual", "rule"):
                    self.until = time.time() + self.hold_on_arrival
                self.hold_on_arrival = None
            self._publish_state()
        t = threading.Timer(SETTLE_S, done)
        t.daemon = True
        t.start()

    def _go_home_locked(self) -> None:
        self.mode = "idle"
        self.until = None
        self.hold_on_arrival = None
        self.target = None
        self.patrol_step = None
        self.step_until = None
        home = self.cfg.get("home_preset")
        if home and self.connected and not (self.view == "home" and not self.moving):
            self._goto_locked(home)

    # ------------------------------------------------------------------ Takt
    def _run(self) -> None:
        while not self._stop.wait(0.1):
            try:
                self._tick()
            except Exception:  # noqa: BLE001
                log.exception("PTZ Kamera %s: Takt fehlgeschlagen", self.id)
                time.sleep(1.0)

    def _tick(self) -> None:
        now = time.time()
        if not self.connected:
            if now < self.next_connect:
                return
            self.next_connect = now + CONNECT_RETRY_S
            try:
                self._wait(self._submit(self._connect), timeout=60)
                with self.lock:
                    self.connected = True
                    self.error = None
                    if self.mode == "error":
                        self.mode = "idle"
                log.info("PTZ Kamera %s verbunden (%s, Port %s, Profil %s)", self.id,
                         " ".join(x for x in ((self.client.device or {}).get("manufacturer"),
                                              (self.client.device or {}).get("model")) if x),
                         self.client.port, self.client.profile)
                self.presets(max_age=0)
            except Exception as e:  # noqa: BLE001
                with self.lock:
                    self.error = f"{e}"
                    self.mode = "error"
                log.info("PTZ Kamera %s nicht verbunden: %s", self.id, e)
                self._publish_state()
                return
            with self.lock:
                if not self.started_home and self.cfg.get("home_on_start") and self.cfg.get("home_preset"):
                    self._goto_locked(self.cfg["home_preset"])
                self.started_home = True
            self._publish_state()
            return

        # Handbewegung: Stopp zur geplanten Zeit (ContinuousMove hat zusätzlich einen Timeout auf der Kamera)
        stop_at = self.stop_at
        if stop_at and now >= stop_at:
            with self.lock:
                if self.stop_at == stop_at:
                    self.stop_at = None
                    self.gen += 1
                    gen = self.gen
                else:
                    gen = None
            if gen is not None:
                fut = self._submit(lambda: self._require().stop())
                fut.add_done_callback(lambda f: self._settle_later(gen, preset=None))

        with self.lock:
            if self.mode in ("manual", "rule") and self.until and now >= self.until and not self.moving:
                log.info("PTZ Kamera %s: %s beendet – zurück zur Grundposition", self.id,
                         "Handsteuerung" if self.mode == "manual" else "Regel-Position")
                self._go_home_locked()
                if self.mode == "idle":
                    self.next_patrol_at = now + float(self.cfg["patrol"].get("pause_s") or 0)
        self._follow_tick(now)
        self._patrol_tick(now)
        self._publish_state()

    def _automation_allowed(self, now: float, part: dict) -> bool:
        from .rules import schedule_active
        if self.paused_until and now < self.paused_until:
            return False
        if self.paused_until and now >= self.paused_until:
            self.paused_until = None
        return bool(part.get("enabled")) and schedule_active(part.get("schedule"))

    # ------------------------------------------------------------------ Patrouille
    def _patrol_tick(self, now: float) -> None:
        pc = self.cfg["patrol"]
        steps = [s for s in pc.get("steps") or [] if s.get("preset")]
        with self.lock:
            if self.mode == "patrol" and (not steps or not self._automation_allowed(now, pc)):
                self._go_home_locked()
                return
            if self.mode == "idle" and steps and not self.moving and now >= self.next_patrol_at \
                    and self._automation_allowed(now, pc):
                self.mode = "patrol"
                self.patrol_step = 0
                self.step_until = None
                log.info("PTZ Kamera %s: Patrouille startet (%d Schritte)", self.id, len(steps))
                self._goto_locked(str(steps[0]["preset"]))
                return
            if self.mode != "patrol" or self.moving or self.patrol_step is None:
                return
            if self.step_until is None:
                dwell = float(steps[min(self.patrol_step, len(steps) - 1)].get("dwell_s") or 20)
                self.step_until = now + dwell
                self.until = self.step_until
                return
            if now < self.step_until:
                return
            nxt = self.patrol_step + 1
            if nxt >= len(steps):
                log.info("PTZ Kamera %s: Patrouille beendet", self.id)
                self._go_home_locked()
                self.next_patrol_at = now + float(pc.get("pause_s") or 0) + SETTLE_S + 5
                return
            self.patrol_step = nxt
            self.step_until = None
            self.until = None
            self._goto_locked(str(steps[nxt]["preset"]))

    # ------------------------------------------------------------------ Verfolgung
    def _follow_tick(self, now: float) -> None:
        fc = self.cfg["follow"]
        msg = self.dets
        labels = set(fc.get("labels") or ["person"])
        objs = [o for o in (msg or {}).get("objects") or [] if o.get("label") in labels and o.get("bbox")]
        fresh = msg is not None and now - float(msg.get("ts") or 0) < 3.0
        with self.lock:
            if self.mode == "follow":
                if not self._automation_allowed(now, fc):
                    self._go_home_locked()
                    return
                if now - self.follow_started > float(fc.get("max_s") or 300):
                    log.info("PTZ Kamera %s: Verfolgung nach max_s beendet", self.id)
                    self.follow_cooldown_until = now + FOLLOW_COOLDOWN_S
                    self._go_home_locked()
                    return
            elif self.mode not in ("idle", "patrol") or now < self.follow_cooldown_until \
                    or not self._automation_allowed(now, fc):
                self.seen_since.clear()
                return
        if not fresh:
            objs = []
        if self.mode != "follow":
            # Start: dasselbe bestätigte Objekt ≥ FOLLOW_ENGAGE_S im Bild
            ids = {int(o["track_id"]) for o in objs if o.get("track_id") is not None}
            self.seen_since = {k: v for k, v in self.seen_since.items() if k in ids}
            for o in objs:
                tid = int(o.get("track_id") or 0)
                self.seen_since.setdefault(tid, now)
            ready = [o for o in objs if now - self.seen_since.get(int(o.get("track_id") or 0), now) >= FOLLOW_ENGAGE_S]
            if not ready or self.moving:
                return
            o = max(ready, key=lambda x: (x["bbox"][2] - x["bbox"][0]) * (x["bbox"][3] - x["bbox"][1]))
            with self.lock:
                self.mode = "follow"
                self.patrol_step = None
                self.step_until = None
                self.until = None
                self.target = {"track_id": int(o.get("track_id") or 0), "label": o.get("label")}
                self.follow_started = now
                self.target_seen = now
            log.info("PTZ Kamera %s: verfolge %s (Track %s)", self.id, o.get("label"), o.get("track_id"))
        tgt = self.target or {}
        o = next((x for x in objs if int(x.get("track_id") or -1) == tgt.get("track_id")), None)
        if o is None and objs:
            o = max(objs, key=lambda x: (x["bbox"][2] - x["bbox"][0]) * (x["bbox"][3] - x["bbox"][1]))
            with self.lock:
                self.target = {"track_id": int(o.get("track_id") or 0), "label": o.get("label")}
        if o is None:
            if now - self.target_seen > float(fc.get("lost_s") or 8):
                log.info("PTZ Kamera %s: Ziel verloren – zurück zur Grundposition (gelernte Schrittweite "
                         "Schwenken %.2f, Neigen %.2f Bild/s)", self.id, self.gain["pan"], self.gain["tilt"])
                with self.lock:
                    self._go_home_locked()
                    self.next_patrol_at = now + float(self.cfg["patrol"].get("pause_s") or 0)
            return
        self.target_seen = now
        # nur Bilder auswerten, die nach dem Stillstand der letzten Bewegung aufgenommen wurden
        latency = float(fc.get("latency_s") or FOLLOW_LATENCY_S)
        if self.moving or float(msg.get("ts") or 0) < self.pulse_end + latency:
            return
        x1, y1, x2, y2 = o["bbox"]
        cx = (x1 + x2) / 2
        cy = y1 + 0.35 * (y2 - y1) if o.get("label") == "person" else (y1 + y2) / 2
        err = {"pan": cx - 0.5, "tilt": cy - 0.5}
        self._learn(err, int(o.get("track_id") or 0))
        use_tilt = fc.get("tilt", True) is not False
        cand = [ax for ax, db in (("pan", FOLLOW_DEADBAND_X), ("tilt", FOLLOW_DEADBAND_Y))
                if abs(err[ax]) >= db and (ax == "pan" or use_tilt)]
        if not cand:
            self.last_pulse = None
            return
        # eine Achse je Impuls (die Kamera fährt beide Achsen gleich schnell) – die mit der größeren Abweichung
        ax = max(cand, key=lambda k: abs(err[k]) / (FOLLOW_DEADBAND_X if k == "pan" else FOLLOW_DEADBAND_Y))
        e = err[ax]
        dur = max(FOLLOW_PULSE_MIN_S, min(FOLLOW_PULSE_MAX_S, FOLLOW_FRACTION * abs(e) / self.gain[ax]))
        sp = float(fc.get("speed") or 0.5)
        # Ziel rechts (e > 0) → nach rechts schwenken (pan +); Ziel unten (e > 0) → nach unten neigen (tilt −)
        v = sp if e > 0 else -sp
        if ax == "tilt":
            v = -v
        if self.cfg.get("invert_pan" if ax == "pan" else "invert_tilt"):
            v = -v
        v *= self.axis_sign[ax]
        pan, tilt = (v, 0.0) if ax == "pan" else (0.0, v)
        self.last_pulse = {"axis": ax, "err": e, "dur": dur, "track_id": int(o.get("track_id") or 0)}
        with self.lock:
            self.gen += 1
            gen = self.gen
            self.moving = True
            self.view = "away"
            self.preset = None

        def run():
            c = self._require()
            c.continuous_move(pan, tilt, 0.0, timeout_s=dur)
            time.sleep(dur)               # ab Rückkehr des Befehls (die Kamera braucht ≈ 0,1 s zum Anlaufen)
            c.stop()

        def done(f: Future):
            if f.exception() is not None:
                log.info("PTZ Kamera %s: Verfolgungs-Impuls fehlgeschlagen: %s", self.id, f.exception())
                self.last_pulse = None
            with self.lock:
                self.pulse_end = time.time()
                if gen == self.gen:
                    self.moving = False
            self._publish_state()

        self._submit(run).add_done_callback(done)

    def _learn(self, err: dict[str, float], track_id: int) -> None:
        """Nach einem Impuls: tatsächliche Verschiebung des Ziels mit der erwarteten vergleichen und Schrittweite
        bzw. Richtung der Achse nachführen. Bewegt sich das Ziel selbst, mitteln sich Fehler über mehrere Impulse."""
        lp, self.last_pulse = self.last_pulse, None
        if not lp or lp["track_id"] != track_id:
            return
        ax = lp["axis"]
        before, after = lp["err"], err[ax]
        expected = (self.gain[ax] * lp["dur"]) * (1 if before > 0 else -1)   # Abweichung soll um so viel sinken
        observed = before - after
        if abs(expected) < 0.05:
            return
        ratio = observed / expected
        if ratio < -0.5 and abs(observed) > 0.06:
            self.wrong_dir[ax] += 1
            if self.wrong_dir[ax] >= 2:
                self.axis_sign[ax] *= -1
                self.wrong_dir[ax] = 0
                log.warning("PTZ Kamera %s: %s fährt in die falsche Richtung – Richtung für die Verfolgung umgekehrt "
                            "(Einstellung „%s vertauscht“ setzen, damit es auch die Handsteuerung betrifft)", self.id,
                            "Schwenken" if ax == "pan" else "Neigen", "Links/rechts" if ax == "pan" else "Oben/unten")
            return
        self.wrong_dir[ax] = 0
        if 0.2 <= ratio <= 4.0:
            g = max(FOLLOW_GAIN_MIN, min(FOLLOW_GAIN_MAX, self.gain[ax] * (0.6 + 0.4 * ratio)))
            if abs(g - self.gain[ax]) > 0.01:
                log.debug("PTZ Kamera %s: Schrittweite %s %.2f → %.2f (Verhältnis %.2f)", self.id, ax, self.gain[ax], g, ratio)
            self.gain[ax] = g

    # ------------------------------------------------------------------ Zustand
    def state(self) -> dict[str, Any]:
        with self.lock:
            return {"mode": self.mode, "view": self.view, "moving": self.moving, "preset": self.preset,
                    "preset_name": self.preset_name(self.preset),
                    "until": _iso(self.until), "patrol_step": self.patrol_step,
                    "target": dict(self.target) if self.target else None,
                    "paused_until": _iso(self.paused_until), "error": self.error,
                    "updated_at": datetime.now(timezone.utc).isoformat()}

    def _publish_state(self) -> None:
        if self._stop.is_set():
            return              # beendet (PTZ aus/Kamera gelöscht) – laufende Fahrten melden nichts mehr
        st = self.state()
        key = tuple((k, str(v)) for k, v in st.items() if k != "updated_at")
        if key == self._last_pub:
            return
        self._last_pub = key
        self.publish(self.id, self.cam, st)


class PtzManager:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.controllers: dict[int, Controller] = {}
        self.known: set[int] = set()

    # Ausgabe (WS, intern, extern)
    @staticmethod
    def _publish(cam_id: int, cam: dict[str, Any], st: dict[str, Any]) -> None:
        from .mqtt_bus import bus
        from .util import camera_slug
        from .ws import hub
        hub.broadcast({"type": "ptz", "camera_id": cam_id, "state": st})
        bus.publish(f"vg/internal/ptz/{cam_id}", {"camera_id": cam_id, "view": st["view"], "moving": st["moving"],
                                                  "mode": st["mode"], "ts": round(time.time(), 3)},
                    retain=True, qos=1)
        bus.publish(f"{bus.prefix}/{camera_slug(cam.get('name') or str(cam_id))}/ptz",
                    {"mode": st["mode"], "view": st["view"], "preset_name": st["preset_name"]}, retain=True, qos=1)

    def reload(self) -> None:
        """Mit der DB abgleichen (nach Kamera-Änderungen und beim Start)."""
        with SessionLocal() as s:
            rows = s.scalars(select(Camera)).all()
            cams = {c.id: {"id": c.id, "name": c.name, "url": c.url, "ptz": dict(c.ptz or {})}
                    for c in rows if c.enabled and c.ptz_enabled}
            all_ids = {c.id for c in rows}
        from .mqtt_bus import bus
        with self.lock:
            for cid in list(self.controllers):
                if cid not in cams:
                    log.info("PTZ Kamera %s aus", cid)
                    ctl = self.controllers.pop(cid)
                    ctl.close()
                    bus.publish(f"vg/internal/ptz/{cid}", b"", retain=True, qos=1)
                    from .util import camera_slug
                    bus.publish(f"{bus.prefix}/{camera_slug(ctl.cam.get('name') or str(cid))}/ptz", b"", retain=True, qos=1)
            for cid, cam in cams.items():
                cur = self.controllers.get(cid)
                if cur is None:
                    log.info("PTZ Kamera %s an", cid)
                    self.controllers[cid] = Controller(cam, self._publish)
                else:
                    cur.update(cam)
            # Kameras, die PTZ nie hatten oder gelöscht wurden: gespeicherten Zustand räumen
            for cid in self.known - set(cams):
                bus.publish(f"vg/internal/ptz/{cid}", b"", retain=True, qos=1)
            self.known = set(cams) & all_ids

    def get(self, cam_id: int) -> Optional[Controller]:
        return self.controllers.get(cam_id)

    def state(self, cam_id: int) -> Optional[dict[str, Any]]:
        c = self.controllers.get(cam_id)
        return c.state() if c else None

    def on_detections(self, cam_id: int, msg: dict[str, Any]) -> None:
        c = self.controllers.get(cam_id)
        if c is not None:
            c.on_detections(msg)

    def on_health(self, cam_id: int, msg: dict[str, Any]) -> None:
        c = self.controllers.get(cam_id)
        if c is not None:
            c.on_health(msg)

    def stop(self) -> None:
        with self.lock:
            for c in self.controllers.values():
                c.close()
            self.controllers.clear()


manager = PtzManager()


def probe(url: str, cfg: dict[str, Any]) -> dict[str, Any]:
    """Einrichtung: Verbindung testen, Fähigkeiten und Presets lesen (ohne bestehende Steuerung)."""
    host, port, user, pw = credentials(url, cfg)
    if not host:
        return {"ok": False, "port": None, "device": None, "capabilities": None, "presets": [],
                "error": "Kamera-URL enthält keinen Host"}
    c = OnvifPtz(host, port, user, pw, cfg.get("profile_token"))
    try:
        c.connect()
        presets = c.presets()
        return {"ok": True, "port": c.port, "device": c.device, "capabilities": c.caps, "presets": presets,
                "profile_token": c.profile, "error": None}
    except OnvifError as e:
        return {"ok": False, "port": c.port, "device": c.device, "capabilities": None, "presets": [],
                "error": str(e)}
    finally:
        c.close()
