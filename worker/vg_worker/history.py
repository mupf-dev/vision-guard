"""Zeitmaschine im Worker (B1) – Gerüst aus K3, Umsetzung: B1.

Vertrag: CONTRACT.md „Nachträge: Welle 3“, W3.G. Verdrahtung ist fertig:
- ``Pipeline.apply_config`` ruft ``configure(cameras)`` (``history_enabled``, ``history_interval_s``,
  ``history_tiling``); ``set_settings(s)`` liefert ``history_min_change``/``history_model``.
- GPU-Lease: ``pause_gpu(holder) -> bool`` / ``resume_gpu()`` sind beim Lease-Manager registriert („history“) –
  Bildturm (fp16, GPU) entladen bzw. wieder erlauben.
- Text-Embeddings: ``pipeline.embed.siglip = self`` setzen, sobald ``encode_text``/``encode_images`` bereitstehen
  (``vg/internal/embed`` mit ``model:"siglip2"``).
- Bildquelle: ``inventory.KeyframeGrabber`` je Kamera oder Frame-Tap (``pipeline.taps``).
- Ergebnis: ``publish("vg/internal/history/{cid}", {...}, 1, False)``; Vorschau über ``util.store_media``.
- ``status_text()`` → ``models.history``, ``perf()`` → ``perf.history``.
"""
from __future__ import annotations

import logging
import threading
from typing import Callable

log = logging.getLogger("vg.history")


class HistoryEngine:
    model_name = "ViT-B-16-SigLIP2-256"

    def __init__(self, publish: Callable[..., None], taps, gpu_turn):
        self.publish = publish
        self.taps = taps                  # frametap.FrameTapHub
        self.gpu = gpu_turn               # inventory.GpuTurn (GPU abwechselnd mit der Detektion)
        self.lock = threading.Lock()
        self.cams: dict[int, dict] = {}
        self.status = "off"               # off | loading | ready | paused: … | error: …
        self._paused: str | None = None

    def configure(self, cameras: list[dict]) -> None:
        with self.lock:
            self.cams = {int(c["id"]): c for c in cameras if c.get("history_enabled")}

    def set_settings(self, s: dict | None) -> None:
        return None

    # ------------------------------------------------------------------ GPU-Lease (W3.C)
    def pause_gpu(self, holder: str) -> bool:
        self._paused = holder or "?"
        return True                       # B1: Bildturm entladen (blockierend, ≤ 8 s)

    def resume_gpu(self) -> None:
        self._paused = None

    def status_text(self) -> str:
        return f"paused: GPU-Lease {self._paused}" if self._paused and self.status != "off" else self.status

    def perf(self) -> dict:
        return {"per_min": 0, "ms": 0.0, "skipped": 0}

    def stop(self) -> None:
        self.taps.unregister("history")
