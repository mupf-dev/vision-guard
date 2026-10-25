"""Embedding-Dienst (W2, K2): beantwortet `vg/internal/embed` mit dem geteilten DINOv2 der Tiererkennung.

Vertrag: CONTRACT.md „Nachträge: Welle 2“, W2.E. Kein eigenes Modell: `identify.PetModel` wird geteilt (0 MB
zusätzliches VRAM, wenn die Tiererkennung läuft; sonst lädt es hier bei Bedarf). Eigener Thread mit kleiner
Warteschlange (voll → sofort `ok:false, error:"busy"`), Batches ≤ 8 – die Detektion wird nie aufgehalten.

Anfrage `{request_id, kind:"item|image|text", path|image_b64, bbox?, augment?, text?}` →
Antwort `vg/internal/embed_result` `{request_id, ok, kind, model, dim, embedding, variants, width, height, ms, error}`.
"""
from __future__ import annotations

import base64
import logging
import os
import queue
import threading
import time
from collections import deque
from collections.abc import Callable

import numpy as np

from . import settings
from .util import abs_media_path

log = logging.getLogger("vg.embed")

QSIZE = 32
MAX_BATCH = 8
LOAD_WAIT_S = 60.0          # so lange auf das (erstmals ladende) Modell warten, dann Fehler „Modell lädt noch“
MAX_IMAGE_BYTES = 4 * 1024 * 1024


class EmbedService:
    def __init__(self, publish: Callable[..., None], pet_model):
        self.publish = publish
        self.pet = pet_model                         # identify.PetModel (geteilt)
        self.q: queue.Queue[dict] = queue.Queue(maxsize=QSIZE)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.hist: deque[tuple[float, float]] = deque(maxlen=500)   # (ts, ms je Anfrage)
        self.dropped = 0

    # ------------------------------------------------------------------ Eingang (MQTT-Thread)
    def submit(self, msg: dict) -> None:
        if not isinstance(msg, dict) or not msg.get("request_id"):
            return
        if self._thread is None:
            self._thread = threading.Thread(target=self._run, name="embed", daemon=True)
            self._thread.start()
        try:
            self.q.put_nowait(msg)
        except queue.Full:
            self.dropped += 1
            self._reply(msg, ok=False, error="busy")

    def stop(self) -> None:
        self._stop.set()

    # ------------------------------------------------------------------ Status
    def status(self) -> str:
        if self._thread is None:
            return "idle"
        return self.pet.status if self.pet._thread else "waiting for model"

    def perf(self) -> dict:
        now = time.time()
        h = [ms for ts, ms in self.hist if now - ts <= 60]
        return {"requests_per_min": len(h), "ms": round(sum(h) / len(h), 2) if h else 0.0, "dropped": self.dropped}

    # ------------------------------------------------------------------ Verarbeitung
    def _reply(self, req: dict, *, ok: bool, error: str | None = None, **extra) -> None:
        body = {"request_id": req.get("request_id"), "ok": ok, "kind": req.get("kind"), "error": error,
                "embedding": None, "variants": None}
        body.update(extra)
        self.publish("vg/internal/embed_result", body, 1, False)

    def _wait_model(self) -> str | None:
        self.pet.ensure_started()
        deadline = time.time() + LOAD_WAIT_S
        while not self.pet.ready():
            if self.pet.status.startswith("error"):
                return f"Modell nicht verfügbar: {self.pet.status}"
            if time.time() > deadline or self._stop.is_set():
                return "Modell lädt noch"
            time.sleep(0.1)
        return None

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                first = self.q.get(timeout=1.0)
            except queue.Empty:
                continue
            batch = [first]
            while len(batch) < MAX_BATCH:
                try:
                    batch.append(self.q.get_nowait())
                except queue.Empty:
                    break
            err = self._wait_model()
            for req in batch:
                if err:
                    self._reply(req, ok=False, error=err)
                    continue
                try:
                    self._handle(req)
                except Exception as e:  # noqa: BLE001
                    log.warning("embed %s failed: %s", req.get("kind"), e)
                    self._reply(req, ok=False, error=f"{type(e).__name__}: {e}"[:200])

    def _load_image(self, req: dict) -> np.ndarray:
        import cv2
        if req.get("image_b64"):
            raw = base64.b64decode(req["image_b64"])
            if len(raw) > MAX_IMAGE_BYTES:
                raise ValueError("Bild zu groß (max. 4 MB)")
            img = cv2.imdecode(np.frombuffer(raw, np.uint8), cv2.IMREAD_COLOR)
        else:
            rel = str(req.get("path") or "")
            path = abs_media_path(rel)
            if not rel or not os.path.isfile(path):
                raise FileNotFoundError(f"Bild nicht gefunden: {rel}")
            img = cv2.imread(path, cv2.IMREAD_COLOR)
        if img is None or img.size == 0:
            raise ValueError("Bild nicht lesbar")
        bbox = req.get("bbox")
        if bbox:
            h, w = img.shape[:2]
            x1, y1, x2, y2 = (float(v) for v in bbox)
            x1, x2 = sorted((max(0.0, min(1.0, x1)), max(0.0, min(1.0, x2))))
            y1, y2 = sorted((max(0.0, min(1.0, y1)), max(0.0, min(1.0, y2))))
            a, b, c, d = int(x1 * w), int(y1 * h), int(round(x2 * w)), int(round(y2 * h))
            if c - a < 4 or d - b < 4:
                raise ValueError("Ausschnitt zu klein")
            img = img[b:d, a:c]
        return np.ascontiguousarray(img)

    @staticmethod
    def _degrade(img: np.ndarray, size: int) -> np.ndarray:
        """Kamera-ähnliche Fassung: auf `size` px (längste Seite) verkleinern, leicht unscharf, JPEG q=40."""
        import cv2
        h, w = img.shape[:2]
        f = size / max(h, w)
        if f < 1:
            img = cv2.resize(img, (max(1, int(w * f)), max(1, int(h * f))), interpolation=cv2.INTER_AREA)
        img = cv2.GaussianBlur(img, (3, 3), 0.8)
        ok, enc = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 40])
        return cv2.imdecode(enc, cv2.IMREAD_COLOR) if ok else img

    def _handle(self, req: dict) -> None:
        kind = req.get("kind")
        if kind == "text":
            self._reply(req, ok=False, error="kind text nicht unterstützt")
            return
        if kind not in ("item", "image"):
            self._reply(req, ok=False, error=f"unbekannte Art: {kind}")
            return
        t0 = time.perf_counter()
        img = self._load_image(req)
        h, w = img.shape[:2]
        images = [img]
        if kind == "item" and req.get("augment"):
            images += [img[:, ::-1].copy(), self._degrade(img, 120), self._degrade(img, 64)]
        embs = self.pet.embed_batch(images)
        if len(embs) > 1:
            mean = embs.mean(axis=0)
            mean = mean / max(float(np.linalg.norm(mean)), 1e-9)
            variants = [[round(float(v), 6) for v in e] for e in embs]
        else:
            mean, variants = embs[0], None
        ms = (time.perf_counter() - t0) * 1000
        self.hist.append((time.time(), ms))
        self._reply(req, ok=True, model=settings.PET_MODEL, dim=int(mean.shape[0]),
                    embedding=[round(float(v), 6) for v in mean], variants=variants, width=int(w), height=int(h),
                    ms=round(ms, 2))
