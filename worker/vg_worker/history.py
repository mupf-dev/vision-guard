"""Zeitmaschine im Worker (B1): Bild-Embeddings je Kamera im Intervall + Textkodierung für die Suche.

Vertrag: CONTRACT.md „Nachträge: Welle 3“, W3.G (Modell laut docs/plan/roadmap/messungen.md §5: SigLIP 2
ViT-B/16-256, open_clip 3.2.0). Ablauf:

- ``Pipeline.apply_config`` ruft ``configure(cameras)``; Kameras mit ``history_enabled`` bekommen einen
  Frame-Tap-Abnehmer „history“ mit ``history_interval_s`` (Bildquelle = analysierter Detect-Frame, max.
  ``VG_MAX_FRAME_WIDTH`` breit – kein zusätzliches ffmpeg). Der Abnehmer läuft im eigenen Thread des Taps; ein
  langsamer Durchlauf verwirft Frames statt zu queuen (``perf.history.skipped``).
- Je Durchlauf: Gesamtbild (Kachel 0) und optional 4 überlappende Kacheln (``history_tiling``) → Bildturm fp16 auf
  der GPU (abwechselnd mit der Detektion über ``GpuTurn``). Dedupe gegen das zuletzt **gespeicherte** Embedding der
  Kamera: größte Änderung über alle Kacheln ``1 − cos < history_min_change`` → nur ``extend`` (Backend verlängert
  ``last_ts``; spätestens nach ``REFRESH_S`` = 30 min doch ein neues Bild), sonst Vorschau 256 px breit (``history/YYYY-MM-DD/cam{N}_{unix}.jpg``) + Vektoren.
- Textturm auf der CPU (``encode_text``), über ``vg/internal/embed`` mit ``model:"siglip2"`` (``Pipeline.embed.siglip``
  zeigt auf diese Engine). Ohne aktive Kamera wird das Modell erst bei der ersten Anfrage geladen und nach 10 min
  Leerlauf wieder entladen.
- GPU-Lease (W3.C): ``pause_gpu`` schiebt den Bildturm in den Arbeitsspeicher (kein erneutes Laden von der Platte),
  ``resume_gpu`` erlaubt wieder die GPU. Während der Lease verfallen die Takte.
- Fehler beim Laden (fehlendes Paket, kein Netz, kein VRAM) → ``models.history = "error: …"``; die Detektion läuft
  weiter, neuer Versuch nach ``RETRY_S``.
"""
from __future__ import annotations

import base64
import logging
import os
import threading
import time
from collections import deque
from typing import Callable

import numpy as np

from . import settings
from .util import day_dir, write_jpeg

log = logging.getLogger("vg.history")

DIM = 768                      # Spaltenbreite halfvec(768); kleinere Modelle werden mit Nullen aufgefüllt
THUMB_W = 256                  # Vorschau 256×144 bei 16:9
THUMB_Q = 80
REFRESH_S = 1800.0             # auch ohne Änderung spätestens so oft ein neues Bild (Filmstreifen, gelöschte Zeilen)
RETRY_S = 300.0                # nach einem Ladefehler frühestens so spät erneut laden
IDLE_UNLOAD_S = 600.0          # ohne aktive Kamera: Modell nach so langer Zeit ohne Textanfrage entladen
TEXT_WAIT_S = 4.0              # Textanfrage wartet so lange auf ein (erst)ladendes Modell (Backend-Timeout 5 s)
# Kacheln: 2×2 mit 10 % Überlappung (kleine Gegenstände an den Kachelgrenzen)
TILES = [(1, (0.0, 0.0, 0.55, 0.55)), (2, (0.45, 0.0, 1.0, 0.55)),
         (3, (0.0, 0.45, 0.55, 1.0)), (4, (0.45, 0.45, 1.0, 1.0))]

# Setting history_model → (open_clip-Architektur, pretrained, Anzeigename)
MODELS = {
    "siglip2-b16-256": ("ViT-B-16-SigLIP2-256", "webli"),
    "mobileclip2-s0": ("MobileCLIP2-S0", "dfndr2b"),
}
DEFAULT_MODEL = "siglip2-b16-256"


def _pad(e: np.ndarray) -> np.ndarray:
    """[n, d] → [n, DIM] (Nullen anhängen ändert den Kosinus nicht)."""
    if e.shape[1] == DIM:
        return e
    if e.shape[1] > DIM:
        raise ValueError(f"Embedding zu breit: {e.shape[1]} > {DIM}")
    return np.concatenate([e, np.zeros((e.shape[0], DIM - e.shape[1]), dtype=e.dtype)], axis=1)


def vec_b64(v: np.ndarray) -> str:
    return base64.b64encode(np.ascontiguousarray(v, dtype="<f2").tobytes()).decode()


class HistoryEncoder:
    """open_clip-Modell: Bildturm (fp16, GPU oder CPU-Parkplatz), Textturm (fp32, CPU)."""

    def __init__(self, key: str):
        import open_clip
        import torch

        self.torch = torch
        self.key = key
        arch, pretrained = MODELS[key]
        self.arch = arch
        self.model_name = arch
        cache = os.path.join(settings.MODELS_DIR, "history")
        os.makedirs(cache, exist_ok=True)
        t0 = time.time()
        model = open_clip.create_model(arch, pretrained=pretrained, cache_dir=cache)
        model.eval()
        self.tok = open_clip.get_tokenizer(arch, cache_dir=cache)
        cfg = dict(getattr(model.visual, "preprocess_cfg", None) or {})
        if not cfg.get("mean"):
            cfg.update(open_clip.get_pretrained_cfg(arch, pretrained) or {})
        size = cfg.get("size") or getattr(model.visual, "image_size", 256)
        self.size = (int(size[0]), int(size[1])) if isinstance(size, (tuple, list)) else (int(size), int(size))
        self.mean = np.array(cfg.get("mean") or (0.5, 0.5, 0.5), np.float32).reshape(1, 1, 3)
        self.std = np.array(cfg.get("std") or (0.5, 0.5, 0.5), np.float32).reshape(1, 1, 3)
        self.model = model
        self.visual = model.visual.half()          # fp16 (auf der CPU nur geparkt, gerechnet wird auf der GPU)
        self.device = "cpu"
        self.lock = threading.Lock()               # Bildturm: Rechnen vs. Verschieben (Lease)
        self.text_lock = threading.Lock()
        log.info("Zeitmaschine: %s/%s geladen in %.1f s (Bild %dx%d)", arch, pretrained, time.time() - t0, *self.size)

    # ------------------------------------------------------------------ Bildturm
    def to_gpu(self) -> None:
        dev = settings.DEVICE if settings.DEVICE.startswith("cuda") else "cuda:0"
        with self.lock:
            if self.device == "cpu" and self.torch.cuda.is_available():
                self.visual.to(dev)
                self.device = dev

    def to_cpu(self) -> None:
        with self.lock:
            if self.device != "cpu":
                self.visual.to("cpu")
                self.device = "cpu"
        try:
            self.torch.cuda.empty_cache()
        except Exception:  # noqa: BLE001
            pass

    def _prep(self, img: np.ndarray) -> np.ndarray:
        import cv2
        # „squash“ wie im open_clip-Preprocessing (ganzer Bildinhalt, kein Mittenausschnitt)
        x = cv2.resize(img, self.size, interpolation=cv2.INTER_AREA)[:, :, ::-1].astype(np.float32) / 255.0
        return ((x - self.mean) / self.std).transpose(2, 0, 1)

    def encode_images(self, imgs: list[np.ndarray], gpu_turn=None) -> np.ndarray:
        """BGR-Bilder → [n, DIM] L2-normiert (float32). Nur mit Bildturm auf der GPU."""
        torch = self.torch
        x = torch.from_numpy(np.stack([self._prep(i) for i in imgs]))
        with self.lock:
            if self.device == "cpu":
                raise RuntimeError("Bildturm nicht auf der GPU (pausiert)")
            x = x.to(self.device, non_blocking=True).half()
            slot = gpu_turn.inventory_slot() if gpu_turn is not None else threading.Lock()
            with slot, torch.inference_mode():
                e = self.visual(x).float()
            e = torch.nn.functional.normalize(e, dim=-1).cpu().numpy()
        return _pad(e)

    # ------------------------------------------------------------------ Textturm
    def encode_text(self, texts: list[str]) -> np.ndarray:
        torch = self.torch
        with self.text_lock, torch.inference_mode():
            t = self.tok(texts)
            e = self.model.encode_text(t).float()
            e = torch.nn.functional.normalize(e, dim=-1).numpy()
        return _pad(e)

    def close(self) -> None:
        self.to_cpu()
        self.model = None
        self.visual = None


class HistoryEngine:
    model_name = "ViT-B-16-SigLIP2-256"      # für embed_result.model (wird nach dem Laden aktualisiert)

    def __init__(self, publish: Callable[..., None], taps, gpu_turn):
        self.publish = publish
        self.taps = taps                  # frametap.FrameTapHub
        self.gpu = gpu_turn               # inventory.GpuTurn (GPU abwechselnd mit der Detektion)
        self.lock = threading.Lock()
        self.cams: dict[int, dict] = {}
        self.status = "off"               # off | loading | ready | paused: … | error: …
        self._paused: str | None = None
        self.min_change = 0.05
        self.model_key = DEFAULT_MODEL
        self.enc: HistoryEncoder | None = None
        self._loading = threading.Event()  # gesetzt, solange ein Ladeversuch läuft
        self._load_err_ts = 0.0
        self._last_text = 0.0
        self._tap_on = False
        self.last: dict[int, np.ndarray] = {}      # cid → zuletzt gespeicherte Vektoren [k, DIM]
        self.last_store: dict[int, float] = {}     # cid → Zeitpunkt des zuletzt gespeicherten Bildes
        self.runs: deque[tuple[float, float]] = deque(maxlen=2000)   # (ts, ms)
        self.skips: deque[float] = deque(maxlen=2000)
        self.stored: deque[float] = deque(maxlen=2000)
        self._stop = threading.Event()
        self._house = threading.Thread(target=self._housekeeping, name="history-house", daemon=True)
        self._house.start()

    # ------------------------------------------------------------------ Konfiguration
    def configure(self, cameras: list[dict]) -> None:
        with self.lock:
            self.cams = {int(c["id"]): c for c in cameras if c.get("history_enabled")}
            for cid in list(self.last):
                if cid not in self.cams:
                    self.last.pop(cid, None)
            want_tap = bool(self.cams)
        if want_tap and not self._tap_on:
            self.taps.register("history", self._on_frame, cameras=self._wants, interval_s=self._interval)
            self._tap_on = True
        elif not want_tap and self._tap_on:
            self.taps.unregister("history")
            self._tap_on = False
        if want_tap:
            self._ensure_loading()

    def set_settings(self, s: dict | None) -> None:
        if not s:
            return
        try:
            if s.get("history_min_change") is not None:
                self.min_change = max(0.0, min(0.5, float(s["history_min_change"])))
        except (TypeError, ValueError):
            pass
        key = str(s.get("history_model") or DEFAULT_MODEL)
        if key not in MODELS:
            log.warning("Unbekanntes history_model %r – nutze %s", key, DEFAULT_MODEL)
            key = DEFAULT_MODEL
        if key != self.model_key:
            log.info("Zeitmaschine: Modellwechsel %s → %s", self.model_key, key)
            self.model_key = key
            with self.lock:
                old, self.enc = self.enc, None
                self.last.clear()
            if old is not None:
                old.close()
            self.status = "off"
            self._load_err_ts = 0.0
            if self.cams:
                self._ensure_loading()

    def _wants(self, cid: int) -> bool:
        return cid in self.cams

    def _interval(self, cid: int) -> float:
        c = self.cams.get(cid) or {}
        try:
            return max(5.0, min(600.0, float(c.get("history_interval_s") or 30)))
        except (TypeError, ValueError):
            return 30.0

    # ------------------------------------------------------------------ Laden
    def _ensure_loading(self) -> None:
        if self.enc is not None or self._loading.is_set():
            return
        if self.status.startswith("error") and time.time() - self._load_err_ts < RETRY_S:
            return
        self._loading.set()
        threading.Thread(target=self._load, name="history-load", daemon=True).start()

    def _load(self) -> None:
        key = self.model_key
        self.status = "loading"
        try:
            enc = HistoryEncoder(key)
            if key != self.model_key:          # während des Ladens gewechselt
                enc.close()
                return
            if self.cams and not self._paused:
                enc.to_gpu()
            with self.lock:
                self.enc = enc
            self.model_name = enc.model_name
            self.status = "ready"
        except Exception as e:  # noqa: BLE001
            self._load_err_ts = time.time()
            self.status = f"error: {type(e).__name__}: {e}"[:200]
            log.warning("Zeitmaschine: Modell %s nicht ladbar: %s", key, e)
        finally:
            self._loading.clear()

    def _housekeeping(self) -> None:
        while not self._stop.wait(5.0):
            try:
                enc = self.enc
                if self.cams:
                    if enc is None:
                        self._ensure_loading()
                    elif not self._paused and enc.device == "cpu":
                        enc.to_gpu()
                elif enc is not None:
                    if enc.device != "cpu":
                        enc.to_cpu()           # keine Kamera mehr: Bildturm aus dem VRAM
                    if time.time() - self._last_text > IDLE_UNLOAD_S:
                        with self.lock:
                            self.enc = None
                        enc.close()
                        self.status = "off"
                        log.info("Zeitmaschine: Modell nach Leerlauf entladen")
            except Exception:  # noqa: BLE001
                log.exception("Zeitmaschine: Haushalt fehlgeschlagen")

    # ------------------------------------------------------------------ Embedding-Dienst (siglip2)
    def _encoder_for_request(self) -> HistoryEncoder:
        self._last_text = time.time()
        enc = self.enc
        if enc is not None:
            return enc
        self._ensure_loading()
        deadline = time.time() + TEXT_WAIT_S
        while time.time() < deadline:
            if self.enc is not None:
                return self.enc
            if not self._loading.is_set() and self.status.startswith("error"):
                break
            time.sleep(0.1)
        if self.status.startswith("error"):
            raise RuntimeError(f"Modell nicht verfügbar: {self.status}")
        raise RuntimeError("Modell lädt noch – bitte gleich noch einmal versuchen")

    def encode_text(self, texts: list[str]) -> np.ndarray:
        return self._encoder_for_request().encode_text(texts)

    def encode_images(self, imgs: list[np.ndarray]) -> np.ndarray:
        enc = self._encoder_for_request()
        if enc.device == "cpu":
            raise RuntimeError("Bildturm nicht aktiv (keine Kamera mit Zeitmaschine oder GPU-Lease)")
        return enc.encode_images(imgs, self.gpu)

    # ------------------------------------------------------------------ Frame-Tap
    def _on_frame(self, f) -> None:
        cfg = self.cams.get(f.camera_id)
        enc = self.enc
        if cfg is None:
            return
        if enc is None or self._paused or enc.device == "cpu":
            self.skips.append(time.time())
            return
        t0 = time.perf_counter()
        img = f.image
        h, w = img.shape[:2]
        crops = [img]
        boxes = [(0, [0.0, 0.0, 1.0, 1.0])]
        if cfg.get("history_tiling"):
            for tile, (x1, y1, x2, y2) in TILES:
                crops.append(img[int(y1 * h):int(y2 * h), int(x1 * w):int(x2 * w)])
                boxes.append((tile, [x1, y1, x2, y2]))
        try:
            vecs = enc.encode_images(crops, self.gpu)
        except RuntimeError as e:
            if "pausiert" in str(e):
                self.skips.append(time.time())
                return
            raise
        cid = f.camera_id
        prev = self.last.get(cid)
        change = 1.0
        if prev is not None and prev.shape == vecs.shape:
            change = float(np.max(1.0 - np.sum(prev * vecs, axis=1)))
        msg = {"camera_id": cid, "ts": round(f.ts, 3), "model": self.model_key, "dim": DIM,
               "change": round(change, 4)}
        if change < self.min_change and f.ts - self.last_store.get(cid, 0.0) < REFRESH_S:
            msg.update({"thumb_path": None, "extend": True, "vectors": []})
        else:
            th = max(2, int(round(THUMB_W * h / max(1, w))))
            import cv2
            thumb = cv2.resize(img, (THUMB_W, th), interpolation=cv2.INTER_AREA)
            path = f"{day_dir('history', f.ts)}/cam{cid}_{int(f.ts)}.jpg"
            write_jpeg(path, thumb, THUMB_Q)
            msg.update({"thumb_path": path, "extend": False,
                        "vectors": [{"tile": t, "bbox": [round(v, 3) for v in b], "vec_b64": vec_b64(v)}
                                    for (t, b), v in zip(boxes, vecs)]})
            self.last[cid] = vecs
            self.last_store[cid] = f.ts
            self.stored.append(time.time())
        self.publish(f"vg/internal/history/{cid}", msg, 1, False)
        self.runs.append((time.time(), (time.perf_counter() - t0) * 1000))

    # ------------------------------------------------------------------ GPU-Lease (W3.C)
    def pause_gpu(self, holder: str) -> bool:
        self._paused = holder or "?"
        enc = self.enc
        if enc is not None:
            enc.to_cpu()                  # wartet auf einen laufenden Durchlauf (Lock), dann aus dem VRAM
        return True

    def resume_gpu(self) -> None:
        self._paused = None
        enc = self.enc
        if enc is not None and self.cams:
            try:
                enc.to_gpu()
            except Exception:  # noqa: BLE001
                log.exception("Zeitmaschine: Bildturm nicht zurück auf die GPU")

    # ------------------------------------------------------------------ Status
    def status_text(self) -> str:
        return f"paused: GPU-Lease {self._paused}" if self._paused and self.status != "off" else self.status

    def perf(self) -> dict:
        now = time.time()
        r = [m for t, m in list(self.runs) if now - t <= 60]
        return {"per_min": len(r), "ms": round(sum(r) / len(r), 1) if r else 0.0,
                "skipped": sum(1 for t in list(self.skips) if now - t <= 60),
                "stored_per_min": sum(1 for t in list(self.stored) if now - t <= 60),
                "cameras": len(self.cams)}

    def stop(self) -> None:
        self._stop.set()
        self.taps.unregister("history")
