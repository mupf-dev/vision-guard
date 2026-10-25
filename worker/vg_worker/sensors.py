"""Virtuelle Sensoren im Worker (A3) – Gerüst aus K3, Umsetzung: A3+C4.

Vertrag: CONTRACT.md „Nachträge: Welle 3“, W3.F. Verdrahtung ist fertig:
- ``Pipeline.apply_config`` ruft ``configure(cameras)`` (je Kamera ``cfg["sensors"]``, nur Arten
  brightness|blink|reference|fill).
- Bildquelle: Frame-Tap (``pipeline.taps.register("sensors", self._on_frame, cameras=…, interval_s=…)``), Personen
  im Bereich → ``occluded``. DINOv2 für reference/fill: ``self.pet`` (geteiltes identify.PetModel,
  ``embed_batch([BGR, …])`` → L2-normierte 384-d).
- Ergebnis: ``publish("vg/internal/sensor/{cid}", {camera_id, ts, frame_ts, values:[…]}, 0, False)``.
- ``perf()`` → ``vg/internal/system.perf.sensors``.
"""
from __future__ import annotations

import logging
import threading
from typing import Callable

log = logging.getLogger("vg.sensors")

WORKER_KINDS = ("brightness", "blink", "reference", "fill")


class SensorEngine:
    def __init__(self, publish: Callable[..., None], taps, pet_model):
        self.publish = publish
        self.taps = taps                  # frametap.FrameTapHub
        self.pet = pet_model              # identify.PetModel (geteiltes DINOv2)
        self.lock = threading.Lock()
        self.cams: dict[int, list[dict]] = {}

    def configure(self, cameras: list[dict]) -> None:
        """Mit allen aktiven Kamera-Konfigurationen (aus Pipeline.apply_config)."""
        with self.lock:
            self.cams = {int(c["id"]): [s for s in (c.get("sensors") or []) if s.get("kind") in WORKER_KINDS]
                         for c in cameras if c.get("sensors")}
        # A3: Frame-Tap registrieren/entfernen, sobald Messungen umgesetzt sind

    def perf(self) -> dict:
        return {"per_min": 0, "ms": 0.0}

    def stop(self) -> None:
        self.taps.unregister("sensors")
