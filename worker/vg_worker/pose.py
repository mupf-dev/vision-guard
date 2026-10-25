"""Pose-Stufe (C1/D1): YOLO11n-pose auf Kameras mit `pose_enabled` oder `gesture_enabled`.

Ablauf (Empfehlungen aus messungen.md §2):
- **Ganzbild-Pose** statt Ausschnitten (genauer und billiger: 9 ms je Bild bei 640 px), nur wenn auf der Kamera ein
  bestätigter Personen-Track läuft, höchstens `POSE_FPS` (2 Hz) bzw. `GESTURE_FPS` (5 Hz, Gesten-Kameras).
- Eigener Thread mit Warteschlange „neuestes Bild je Kamera“ (ältere werden verworfen), mehrere Kameras in einem
  Batch, GPU abwechselnd mit der Sicherheitspipeline (`GpuTurn`) – die Detektion hat immer Vorrang. Zeitbudget:
  höchstens `POSE_MAX_BUSY` (Anteil GPU-Zeit), darüber werden Bilder übersprungen.
- Pose-Personen werden per IoU den Tracks zugeordnet → `posture.classify` → `PostureSmoother` → Track-Attribute
  `posture`, `posture_since`, `motion` (`CameraTracker.set_pose`). Pose-Personen ohne Track halten einen liegenden
  Track am Leben (Detektor verliert liegende Personen in ~50 % der Bilder).
- Gesten (`gestures.py`) nur auf `gesture_enabled`-Kameras → `vg/internal/gesture/{camera_id}`.
- Modell wird erst bei Bedarf geladen und nach `POSE_UNLOAD_S` ohne Pose-Kamera entladen. Ladefehler →
  `models.pose = "error: …"`, die Detektion läuft weiter (erneuter Versuch nach 5 min).
"""
from __future__ import annotations

import logging
import os
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Callable

import numpy as np

from . import gestures as gest
from . import posture as post
from . import settings

log = logging.getLogger("vg.pose")

MODEL_DIR = os.path.join(settings.MODELS_DIR, "pose")


def _iou(a, b) -> float:
    ix = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = ix * iy
    if inter <= 0:
        return 0.0
    ua = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / ua if ua > 0 else 0.0


@dataclass
class PoseJob:
    camera_id: int
    ts: float
    image: np.ndarray                    # BGR, volle Detect-Auflösung
    tracks: list[dict]                   # [{tid, bbox (normiert), zones, identity}] bestätigte Personen-Tracks
    gestures: bool                       # Gesten auf dieser Kamera auswerten


@dataclass
class TrackPose:
    smoother: post.PostureSmoother = field(default_factory=post.PostureSmoother)
    gesture: gest.GestureState = field(default_factory=gest.GestureState)
    last: float = 0.0


class PoseModel:
    def __init__(self):
        os.makedirs(MODEL_DIR, exist_ok=True)
        os.environ.setdefault("YOLO_CONFIG_DIR", os.path.join(settings.MODELS_DIR, "ultralytics"))
        import torch
        from ultralytics import YOLO
        self.torch = torch
        self.device = settings.DEVICE if torch.cuda.is_available() else "cpu"
        self.half = self.device.startswith("cuda")
        name = settings.POSE_MODEL
        if os.path.isabs(name):
            path = name
        else:
            alt = os.path.join(settings.MODELS_DIR, name)
            path = alt if os.path.exists(alt) else os.path.join(MODEL_DIR, name)
            if not os.path.exists(path):
                # absoluter Zielpfad statt os.chdir (prozessweit, würde andere Threads stören)
                from ultralytics.utils.downloads import attempt_download_asset
                log.info("downloading %s to %s (once)", name, MODEL_DIR)
                path = attempt_download_asset(path)
                if not os.path.exists(path):
                    raise RuntimeError(f"{name} konnte nicht geladen werden")
        self.model = YOLO(path)
        self.model_name = os.path.splitext(os.path.basename(name))[0]
        self.imgsz = settings.POSE_IMGSZ
        # Aufwärmen ohne GPU-Lock (kurz, Bild klein)
        self.predict([np.zeros((360, 640, 3), np.uint8)])

    def predict(self, images: list[np.ndarray]) -> list[list[dict]]:
        """Je Bild: [{box: [x1,y1,x2,y2] normiert, score, kp: (17,2) normiert, kc: (17,)}]."""
        res = self.model.predict(images, imgsz=self.imgsz, half=self.half, device=self.device,
                                 conf=settings.POSE_MIN_CONF, verbose=False, max_det=30, batch=len(images))
        out: list[list[dict]] = []
        for r in res:
            people: list[dict] = []
            b, k = r.boxes, r.keypoints
            if b is not None and k is not None and len(b):
                boxes = b.xyxyn.float().cpu().numpy()
                sc = b.conf.float().cpu().numpy()
                kp = k.xyn.float().cpu().numpy()
                kc = k.conf.float().cpu().numpy() if k.conf is not None else np.ones(kp.shape[:2], np.float32)
                for i in range(len(boxes)):
                    people.append({"box": [float(v) for v in np.clip(boxes[i], 0, 1)], "score": float(sc[i]),
                                   "kp": kp[i], "kc": kc[i]})
            out.append(people)
        return out

    def close(self):
        self.model = None
        try:
            if self.device.startswith("cuda"):
                self.torch.cuda.empty_cache()
        except Exception:  # noqa: BLE001
            pass


class PoseEngine:
    """Pose-Thread. `get_cam(cid)` liefert die `CameraRuntime` (Tracker, Konfiguration)."""

    def __init__(self, publish: Callable[[str, dict, int, bool], None], gpu, get_cam: Callable[[int], object]):
        self.publish = publish
        self.gpu = gpu
        self.get_cam = get_cam
        self.model: PoseModel | None = None
        self.status = "off"
        self.lock = threading.Lock()
        self.jobs: dict[int, PoseJob] = {}              # neuestes Bild je Kamera
        self.last_submit: dict[int, float] = {}
        self.state: dict[tuple[int, int], TrackPose] = {}
        self.wanted: set[int] = set()
        self.gesture_hold_s = 1.0
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._load_retry_at = 0.0
        self._idle_since = time.time()
        self.runs: deque[tuple[float, int, float]] = deque(maxlen=2000)   # (ts, Personen, ms)
        self.busy: deque[tuple[float, float]] = deque(maxlen=2000)        # (ts, ms) GPU-Zeit
        self.skipped: deque[float] = deque(maxlen=2000)
        self.gestures_sent = 0

    # ------------------------------------------------------------------ Konfiguration
    def configure(self, cameras: list[dict]):
        wanted = {int(c["id"]) for c in cameras if c.get("pose_enabled") or c.get("gesture_enabled")}
        with self.lock:
            self.wanted = wanted
            for cid in [k for k in self.jobs if k not in wanted]:
                self.jobs.pop(cid, None)
            for key in [k for k in self.state if k[0] not in wanted]:
                self.state.pop(key, None)
        if wanted and self._thread is None:
            self._thread = threading.Thread(target=self._run, name="pose", daemon=True)
            self._thread.start()
        if wanted and self.status == "off":
            self.status = "loading"

    def set_settings(self, s: dict | None):
        if not s:
            return
        try:
            self.gesture_hold_s = max(0.5, min(5.0, float(s.get("gesture_hold_s", self.gesture_hold_s))))
        except (TypeError, ValueError):
            pass

    def wants(self, camera_id: int) -> bool:
        return camera_id in self.wanted

    def stop(self):
        self._stop.set()
        self._wake.set()

    # ------------------------------------------------------------------ Eingang (aus Pipeline._post)
    def offer(self, cam, ts: float, image: np.ndarray):
        """Bild einer Pose-Kamera anbieten. Nimmt es nur, wenn ein Personen-Track existiert und die Rate passt."""
        cid = cam.id
        if cid not in self.wanted or self.status.startswith("error"):
            return
        gestures = bool(cam.cfg.get("gesture_enabled"))
        fps = settings.GESTURE_FPS if gestures else settings.POSE_FPS
        if ts - self.last_submit.get(cid, 0.0) < 1.0 / max(0.1, fps) - 0.02:
            return
        tracks = cam.tracker.person_tracks()
        if not tracks:
            return
        if self._over_budget(ts):
            self.skipped.append(time.time())
            return
        self.last_submit[cid] = ts
        with self.lock:
            if cid in self.jobs:
                self.skipped.append(time.time())        # vorheriges Bild noch nicht verarbeitet → ersetzen
            self.jobs[cid] = PoseJob(cid, ts, image, tracks, gestures)
        self._wake.set()

    def _over_budget(self, now: float) -> bool:
        win = 5.0
        used = sum(ms for t, ms in list(self.busy) if now - t <= win)
        return used > settings.POSE_MAX_BUSY * win * 1000.0

    # ------------------------------------------------------------------ Thread
    def _ensure_model(self) -> bool:
        if self.model is not None:
            return True
        if time.time() < self._load_retry_at:
            return False
        self.status = "loading"
        t0 = time.time()
        try:
            self.model = PoseModel()
            self.status = f"ready ({self.model.model_name}, {'CUDA fp16' if self.model.half else self.model.device})"
            log.info("pose model %s ready in %.1fs", self.model.model_name, time.time() - t0)
            return True
        except Exception as e:  # noqa: BLE001
            self.model = None
            self.status = f"error: {type(e).__name__}: {e}"[:300]
            self._load_retry_at = time.time() + 300
            log.error("pose model failed to load: %s", e, exc_info=True)
            return False

    def _run(self):
        while not self._stop.is_set():
            if not self.wanted:
                if self.model is not None and time.time() - self._idle_since > settings.POSE_UNLOAD_S:
                    log.info("no pose camera for %.0fs – unloading pose model", settings.POSE_UNLOAD_S)
                    self.model.close()
                    self.model = None
                if self.model is None:
                    self.status = "off"
                self._wake.wait(1.0)
                self._wake.clear()
                continue
            self._idle_since = time.time()
            if not self._ensure_model():
                with self.lock:
                    self.jobs.clear()
                self._wake.wait(2.0)
                self._wake.clear()
                continue
            with self.lock:
                jobs = list(self.jobs.values())[: settings.POSE_MAX_BATCH]
                for j in jobs:
                    self.jobs.pop(j.camera_id, None)
            if not jobs:
                self._wake.wait(0.5)
                self._wake.clear()
                self._gc()
                continue
            try:
                lock = self.gpu.inventory_slot(0.25)
                with lock:
                    t0 = time.perf_counter()
                    results = self.model.predict([j.image for j in jobs])
                    ms = (time.perf_counter() - t0) * 1000.0
            except Exception as e:  # noqa: BLE001
                log.warning("pose inference failed: %s", e)
                time.sleep(0.5)
                continue
            now = time.time()
            self.busy.append((now, ms))
            self.runs.append((now, sum(len(j.tracks) for j in jobs), ms))
            for j, people in zip(jobs, results):
                try:
                    self._apply(j, people)
                except Exception:  # noqa: BLE001
                    log.exception("pose post-processing camera %s failed", j.camera_id)

    def _gc(self):
        now = time.time()
        with self.lock:
            for key in [k for k, v in self.state.items() if now - v.last > 120]:
                self.state.pop(key, None)

    # ------------------------------------------------------------------ Auswertung
    def _apply(self, job: PoseJob, people: list[dict]):
        cam = self.get_cam(job.camera_id)
        if cam is None:
            return
        h, w = job.image.shape[:2]
        # gierige Zuordnung Pose-Person ↔ Track nach IoU
        pairs = sorted(((_iou(p["box"], t["bbox"]), pi, ti) for pi, p in enumerate(people)
                        for ti, t in enumerate(job.tracks)), reverse=True)
        used_p, used_t, match = set(), set(), {}
        for iou, pi, ti in pairs:
            if iou < settings.POSE_MATCH_IOU:
                break
            if pi in used_p or ti in used_t:
                continue
            used_p.add(pi)
            used_t.add(ti)
            match[ti] = people[pi]
        scale = np.array([w, h], np.float32)
        for ti, t in enumerate(job.tracks):
            p = match.get(ti)
            key = (job.camera_id, t["tid"])
            with self.lock:
                st = self.state.get(key)
                if st is None:
                    st = self.state[key] = TrackPose()
            st.last = job.ts
            box_px = [t["bbox"][0] * w, t["bbox"][1] * h, t["bbox"][2] * w, t["bbox"][3] * h]
            if p is not None:
                kp = p["kp"] * scale
                kc = p["kc"]
                pb = [p["box"][0] * w, p["box"][1] * h, p["box"][2] * w, p["box"][3] * h]
                # Box: die größere Ausdehnung beider Boxen (Detektor schneidet liegende Personen oft ab)
                obs = post.classify(kp, kc, pb)
                if obs == "unknown":
                    obs = post.classify(kp, kc, box_px)
            else:
                kp = kc = None
                obs = post.classify(None, None, box_px) if not t.get("held") else "unknown"
            if p is not None and t.get("held"):
                cam.tracker.pose_keepalive(p["box"], job.ts)
            changed = st.smoother.add(job.ts, obs, kp, kc, box_px)
            sm = st.smoother
            cam.tracker.set_pose(t["tid"], sm.posture, sm.since, sm.motion, changed)
            if job.gestures and p is not None and not t.get("held"):
                g = st.gesture.add(job.ts, gest.classify(kp, kc), self.gesture_hold_s)
                if g is not None:
                    name, score, held = g
                    self.gestures_sent += 1
                    log.info("gesture %s camera %s track %s (score %.2f, %.1fs)", name, job.camera_id, t["tid"],
                             score, held)
                    self.publish(f"vg/internal/gesture/{job.camera_id}", {
                        "camera_id": job.camera_id, "track_id": t["tid"], "gesture": name, "score": score,
                        "held_s": held, "ts": round(job.ts, 3), "zones": list(t.get("zones") or []),
                        "identity": t.get("identity")}, 1, False)
        # Pose-Personen ohne Track: liegende, gerade nicht detektierte Tracks am Leben halten
        for pi, p in enumerate(people):
            if pi in used_p:
                continue
            cam.tracker.pose_keepalive(p["box"], job.ts)

    # ------------------------------------------------------------------ Status
    def status_text(self) -> str:
        return self.status

    def perf(self) -> dict:
        now = time.time()
        r = [(n, ms) for t, n, ms in list(self.runs) if now - t <= 60]
        if not r:
            return {"ms": 0.0, "crops_per_s": 0.0, "runs_per_min": 0, "skipped": 0,
                    "cameras": len(self.wanted), "gestures": self.gestures_sent}
        return {"ms": round(sum(ms for _, ms in r) / len(r), 1),
                "crops_per_s": round(sum(n for n, _ in r) / 60.0, 2), "runs_per_min": len(r),
                "skipped": sum(1 for t in list(self.skipped) if now - t <= 60), "cameras": len(self.wanted),
                "gestures": self.gestures_sent}
