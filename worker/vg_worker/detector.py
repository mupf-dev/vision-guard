"""Batched YOLO11 object detection on the GPU (Ultralytics, PyTorch fp16)."""
from __future__ import annotations

import logging
import os
import time

import numpy as np

from . import settings

log = logging.getLogger("vg.detector")

# COCO classes we expose (COCO name -> VisionGuard label). Everything else is ignored.
LABEL_MAP = {
    "person": "person", "bicycle": "bicycle", "car": "car", "motorcycle": "motorcycle",
    "bus": "bus", "truck": "truck", "train": "train", "boat": "boat",
    "bird": "bird", "cat": "cat", "dog": "dog", "horse": "horse", "sheep": "sheep",
    "cow": "cow", "bear": "bear", "elephant": "elephant", "zebra": "zebra", "giraffe": "giraffe",
    "backpack": "backpack", "handbag": "handbag", "suitcase": "suitcase", "umbrella": "umbrella",
}


class Detector:
    def __init__(self):
        os.makedirs(settings.MODELS_DIR, exist_ok=True)
        os.environ.setdefault("YOLO_CONFIG_DIR", os.path.join(settings.MODELS_DIR, "ultralytics"))
        from ultralytics import YOLO
        from ultralytics.utils import SETTINGS
        try:
            SETTINGS.update({"weights_dir": os.path.join(settings.MODELS_DIR, "ultralytics", "weights"),
                             "sync": False})
        except Exception:  # noqa: BLE001
            pass

        import torch
        self.torch = torch
        # numpy/OpenCV/torch CPU ops run concurrently in many threads -> keep intra-op pools small
        torch.set_num_threads(settings.TORCH_THREADS)
        try:
            import cv2
            cv2.setNumThreads(2)
        except Exception:  # noqa: BLE001
            pass
        self.device = settings.DEVICE if torch.cuda.is_available() else "cpu"
        self.half = settings.DETECT_HALF and self.device.startswith("cuda")
        name = settings.DETECT_MODEL
        path = name if os.path.isabs(name) else os.path.join(settings.MODELS_DIR, name)
        if not os.path.exists(path) and not os.path.isabs(name):
            # ultralytics downloads bare names into CWD -> download from within MODELS_DIR
            cwd = os.getcwd()
            os.chdir(settings.MODELS_DIR)
            try:
                self.model = YOLO(name)
            finally:
                os.chdir(cwd)
        else:
            self.model = YOLO(path)
        self.names = self.model.names
        self.class_ids = [i for i, n in self.names.items() if n in LABEL_MAP]
        self.imgsz = settings.DETECT_IMGSZ
        self.model_name = os.path.basename(name)
        self.last_ms = 0.0
        # warm-up (also fuses layers)
        dummy = np.zeros((432, 768, 3), np.uint8)
        for bs in (1, 2):
            self.detect([dummy] * bs)
        log.info("detector %s on %s (fp16=%s, imgsz=%d) ready", self.model_name, self.device, self.half, self.imgsz)

    def detect(self, images: list[np.ndarray], conf: float | None = None) -> list[list[tuple[str, float, list[float]]]]:
        """Run one batch. Returns per image: [(label, score, [x1,y1,x2,y2] normalized)]."""
        if not images:
            return []
        t0 = time.perf_counter()
        res = self.model.predict(images, imgsz=self.imgsz, half=self.half, device=self.device,
                                 conf=conf if conf is not None else settings.DETECT_MIN_CONF,
                                 classes=self.class_ids, verbose=False, max_det=200, batch=len(images))
        out = []
        for r in res:
            b = r.boxes
            if b is None or len(b) == 0:
                out.append([])
                continue
            xyxyn = b.xyxyn.float().cpu().numpy()
            cls = b.cls.int().cpu().numpy()
            sc = b.conf.float().cpu().numpy()
            dets = []
            for k in range(len(cls)):
                lab = LABEL_MAP.get(self.names[int(cls[k])])
                if lab:
                    dets.append((lab, float(sc[k]), [float(v) for v in np.clip(xyxyn[k], 0, 1)]))
            out.append(dets)
        self.last_ms = (time.perf_counter() - t0) * 1000.0
        return out
