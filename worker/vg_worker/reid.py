"""Personen-Wiedererkennung über Kameras (C2): OSNet-Embeddings für Personen-Tracks → `POST /api/internal/reid`.

Ablauf (Entwurf c1-d1-c2 §2, Messung reidtest in messungen.md §11):
- Nur Kameras mit `reid_enabled`. Je bestätigtem Personen-Track höchstens alle `REID_INTERVAL_S` (3 s) und höchstens
  `REID_MAX_PER_TRACK` (5) Embeddings, nur wenn die Box im Detect-Bild ≥ `REID_MIN_HEIGHT` px hoch ist, nicht am
  Bildrand abgeschnitten, nicht liegend und nicht von einer anderen Person überdeckt.
- Modell: OSNet x0_25 (MSMT17) als ONNX (onnxruntime, bereits im Image). Standard **CPU** (≈ 6 ms je Ausschnitt,
  0 MB VRAM); `VG_REID_DEVICE=cuda` nutzt die GPU (dann abwechselnd mit der Detektion über `GpuTurn` und bei einer
  GPU-Lease automatisch auf der CPU).
- Eigener Thread, Warteschlange mit fester Größe (voll → verwerfen). Lazy laden, nach `REID_UNLOAD_S` ohne
  ReID-Kamera entladen; Ladefehler → `models.reid = "error: …"`, die Detektion läuft weiter (neuer Versuch nach 5 min).
- **Datenschutz:** Embeddings existieren nur im RAM dieses Prozesses und gehen per HTTP ans Backend (Galerie nur im
  RAM). Nichts davon wird geloggt oder auf Platte geschrieben; nur die Modellgewichte liegen unter /models/reid.
"""
from __future__ import annotations

import hashlib
import logging
import os
import queue
import threading
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Callable

import numpy as np

from . import settings

log = logging.getLogger("vg.reid")

MODEL_DIR = os.path.join(settings.MODELS_DIR, "reid")
_MEAN = np.array([0.485, 0.456, 0.406], np.float32).reshape(1, 1, 3)
_STD = np.array([0.229, 0.224, 0.225], np.float32).reshape(1, 1, 3)
IN_W, IN_H = 128, 256


def _sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def ensure_weights(url: str | None = None, sha: str | None = None) -> str:
    """ONNX-Gewichte nach /models/reid laden (einmalig, Prüfsumme). Liefert den Pfad."""
    url = url or settings.REID_MODEL_URL
    sha = (settings.REID_MODEL_SHA256 if sha is None else sha).strip().lower()
    if os.path.isabs(url) or url.startswith("file:"):
        return url.removeprefix("file:")
    os.makedirs(MODEL_DIR, exist_ok=True)
    path = os.path.join(MODEL_DIR, os.path.basename(url.split("?", 1)[0]) or "reid.onnx")
    if os.path.exists(path) and (not sha or _sha256(path) == sha):
        return path
    import requests
    log.info("downloading ReID weights %s to %s (once)", url, MODEL_DIR)
    tmp = path + ".part"
    with requests.get(url, timeout=60, stream=True) as r:
        r.raise_for_status()
        with open(tmp, "wb") as f:
            for chunk in r.iter_content(1 << 16):
                f.write(chunk)
    got = _sha256(tmp)
    if sha and got != sha:
        os.remove(tmp)
        raise RuntimeError(f"Prüfsumme der ReID-Gewichte falsch ({got[:12]}…)")
    os.replace(tmp, path)
    return path


class ReidModel:
    """OSNet als ONNX-Sitzung. Der Export hat Batch 16 fest – die Eingabe wird beim Laden dynamisch gemacht
    (der Reshape vor dem FC-Teil rechnet mit der Laufzeit-Form, Ergebnis identisch, reidtest)."""

    def __init__(self, device: str | None = None, path: str | None = None):
        import onnx
        import onnxruntime as ort
        try:
            ort.preload_dlls()
        except Exception:  # noqa: BLE001
            pass
        path = path or ensure_weights()
        m = onnx.load(path)
        for vi in list(m.graph.input) + list(m.graph.output) + list(m.graph.value_info):
            dims = vi.type.tensor_type.shape.dim
            if len(dims) and (dims[0].dim_value in (0, 16) or dims[0].dim_param):
                dims[0].dim_param = "N"
        so = ort.SessionOptions()
        so.intra_op_num_threads = max(1, settings.REID_THREADS)
        so.inter_op_num_threads = 1
        so.log_severity_level = 3
        device = (device or settings.REID_DEVICE).lower()
        if device.startswith("cuda") and "CUDAExecutionProvider" in ort.get_available_providers():
            prov = [("CUDAExecutionProvider", {"device_id": 0, "cudnn_conv_algo_search": "HEURISTIC",
                                               "arena_extend_strategy": "kSameAsRequested"}), "CPUExecutionProvider"]
        else:
            prov = ["CPUExecutionProvider"]
        self.sess = ort.InferenceSession(m.SerializeToString(), so, providers=prov)
        self.input = self.sess.get_inputs()[0].name
        self.device = "CUDA" if self.sess.get_providers()[0].startswith("CUDA") else "CPU"
        self.dim = int(self.sess.get_outputs()[0].shape[-1] or 512)
        self.name = os.path.splitext(os.path.basename(path))[0]
        self.embed([np.zeros((256, 100, 3), np.uint8)])          # Aufwärmen

    @staticmethod
    def prep(img: np.ndarray) -> np.ndarray:
        import cv2
        x = cv2.resize(img, (IN_W, IN_H), interpolation=cv2.INTER_LINEAR)[:, :, ::-1].astype(np.float32) / 255.0
        return ((x - _MEAN) / _STD).transpose(2, 0, 1)

    def embed(self, images: list[np.ndarray], flip: bool | None = None) -> np.ndarray:
        """L2-normierte Embeddings (N×512) für BGR-Personenausschnitte."""
        flip = settings.REID_FLIP if flip is None else flip
        x = np.stack([self.prep(im) for im in images]).astype(np.float32)
        if flip:
            x = np.concatenate([x, x[:, :, :, ::-1]], axis=0)
        y = self.sess.run(None, {self.input: np.ascontiguousarray(x)})[0].astype(np.float32)
        if flip:
            n = len(images)
            y = y[:n] + y[n:]
        y /= np.maximum(np.linalg.norm(y, axis=1, keepdims=True), 1e-9)
        return y

    def close(self):
        self.sess = None


def _overlap_of(a, b) -> float:
    ix = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    area = max(1e-12, (a[2] - a[0]) * (a[3] - a[1]))
    return ix * iy / area


@dataclass
class ReidJob:
    camera_id: int
    track_id: int
    ts: float
    crop: np.ndarray
    quality: float


class ReidEngine:
    """ReID-Thread. `reid_fn(body)` = POST /api/internal/reid (blockierend), `get_cam(cid)` → CameraRuntime."""

    def __init__(self, reid_fn: Callable[[dict], dict | None], get_cam: Callable[[int], object], gpu=None):
        self.reid_fn = reid_fn
        self.get_cam = get_cam
        self.gpu = gpu
        self.model: ReidModel | None = None
        self.status = "off"
        self.wanted: set[int] = set()
        self.q: queue.Queue[ReidJob] = queue.Queue(maxsize=32)
        self.tstate: dict[tuple[int, int], list[float]] = {}     # (cid, tid) → [anzahl, letzter Versuch, zuletzt gesehen]
        self.lock = threading.Lock()
        self.http = ThreadPoolExecutor(max_workers=2, thread_name_prefix="reid-http")
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._load_retry_at = 0.0
        self._idle_since = time.time()
        self._paused: str | None = None                          # GPU-Lease: solange auf der CPU rechnen
        self.runs: deque[tuple[float, int, float]] = deque(maxlen=2000)   # (ts, Ausschnitte, ms)
        self.skipped: deque[float] = deque(maxlen=2000)
        self.stats = {"sent": 0, "linked": 0, "new": 0, "errors": 0}

    # ------------------------------------------------------------------ Konfiguration
    def configure(self, cameras: list[dict]):
        wanted = {int(c["id"]) for c in cameras if c.get("reid_enabled")}
        with self.lock:
            self.wanted = wanted
            for key in [k for k in self.tstate if k[0] not in wanted]:
                self.tstate.pop(key, None)
        if wanted and self._thread is None:
            self._thread = threading.Thread(target=self._run, name="reid", daemon=True)
            self._thread.start()
        if wanted and self.status == "off":
            self.status = "loading"
        self._wake.set()

    def wants(self, camera_id: int) -> bool:
        return camera_id in self.wanted

    def stop(self):
        self._stop.set()
        self._wake.set()
        self.http.shutdown(wait=False, cancel_futures=True)

    # GPU-Lease (nur relevant mit VG_REID_DEVICE=cuda): während der Lease auf der CPU rechnen
    def pause_gpu(self, holder: str) -> bool:
        self._paused = holder or "?"
        m = self.model
        if m is not None and m.device == "CUDA":
            self.model = None             # nächster Durchlauf lädt auf der CPU
            m.close()
        return True

    def resume_gpu(self) -> None:
        self._paused = None
        m = self.model
        if m is not None and settings.REID_DEVICE.startswith("cuda") and m.device != "CUDA":
            self.model = None             # nächster Durchlauf lädt wieder auf der GPU
            m.close()

    # ------------------------------------------------------------------ Eingang (aus Pipeline._post)
    def offer(self, cam, ts: float, image: np.ndarray, touched: list) -> int:
        """Personen-Ausschnitte dieses Bildes einreihen (nur geeignete Tracks, gedrosselt). Liefert die Anzahl."""
        if cam.id not in self.wanted or self.status.startswith("error"):
            return 0
        persons = [t for t in touched if t.label == "person" and not t.ended]
        if not persons:
            return 0
        h, w = image.shape[:2]
        n = 0
        for tr in persons:
            key = (cam.id, tr.tid)
            st = self.tstate.get(key)
            if st is None:
                st = self.tstate[key] = [0, 0.0, ts]
            st[2] = ts
            if st[0] >= settings.REID_MAX_PER_TRACK or ts - st[1] < settings.REID_INTERVAL_S:
                continue
            if tr.last_seen < ts - 1e-3 or tr.score < settings.REID_MIN_SCORE:
                continue                  # nicht in diesem Bild detektiert / unsicher
            x1, y1, x2, y2 = tr.bbox
            bh, bw = (y2 - y1) * h, (x2 - x1) * w
            if bh < settings.REID_MIN_HEIGHT or bh < 1.2 * bw:
                continue                  # zu klein bzw. liegend/sitzend breit
            if x1 <= 0.003 or y1 <= 0.003 or x2 >= 0.997 or y2 >= 0.997:
                continue                  # am Bildrand abgeschnitten
            if (tr.attributes or {}).get("posture") == "lying":
                continue
            if any(o is not tr and _overlap_of(tr.bbox, o.bbox) > 0.25 for o in persons):
                continue                  # überdeckt → Embedding wäre gemischt
            X1, Y1 = max(0, int(x1 * w)), max(0, int(y1 * h))
            X2, Y2 = min(w, int(round(x2 * w))), min(h, int(round(y2 * h)))
            crop = np.ascontiguousarray(image[Y1:Y2, X1:X2])
            if crop.size == 0:
                continue
            st[1] = ts
            q = float(tr.score) * min(1.0, bh / 256.0)
            try:
                self.q.put_nowait(ReidJob(cam.id, tr.tid, ts, crop, round(q, 4)))
                st[0] += 1
                n += 1
            except queue.Full:
                self.skipped.append(time.time())
        if n:
            self._wake.set()
        return n

    # ------------------------------------------------------------------ Thread
    def _ensure_model(self) -> bool:
        if self.model is not None:
            return True
        if time.time() < self._load_retry_at:
            return False
        self.status = "loading"
        t0 = time.time()
        try:
            dev = "cpu" if self._paused else settings.REID_DEVICE
            self.model = ReidModel(dev)
            self.status = f"ready ({self.model.name}, {self.model.device})"
            log.info("ReID model %s ready on %s in %.2fs", self.model.name, self.model.device, time.time() - t0)
            return True
        except Exception as e:  # noqa: BLE001
            self.model = None
            self.status = f"error: {type(e).__name__}: {e}"[:300]
            self._load_retry_at = time.time() + 300
            log.error("ReID model failed to load: %s", e)
            return False

    def _drain(self) -> list[ReidJob]:
        jobs: list[ReidJob] = []
        while len(jobs) < settings.REID_MAX_BATCH:
            try:
                jobs.append(self.q.get_nowait())
            except queue.Empty:
                break
        return jobs

    def _run(self):
        while not self._stop.is_set():
            if not self.wanted:
                self._drain()
                if self.model is not None and time.time() - self._idle_since > settings.REID_UNLOAD_S:
                    log.info("no ReID camera for %.0fs – unloading ReID model", settings.REID_UNLOAD_S)
                    m, self.model = self.model, None
                    m.close()
                if self.model is None:
                    self.status = "off"
                self._wake.wait(2.0)
                self._wake.clear()
                continue
            self._idle_since = time.time()
            if not self._ensure_model():
                self._drain()
                self._wake.wait(2.0)
                self._wake.clear()
                continue
            jobs = self._drain()
            if not jobs:
                self._wake.wait(1.0)
                self._wake.clear()
                self._gc()
                continue
            model = self.model
            if model is None:
                continue
            try:
                t0 = time.perf_counter()
                if model.device == "CUDA" and self.gpu is not None:
                    with self.gpu.inventory_slot(0.25):
                        emb = model.embed([j.crop for j in jobs])
                else:
                    emb = model.embed([j.crop for j in jobs])
                ms = (time.perf_counter() - t0) * 1000.0
            except Exception as e:  # noqa: BLE001
                log.warning("ReID inference failed: %s", e)
                time.sleep(0.5)
                continue
            self.runs.append((time.time(), len(jobs), ms))
            for j, e in zip(jobs, emb):
                try:
                    self.http.submit(self._send, j, e)
                except RuntimeError:      # Executor beendet
                    return

    def _send(self, job: ReidJob, emb: np.ndarray):
        body = {"camera_id": job.camera_id, "track_id": job.track_id,
                "embedding": [round(float(v), 5) for v in emb], "quality": job.quality, "ts": round(job.ts, 3)}
        try:
            r = self.reid_fn(body)
        except Exception as e:  # noqa: BLE001
            self.stats["errors"] += 1
            log.warning("reid call failed (camera %s, track %s): %s", job.camera_id, job.track_id, e)
            with self.lock:
                st = self.tstate.get((job.camera_id, job.track_id))
                if st is not None:
                    st[0] = max(0, st[0] - 1)
            return
        self.stats["sent"] += 1
        if not r or not r.get("presence_key"):
            return
        if r.get("is_new"):
            self.stats["new"] += 1
        else:
            self.stats["linked"] += 1
        cam = self.get_cam(job.camera_id)
        if cam is not None:
            cam.tracker.set_reid(job.track_id, str(r["presence_key"]), r.get("identity"))

    def _gc(self):
        now = time.time()
        with self.lock:
            for key in [k for k, v in self.tstate.items() if now - v[2] > 120]:
                self.tstate.pop(key, None)

    # ------------------------------------------------------------------ Status
    def status_text(self) -> str:
        if self._paused and self.status.startswith("ready"):
            return f"{self.status} – CPU während GPU-Lease {self._paused}"
        return self.status

    def perf(self) -> dict:
        now = time.time()
        r = [(n, ms) for t, n, ms in list(self.runs) if now - t <= 60]
        return {"embeds_per_min": sum(n for n, _ in r), "runs_per_min": len(r),
                "ms": round(sum(ms for _, ms in r) / len(r), 1) if r else 0.0,
                "skipped": sum(1 for t in list(self.skipped) if now - t <= 60), "cameras": len(self.wanted),
                **self.stats}
