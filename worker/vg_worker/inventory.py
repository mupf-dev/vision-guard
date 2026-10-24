"""Inventar (Gegenstände): langsame Pipeline auf ausgewählten Kameras.

Aufbau (siehe CONTRACT.md, Nachtrag G):

* ``KeyframeGrabber`` – holt ein Bild in voller Auflösung vom Hauptstream. Bevorzugt das letzte fertige
  Segment des Ringpuffers (Keyframe am Segmentanfang, keine weitere RTSP-Verbindung); Fallbacks:
  go2rtc ``/api/frame.jpeg`` und ein einmaliges ffmpeg ``-skip_frame nokey`` auf ``main_rtsp``.
* ``InventoryModel`` – YOLOE-11 mit Text-Prompts (``set_classes``; Text-Embeddings werden im
  ``/models``-Volume gecacht, der Textencoder wird danach nicht mehr gebraucht). Alternativ ein Modell mit festen
  Klassennamen (COCO-YOLO oder YOLOE ``-pf``): dann werden Vokabular-Einträge über ihre Namen zugeordnet.
  Gesamtbild + optionale Kacheln, klassenweise NMS mit Kantenkorrektur.
* ``InventoryState`` – Stabilisierung je Kamera (Zuordnung per Label + IoU, ``confirm_n``/``miss_m``,
  Verdeckung durch Personen, Crops höchstens 1×/min unter ``inventory/YYYY-MM-DD/``).
* ``InventoryEngine`` – eigener Thread, Modell lazy erst bei der ersten Kamera mit ``inventory_enabled``,
  höchstens ein offener Durchlauf je Kamera (fällige Takte werden übersprungen, nichts wird angestaut),
  GPU abwechselnd mit der Sicherheitspipeline (``GpuTurn``), Rechenbudget ``VG_INVENTORY_MAX_BUSY``.
  Fehler bleiben in diesem Thread; die Detektion läuft immer weiter.
"""
from __future__ import annotations

import hashlib
import logging
import math
import os
import subprocess
import threading
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from typing import Callable

import cv2
import numpy as np

from . import settings
from .util import crop_norm, day_dir, draw_objects, point_in_polygon, rtsp_input_args, write_jpeg

log = logging.getLogger("vg.inventory")

Publish = Callable[[str, dict, int, bool], None]

# Personen und Fahrzeuge deckt die Sicherheitspipeline ab – nie im Inventar.
EXCLUDED_LABELS = {"person", "car", "truck", "bus", "motorcycle", "bicycle", "train", "boat"}

MATCH_IOU = 0.3
MATCH_CENTER = 0.05
MOVED_CENTER = 0.10
OCCLUDE_OVERLAP = 0.3          # Anteil der Gegenstandsfläche, den eine Person überdecken muss
CARRIED_OVERLAP = 0.6          # Detektion fast ganz in einer Personenbox → getragen, zählt nicht
MISSING_KEEP_RUNS = 10         # "missing"-Einträge so viele Durchläufe mitführen
NMS_IOU = 0.5
CONTAIN = 0.8                  # kleinere Box gleichen Labels zu ≥ 80 % in einer besseren → Duplikat
CROSS_LABEL_IOU = 0.7          # fast dieselbe Box mit anderem Label → nur das beste Label
MODEL_DIR = os.path.join(settings.MODELS_DIR, "yoloe")   # YOLOE-Gewichte, mobileclip_blt.ts, Text-Embeddings
EDGE_PX = 3                    # Kachel-Detektion berührt eine innere Kachelkante → abgeschnitten, verwerfen


# ============================================================================ GPU-Wechsel
class GpuTurn:
    """Lässt die Inventar-Aufrufe zwischen den Batches der Sicherheitspipeline laufen.

    ``_infer_loop`` hält ``lock`` während ``detect`` und setzt danach ``batch_done``. Der Inventar-Thread wartet vor
    jedem ``predict`` auf das Ende eines Sicherheits-Batches (oder einen kurzen Timeout, wenn gerade keiner läuft)
    und nimmt dann den Lock – beide wechseln sich also ab, statt gleichzeitig um die GPU zu konkurrieren.
    """

    def __init__(self):
        self.lock = threading.Lock()
        self.batch_done = threading.Event()
        self.last_main = 0.0

    def main_batch_done(self):
        self.last_main = time.time()
        self.batch_done.set()

    def inventory_slot(self, timeout: float = 0.25):
        if time.time() - self.last_main < 1.0:      # Sicherheitspipeline aktiv → auf ihr Batch-Ende warten
            self.batch_done.clear()
            self.batch_done.wait(timeout)
        return self.lock


# ============================================================================ Geometrie
def _iou(a, b) -> float:
    ix = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = ix * iy
    if inter <= 0:
        return 0.0
    ua = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / ua if ua > 0 else 0.0


def _overlap_of(a, b) -> float:
    """Anteil der Fläche von ``a``, der in ``b`` liegt."""
    ix = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    area = max(1e-12, (a[2] - a[0]) * (a[3] - a[1]))
    return ix * iy / area


def _center(b) -> tuple[float, float]:
    return (b[0] + b[2]) / 2.0, (b[1] + b[3]) / 2.0


def _cdist(a, b) -> float:
    ca, cb = _center(a), _center(b)
    return math.hypot(ca[0] - cb[0], ca[1] - cb[1])


def parse_tiles(spec: str) -> tuple[int, int]:
    s = (spec or "").lower().strip()
    if s in ("", "0", "off", "none", "1x1"):
        return 0, 0
    try:
        c, r = s.split("x", 1)
        c, r = max(1, int(c)), max(1, int(r))
        return (0, 0) if c * r <= 1 else (c, r)
    except ValueError:
        return 2, 2


def tile_boxes(w: int, h: int, cols: int, rows: int, overlap: float) -> list[tuple[int, int, int, int]]:
    """Kacheln (x1, y1, x2, y2) in Pixeln mit Überlappung."""
    overlap = min(max(overlap, 0.0), 0.5)
    out = []
    tw = w / (cols - (cols - 1) * overlap)
    th = h / (rows - (rows - 1) * overlap)
    for r in range(rows):
        for c in range(cols):
            x1 = int(round(c * tw * (1 - overlap)))
            y1 = int(round(r * th * (1 - overlap)))
            x2 = w if c == cols - 1 else min(w, int(round(x1 + tw)))
            y2 = h if r == rows - 1 else min(h, int(round(y1 + th)))
            out.append((x1, y1, x2, y2))
    return out


def merge_detections(dets: list[tuple[str, str, float, list[float]]]) -> list[tuple[str, str, float, list[float]]]:
    """Klassenweise NMS (je Vokabular-Label) + Unterdrückung enthaltener Duplikate."""
    by_label: dict[str, list] = {}
    for d in dets:
        by_label.setdefault(d[0], []).append(d)
    out = []
    for items in by_label.values():
        items.sort(key=lambda d: -d[2])
        kept: list = []
        for d in items:
            if any(_iou(d[3], k[3]) >= NMS_IOU or _overlap_of(d[3], k[3]) >= CONTAIN for k in kept):
                continue
            kept.append(d)
        out.extend(kept)
    # Labelübergreifend: dieselbe Box mit mehreren Labels (Handy als tablet/book/plate, Sofa als bed …) → nur das
    # Label mit dem höchsten Score behalten (docs/plan/03-machbarkeit-ergebnis.md, „Doppeltreffer“)
    out.sort(key=lambda d: -d[2])
    final: list = []
    for d in out:
        if any(k[0] != d[0] and _iou(d[3], k[3]) >= CROSS_LABEL_IOU for k in final):
            continue
        final.append(d)
    return final


# ============================================================================ Keyframes
def _decode_first_frame(args: list[str], timeout: float) -> np.ndarray | None:
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", *args,
           "-frames:v", "1", "-an", "-f", "image2pipe", "-c:v", "bmp", "-"]
    r = subprocess.run(cmd, capture_output=True, timeout=timeout, check=False)
    if r.returncode != 0 or not r.stdout:
        raise RuntimeError((r.stderr or b"").decode(errors="replace").strip()[-200:] or "ffmpeg lieferte kein Bild")
    img = cv2.imdecode(np.frombuffer(r.stdout, np.uint8), cv2.IMREAD_COLOR)
    if img is None:
        raise RuntimeError("Bild nicht decodierbar")
    return img


class KeyframeGrabber:
    """Volles Bild des Hauptstreams für eine Kamera."""

    def __init__(self, camera_id: int):
        self.camera_id = camera_id
        self.last_seg: str | None = None
        self.source = "-"              # buffer | go2rtc | rtsp
        self.last_error = ""

    def grab(self, runtime, cfg: dict) -> tuple[np.ndarray, float] | None:
        """Liefert (Bild BGR, Aufnahmezeit) oder None, wenn (noch) kein neues Bild vorliegt."""
        mode = settings.INVENTORY_SOURCE
        rec = getattr(runtime, "recorder", None) if runtime is not None else None
        if mode in ("auto", "buffer") and rec is not None:
            try:
                res = self._from_buffer(rec)
                if res == "same":
                    return None
                if res is not None:
                    self.source = "buffer"
                    return res
            except Exception as e:  # noqa: BLE001
                self.last_error = f"buffer: {e}"
            if mode == "buffer":
                return None
        if mode in ("auto", "go2rtc"):
            try:
                img = self._from_go2rtc()
                self.source = "go2rtc"
                return img, time.time()
            except Exception as e:  # noqa: BLE001
                self.last_error = f"go2rtc: {e}"
            if mode == "go2rtc":
                return None
        url = cfg.get("main_rtsp") or cfg.get("detect_url_rtsp")
        if url:
            try:
                t = time.time()
                img = _decode_first_frame([*rtsp_input_args(url), "-skip_frame", "nokey", "-i", url], 20.0)
                self.source = "rtsp"
                return img, t
            except Exception as e:  # noqa: BLE001
                self.last_error = f"rtsp: {e}"
        return None

    def _from_buffer(self, rec):
        segs = rec._segments()                         # [(start_ts, path)] aufsteigend
        if len(segs) < 2:
            return None
        newest_start = segs[-1][0]
        if time.time() - newest_start > settings.BUFFER_SEGMENT_S * 3 + 5:
            return None                                # Ringpuffer hängt → Fallback
        ts, path = segs[-2]                            # letztes *fertiges* Segment, beginnt mit Keyframe
        if path == self.last_seg:
            return "same"
        img = _decode_first_frame(["-i", path], 10.0)
        self.last_seg = path
        return img, ts

    def _from_go2rtc(self) -> np.ndarray:
        import requests
        r = requests.get(f"{settings.GO2RTC_URL}/api/frame.jpeg", params={"src": f"cam_{self.camera_id}"}, timeout=10)
        r.raise_for_status()
        img = cv2.imdecode(np.frombuffer(r.content, np.uint8), cv2.IMREAD_COLOR)
        if img is None:
            raise RuntimeError("kein JPEG")
        return img


# ============================================================================ Modell
class InventoryModel:
    """YOLOE mit Text-Prompts oder ein Modell mit festen Klassennamen (COCO-YOLO, YOLOE-pf)."""

    def __init__(self):
        os.makedirs(settings.MODELS_DIR, exist_ok=True)
        os.environ.setdefault("YOLO_CONFIG_DIR", os.path.join(settings.MODELS_DIR, "ultralytics"))
        import torch
        self.torch = torch
        name = settings.INVENTORY_MODEL
        base = os.path.basename(name).lower()
        self.text_prompts = "yoloe" in base and "-pf" not in base
        self.device = settings.DEVICE if torch.cuda.is_available() else "cpu"
        self.half = settings.INVENTORY_HALF and self.device.startswith("cuda")
        self.model_name = os.path.splitext(os.path.basename(name))[0]
        if "yoloe" in base:
            from ultralytics import YOLOE as _Cls
        else:
            from ultralytics import YOLO as _Cls
        os.makedirs(MODEL_DIR, exist_ok=True)
        self._cls = _Cls
        if os.path.isabs(name):
            self.weights = name
        else:
            alt = os.path.join(settings.MODELS_DIR, name)
            # sonst liegt es in /models/yoloe bzw. wird einmalig dorthin geladen (absoluter Pfad, kein chdir)
            self.weights = alt if os.path.exists(alt) else self._asset(name)
        self.model = _Cls(self.weights)
        self.base_names = dict(self.model.names) if not self.text_prompts else {}
        self.vocab_key = None
        self.class_map: dict[int, tuple[str, str]] = {}            # Klassenindex → (label, prompt)
        self.classes: list[int] | None = None
        self.warm = False

    @staticmethod
    def _asset(name: str) -> str:
        """Ultralytics-Asset (Gewichte, mobileclip_blt.ts) unter /models/yoloe – absoluter Pfad, kein ``os.chdir``
        (das würde prozessweit gelten und relative Pfade anderer Threads umbiegen)."""
        path = os.path.join(MODEL_DIR, name)
        if not os.path.exists(path):
            from ultralytics.utils.downloads import attempt_download_asset
            log.info("downloading %s to %s (once)", name, MODEL_DIR)
            path = attempt_download_asset(path)
            if not os.path.exists(path):
                raise RuntimeError(f"{name} konnte nicht geladen werden")
        return path

    # -- Vokabular -------------------------------------------------------------
    def set_vocab(self, vocab: list[dict]):
        """Vokabular (Vereinigung aller Inventar-Kameras) setzen; nur bei Änderung."""
        entries = [e for e in vocab if e.get("label") and e["label"] not in EXCLUDED_LABELS]
        key = repr([(e["label"], tuple(e.get("prompts") or [])) for e in entries])
        if key == self.vocab_key:
            return
        if self.text_prompts:
            prompts: list[str] = []
            cmap: dict[int, tuple[str, str]] = {}
            for e in entries:
                for p in (e.get("prompts") or [e["label"].replace("_", " ")]):
                    p = str(p).strip()
                    if p and p not in prompts:
                        cmap[len(prompts)] = (e["label"], p)
                        prompts.append(p)
            if not prompts:
                self.class_map, self.vocab_key = {}, key
                return
            pe = self._text_embeddings(prompts)
            self.model.set_classes(prompts, pe)
            self.class_map = cmap
            self.classes = None
            if getattr(self.model, "predictor", None) is not None:
                # Predictor neu aufsetzen (Gerät/fp16 für die neuen Embeddings); Aufwärmen ohne GPU-Lock
                self.model.predictor = None
                self.warm = False
        else:
            lower = {i: str(n).lower() for i, n in self.base_names.items()}
            cmap = {}
            for e in entries:
                cands = {e["label"].lower(), e["label"].replace("_", " ").lower(),
                         *(str(p).lower() for p in (e.get("prompts") or []))}
                for i, n in lower.items():
                    if n in cands and i not in cmap:
                        cmap[i] = (e["label"], self.base_names[i])
            self.class_map = cmap
            self.classes = sorted(cmap) or None
        self.vocab_key = key
        log.info("inventory vocabulary: %d labels, %d classes (%s)", len(entries), len(self.class_map),
                 "text prompts" if self.text_prompts else "fixed names")

    def _text_embeddings(self, prompts: list[str]):
        torch = self.torch
        h = hashlib.sha1((self.model_name + "\n" + "\n".join(prompts)).encode()).hexdigest()[:16]
        cache = os.path.join(MODEL_DIR, f"pe_{self.model_name}_{h}.pt")
        if os.path.exists(cache):
            try:
                pe = torch.load(cache, map_location="cpu", weights_only=True)
                if pe.ndim == 3 and pe.shape[1] == len(prompts):
                    return pe.to(self.device)
            except Exception as e:  # noqa: BLE001
                log.warning("text embedding cache unreadable (%s) – recomputing", e)
        t0 = time.time()
        # Immer auf der CPU: MobileCLIP-Textencoder (~600 MB) und eine CPU-Kopie des YOLOE-Kopfs, beide danach
        # verworfen. ``model.get_text_pe`` würde den Encoder auf das Gerät des Modells legen – nach der ersten
        # Inferenz also in den VRAM, außerhalb des GPU-Wechsels und parallel zur Detektion.
        import clip  # noqa: PLC0415  (eigene Schicht im Dockerfile)
        with torch.inference_mode():
            enc = torch.jit.load(self._asset("mobileclip_blt.ts"), map_location="cpu")
            tokens = clip.clip.tokenize(prompts, truncate=True)
            feats = torch.cat([enc(t).float() for t in tokens.split(80)], dim=0)
            del enc
            feats = feats.reshape(-1, len(prompts), feats.shape[-1])
            cpu_model = self._cls(self.weights)              # frisch geladen → CPU, fp32
            head = cpu_model.model.model[-1]
            pe = head.get_tpe(feats).detach().float().cpu()
            del cpu_model, head
        os.makedirs(os.path.dirname(cache), exist_ok=True)
        tmp = f"{cache}.tmp{os.getpid()}"
        torch.save(pe, tmp)
        os.replace(tmp, cache)
        log.info("text embeddings for %d prompts computed on CPU in %.1fs (cached %s)", len(prompts), time.time() - t0, cache)
        return pe.to(self.device)

    # -- Inferenz --------------------------------------------------------------
    @staticmethod
    def prepare(img: np.ndarray, imgsz: int) -> tuple[np.ndarray, float]:
        """Bild außerhalb des GPU-Locks auf ``imgsz`` (lange Seite) verkleinern. Rückgabe (Bild, Faktor)."""
        h, w = img.shape[:2]
        s = imgsz / float(max(h, w))
        if s >= 1.0:
            return img, 1.0
        small = cv2.resize(img, (max(1, int(round(w * s))), max(1, int(round(h * s)))), interpolation=cv2.INTER_AREA)
        return small, s

    def warmup(self, imgsz: int):
        """Erste Inferenz (Gerät, Fuse, fp16, cuDNN) mit einem kleinen Dummy-Bild – bewusst ohne GPU-Lock,
        damit die 5-fps-Pipeline nicht sekundenlang blockiert."""
        if self.warm or not self.class_map:
            return
        t0 = time.time()
        self.predict(np.zeros((360, 640, 3), np.uint8), imgsz, 0.5)
        self.warm = True
        log.info("inventory model warm-up in %.1fs", time.time() - t0)

    def predict(self, img: np.ndarray, imgsz: int, conf: float, scale: float = 1.0) -> list[tuple[str, str, float, list[float]]]:
        """Eine Inferenz. Rückgabe [(label, prompt, score, [x1,y1,x2,y2] in Pixeln des Originalbilds)];
        ``scale`` = Verkleinerungsfaktor aus ``prepare``."""
        if not self.class_map:
            return []
        res = self.model.predict(img, imgsz=imgsz, conf=conf, half=self.half, device=self.device,
                                 classes=self.classes, verbose=False, max_det=300, retina_masks=False)
        inv = 1.0 / scale if scale > 0 else 1.0
        out = []
        for r in res:
            b = r.boxes
            if b is None or len(b) == 0:
                continue
            xyxy = b.xyxy.float().cpu().numpy()
            cls = b.cls.int().cpu().numpy()
            sc = b.conf.float().cpu().numpy()
            for k in range(len(cls)):
                m = self.class_map.get(int(cls[k]))
                if m:
                    out.append((m[0], m[1], float(sc[k]), [float(v) * inv for v in xyxy[k]]))
        return out

    def close(self):
        self.model = None
        try:
            if self.device.startswith("cuda"):
                self.torch.cuda.empty_cache()
        except Exception:  # noqa: BLE001
            pass


# ============================================================================ Zustand je Kamera
@dataclass
class Item:
    item_id: str
    label: str
    prompt: str
    score: float
    bbox: list[float]
    first_seen: float
    last_seen: float
    first_run: int
    state: str = "candidate"                 # candidate | present | missing
    hits: int = 0
    misses: int = 0
    hit_runs: deque = field(default_factory=lambda: deque(maxlen=32))
    anchor: tuple[float, float] = (0.0, 0.0)
    zones: list[str] = field(default_factory=list)
    occluded: bool = False
    missing_runs: int = 0
    crop_path: str | None = None
    crop_score: float = 0.0
    crop_ts: float = 0.0

    def to_msg(self) -> dict:
        return {"item_id": self.item_id, "label": self.label, "prompt": self.prompt, "score": round(self.score, 3),
                "bbox": [round(v, 4) for v in self.bbox], "zones": list(self.zones), "state": self.state,
                "occluded": self.occluded, "first_seen": round(self.first_seen, 3),
                "last_seen": round(self.last_seen, 3), "hits": self.hits, "misses": self.misses,
                "crop_path": self.crop_path}


class InventoryState:
    """Stabilisierung der Inventar-Detektionen einer Kamera (nur flüchtiger Zustand im Worker)."""

    def __init__(self, camera_id: int):
        self.camera_id = camera_id
        self.items: dict[str, Item] = {}
        self.run = 0

    def _zones_of(self, bbox, zones_cfg) -> list[str]:
        x = (bbox[0] + bbox[2]) / 2.0
        y = bbox[3]
        return [str(z.get("id")) for z in zones_cfg or []
                if z.get("polygon") and point_in_polygon(x, y, z["polygon"])]

    def update(self, dets: list[tuple[str, str, float, list[float]]], frame_ts: float, image: np.ndarray,
               persons: list[list[float]], confirm_n: int, miss_m: int, zones_cfg, labels: set[str]) -> list[dict]:
        """Ein Durchlauf. ``dets``: [(label, prompt, score, bbox normiert)]. Rückgabe: changes."""
        self.run += 1
        run = self.run
        confirm_n = max(1, int(confirm_n))
        miss_m = max(1, int(miss_m))
        changes: list[dict] = []
        # Gegenstände, deren Label nicht mehr zum Vokabular der Kamera gehört, verwerfen
        for iid in [i for i, it in self.items.items() if it.label not in labels]:
            self.items.pop(iid)
        # getragene Dinge (fast ganz in einer Personenbox) ignorieren
        dets = [d for d in dets if not any(_overlap_of(d[3], p) >= CARRIED_OVERLAP for p in persons)]

        # --- Zuordnung: gleiches Label, IoU ≥ 0,3 oder Mittelpunktabstand < 0,05 (gierig nach Güte)
        pairs = []
        for di, d in enumerate(dets):
            for iid, it in self.items.items():
                if it.label != d[0]:
                    continue
                iou = _iou(d[3], it.bbox)
                cd = _cdist(d[3], it.bbox)
                if iou >= MATCH_IOU or cd < MATCH_CENTER:
                    pairs.append((iou - cd, di, iid))
        pairs.sort(reverse=True)
        used_d, used_i = set(), set()
        matched: list[tuple[tuple, Item]] = []
        for _, di, iid in pairs:
            if di in used_d or iid in used_i:
                continue
            used_d.add(di)
            used_i.add(iid)
            matched.append((dets[di], self.items[iid]))

        for d, it in matched:
            label, prompt, score, bbox = d
            it.hits += 1
            it.misses = 0
            it.occluded = False
            it.missing_runs = 0
            it.hit_runs.append(run)
            it.last_seen = frame_ts
            it.prompt = prompt
            it.score = 0.6 * it.score + 0.4 * score
            if it.state == "present" and math.hypot(_center(bbox)[0] - it.anchor[0],
                                                     _center(bbox)[1] - it.anchor[1]) > MOVED_CENTER:
                it.bbox = list(bbox)
                it.anchor = _center(bbox)
                changes.append({"item_id": it.item_id, "type": "moved"})
                self._maybe_crop(it, image, frame_ts, score, force=True)
            else:
                it.bbox = [0.5 * a + 0.5 * b for a, b in zip(it.bbox, bbox)]
            if it.state == "candidate":
                recent = sum(1 for r in it.hit_runs if r > run - 2 * confirm_n)
                if recent >= confirm_n:
                    it.state = "present"
                    it.anchor = _center(it.bbox)
                    changes.append({"item_id": it.item_id, "type": "appeared"})
                    self._maybe_crop(it, image, frame_ts, score, force=True)
            elif it.state == "missing":
                it.state = "present"
                it.anchor = _center(it.bbox)
                changes.append({"item_id": it.item_id, "type": "appeared"})
                self._maybe_crop(it, image, frame_ts, score, force=True)
            else:
                self._maybe_crop(it, image, frame_ts, score)
            it.zones = self._zones_of(it.bbox, zones_cfg)

        # --- neue Kandidaten
        for di, d in enumerate(dets):
            if di in used_d:
                continue
            label, prompt, score, bbox = d
            it = Item(item_id=str(uuid.uuid4()), label=label, prompt=prompt, score=score, bbox=list(bbox),
                      first_seen=frame_ts, last_seen=frame_ts, first_run=run, hits=1)
            it.hit_runs.append(run)
            it.anchor = _center(bbox)
            it.zones = self._zones_of(bbox, zones_cfg)
            self.items[it.item_id] = it
            if confirm_n <= 1:
                it.state = "present"
                changes.append({"item_id": it.item_id, "type": "appeared"})
                self._maybe_crop(it, image, frame_ts, score, force=True)

        # --- nicht gefundene: Verdeckung durch Personen zählt nicht als Fehlschlag
        for iid in list(self.items):
            if iid in used_i:
                continue
            it = self.items[iid]
            if it.first_run == run:
                continue
            it.occluded = any(_overlap_of(it.bbox, p) > OCCLUDE_OVERLAP for p in persons)
            if it.state == "candidate":
                if run - it.first_run >= 2 * confirm_n:
                    self.items.pop(iid)          # nie bestätigt → still verwerfen
                continue
            if it.state == "missing":
                it.missing_runs += 1
                if it.missing_runs > MISSING_KEEP_RUNS:
                    self.items.pop(iid)
                continue
            if it.occluded:
                continue
            it.misses += 1
            if it.misses >= miss_m:
                it.state = "missing"
                it.missing_runs = 0
                changes.append({"item_id": it.item_id, "type": "disappeared"})
        return changes

    def _maybe_crop(self, it: Item, image: np.ndarray, ts: float, score: float, force: bool = False):
        """Crop beim Erscheinen/Verschieben bzw. bei besserem Score – je Gegenstand höchstens 1×/min."""
        if ts - it.crop_ts < settings.INVENTORY_CROP_MIN_S and it.crop_path:
            return
        if not force and score <= it.crop_score * 1.1:
            return
        try:
            crop = crop_norm(image, it.bbox, margin=0.25)
            if crop is None:
                return
            path = f"{day_dir('inventory', ts)}/{it.item_id}_{int(ts)}.jpg"
            write_jpeg(path, crop)
            it.crop_path = path
            it.crop_score = score
            it.crop_ts = ts
        except Exception as e:  # noqa: BLE001
            log.warning("inventory crop write failed: %s", e)

    def write_snapshot(self, image: np.ndarray, frame_ts: float) -> str | None:
        """Übersichtsbild mit allen sichtbaren Gegenständen (nur bei Änderungen)."""
        try:
            objs = [{"label": it.label, "score": it.score, "bbox": it.bbox}
                    for it in self.items.values() if it.state == "present"]
            img = draw_objects(image, objs)
            h, w = img.shape[:2]
            if w > 1920:
                img = cv2.resize(img, (1920, int(round(h * 1920 / w))), interpolation=cv2.INTER_AREA)
            path = f"{day_dir('inventory', frame_ts)}/cam{self.camera_id}_{int(frame_ts)}.jpg"
            return write_jpeg(path, img)
        except Exception as e:  # noqa: BLE001
            log.warning("inventory snapshot write failed: %s", e)
            return None

    def items_msg(self) -> list[dict]:
        return [it.to_msg() for it in self.items.values()]


# ============================================================================ Engine
class CamInv:
    def __init__(self, cfg: dict):
        self.id = int(cfg["id"])
        self.cfg = cfg
        self.grabber = KeyframeGrabber(self.id)
        self.state = InventoryState(self.id)
        self.next_due = time.time() + 2.0     # Ringpuffer braucht ein paar Sekunden
        self.interval_eff = self.interval
        self.removed = False
        self.persons: deque[tuple[float, list[list[float]]]] = deque(maxlen=400)
        self.last_gpu_s = 0.0
        self.width = 0
        self.height = 0
        self.fail_count = 0
        self.runs_done = 0

    @property
    def interval(self) -> float:
        try:
            return min(60.0, max(1.0, float(self.cfg.get("inventory_interval_s") or 3)))
        except (TypeError, ValueError):
            return 3.0

    def vocab(self) -> list[dict]:
        return [e for e in (self.cfg.get("inventory_vocab") or [])
                if e.get("label") and e["label"] not in EXCLUDED_LABELS]

    def persons_at(self, ts: float) -> list[list[float]]:
        hold = settings.INVENTORY_PERSON_HOLD_S
        out = []
        for t, boxes in list(self.persons):
            if ts - hold <= t <= ts + hold:
                out.extend(boxes)
        return out


class InventoryEngine:
    def __init__(self, publish: Publish, gpu: GpuTurn, runtime_of: Callable[[int], object]):
        self.publish = publish
        self.gpu = gpu
        self.runtime_of = runtime_of
        self.session = str(uuid.uuid4())
        self.lock = threading.RLock()
        self.cams: dict[int, CamInv] = {}
        self.confirm_n = 2
        self.miss_m = 3
        self.model: InventoryModel | None = None
        self.status = "off"                       # off | loading | ready | error: …
        self._load_retry_at = 0.0
        self._idle_since = time.time()
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._thread: threading.Thread | None = None
        self.runs: deque[tuple[float, float]] = deque(maxlen=500)     # (ts, gpu ms)
        self.skips: deque[float] = deque(maxlen=500)
        self.cols, self.rows = parse_tiles(settings.INVENTORY_TILES)

    # ------------------------------------------------------------------ config
    def set_settings(self, s: dict | None):
        if not s:
            return
        try:
            self.confirm_n = max(1, min(10, int(s.get("inventory_confirm_n", self.confirm_n))))
            self.miss_m = max(1, min(20, int(s.get("inventory_miss_m", self.miss_m))))
        except (TypeError, ValueError):
            pass

    def configure(self, cameras: list[dict]):
        """Mit allen aktiven Kamera-Konfigurationen aufrufen (aus ``Pipeline.apply_config``)."""
        wanted = {int(c["id"]): c for c in cameras
                  if c.get("enabled", True) and c.get("inventory_enabled") and c.get("detect_url_rtsp")}
        stopped = []
        with self.lock:
            for cid in list(self.cams):
                if cid not in wanted:
                    c = self.cams.pop(cid)
                    c.removed = True
                    stopped.append(c)
            for cid, cfg in wanted.items():
                cur = self.cams.get(cid)
                if cur is None:
                    log.info("inventory on for camera %s (every %ss)", cid, cfg.get("inventory_interval_s"))
                    self.cams[cid] = CamInv(cfg)
                else:
                    changed = cur.cfg.get("inventory_interval_s") != cfg.get("inventory_interval_s")
                    cur.cfg = cfg
                    if changed:
                        cur.interval_eff = cur.interval
                        cur.next_due = min(cur.next_due, time.time() + 1.0)
            for c in stopped:
                log.info("inventory off for camera %s", c.id)
                self._publish_stopped(c)
        if wanted and self._thread is None:
            self._thread = threading.Thread(target=self._run, name="inventory", daemon=True)
            self._thread.start()
        self._wake.set()

    def note_persons(self, camera_id: int, ts: float, boxes: list[list[float]]):
        c = self.cams.get(camera_id)
        if c is not None:
            c.persons.append((ts, boxes))

    def wants_persons(self, camera_id: int) -> bool:
        return camera_id in self.cams

    def stop(self):
        self._stop.set()
        self._wake.set()
        with self.lock:
            cams = list(self.cams.values())
            self.cams.clear()
            for c in cams:
                c.removed = True
                self._publish_stopped(c)

    # ------------------------------------------------------------------ status
    def status_text(self) -> str:
        return self.status

    def perf(self) -> dict:
        now = time.time()
        r = [m for t, m in list(self.runs) if now - t <= 60]
        return {"runs_per_min": len(r), "ms": round(sum(r) / len(r), 1) if r else 0.0,
                "skipped": sum(1 for t in list(self.skips) if now - t <= 60), "cameras": len(self.cams)}

    # ------------------------------------------------------------------ messages
    def _base_msg(self, c: CamInv, frame_ts: float) -> dict:
        return {"camera_id": c.id, "ts": round(time.time(), 3), "frame_ts": round(frame_ts, 3),
                "session": self.session, "interval_s": round(c.interval_eff, 2),
                "model": self.model.model_name if self.model else os.path.splitext(os.path.basename(settings.INVENTORY_MODEL))[0],
                "width": c.width or None, "height": c.height or None}

    def _publish_stopped(self, c: CamInv):
        m = self._base_msg(c, time.time())
        m.update({"snapshot_path": None, "stopped": True, "items": [], "changes": []})
        self.publish(f"vg/internal/inventory/{c.id}", m, 1, True)

    # ------------------------------------------------------------------ thread
    def _ensure_model(self) -> bool:
        if self.model is not None:
            return True
        if time.time() < self._load_retry_at:
            return False
        self.status = "loading"
        t0 = time.time()
        try:
            self.model = InventoryModel()
            self.status = "ready"
            log.info("inventory model %s ready in %.1fs (text prompts=%s, device=%s, fp16=%s)",
                     self.model.model_name, time.time() - t0, self.model.text_prompts, self.model.device, self.model.half)
            return True
        except Exception as e:  # noqa: BLE001
            self.model = None
            self.status = f"error: {type(e).__name__}: {e}"[:300]
            self._load_retry_at = time.time() + 300
            log.error("inventory model failed to load: %s", e, exc_info=True)
            return False

    def _run(self):
        last_err_log = 0.0
        while not self._stop.is_set():
            with self.lock:
                cams = list(self.cams.values())
            now = time.time()
            if not cams:
                if self.model is not None and now - self._idle_since > settings.INVENTORY_UNLOAD_S:
                    log.info("no inventory camera for %.0fs – unloading model", settings.INVENTORY_UNLOAD_S)
                    self.model.close()
                    self.model = None
                    self.status = "off"
                self._wake.wait(1.0)
                self._wake.clear()
                continue
            self._idle_since = now
            if not self._ensure_model():
                self._wake.wait(2.0)
                self._wake.clear()
                continue
            try:
                union: dict[str, dict] = {}
                for c in cams:
                    for e in c.vocab():
                        union.setdefault(e["label"], e)
                self.model.set_vocab(list(union.values()))
            except Exception as e:  # noqa: BLE001
                if now - last_err_log > 60:
                    log.error("inventory vocabulary failed: %s", e, exc_info=True)
                    last_err_log = now
                if self.status == "ready":
                    self.status = f"error: Vokabular: {e}"[:300]
                self._wake.wait(30.0)
                self._wake.clear()
                continue
            if self.status.startswith("error: Vokabular"):
                self.status = "ready"
            c = min(cams, key=lambda x: x.next_due)
            if c.next_due > now:
                self._wake.wait(min(0.5, c.next_due - now))
                self._wake.clear()
                continue
            # Takte, die wegen eines laufenden Durchlaufs/Budgets nicht drankamen, verfallen (nicht nachholen)
            overdue = now - c.next_due
            if c.runs_done and overdue > c.interval_eff:
                for _ in range(min(int(overdue // c.interval_eff), 10)):
                    self.skips.append(now)
            try:
                self._run_camera(c, len(cams))
            except Exception as e:  # noqa: BLE001
                c.next_due = time.time() + max(5.0, c.interval)
                if now - last_err_log > 30:
                    log.warning("inventory run camera %s failed: %s", c.id, e, exc_info=True)
                    last_err_log = now

    def _run_camera(self, c: CamInv, n_cams: int):
        t_start = time.time()
        rt = self.runtime_of(c.id)
        got = c.grabber.grab(rt, c.cfg)
        if got is None:
            c.fail_count += 1
            if c.fail_count % 20 == 1 and c.grabber.last_error:
                log.info("inventory camera %s: no keyframe yet (%s)", c.id, c.grabber.last_error)
            c.next_due = time.time() + (0.5 if c.fail_count < 20 else 3.0)
            return
        c.fail_count = 0
        image, frame_ts = got
        h, w = image.shape[:2]
        c.width, c.height = w, h
        cfg = c.cfg
        vocab = c.vocab()
        labels = {e["label"] for e in vocab}
        try:
            cam_min = float(cfg.get("inventory_min_score") or 0.35)
        except (TypeError, ValueError):
            cam_min = 0.35
        thr = {e["label"]: (float(e["min_score"]) if e.get("min_score") is not None else cam_min) for e in vocab}
        conf = max(settings.INVENTORY_MIN_CONF, min(thr.values()) if thr else cam_min)

        # --- Inferenz: Gesamtbild + Kacheln, jeder Aufruf in einer eigenen GPU-Lücke
        jobs: list[tuple[np.ndarray, int, int, int, tuple | None]] = [(image, settings.INVENTORY_IMGSZ, 0, 0, None)]
        if cfg.get("inventory_tiling", False) and self.cols:
            for (x1, y1, x2, y2) in tile_boxes(w, h, self.cols, self.rows, settings.INVENTORY_OVERLAP):
                jobs.append((np.ascontiguousarray(image[y1:y2, x1:x2]), settings.INVENTORY_TILE_IMGSZ, x1, y1, (x1, y1, x2, y2)))
        gpu_s = 0.0
        raw: list[tuple[str, str, float, list[float]]] = []
        self.model.warmup(settings.INVENTORY_IMGSZ)          # nur nach Laden/Vokabularwechsel, ohne Lock
        for img, imgsz, ox, oy, tile in jobs:
            if self._stop.is_set() or c.removed:
                return
            small, scale = self.model.prepare(img, imgsz)     # CPU-Arbeit vor dem Lock
            lock = self.gpu.inventory_slot()
            with lock:
                t0 = time.perf_counter()
                dets = self.model.predict(small, imgsz, conf, scale)
                gpu_s += time.perf_counter() - t0
            th_, tw_ = img.shape[:2]
            for label, prompt, score, (bx1, by1, bx2, by2) in dets:
                if label not in labels or score < thr.get(label, cam_min):
                    continue
                if tile is not None:
                    # an einer *inneren* Kachelkante abgeschnitten → das Gesamtbild/die Nachbarkachel hat es ganz
                    if (bx1 <= EDGE_PX and tile[0] > 0) or (by1 <= EDGE_PX and tile[1] > 0) or \
                            (bx2 >= tw_ - EDGE_PX and tile[2] < w) or (by2 >= th_ - EDGE_PX and tile[3] < h):
                        continue
                X1, Y1, X2, Y2 = bx1 + ox, by1 + oy, bx2 + ox, by2 + oy
                raw.append((label, prompt, score, [max(0.0, X1 / w), max(0.0, Y1 / h), min(1.0, X2 / w), min(1.0, Y2 / h)]))
        dets = merge_detections(raw)

        # --- Stabilisierung + Nachricht
        persons = c.persons_at(frame_ts)
        changes = c.state.update(dets, frame_ts, image, persons, self.confirm_n, self.miss_m,
                                 cfg.get("zones") or [], labels)
        snapshot = c.state.write_snapshot(image, frame_ts) if changes else None
        c.last_gpu_s = gpu_s
        # Rechenbudget: Summe(GPU-Zeit / Intervall) über alle Inventar-Kameras ≤ MAX_BUSY
        budget = max(0.01, settings.INVENTORY_MAX_BUSY)
        c.interval_eff = max(c.interval, gpu_s * max(1, n_cams) / budget)
        c.next_due = t_start + c.interval_eff
        c.runs_done += 1
        self.runs.append((time.time(), gpu_s * 1000.0))
        with self.lock:
            if c.removed:
                return                              # inzwischen abgeschaltet → "stopped" ist schon raus
            m = self._base_msg(c, frame_ts)
            m.update({"snapshot_path": snapshot, "stopped": False, "items": c.state.items_msg(), "changes": changes})
            self.publish(f"vg/internal/inventory/{c.id}", m, 1, True)
        if changes:
            log.info("inventory camera %s: %s (%d items, %.0f ms GPU, source %s)", c.id,
                     ", ".join(f"{x['type']}" for x in changes), len(c.state.items), gpu_s * 1000, c.grabber.source)
