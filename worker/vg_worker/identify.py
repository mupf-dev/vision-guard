"""Identification models (faces, license plates, pets) – each lazily loaded on its own thread.

Every model runs in a dedicated thread with a small bounded job queue (jobs are dropped if the queue
is full – detection must never be slowed down by identification). Failures are isolated: a model that
fails to load reports its error in `status` and simply ignores jobs.
"""
from __future__ import annotations

import logging
import os
import queue
import re
import threading
import time
import traceback
from collections.abc import Callable
from dataclasses import dataclass

import numpy as np

from . import settings
from .util import new_media_path, write_jpeg

log = logging.getLogger("vg.identify")

VEHICLES = {"car", "truck", "bus", "motorcycle"}
PETS = {"dog", "cat", "horse", "bird"}


@dataclass
class Job:
    camera_id: int
    track_id: int
    image: np.ndarray          # BGR crop
    ts: float
    extra: dict


class ModelWorker:
    """Base: thread + queue + lazy load + status."""
    key = "model"

    def __init__(self, on_result: Callable[[str, Job, dict], None], qsize: int = 32):
        self.on_result = on_result
        self.q: queue.Queue[Job] = queue.Queue(maxsize=qsize)
        self.status = "idle"           # idle | loading | ready (...) | error: ...
        self.model = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.jobs_done = 0
        self.dropped = 0
        self.last_ms = 0.0
        self.avg_ms = 0.0

    def ensure_started(self):
        if self._thread is None:
            self._thread = threading.Thread(target=self._run, name=f"id-{self.key}", daemon=True)
            self._thread.start()

    def submit(self, job: Job) -> bool:
        if self.status.startswith("error"):
            return False
        self.ensure_started()
        try:
            self.q.put_nowait(job)
            return True
        except queue.Full:
            self.dropped += 1
            return False

    def stop(self):
        self._stop.set()

    def load(self):  # pragma: no cover - abstract
        raise NotImplementedError

    def process(self, job: Job) -> dict | None:  # pragma: no cover - abstract
        raise NotImplementedError

    def _run(self):
        self.status = "loading"
        t0 = time.time()
        try:
            self.load()
        except Exception as e:  # noqa: BLE001
            self.status = f"error: {type(e).__name__}: {e}"[:300]
            log.error("%s model failed to load: %s\n%s", self.key, e, traceback.format_exc())
            # drain queue forever so producers never block
            while not self._stop.is_set():
                try:
                    j = self.q.get(timeout=1.0)
                    self.on_result(self.key, j, {"error": self.status})
                except queue.Empty:
                    pass
            return
        log.info("%s model ready (%s) in %.1fs", self.key, self.status, time.time() - t0)
        while not self._stop.is_set():
            try:
                job = self.q.get(timeout=1.0)
            except queue.Empty:
                continue
            t1 = time.perf_counter()
            try:
                res = self.process(job)
            except Exception as e:  # noqa: BLE001
                log.warning("%s inference failed: %s", self.key, e)
                res = {"error": str(e)}
            self.last_ms = (time.perf_counter() - t1) * 1000
            self.avg_ms = self.last_ms if self.avg_ms == 0 else 0.95 * self.avg_ms + 0.05 * self.last_ms
            self.jobs_done += 1
            try:
                self.on_result(self.key, job, res or {})
            except Exception:  # noqa: BLE001
                log.exception("%s result handler failed", self.key)


def _ort_providers():
    import onnxruntime as ort
    av = ort.get_available_providers()
    if "CUDAExecutionProvider" in av:
        return [("CUDAExecutionProvider", {"device_id": 0, "cudnn_conv_algo_search": "HEURISTIC",
                                           "arena_extend_strategy": "kSameAsRequested"}),
                "CPUExecutionProvider"]
    return ["CPUExecutionProvider"]


def _providers_used(sess) -> str:
    try:
        p = sess.get_providers()[0]
        return "CUDA" if p.startswith("CUDA") else ("TensorRT" if p.startswith("Tensorrt") else "CPU")
    except Exception:  # noqa: BLE001
        return "?"


# ============================================================================ faces
class FaceModel(ModelWorker):
    key = "face"

    def load(self):
        import onnxruntime as ort
        try:
            ort.preload_dlls()
        except Exception:  # noqa: BLE001
            pass
        from insightface.app import FaceAnalysis
        root = os.path.join(settings.MODELS_DIR, "insightface")
        os.makedirs(root, exist_ok=True)
        app = FaceAnalysis(name=settings.FACE_MODEL, root=root, allowed_modules=["detection", "recognition"],
                           providers=_ort_providers())
        app.prepare(ctx_id=0, det_thresh=0.5, det_size=(320, 320))
        self.model = app
        self.det = app.det_model
        self.rec = app.models["recognition"]
        # warm-up
        self.det.detect(np.zeros((320, 240, 3), np.uint8))
        self.status = f"{settings.FACE_MODEL} ({_providers_used(self.det.session)})"

    def process(self, job: Job) -> dict | None:
        """Bestes Gesicht im Kopf-Ausschnitt: Merkmal (512-d, L2), Qualität und Merkmale (Paket B, ID.F).

        `job.extra["px_scale"]`: Originalpixel je Ausschnitt-Pixel (> 1, wenn ein Vollbild-Ausschnitt verkleinert
        wurde); `size` ist die kürzere Gesichtsseite in Pixeln des Bildes, aus dem geschnitten wurde."""
        import cv2
        from insightface.utils import face_align

        from . import face_quality as fq
        img = job.image
        h, w = img.shape[:2]
        px_scale = float(job.extra.get("px_scale") or 1.0)
        # tiny crops: upscale so that faces reach the detector's useful range
        scale = 1.0
        if max(h, w) < 240:
            scale = 240.0 / max(h, w)
            img = cv2.resize(img, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_CUBIC)
        bboxes, kpss = self.det.detect(img, max_num=0)
        if bboxes is None or len(bboxes) == 0 or kpss is None:
            return None
        best = None
        for i in range(len(bboxes)):
            x1, y1, x2, y2, sc = bboxes[i]
            size = min(x2 - x1, y2 - y1) / scale * px_scale          # Originalpixel
            if sc < settings.FACE_MIN_DET or size < settings.FACE_MIN_SIZE * 0.5:
                continue
            yaw, pitch = fq.head_pose(kpss[i])
            # Vorauswahl ohne Schärfe (die braucht das ausgerichtete Bild)
            q0 = fq.quality(float(sc), size, yaw, pitch, None)
            if best is None or q0 > best[0]:
                best = (q0, i, size, yaw, pitch)
        if best is None:
            return None
        _, i, size, yaw, pitch = best
        if size < settings.FACE_MIN_SIZE:
            return {"face_quality": best[0], "too_small": True}
        aimg = face_align.norm_crop(img, landmark=kpss[i], image_size=self.rec.input_size[0])
        sharp = fq.sharpness(aimg)
        det = float(bboxes[i, 4])
        q = fq.quality(det, size, yaw, pitch, sharp)
        emb = np.asarray(self.rec.get_feat(aimg), dtype=np.float32).ravel()
        n = float(np.linalg.norm(emb))
        if n <= 0:
            return None
        emb = emb / n
        x1, y1, x2, y2 = bboxes[i, :4]
        m = 0.25 * (x2 - x1)
        H, W = img.shape[:2]
        fc = img[int(max(0, y1 - m)):int(min(H, y2 + m)), int(max(0, x1 - m)):int(min(W, x2 + m))]
        if fc.size and max(fc.shape[:2]) > settings.FACE_SAMPLE_MAX_PX:
            s = settings.FACE_SAMPLE_MAX_PX / max(fc.shape[:2])
            fc = cv2.resize(fc, (max(1, int(fc.shape[1] * s)), max(1, int(fc.shape[0] * s))),
                            interpolation=cv2.INTER_AREA)
        return {"embedding": emb, "quality": q, "face_crop": np.ascontiguousarray(fc), "size": size,
                "yaw": yaw, "pitch": pitch, "sharpness": sharp, "det_score": round(det, 3),
                "day_night": fq.day_night(job.image)}


# ============================================================================ plates
_PLATE_RE = re.compile(r"[^A-Z0-9]")


class PlateModel(ModelWorker):
    key = "plate"

    def load(self):
        cache = os.path.join(settings.MODELS_DIR, "alpr")
        os.makedirs(cache, exist_ok=True)
        # both libraries keep a module level MODEL_CACHE_DIR (~/.cache/...) – redirect to /models
        import pathlib
        try:
            import open_image_models.detection.core.hub as oim_hub
            oim_hub.MODEL_CACHE_DIR = pathlib.Path(cache) / "open-image-models"
        except Exception:  # noqa: BLE001
            pass
        try:
            import fast_plate_ocr.inference.hub as fpo_hub
            fpo_hub.MODEL_CACHE_DIR = pathlib.Path(cache) / "fast-plate-ocr"
        except Exception:  # noqa: BLE001
            pass
        from fast_alpr import ALPR
        prov = _ort_providers()
        self.model = ALPR(detector_model=settings.PLATE_DETECTOR, detector_conf_thresh=0.4,
                          detector_providers=prov, ocr_model=settings.PLATE_OCR, ocr_providers=prov,
                          ocr_device="cuda" if len(prov) > 1 else "cpu")
        self.model.predict(np.zeros((256, 384, 3), np.uint8))
        try:
            sess = self.model.detector.detector.detector.model  # best effort for status
            prov_s = _providers_used(sess)
        except Exception:  # noqa: BLE001
            prov_s = "CUDA" if len(prov) > 1 else "CPU"
        self.status = f"fast-alpr {settings.PLATE_DETECTOR} + {settings.PLATE_OCR} ({prov_s})"

    def process(self, job: Job) -> dict | None:
        res = self.model.predict(job.image)
        best = None
        for r in res:
            if r.ocr is None or not r.ocr.text:
                continue
            text = _PLATE_RE.sub("", r.ocr.text.upper())
            conf = r.ocr.confidence
            conf = float(np.mean(conf)) if isinstance(conf, (list, tuple)) and conf else float(conf or 0)
            if len(text) < 4 or conf < settings.PLATE_MIN_CONF:
                continue
            q = conf * float(r.detection.confidence)
            if best is None or q > best[0]:
                bb = r.detection.bounding_box
                H, W = job.image.shape[:2]
                pc = job.image[max(0, bb.y1 - 4):min(H, bb.y2 + 4), max(0, bb.x1 - 6):min(W, bb.x2 + 6)]
                best = (q, text, conf, np.ascontiguousarray(pc))
        if best is None:
            return None
        return {"plate_text": best[1], "quality": round(float(best[0]), 4), "ocr_conf": float(best[2]), "plate_crop": best[3]}


# ============================================================================ pets
class PetModel(ModelWorker):
    """DINOv2 (timm) – Tier-Wiedererkennung; seit W2 auch geteiltes Modell des Embedding-Dienstes (embed.py)."""
    key = "pet"

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.infer_lock = threading.Lock()

    def load(self):
        import timm
        import torch
        os.makedirs(os.path.join(settings.MODELS_DIR, "hf"), exist_ok=True)
        os.environ.setdefault("HF_HOME", os.path.join(settings.MODELS_DIR, "hf"))
        self.torch = torch
        self.device = "cuda" if torch.cuda.is_available() else "cpu"
        name = settings.PET_MODEL
        kw = {"img_size": 224} if "dinov2" in name else {}
        m = timm.create_model(name, pretrained=True, num_classes=0, **kw)
        m.eval().to(self.device)
        if self.device == "cuda":
            m.half()
        self.model = m
        cfg = timm.data.resolve_data_config({}, model=m)
        self.mean = torch.tensor(cfg["mean"], device=self.device).view(1, 3, 1, 1)
        self.std = torch.tensor(cfg["std"], device=self.device).view(1, 3, 1, 1)
        self.size = 224
        self.process(Job(0, 0, np.zeros((100, 100, 3), np.uint8), 0, {}))
        self.status = f"{name} ({'CUDA fp16' if self.device == 'cuda' else 'CPU'})"

    def _prep(self, img: np.ndarray) -> np.ndarray:
        import cv2
        h, w = img.shape[:2]
        # pad to square (keeps aspect), then resize
        s = max(h, w)
        canvas = np.zeros((s, s, 3), np.uint8)
        canvas[(s - h) // 2:(s - h) // 2 + h, (s - w) // 2:(s - w) // 2 + w] = img
        return cv2.resize(canvas, (self.size, self.size), interpolation=cv2.INTER_AREA)[:, :, ::-1].copy()

    def embed_batch(self, images: list[np.ndarray]) -> np.ndarray:
        """L2-normierte Embeddings (N×384) für BGR-Bilder. Thread-sicher (geteilt mit embed.EmbedService, W2);
        nur aufrufen, wenn das Modell geladen ist (`ready()`)."""
        torch = self.torch
        x = np.stack([self._prep(im) for im in images])
        with self.infer_lock, torch.inference_mode():
            t = torch.from_numpy(x).to(self.device).permute(0, 3, 1, 2).float() / 255.0
            t = (t - self.mean) / self.std
            if self.device == "cuda":
                t = t.half()
            f = self.model(t).float()
            f = torch.nn.functional.normalize(f, dim=1)
        return f.cpu().numpy().astype(np.float32)

    def ready(self) -> bool:
        return self.model is not None and not self.status.startswith(("error", "loading", "idle"))

    def process(self, job: Job) -> dict | None:
        emb = self.embed_batch([job.image])[0]
        return {"embedding": emb, "quality": float(job.extra.get("quality", 0.0))}


# ============================================================================ manager
class Identifier:
    """Routes identification jobs and turns model results into /identify calls."""

    def __init__(self, identify_fn: Callable[[dict], dict | None], on_identity: Callable[[int, int, dict | None, dict | None], None],
                 on_pending: Callable[[int, int, int], None]):
        self.identify_fn = identify_fn          # POST /api/internal/identify (blocking)
        self.on_identity = on_identity          # (camera_id, track_id, identity|None, attributes|None, clear=False)
        self.on_pending = on_pending            # (camera_id, track_id, delta)
        self.face = FaceModel(self._on_result)
        self.plate = PlateModel(self._on_result)
        self.pet = PetModel(self._on_result)
        self.models = {"face": self.face, "plate": self.plate, "pet": self.pet}
        from concurrent.futures import ThreadPoolExecutor
        self.http = ThreadPoolExecutor(max_workers=4, thread_name_prefix="identify-http")
        self.stats = {"face_found": 0, "face_identify": 0, "plate_reads": 0, "plate_identify": 0,
                      "pet_identify": 0, "identify_errors": 0}
        # Identitäten 07 (Paket B): Gesichter je Track sammeln, vorläufig/endgültig zuordnen
        from .face_track import FaceCollector
        self.faces = FaceCollector(self)

    def needed(self, cameras: list[dict]):
        """Start (load) models required by the current camera config."""
        if any(c.get("face_recognition") for c in cameras):
            self.face.ensure_started()
        if any(c.get("plate_recognition") for c in cameras):
            self.plate.ensure_started()
        if any(c.get("pet_recognition") for c in cameras):
            self.pet.ensure_started()

    def status(self) -> dict:
        return {k: m.status if m._thread else "not loaded (not needed)" for k, m in self.models.items()}

    def submit(self, kind: str, job: Job) -> bool:
        m = self.models[kind]
        if m.submit(job):
            self.on_pending(job.camera_id, job.track_id, +1)
            return True
        return False

    # ---- results (called from the model threads) ---------------------------
    def _on_result(self, kind: str, job: Job, res: dict):
        try:
            if not res or "error" in res:
                return
            tr = job.extra.get("track_state")   # per-track identification bookkeeping (dict owned by pipeline)
            if kind == "face":
                self._face_result(job, res, tr)
            elif kind == "plate":
                self._plate_result(job, res, tr)
            elif kind == "pet":
                self._pet_result(job, res, tr)
        finally:
            self.on_pending(job.camera_id, job.track_id, -1)

    def _face_result(self, job: Job, res: dict, st: dict):
        if "embedding" not in res or "faces" not in job.extra:
            return
        self.stats["face_found"] += 1
        self.faces.on_face(job, res)

    def call_async(self, body, camera_id: int, track_id: int, done: Callable[[dict | None], None]):
        """POST /api/internal/identify im HTTP-Pool; der Track wartet (pending) bis zur Antwort.
        `body` ist ein dict oder eine Funktion, die es im HTTP-Thread baut (None = nichts senden)."""
        self.on_pending(camera_id, track_id, +1)

        def run():
            b = None
            try:
                b = body() if callable(body) else body
                if b is None:
                    return
                if b.get("kind") == "face":
                    self.stats["face_identify"] += 1
                done(self.identify_fn(b))
            except Exception as e:  # noqa: BLE001
                self.stats["identify_errors"] += 1
                log.warning("identify (%s/%s) failed: %s", (b or {}).get("kind"), (b or {}).get("phase", "single"), e)
            finally:
                self.on_pending(camera_id, track_id, -1)
        try:
            self.http.submit(run)
        except RuntimeError:          # Pool beendet (Worker fährt herunter)
            self.on_pending(camera_id, track_id, -1)

    def _plate_result(self, job: Job, res: dict, st: dict):
        text = res.get("plate_text")
        if not text:
            return
        self.stats["plate_reads"] += 1
        with st["lock"]:
            st["plate_reads"][text] = st["plate_reads"].get(text, 0) + 1
            cnt = st["plate_reads"][text]
            if cnt < 2 or text in st["plate_sent"] or len(st["plate_sent"]) >= 3:
                return
            # only send if this is the dominant reading
            if cnt < max(st["plate_reads"].values()):
                return
            st["plate_sent"].add(text)
        self.on_identity(job.camera_id, job.track_id, None, {"plate_text": text})
        path = new_media_path("crops", "jpg", job.ts)
        write_jpeg(path, res["plate_crop"] if res["plate_crop"].size else job.image)
        body = {"kind": "plate", "camera_id": job.camera_id, "track_id": job.track_id, "embedding": None,
                "plate_text": text, "crop_path": path, "quality": res["quality"]}
        self.stats["plate_identify"] += 1
        self._call(body, "plate", job, attributes={"plate_text": text})

    def _pet_result(self, job: Job, res: dict, st: dict):
        if "embedding" not in res:
            return
        path = job.extra.get("crop_path")
        if not path:
            path = new_media_path("crops", "jpg", job.ts)
            write_jpeg(path, job.image)
        body = {"kind": "pet", "camera_id": job.camera_id, "track_id": job.track_id,
                "embedding": [round(float(v), 6) for v in res["embedding"]], "plate_text": None,
                "crop_path": path, "quality": float(res.get("quality", 0.0))}
        self.stats["pet_identify"] += 1
        self._call(body, "pet", job)

    def _call(self, body: dict, kind: str, job: Job, attributes: dict | None = None):
        self.on_pending(job.camera_id, job.track_id, +1)

        def run():
            try:
                r = self.identify_fn(body)
                if r and r.get("identity_id") is not None:
                    ident = {"id": r["identity_id"], "display_name": r.get("display_name"),
                             "name": r.get("name"), "kind": kind}
                    self.on_identity(job.camera_id, job.track_id, ident, attributes)
            except Exception as e:  # noqa: BLE001
                self.stats["identify_errors"] += 1
                log.warning("identify (%s) failed: %s", kind, e)
                st = job.extra.get("track_state")
                if st is not None:
                    with st["lock"]:
                        if kind == "plate" and body.get("plate_text"):
                            st["plate_sent"].discard(body["plate_text"])
            finally:
                self.on_pending(job.camera_id, job.track_id, -1)
        self.http.submit(run)

    def stop(self):
        for m in self.models.values():
            m.stop()
        self.http.shutdown(wait=False, cancel_futures=True)
