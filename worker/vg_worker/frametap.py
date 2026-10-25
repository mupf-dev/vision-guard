"""Frame-Tap (W3, K3): analysierte Detect-Frames an weitere Abnehmer je Kamera verteilen.

Vertrag: CONTRACT.md „Nachträge: Welle 3“, W3.D. ``Pipeline._post`` ruft nach dem Tracking ``taps.offer(...)``
auf. Jeder Abnehmer hat einen eigenen Thread und je Kamera genau einen Platz: ein neuer Frame ersetzt einen noch
nicht abgeholten (verwerfen statt queuen) – ein langsamer Abnehmer bremst also weder die Detektion noch andere
Abnehmer.

    def on_frame(f: TapFrame):
        roi = f.image[y1:y2, x1:x2]        # nur lesen!
    pipeline.taps.register("sensors", on_frame, cameras=lambda cid: cid in engine.cams,
                           interval_s=lambda cid: engine.interval(cid))

Abnehmer in W3: ``sensors`` (A3, CPU-Messungen), ``history`` (B1, Bildquelle ohne Keyframe).
"""
from __future__ import annotations

import logging
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Callable, Optional, Union

import numpy as np

log = logging.getLogger("vg.frametap")


@dataclass
class TapFrame:
    camera_id: int
    ts: float                       # Worker-Wanduhr beim Lesen des Frames (wie detections.ts)
    image: np.ndarray               # BGR uint8, Detect-Auflösung – NUR LESEN (wird mit anderen geteilt)
    width: int
    height: int
    objects: list[dict] = field(default_factory=list)    # Obj wie in vg/internal/detections (normiert)
    persons: list[list[float]] = field(default_factory=list)   # rohe Personen-Boxen (Score ≥ 0,3), normiert
    runtime: Any = None             # CameraRuntime (z. B. für inventory.KeyframeGrabber)


Interval = Union[float, Callable[[int], float]]


class FrameTap:
    def __init__(self, name: str, callback: Callable[[TapFrame], None], cameras: Callable[[int], bool],
                 interval_s: Interval):
        self.name = name
        self.callback = callback
        self.cameras = cameras
        self.interval_s = interval_s
        self.slots: dict[int, TapFrame] = {}
        self.last_offer: dict[int, float] = {}
        self.lock = threading.Lock()
        self.wake = threading.Event()
        self.stop_ev = threading.Event()
        self.done: deque[tuple[float, float]] = deque(maxlen=2000)   # (ts, ms)
        self.dropped: deque[float] = deque(maxlen=2000)
        self._last_err = 0.0
        self.thread = threading.Thread(target=self._run, name=f"tap-{name}", daemon=True)
        self.thread.start()

    def _interval(self, cid: int) -> float:
        try:
            v = self.interval_s(cid) if callable(self.interval_s) else self.interval_s
            return max(0.0, float(v))
        except Exception:  # noqa: BLE001
            return 1.0

    def wants(self, cid: int, ts: float) -> bool:
        try:
            if not self.cameras(cid):
                return False
        except Exception:  # noqa: BLE001
            return False
        return ts - self.last_offer.get(cid, 0.0) >= self._interval(cid)

    def put(self, f: TapFrame) -> None:
        with self.lock:
            if f.camera_id in self.slots:
                self.dropped.append(time.time())
            self.slots[f.camera_id] = f
            self.last_offer[f.camera_id] = f.ts
        self.wake.set()

    def _run(self) -> None:
        while not self.stop_ev.is_set():
            self.wake.wait(1.0)
            self.wake.clear()
            while not self.stop_ev.is_set():
                with self.lock:
                    if not self.slots:
                        break
                    cid = next(iter(self.slots))
                    f = self.slots.pop(cid)
                t0 = time.perf_counter()
                try:
                    self.callback(f)
                except Exception:  # noqa: BLE001
                    if time.time() - self._last_err > 60:
                        self._last_err = time.time()
                        log.exception("frame tap %s failed (camera %s)", self.name, cid)
                self.done.append((time.time(), (time.perf_counter() - t0) * 1000))

    def perf(self) -> dict:
        now = time.time()
        d = [m for t, m in list(self.done) if now - t <= 60]
        return {"frames_per_min": len(d), "ms": round(sum(d) / len(d), 1) if d else 0.0,
                "dropped": sum(1 for t in list(self.dropped) if now - t <= 60)}

    def stop(self) -> None:
        self.stop_ev.set()
        self.wake.set()


class FrameTapHub:
    def __init__(self):
        self._taps: dict[str, FrameTap] = {}
        self._lock = threading.Lock()

    def register(self, name: str, callback: Callable[[TapFrame], None], *, cameras: Callable[[int], bool],
                 interval_s: Interval = 1.0) -> FrameTap:
        """Abnehmer anlegen (ersetzt einen gleichnamigen). ``cameras(cid)`` entscheidet je Kamera,
        ``interval_s`` (Zahl oder Funktion je Kamera) drosselt; 0 = jeder analysierte Frame."""
        tap = FrameTap(name, callback, cameras, interval_s)
        with self._lock:
            old = self._taps.pop(name, None)
            self._taps[name] = tap
        if old is not None:
            old.stop()
        return tap

    def unregister(self, name: str) -> None:
        with self._lock:
            tap = self._taps.pop(name, None)
        if tap is not None:
            tap.stop()

    def active(self) -> bool:
        return bool(self._taps)

    def offer(self, runtime, ts: float, image: np.ndarray, det_msg: Optional[dict],
              persons: Optional[list[list[float]]] = None) -> None:
        """Aus Pipeline._post: billig, wenn kein Abnehmer die Kamera will (kein Kopieren des Bildes)."""
        with self._lock:
            taps = list(self._taps.values())
        if not taps:
            return
        cid = runtime.id
        want = [t for t in taps if t.wants(cid, ts)]
        if not want:
            return
        h, w = image.shape[:2]
        f = TapFrame(camera_id=cid, ts=ts, image=image, width=w, height=h,
                     objects=list((det_msg or {}).get("objects") or []), persons=list(persons or []),
                     runtime=runtime)
        for t in want:
            t.put(f)

    def perf(self) -> dict:
        with self._lock:
            return {name: t.perf() for name, t in self._taps.items()}

    def stop(self) -> None:
        with self._lock:
            taps = list(self._taps.values())
            self._taps.clear()
        for t in taps:
            t.stop()
