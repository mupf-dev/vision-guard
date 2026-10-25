"""Camera runtime management + central batched inference loop."""
from __future__ import annotations

import logging
import threading
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from typing import Callable

from . import settings
from .health import HealthMonitor
from .identify import PETS, VEHICLES, Identifier, Job
from .ingest import FrameSource
from .inventory import GpuTurn, InventoryEngine
from .recorder import RingRecorder
from .tracking import CameraTracker
from .util import crop_norm, draw_objects, write_jpeg

log = logging.getLogger("vg.pipeline")

Publish = Callable[[str, dict, int, bool], None]   # topic, payload, qos, retain

# config keys that require restarting ffmpeg processes (Inventar-Felder bewusst NICHT – nur update_cfg)
_STREAM_KEYS = ("detect_url_rtsp", "main_rtsp", "detect_fps")


class CameraRuntime:
    def __init__(self, cfg: dict):
        self.id = int(cfg["id"])
        self.cfg = cfg
        name = f"cam_{self.id}"
        self.source = FrameSource(name, cfg["detect_url_rtsp"], float(cfg.get("detect_fps") or 5))
        self.recorder = RingRecorder(name, cfg.get("main_rtsp") or cfg["detect_url_rtsp"]) if settings.RECORDER_ENABLED else None
        self.tracker = CameraTracker(self.id, float(cfg.get("detect_fps") or 5))
        self.tracker.configure(cfg)
        self.last_seq = 0
        self.busy = False                       # post-processing of the previous frame still running
        self.post = ThreadPoolExecutor(max_workers=1, thread_name_prefix=f"post-{self.id}")
        self.done_times: deque[float] = deque(maxlen=64)
        self.inference_ms = 0.0
        self.last_ts = 0.0

    def start(self):
        self.source.start()
        if self.recorder:
            self.recorder.start()

    def stop(self):
        self.source.stop()
        if self.recorder:
            self.recorder.stop()
        self.post.shutdown(wait=False, cancel_futures=True)

    def join(self):
        self.source.join()
        if self.recorder:
            self.recorder.join()

    def update_cfg(self, cfg: dict):
        self.cfg = cfg
        self.tracker.configure(cfg)

    def fps(self) -> float:
        now = time.time()
        t = [x for x in self.done_times if now - x <= 5.0]
        if len(t) < 2:
            return 0.0
        return (len(t) - 1) / max(1e-3, t[-1] - t[0])

    def status(self) -> dict:
        st = self.source.state
        fps = self.fps()
        if st == "online" and fps == 0.0 and time.time() - self.last_ts > 10:
            st = "starting"
        return {"state": st, "fps": round(fps, 2), "width": self.source.width or None,
                "height": self.source.height or None, "inference_ms": round(self.inference_ms, 1),
                "ts": round(time.time(), 3)}


class Pipeline:
    def __init__(self, publish: Publish, identify_fn: Callable[[dict], dict | None]):
        self.publish = publish
        self.cams: dict[int, CameraRuntime] = {}
        self.lock = threading.RLock()
        self._stop = threading.Event()
        self.detector = None
        self.detector_status = "loading"
        self.identifier = Identifier(identify_fn, self._on_identity, self._on_pending)
        self.batch_hist: deque[tuple[float, int, float]] = deque(maxlen=200)   # (ts, batch size, ms)
        self.frames_total = 0
        self._wake = threading.Event()
        # Inventar (Gegenstände): eigener Thread, GPU abwechselnd mit _infer_loop
        self.gpu = GpuTurn()
        # W2 (B4): Exemplar-Abgleich mit dem geteilten DINOv2 der Tiererkennung + /api/internal/identify
        self.inventory = InventoryEngine(publish, self.gpu, lambda cid: self.cams.get(cid),
                                         pet_model=self.identifier.pet, identify_fn=identify_fn)
        # Kamera-Gesundheit (C5): eigener CPU-Thread, Prüfbilder aus dem Ingest
        self.health = HealthMonitor(publish, self._cams_snapshot)
        # W2 (K2): Embedding-Dienst vg/internal/embed mit dem geteilten DINOv2 der Tiererkennung
        from .embed import EmbedService
        self.embed = EmbedService(publish, self.identifier.pet)
        # W2 (C1/D1): Pose-Stufe (Haltung, Gesten) nur auf Kameras mit pose_enabled/gesture_enabled
        from .pose import PoseEngine
        self.pose = PoseEngine(publish, self.gpu, lambda cid: self.cams.get(cid))
        self.lying_grace_s = settings.LYING_GRACE_S
        # W3 (K3): Frame-Tap (Abnehmer je Kamera: A3 Sensoren, B1 Zeitmaschine) und GPU-Lease (A2 vlm)
        from .frametap import FrameTapHub
        from .gpu_lease import GpuLeaseManager
        self.taps = FrameTapHub()
        self.lease = GpuLeaseManager(publish)
        self.lease.register("inventory", self.inventory.pause_gpu, self.inventory.resume_gpu)
        # W3: Fach-Module (Gerüste aus K3): Sensoren (A3), Zeitmaschine (B1)
        from .history import HistoryEngine
        from .sensors import SensorEngine
        self.sensors = SensorEngine(publish, self.taps, self.identifier.pet)
        self.history = HistoryEngine(publish, self.taps, self.gpu)
        self.lease.register("history", self.history.pause_gpu, self.history.resume_gpu)
        self.embed.siglip = self.history          # B1: vg/internal/embed mit model "siglip2" (lädt bei Bedarf)

    # ------------------------------------------------------------------ lifecycle
    def load_detector(self):
        from .detector import Detector
        try:
            self.detector = Detector()
            self.detector_status = f"{self.detector.model_name} ({'CUDA fp16' if self.detector.half else self.detector.device})"
        except Exception as e:
            self.detector_status = f"error: {e}"
            raise

    def start(self):
        threading.Thread(target=self._infer_loop, name="infer", daemon=True).start()
        threading.Thread(target=self._expire_loop, name="expire", daemon=True).start()
        self.health.start()
        self.lease.start()

    def _cams_snapshot(self) -> dict[int, CameraRuntime]:
        with self.lock:
            return dict(self.cams)

    def stop(self):
        self._stop.set()
        self.inventory.stop()
        self.health.stop()
        self.embed.stop()
        self.pose.stop()
        self.sensors.stop()
        self.history.stop()
        self.taps.stop()
        self.lease.stop()
        with self.lock:
            cams = list(self.cams.values())
            self.cams.clear()
        for c in cams:
            for m in c.tracker.expire(time.time(), force=True):
                self.publish(f"vg/internal/track/{c.id}", m, 1, False)
            c.stop()
        for c in cams:
            c.join()
        self.identifier.stop()

    def apply_config(self, cameras: list[dict]):
        """Start/stop/restart only cameras whose configuration changed."""
        wanted = {int(c["id"]): c for c in cameras if c.get("enabled", True) and c.get("detect_url_rtsp")}
        stopped = []
        with self.lock:
            for cid in list(self.cams):
                if cid not in wanted:
                    log.info("camera %s removed/disabled", cid)
                    stopped.append(self.cams.pop(cid))
            for cid, cfg in wanted.items():
                cur = self.cams.get(cid)
                if cur is not None and any(cur.cfg.get(k) != cfg.get(k) for k in _STREAM_KEYS):
                    log.info("camera %s stream settings changed – restarting", cid)
                    stopped.append(self.cams.pop(cid))
                    cur = None
                if cur is None:
                    log.info("camera %s (%s) start: %s @ %s fps", cid, cfg.get("name"), cfg["detect_url_rtsp"], cfg.get("detect_fps"))
                    rt = CameraRuntime(cfg)
                    rt.tracker.lying_grace_s = self.lying_grace_s
                    self.cams[cid] = rt
                    rt.start()
                else:
                    cur.update_cfg(cfg)
        for c in stopped:
            for m in c.tracker.expire(time.time(), force=True):
                self.publish(f"vg/internal/track/{c.id}", m, 1, False)
            c.stop()
            self.publish(f"vg/internal/status/{c.id}", {"state": "offline", "fps": 0.0, "width": None, "height": None,
                                                         "inference_ms": None, "ts": round(time.time(), 3)}, 0, True)
        for c in stopped:
            threading.Thread(target=c.join, daemon=True).start()
        self.identifier.needed(list(wanted.values()))
        try:
            self.inventory.configure(list(wanted.values()))
        except Exception:  # noqa: BLE001
            log.exception("inventory configuration failed")
        try:
            self.pose.configure(list(wanted.values()))
        except Exception:  # noqa: BLE001
            log.exception("pose configuration failed")
        for name, mod in (("sensors", self.sensors), ("history", self.history)):   # W3
            try:
                mod.configure(list(wanted.values()))
            except Exception:  # noqa: BLE001
                log.exception("%s configuration failed", name)

    def set_settings(self, s: dict | None):
        """Globale Settings aus GET /api/internal/settings (Inventar: confirm_n/miss_m)."""
        self.inventory.set_settings(s)
        self.pose.set_settings(s)
        try:
            self.history.set_settings(s)          # W3 (B1)
        except Exception:  # noqa: BLE001
            log.exception("history settings failed")
        if s and s.get("fall_track_grace_s") is not None:
            try:
                self.lying_grace_s = max(0.0, min(300.0, float(s["fall_track_grace_s"])))
            except (TypeError, ValueError):
                pass
            with self.lock:
                for c in self.cams.values():
                    c.tracker.lying_grace_s = self.lying_grace_s

    # ------------------------------------------------------------------ inference
    def _infer_loop(self):
        rr = 0
        while not self._stop.is_set():
            with self.lock:
                cams = list(self.cams.values())
            if self.detector is None or not cams:
                time.sleep(0.1)
                continue
            # round-robin start so that no camera starves when the batch is capped
            rr = (rr + 1) % len(cams)
            cams = cams[rr:] + cams[:rr]
            batch = []
            for c in cams:
                if c.busy:
                    continue
                f = c.source.latest()
                if f is None or f.seq <= c.last_seq:
                    continue
                batch.append((c, f))
                if len(batch) >= settings.DETECT_MAX_BATCH:
                    break
            if not batch:
                time.sleep(0.005)
                continue
            try:
                with self.gpu.lock:
                    results = self.detector.detect([f.image for _, f in batch])
            except Exception:
                log.exception("detection failed")
                time.sleep(0.5)
                continue
            finally:
                self.gpu.main_batch_done()
            ms = self.detector.last_ms
            now = time.time()
            self.batch_hist.append((now, len(batch), ms))
            self.frames_total += len(batch)
            for (c, f), dets in zip(batch, results):
                c.last_seq = f.seq
                c.inference_ms = ms if c.inference_ms == 0 else 0.8 * c.inference_ms + 0.2 * ms
                c.busy = True
                try:
                    c.post.submit(self._post, c, f, dets)
                except RuntimeError:     # executor shut down (camera removed)
                    c.busy = False

    def _post(self, c: CameraRuntime, f, dets):
        try:
            det_msg, msgs, touched = c.tracker.process(f.ts, f.image, dets)
            c.done_times.append(time.time())
            c.last_ts = f.ts
            self.publish(f"vg/internal/detections/{c.id}", det_msg, 0, False)
            persons = None
            if self.inventory.wants_persons(c.id) or self.taps.active():
                # rohe Detektionen (unabhängig vom Label-Filter der Kamera) – Personen verdecken Gegenstände/Bereiche
                persons = [d[2] for d in dets if d[0] == "person" and d[1] >= 0.3]
            if self.inventory.wants_persons(c.id):
                self.inventory.note_persons(c.id, f.ts, persons)
            if self.taps.active():      # W3: Frame-Tap (gedrosselt je Abnehmer/Kamera, verwirft statt queuen)
                self.taps.offer(c, f.ts, f.image, det_msg, persons)
            for m in msgs:
                self.publish(f"vg/internal/track/{c.id}", m, 1, False)
            if self.pose.wants(c.id):
                self.pose.offer(c, f.ts, f.image)
            self._dispatch_identification(c, f, touched)
        except Exception:
            log.exception("post-processing camera %s failed", c.id)
        finally:
            c.busy = False

    def _dispatch_identification(self, c: CameraRuntime, f, touched):
        cfg = c.cfg
        now = f.ts
        for tr in touched:
            st = tr.ids
            label = tr.label
            if cfg.get("face_recognition") and label == "person":
                if st["face_sent"] < settings.FACE_MAX_IDENTIFY and now - st["face_last"] >= settings.FACE_INTERVAL_S \
                        and tr.score >= float(cfg.get("min_score") or 0.5) * 0.8:
                    # upper part of the person box (head + shoulders)
                    crop = crop_norm(f.image, tr.bbox, margin=0.05, region=(0.0, 0.45))
                    if crop is not None and crop.shape[0] >= 40 and crop.shape[1] >= 24:
                        st["face_last"] = now
                        self.identifier.submit("face", Job(c.id, tr.tid, crop, now, {"track_state": st}))
            elif cfg.get("plate_recognition") and label in VEHICLES:
                if len(st["plate_sent"]) < 3 and st["plate_tries"] < 60 and now - st["plate_last"] >= settings.PLATE_INTERVAL_S:
                    crop = crop_norm(f.image, tr.bbox, margin=0.03)
                    if crop is not None and crop.shape[0] >= 48 and crop.shape[1] >= 64:
                        st["plate_last"] = now
                        st["plate_tries"] += 1
                        self.identifier.submit("plate", Job(c.id, tr.tid, crop, now, {"track_state": st}))
            elif cfg.get("pet_recognition") and label in PETS:
                if not st["pet_done"] and now - tr.first_seen >= settings.PET_DELAY_S and tr.best_crop is not None:
                    st["pet_done"] = True
                    self.identifier.submit("pet", Job(c.id, tr.tid, tr.best_crop, now,
                                                      {"track_state": st, "crop_path": tr.crop_path,
                                                       "quality": round(tr.best_quality, 5)}))

    def _on_identity(self, camera_id: int, track_id: int, identity, attributes):
        c = self.cams.get(camera_id)
        if c is not None:
            c.tracker.set_identity(track_id, identity, attributes)

    def _on_pending(self, camera_id: int, track_id: int, delta: int):
        c = self.cams.get(camera_id)
        if c is not None:
            c.tracker.change_pending(track_id, delta)

    def _expire_loop(self):
        while not self._stop.wait(0.25):
            with self.lock:
                cams = list(self.cams.values())
            now = time.time()
            for c in cams:
                try:
                    for m in c.tracker.expire(now):
                        self.publish(f"vg/internal/track/{c.id}", m, 1, False)
                except Exception:
                    log.exception("expire camera %s failed", c.id)

    # ------------------------------------------------------------------ commands
    def command(self, camera_id: int, msg: dict):
        action = msg.get("action")
        rid = msg.get("request_id")
        path = msg.get("path") or ""

        def reply(ok: bool, error: str | None = None):
            self.publish("vg/internal/cmd_result", {"request_id": rid, "action": action, "ok": ok,
                                                    "path": path if ok else path or None, "error": error}, 1, False)

        c = self.cams.get(camera_id)
        if c is None:
            return reply(False, f"camera {camera_id} not active in worker")
        if action == "health_reference":
            try:
                path = self.health.set_reference(c, str(msg.get("mode") or "auto"))
                reply(True)
            except Exception as e:  # noqa: BLE001
                log.warning("health reference camera %s failed: %s", camera_id, e)
                reply(False, str(e))
            return
        if not path:
            return reply(False, "missing path")
        if action == "snapshot":
            try:
                f = c.source.latest()
                if f is None:
                    return reply(False, "no frame available (camera offline?)")
                img = draw_objects(f.image, c.tracker.snapshot_objects())
                write_jpeg(path, img)
                reply(True)
            except Exception as e:  # noqa: BLE001
                log.warning("snapshot failed: %s", e)
                reply(False, str(e))
        elif action == "clip":
            if c.recorder is None:
                return reply(False, "ring buffer disabled")
            pre = max(0.0, min(float(msg.get("pre_s") or 0), settings.BUFFER_KEEP_S - settings.BUFFER_SEGMENT_S))
            post = max(0.0, min(float(msg.get("post_s") or 0), 120.0))
            t0 = time.time()

            def run():
                try:
                    end = t0 + post
                    wait = end - time.time()
                    if wait > 0:
                        time.sleep(wait)
                    c.recorder.export_clip(path, t0 - pre, end)
                    reply(True)
                except Exception as e:  # noqa: BLE001
                    log.warning("clip failed: %s", e)
                    reply(False, str(e))
            threading.Thread(target=run, name=f"clip-{camera_id}", daemon=True).start()
        else:
            reply(False, f"unknown action {action!r}")

    # ------------------------------------------------------------------ status
    def statuses(self) -> dict[int, dict]:
        with self.lock:
            return {cid: c.status() for cid, c in self.cams.items()}

    def models_status(self) -> dict:
        d = {"detector": self.detector_status}
        d.update(self.identifier.status())
        d["inventory"] = self.inventory.status_text()
        d["embed"] = self.embed.status()
        d["pose"] = self.pose.status_text()
        d["history"] = self.history.status_text()
        return d

    def gpu_lease(self) -> dict | None:
        """W3: aktive GPU-Lease für vg/internal/system.gpu_lease."""
        return self.lease.status()

    def perf(self) -> dict:
        now = time.time()
        h = [x for x in self.batch_hist if now - x[0] <= 10]
        if not h:
            return {"fps": 0.0, "batch_ms": 0.0, "avg_batch": 0.0, "inventory": self.inventory.perf(),
                    "health": self.health.perf(), "embed": self.embed.perf(), "pose": self.pose.perf(),
                    "taps": self.taps.perf(), "sensors": self.sensors.perf(), "history": self.history.perf()}
        span = max(1e-3, now - h[0][0])
        return {"fps": round(sum(b for _, b, _ in h) / span, 2),
                "batch_ms": round(sum(m for *_, m in h) / len(h), 2),
                "avg_batch": round(sum(b for _, b, _ in h) / len(h), 2),
                "inventory": self.inventory.perf(), "health": self.health.perf(), "embed": self.embed.perf(),
                "pose": self.pose.perf(), "taps": self.taps.perf(), "sensors": self.sensors.perf(),
                "history": self.history.perf()}
