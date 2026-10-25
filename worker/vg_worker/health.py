"""Kamera-Gesundheit (C5): verdeckt, unscharf, zu dunkel, eingefroren, verschoben – nur CPU, kein VRAM.

Eigener Thread, alle `VG_HEALTH_INTERVAL_S` (10 s) je Kamera ein Prüfbild aus dem Ingest (`FrameSource.latest()`,
kein zusätzlicher Stream). Kennzahlen aus `frame_metrics.py`. Ergebnis per MQTT
`vg/internal/health/{camera_id}` (QoS 1, retain) alle 30 s und bei Änderung (CONTRACT.md W1.F):

    {camera_id, ts, problems:[{type, since, value}], metrics:{…}, reference:{day, night}}

Entprellung im Worker: Ein Befund wird erst gemeldet, wenn er in zwei Prüfungen nacheinander auftritt, und
verschwindet nach zwei unauffälligen Prüfungen (`since` = erste auffällige Prüfung). Die eigentliche Hysterese
(Warnung erst nach `health_alert_after_s`, Entwarnung nach 60 s) macht das Backend.

Referenzbilder: je Kamera eins für Tag (Farbe) und eins für Nacht (IR, Sättigung ≈ 0) unter
`health/cam<N>_<day|night>.jpg` (640 px). Automatisch beim ersten brauchbaren Bild des Modus, manuell per
Kommando `health_reference`. Nach einem Worker-Neustart werden vorhandene Referenzen aus dem Medienordner geladen.
"""
from __future__ import annotations

import logging
import os
import threading
import time
from typing import Any, Callable, Optional

import cv2
import numpy as np

from . import frame_metrics as fm
from . import settings
from .util import abs_media_path, write_jpeg

log = logging.getLogger("vg.health")

MODES = ("day", "night")
REPORT_AFTER_CHECKS = 2      # so viele auffällige Prüfungen in Folge, bevor ein Befund gemeldet wird
CLEAR_AFTER_CHECKS = 2       # so viele unauffällige Prüfungen in Folge beenden einen Befund
BASE_MIN_SAMPLES = 3         # so viele unauffällige Vergleiche, bevor „Antwort eingebrochen“ zählt
REF_WIDTH = 640

Publish = Callable[[str, dict, int, bool], None]


def ref_path(camera_id: int, mode: str) -> str:
    return f"health/cam{camera_id}_{mode}.jpg"


def _r(v: Optional[float], n: int = 3) -> Optional[float]:
    return None if v is None else round(float(v), n)


class CameraHealth:
    """Zustand einer Kamera (Referenzen, gleitende Basis, Befund-Serien)."""

    def __init__(self, camera_id: int):
        self.id = camera_id
        self.refs: dict[str, Optional[dict]] = {m: None for m in MODES}
        self.ref_paths: dict[str, Optional[str]] = {m: None for m in MODES}
        self._ref_loaded: set[str] = set()
        self.base: dict[str, Optional[float]] = {m: None for m in MODES}
        self.base_n: dict[str, int] = {m: 0 for m in MODES}
        self.prev: Optional[dict] = None
        self.prev_seq = -1
        self.streaks: dict[str, dict[str, Any]] = {}
        self.metrics: dict[str, Any] = {}
        self.last_pub = 0.0
        self.last_reported: tuple = ()
        self.last_check = 0.0
        self.mode: Optional[str] = None          # Tag/Nacht aus dem letzten brauchbaren Bild

    # -------------------------------------------------------------- Referenzen
    def load_ref(self, mode: str) -> None:
        if mode in self._ref_loaded:
            return
        self._ref_loaded.add(mode)
        rel = ref_path(self.id, mode)
        try:
            full = abs_media_path(rel)
            if not os.path.exists(full):
                return
            img = cv2.imread(full, cv2.IMREAD_COLOR)
            if img is None:
                return
            self.refs[mode] = fm.metrics(img)
            self.ref_paths[mode] = rel
            log.info("camera %s: %s reference loaded", self.id, mode)
        except Exception as e:  # noqa: BLE001
            log.warning("camera %s: reference %s not loadable: %s", self.id, mode, e)

    def set_ref(self, mode: str, image: np.ndarray, m: dict) -> str:
        h, w = image.shape[:2]
        small = image if w <= REF_WIDTH else cv2.resize(image, (REF_WIDTH, max(1, int(round(h * REF_WIDTH / w)))),
                                                        interpolation=cv2.INTER_AREA)
        rel = write_jpeg(ref_path(self.id, mode), small, 90)
        self.refs[mode] = m
        self.ref_paths[mode] = rel
        self._ref_loaded.add(mode)
        self.base[mode] = None
        self.base_n[mode] = 0
        # Befunde, die sich auf die alte Referenz bezogen, neu bewerten
        for t in ("covered", "blur", "moved"):
            self.streaks.pop(t, None)
        return rel

    # -------------------------------------------------------------- Prüfung
    def check(self, image: np.ndarray, ts: float) -> None:
        m = fm.metrics(image)
        # Tag/Nacht nur aus brauchbaren Bildern bestimmen: ein abgedecktes oder dunkles Bild ist farblos und
        # sähe sonst wie ein IR-Bild aus (für das es evtl. noch keine Referenz gibt)
        if fm.usable_as_reference(m) or self.mode is None:
            self.mode = fm.mode_of(m)
        mode = self.mode
        self.load_ref(mode)
        if self.refs[mode] is None and fm.usable_as_reference(m) \
                and not any(t in self.streaks for t in ("frozen", "covered", "dark")):
            try:
                self.set_ref(mode, image, m)
                log.info("camera %s: automatic %s reference set", self.id, mode)
            except Exception as e:  # noqa: BLE001
                log.warning("camera %s: reference not saved: %s", self.id, e)
        ref = self.refs[mode]
        base = self.base[mode] if self.base_n[mode] >= BASE_MIN_SAMPLES else None
        found, c = fm.classify(ref, m, self.prev, base)
        # gleitende Basis der Korrelationsantwort nur aus unauffälligen Bildern
        if "shift_resp" in c and not found:
            b = self.base[mode]
            self.base[mode] = c["shift_resp"] if b is None else 0.9 * b + 0.1 * c["shift_resp"]
            self.base_n[mode] += 1
        self.prev = m
        for t, v in found.items():
            s = self.streaks.setdefault(t, {"since": ts, "bad": 0, "good": 0, "value": v})
            s["bad"] += 1
            s["good"] = 0
            s["value"] = v
        for t in list(self.streaks):
            if t not in found:
                s = self.streaks[t]
                s["good"] += 1
                if s["good"] >= CLEAR_AFTER_CHECKS or s["bad"] < REPORT_AFTER_CHECKS:
                    del self.streaks[t]
        ref_sharp = ref["sharpness"] if ref else None
        base_now = self.base[mode]
        self.metrics = {
            "brightness": _r(m["brightness"], 1), "sharpness": _r(m["sharpness"]), "sharpness_ref": _r(ref_sharp),
            "diff": _r(c.get("diff")), "inliers": _r(c["shift_resp"] / base_now) if "shift_resp" in c and base_now else None,
            "shift": _r(c.get("shift"), 4), "rotation": None,
            # Zusatzwerte (additiv): Rohgrößen der Messung
            "shift_x": _r(c.get("shift_x"), 4), "shift_y": _r(c.get("shift_y"), 4),
            "shift_resp": _r(c.get("shift_resp")), "base_resp": _r(base_now), "ncc": _r(c.get("ncc")),
            "contrast": _r(m["contrast"], 1), "p95": _r(m["p95"], 1), "saturation": _r(m["saturation"], 1),
            "flat_share": _r(m["flat_share"]), "mode": mode,
        }
        self.last_check = ts

    def problems(self) -> list[dict[str, Any]]:
        out = []
        for t, s in sorted(self.streaks.items(), key=lambda kv: kv[1]["since"]):
            if s["bad"] >= REPORT_AFTER_CHECKS:
                p = {"type": t, "since": round(s["since"], 3), "value": s["value"]}
                if t == "moved":
                    p["shift_x"] = self.metrics.get("shift_x")
                    p["shift_y"] = self.metrics.get("shift_y")
                out.append(p)
        return out

    def message(self, enabled: bool = True) -> dict[str, Any]:
        return {"camera_id": self.id, "ts": round(time.time(), 3), "enabled": enabled,
                "checked_ts": round(self.last_check, 3) if self.last_check else None,
                "problems": self.problems() if enabled else [], "metrics": self.metrics,
                "reference": {m: self.ref_paths[m] for m in MODES}}


class HealthMonitor:
    """Prüft alle aktiven Kameras mit `health_enabled` (Konfiguration aus `/api/internal/cameras`)."""

    def __init__(self, publish: Publish, runtimes: Callable[[], dict[int, Any]]):
        self.publish = publish
        self.runtimes = runtimes
        self.state: dict[int, CameraHealth] = {}
        self.lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self.last_ms = 0.0

    def start(self) -> None:
        if not settings.HEALTH_ENABLED:
            log.info("camera health checks disabled (VG_HEALTH=0)")
            return
        self._thread = threading.Thread(target=self._loop, name="health", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _pub(self, ch: CameraHealth, enabled: bool = True) -> None:
        msg = ch.message(enabled)
        ch.last_pub = time.time()
        ch.last_reported = tuple(p["type"] for p in msg["problems"])
        self.publish(f"vg/internal/health/{ch.id}", msg, 1, True)

    def _loop(self) -> None:
        while not self._stop.wait(max(1.0, settings.HEALTH_INTERVAL_S)):
            try:
                self.tick()
            except Exception:  # noqa: BLE001
                log.exception("health check failed")

    def tick(self) -> None:
        cams = self.runtimes()
        now = time.time()
        t0 = time.perf_counter()
        n = 0
        with self.lock:
            for cid in list(self.state):
                if cid not in cams:
                    self.state.pop(cid, None)     # Kamera entfernt/deaktiviert
            for cid, rt in cams.items():
                enabled = bool(rt.cfg.get("health_enabled", True))
                ch = self.state.get(cid)
                if not enabled:
                    if ch is not None and ch.last_reported != ("disabled",):
                        self._pub(ch, enabled=False)          # einmal „aus“ melden, dann ruhen
                        ch.last_reported = ("disabled",)
                        ch.streaks.clear()
                    elif ch is None:
                        ch = self.state[cid] = CameraHealth(cid)
                        self._pub(ch, enabled=False)
                        ch.last_reported = ("disabled",)
                    continue
                if ch is None:
                    ch = self.state[cid] = CameraHealth(cid)
                if ch.last_reported == ("disabled",):
                    ch.last_reported = ()
                    ch.last_pub = 0.0
                # Keine Prüfung, solange die Kamera nicht läuft (kurze Ausfälle sind kein Bildproblem)
                if rt.source.state != "online":
                    continue
                f = rt.source.latest()
                if f is None or f.seq == ch.prev_seq or now - f.ts > max(5.0, settings.HEALTH_INTERVAL_S):
                    continue
                ch.prev_seq = f.seq
                ch.check(f.image, f.ts)
                n += 1
                reported = tuple(p["type"] for p in ch.problems())
                if reported != ch.last_reported or now - ch.last_pub >= settings.HEALTH_PUBLISH_S:
                    self._pub(ch)
        if n:
            self.last_ms = (time.perf_counter() - t0) * 1000 / n

    # -------------------------------------------------------------- Kommando
    def set_reference(self, rt, mode: str) -> str:
        """Referenz aus dem aktuellen Bild setzen (`mode` day|night|auto). → relativer Pfad; ValueError bei Fehler."""
        if mode not in ("day", "night", "auto"):
            raise ValueError(f"unknown mode {mode!r}")
        f = rt.source.latest()
        if f is None or rt.source.state != "online":
            raise ValueError("no frame available (camera offline?)")
        m = fm.metrics(f.image)
        if not fm.usable_as_reference(m):
            raise ValueError("Bild ungeeignet als Referenz (zu dunkel, kontrastarm oder weitgehend einfarbig)")
        target = fm.mode_of(m) if mode == "auto" else mode
        with self.lock:
            ch = self.state.get(rt.id)
            if ch is None:
                ch = self.state[rt.id] = CameraHealth(rt.id)
            ch._ref_loaded.add(target)
            rel = ch.set_ref(target, f.image, m)
            log.info("camera %s: %s reference set manually", rt.id, target)
            if bool(rt.cfg.get("health_enabled", True)):
                self._pub(ch)
        return rel

    def perf(self) -> dict:
        return {"ms": round(self.last_ms, 2), "cameras": len(self.state)}
