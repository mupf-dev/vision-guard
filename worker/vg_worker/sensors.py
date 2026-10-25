"""Virtuelle Sensoren im Worker (A3/C4). Vertrag: CONTRACT.md „Nachträge: Welle 3“, W3.F.

Bildquelle ist der Frame-Tap (``pipeline.taps``, Abnehmer ``sensors``, eigener Thread, verwirft statt zu stauen).
Je Kamera kommen die Sensoren aus ``GET /api/internal/cameras`` (``cfg["sensors"]``). Gemessen wird nur im Bereich
(Polygon, normiert) auf dem Detect-Frame:

- ``brightness``: mittlere Helligkeit (Luma 0..1) im Polygon.
- ``blink``: Standardabweichung der Helligkeit über ``params.window_s`` (Abtastung mit jedem Tap-Frame, ≈ 5/s).
- ``reference``: DINOv2 (geteiltes ``identify.PetModel``) des Bereichs gegen Referenzbilder ``on``/``off``;
  ``value`` = P(an) = Sigmoid((max cos on − max cos off) / 0,02), ``scores`` = {on, off}.
- ``fill``: DINOv2 gegen ``full``/``half``/``empty`` (mind. zwei Stufen); ``value`` = Füllstand 0..1 =
  Softmax-gewichtetes Mittel der Stufen (1 / 0,5 / 0), ``scores`` = {full, half, empty}.
- ``vlm`` (nur Änderungsmaß): kein Wert, das Backend fragt das Bildmodell.

Für alle Arten: ``occluded`` = Personen-Boxen (Frame-Tap ``persons``) bedecken > 10 % des Bereichs – dann zählt die
Messung nicht (das Backend ignoriert sie). ``change`` = mittlere Absolutdifferenz (0..1) eines 32×32-Graubilds des
Bereichs zu einem Ankerbild; erreicht sie ``change_threshold``, wird sie gemeldet und der Anker neu gesetzt
(bei Verdeckung wird ``change`` nicht berechnet).

Referenzbilder liegen als ganzes Kamerabild im Medien-Volume (``sensors/ref/…``); der Worker skaliert sie auf die
Detect-Auflösung und schneidet den Bereich selbst aus (Embedding je Referenz + Bereich gecacht).

Ergebnis je Kamera und Takt: ``vg/internal/sensor/{cid}`` (QoS 0)
``{camera_id, ts, frame_ts, values:[{sensor_id, value, change, occluded, scores}]}``; ``perf()`` →
``vg/internal/system.perf.sensors = {per_min, ms}``. VRAM: 0 (CPU-Messungen, DINOv2 ist geteilt).
"""
from __future__ import annotations

import logging
import math
import threading
import time
from collections import deque
from typing import Any, Callable, Optional

import cv2
import numpy as np

from .util import abs_media_path

log = logging.getLogger("vg.sensors")

WORKER_KINDS = ("brightness", "blink", "reference", "fill")
CHANGE_ONLY_KINDS = ("vlm",)
EMBED_KINDS = ("reference", "fill")
OCCLUDED_SHARE = 0.10
BLINK_TAP_S = 0.2           # Abtastung, solange ein Blink-Sensor aktiv ist (Detect läuft mit ≈ 5 fps)
VLM_CHANGE_EVERY_S = 15.0   # Änderungsmaß für vlm-Sensoren
SIM_T = 0.02                # Temperatur für P(an) bzw. Füllstand aus Kosinus-Ähnlichkeiten
FILL_LEVELS = {"full": 1.0, "half": 0.5, "empty": 0.0}
FILL_DINO_W = 0.5           # Anteil DINOv2 an der Füllstand-Ähnlichkeit (Rest: Farb-Miniatur)
THUMB = 32


def _thumb(img: np.ndarray) -> np.ndarray:
    """16×16-Farbminiatur (leicht weichgezeichnet) für die Füllstand-Ähnlichkeit."""
    return cv2.resize(cv2.GaussianBlur(img, (5, 5), 0), (16, 16), interpolation=cv2.INTER_AREA).astype(np.float32)


def _pix_sim(a: np.ndarray, b: np.ndarray) -> float:
    return 1.0 - float(np.abs(a - b).mean()) / 255.0


class _Runtime:
    """Laufzeit je Sensor (Anker, Blink-Fenster, Zeitpunkt der letzten Meldung)."""

    def __init__(self):
        self.last_pub = 0.0
        self.anchor: Optional[np.ndarray] = None
        self.samples: deque = deque(maxlen=600)     # (ts, brightness)
        self.mask_key = None
        self.mask: Optional[np.ndarray] = None
        self.rect = (0, 0, 0, 0)


class SensorEngine:
    def __init__(self, publish: Callable[..., None], taps, pet_model):
        self.publish = publish
        self.taps = taps                  # frametap.FrameTapHub
        self.pet = pet_model              # identify.PetModel (geteiltes DINOv2)
        self.lock = threading.Lock()
        self.cams: dict[int, list[dict]] = {}
        self.rt: dict[int, _Runtime] = {}
        self.refs: dict[tuple, tuple] = {}          # (ref_id, region, w, h) → (Embedding, Farb-Miniatur)
        self._registered = False
        self._last_ids: set[int] = set()
        self.done: deque = deque(maxlen=5000)       # (ts, ms, n)
        self._warned: dict[str, float] = {}

    # ------------------------------------------------------------------ Konfiguration
    def configure(self, cameras: list[dict]) -> None:
        """Mit allen aktiven Kamera-Konfigurationen (aus Pipeline.apply_config)."""
        cams: dict[int, list[dict]] = {}
        for c in cameras:
            sens = [s for s in (c.get("sensors") or [])
                    if s.get("kind") in WORKER_KINDS + CHANGE_ONLY_KINDS and len(s.get("region") or []) >= 3]
            if sens:
                cams[int(c["id"])] = sens
        ids = {int(s["id"]) for ss in cams.values() for s in ss}
        ref_ids = {int(r["id"]) for ss in cams.values() for s in ss for r in (s.get("references") or [])}
        with self.lock:
            self.cams = cams
            for sid in list(self.rt):
                if sid not in ids:
                    self.rt.pop(sid, None)
            for k in list(self.refs):
                if k[0] not in ref_ids:
                    self.refs.pop(k, None)
        if any(s.get("kind") in EMBED_KINDS for ss in cams.values() for s in ss):
            try:
                self.pet.ensure_started()
            except Exception:  # noqa: BLE001
                log.exception("sensors: DINOv2 not available")
        if cams and not self._registered:
            self.taps.register("sensors", self._on_frame, cameras=lambda cid: cid in self.cams,
                               interval_s=self._interval)
            self._registered = True
            log.info("sensors: %d sensor(s) on %d camera(s)", len(ids), len(cams))
        elif not cams and self._registered:
            self.taps.unregister("sensors")
            self._registered = False
            log.info("sensors: none active")
        elif cams and ids != self._last_ids:
            log.info("sensors: %d sensor(s) on %d camera(s)", len(ids), len(cams))
        self._last_ids = ids

    def _interval(self, cid: int) -> float:
        sens = self.cams.get(cid) or []
        if any(s.get("kind") == "blink" for s in sens):
            return BLINK_TAP_S
        iv = [self._sensor_interval(s) for s in sens]
        return max(0.5, min(iv)) if iv else 5.0

    @staticmethod
    def _sensor_interval(s: dict) -> float:
        iv = float(s.get("interval_s") or 10)
        if s.get("kind") in CHANGE_ONLY_KINDS:
            iv = min(iv, VLM_CHANGE_EVERY_S)
        return max(0.5, iv)

    # ------------------------------------------------------------------ Messung
    def _region(self, rt: _Runtime, s: dict, w: int, h: int):
        poly = s.get("region") or []
        key = (tuple(tuple(map(float, p)) for p in poly), w, h)
        if rt.mask_key != key:
            pts = np.array([[float(p[0]) * w, float(p[1]) * h] for p in poly], np.float32)
            x1, y1 = max(0, int(math.floor(pts[:, 0].min()))), max(0, int(math.floor(pts[:, 1].min())))
            x2, y2 = min(w, int(math.ceil(pts[:, 0].max()))), min(h, int(math.ceil(pts[:, 1].max())))
            if x2 - x1 < 2 or y2 - y1 < 2:
                rt.mask, rt.rect = None, (0, 0, 0, 0)
            else:
                m = np.zeros((y2 - y1, x2 - x1), np.uint8)
                cv2.fillPoly(m, [np.round(pts - [x1, y1]).astype(np.int32)], 1)
                if not m.any():
                    m[:] = 1
                rt.mask, rt.rect = m.astype(bool), (x1, y1, x2, y2)
            rt.mask_key = key
        return rt.mask, rt.rect

    @staticmethod
    def _occlusion(mask: np.ndarray, rect, persons: list, w: int, h: int) -> float:
        if not persons:
            return 0.0
        x1, y1, x2, y2 = rect
        cover = np.zeros_like(mask)
        for b in persons:
            bx1, by1 = int(b[0] * w) - x1, int(b[1] * h) - y1
            bx2, by2 = int(math.ceil(b[2] * w)) - x1, int(math.ceil(b[3] * h)) - y1
            bx1, by1 = max(0, bx1), max(0, by1)
            bx2, by2 = min(x2 - x1, bx2), min(y2 - y1, by2)
            if bx2 > bx1 and by2 > by1:
                cover[by1:by2, bx1:bx2] = True
        n = int(mask.sum())
        return float((cover & mask).sum()) / n if n else 0.0

    def _on_frame(self, f) -> None:
        t0 = time.perf_counter()
        with self.lock:
            sens = list(self.cams.get(f.camera_id) or [])
        if not sens:
            return
        img = f.image
        h, w = img.shape[:2]
        gray = None
        now = time.time()
        values: list[dict] = []
        embed_jobs: list[tuple[dict, dict, np.ndarray]] = []
        for s in sens:
            sid = int(s["id"])
            with self.lock:
                rt = self.rt.setdefault(sid, _Runtime())
            mask, rect = self._region(rt, s, w, h)
            if mask is None:
                continue
            kind = s.get("kind")
            due = f.ts - rt.last_pub >= self._sensor_interval(s) - 0.05
            if kind != "blink" and not due:
                continue
            if gray is None:
                gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
            x1, y1, x2, y2 = rect
            roi = gray[y1:y2, x1:x2]
            occluded = self._occlusion(mask, rect, f.persons, w, h) > OCCLUDED_SHARE
            bright = float(roi[mask].mean()) / 255.0
            if kind == "blink":
                if not occluded:
                    rt.samples.append((f.ts, bright))
                if not due:
                    continue
            rt.last_pub = f.ts
            entry: dict[str, Any] = {"sensor_id": sid, "value": None, "change": None, "occluded": occluded,
                                     "scores": None}
            if not occluded:
                entry["change"] = self._change(rt, roi, float(s.get("change_threshold") or 0.08))
            if kind == "brightness":
                entry["value"] = round(bright, 4)
            elif kind == "blink":
                win = float((s.get("params") or {}).get("window_s") or 3)
                pts = [b for ts, b in rt.samples if f.ts - ts <= win]
                span = (f.ts - min((ts for ts, _ in rt.samples if f.ts - ts <= win), default=f.ts))
                if len(pts) >= 5 and span >= 0.6 * win:
                    entry["value"] = round(float(np.std(pts)), 4)
                else:
                    continue                # Fenster noch nicht gefüllt (z. B. nach Verdeckung)
            elif kind in EMBED_KINDS:
                if occluded:
                    pass
                elif not self.pet.ready():
                    continue                # Modell lädt noch – keine Aussage statt „Referenzen fehlen“
                else:
                    crop = np.ascontiguousarray(img[y1:y2, x1:x2])
                    embed_jobs.append((s, entry, crop))
            values.append(entry)
        if embed_jobs:
            self._embed(embed_jobs, w, h)
        if values:
            self.publish(f"vg/internal/sensor/{f.camera_id}",
                         {"camera_id": f.camera_id, "ts": round(now, 3), "frame_ts": round(float(f.ts), 3),
                          "values": values}, 0, False)
        self.done.append((time.time(), (time.perf_counter() - t0) * 1000, len(values)))

    @staticmethod
    def _change(rt: _Runtime, roi: np.ndarray, threshold: float) -> float:
        th = cv2.resize(roi, (THUMB, THUMB), interpolation=cv2.INTER_AREA).astype(np.float32)
        if rt.anchor is None:
            rt.anchor = th
            return 0.0
        d = float(np.abs(th - rt.anchor).mean()) / 255.0
        if d >= threshold:
            rt.anchor = th          # Änderung einmal melden, dann neuer Anker
        return round(d, 4)

    # ------------------------------------------------------------------ DINOv2
    def _ref_embeddings(self, s: dict, w: int, h: int) -> dict[str, list[tuple]]:
        out: dict[str, list[tuple]] = {}
        region = tuple(tuple(map(float, p)) for p in (s.get("region") or []))
        todo = []
        for r in s.get("references") or []:
            key = (int(r["id"]), region, w, h)
            emb = self.refs.get(key)
            if emb is None:
                todo.append((key, r))
            else:
                out.setdefault(r["state"], []).append(emb)
        if todo:
            crops, keys, states = [], [], []
            for key, r in todo:
                try:
                    im = cv2.imread(abs_media_path(r["path"]), cv2.IMREAD_COLOR)
                except Exception:  # noqa: BLE001
                    im = None
                if im is None:
                    self._warn(f"ref{r['id']}", f"reference image {r['id']} not readable")
                    continue
                if im.shape[1] != w or im.shape[0] != h:
                    im = cv2.resize(im, (w, h), interpolation=cv2.INTER_AREA)
                xs = [p[0] for p in region]
                ys = [p[1] for p in region]
                x1, x2 = max(0, int(min(xs) * w)), min(w, int(math.ceil(max(xs) * w)))
                y1, y2 = max(0, int(min(ys) * h)), min(h, int(math.ceil(max(ys) * h)))
                if x2 - x1 < 2 or y2 - y1 < 2:
                    continue
                crops.append(np.ascontiguousarray(im[y1:y2, x1:x2]))
                keys.append(key)
                states.append(r["state"])
            if crops:
                embs = self.pet.embed_batch(crops)
                for key, st, e, cr in zip(keys, states, embs, crops):
                    self.refs[key] = (e, _thumb(cr))
                    out.setdefault(st, []).append(self.refs[key])
        return out

    def _embed(self, jobs: list[tuple[dict, dict, np.ndarray]], w: int, h: int) -> None:
        try:
            embs = self.pet.embed_batch([c for _, _, c in jobs])
        except Exception as e:  # noqa: BLE001
            self._warn("embed", f"sensor embedding failed: {e}")
            for _, entry, _ in jobs:
                entry["value"] = None
            return
        for (s, entry, crop), e in zip(jobs, embs):
            try:
                refs = self._ref_embeddings(s, w, h)
            except Exception as ex:  # noqa: BLE001
                self._warn("refs", f"reference embedding failed: {ex}")
                continue
            if s.get("kind") == "fill":
                # Füllstand: DINOv2 allein trennt gleichförmige Flächen (Balken, Schüttgut) schlecht – je zur
                # Hälfte mit der Farbähnlichkeit einer 16×16-Miniatur mischen (W3-Test: halb ≠ voll erst damit)
                th = _thumb(crop)
                sims = {st: float(max(FILL_DINO_W * float(np.dot(e, r[0])) + (1 - FILL_DINO_W) * _pix_sim(th, r[1])
                                      for r in lst)) for st, lst in refs.items() if lst}
            else:
                sims = {st: float(max(float(np.dot(e, r[0])) for r in lst)) for st, lst in refs.items() if lst}
            if s.get("kind") == "reference":
                if "on" in sims and "off" in sims:
                    z = (sims["on"] - sims["off"]) / SIM_T
                    entry["value"] = round(1.0 / (1.0 + math.exp(-max(-50.0, min(50.0, z)))), 4)
                entry["scores"] = {k: round(v, 4) for k, v in sims.items() if k in ("on", "off")} or None
            else:
                lv = {k: v for k, v in sims.items() if k in FILL_LEVELS}
                if len(lv) >= 2:
                    mx = max(lv.values())
                    ws = {k: math.exp((v - mx) / SIM_T) for k, v in lv.items()}
                    tot = sum(ws.values())
                    entry["value"] = round(sum(FILL_LEVELS[k] * wv for k, wv in ws.items()) / tot, 4)
                entry["scores"] = {k: round(v, 4) for k, v in lv.items()} or None

    def _warn(self, key: str, msg: str) -> None:
        if time.time() - self._warned.get(key, 0.0) > 60:
            self._warned[key] = time.time()
            log.warning(msg)

    # ------------------------------------------------------------------ Status
    def perf(self) -> dict:
        now = time.time()
        d = [(ms, n) for t, ms, n in list(self.done) if now - t <= 60]
        runs = [ms for ms, n in d if n]
        return {"per_min": sum(n for _, n in d), "ms": round(sum(runs) / len(runs), 2) if runs else 0.0}

    def stop(self) -> None:
        self.taps.unregister("sensors")
        self._registered = False
