"""Small helpers: media paths, JPEG writing, geometry."""
from __future__ import annotations

import contextlib
import datetime as _dt
import logging
import os
import threading
import uuid

import cv2
import numpy as np

from . import settings

log = logging.getLogger("vg.util")

# Files in the shared media volume must be manageable by the backend (which may run as another user).
os.umask(0o002)


_FFMPEG_MAJOR: int | None = None


def ffmpeg_major() -> int:
    global _FFMPEG_MAJOR
    if _FFMPEG_MAJOR is None:
        import re
        import subprocess
        try:
            out = subprocess.run(["ffmpeg", "-version"], capture_output=True, text=True, timeout=10).stdout
            m = re.search(r"ffmpeg version n?(\d+)", out)
            _FFMPEG_MAJOR = int(m.group(1)) if m else 6
        except Exception:  # noqa: BLE001
            _FFMPEG_MAJOR = 6
    return _FFMPEG_MAJOR


def rtsp_input_args(url: str, timeout_s: float = 10.0) -> list[str]:
    """Input options for RTSP over TCP with a socket timeout (option name differs between ffmpeg 4 and 5+)."""
    if not url.startswith(("rtsp://", "rtsps://")):
        return []
    opt = "-stimeout" if ffmpeg_major() < 5 else "-timeout"
    return ["-rtsp_transport", "tcp", opt, str(int(timeout_s * 1e6))]


def day_dir(kind: str, ts: float | None = None) -> str:
    """Relative directory like 'crops/2026-10-24'."""
    d = _dt.datetime.fromtimestamp(ts) if ts else _dt.datetime.now()
    return f"{kind}/{d:%Y-%m-%d}"


def new_media_path(kind: str, ext: str = "jpg", ts: float | None = None) -> str:
    return f"{day_dir(kind, ts)}/{uuid.uuid4().hex}.{ext}"


def abs_media_path(rel: str) -> str:
    """Resolve a relative media path safely inside MEDIA_DIR."""
    rel = rel.lstrip("/")
    root = os.path.realpath(settings.MEDIA_DIR)
    full = os.path.realpath(os.path.join(root, rel))
    if not (full == root or full.startswith(root + os.sep)):
        raise ValueError(f"path escapes media dir: {rel!r}")
    return full


# --- Medien schreiben -----------------------------------------------------------------------------
# Alle Medien (Crops, Snapshots, Inventar-Bilder, Clips) gehen AUSSCHLIESSLICH über store_media() bzw.
# media_target(). Heute: atomar ins gemeinsame Volume. Später (D3, VG_MEDIA_MODE=upload) wird nur hier
# auf `PUT /api/internal/media/{rel_path}` umgestellt – kein anderer Code schreibt nach MEDIA_DIR.

@contextlib.contextmanager
def media_target(rel_path: str, suffix: str = ""):
    """Temporäre Zieldatei für Medien, die ein externer Prozess (ffmpeg) erzeugt.

    ``with media_target("clips/…/x.mp4", ".mp4") as tmp: <tmp schreiben>`` – bei Erfolg wird ``tmp`` atomar
    unter ``rel_path`` abgelegt, bei einer Ausnahme verworfen. ``suffix`` erhält die Endung (ffmpeg erkennt
    daran das Format)."""
    full = abs_media_path(rel_path)
    os.makedirs(os.path.dirname(full), exist_ok=True)
    tmp = f"{full}.part{os.getpid()}-{threading.get_ident()}{suffix}"
    try:
        yield tmp
        if not os.path.exists(tmp):
            raise RuntimeError(f"no output written for {rel_path}")
        os.replace(tmp, full)
    finally:
        try:
            os.remove(tmp)
        except OSError:
            pass


def store_media(rel_path: str, data: bytes) -> str:
    """Speichert Rohdaten atomar unter MEDIA_DIR/rel_path (einzige Schreibstelle für Medien). → rel_path"""
    with media_target(rel_path) as tmp:
        with open(tmp, "wb") as f:
            f.write(data)
    return rel_path


def write_jpeg(rel_path: str, img: np.ndarray, quality: int | None = None) -> str:
    """Encode a JPEG and store it via store_media(). Returns rel_path."""
    ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, int(quality or settings.JPEG_QUALITY)])
    if not ok:
        raise RuntimeError("jpeg encode failed")
    return store_media(rel_path, buf.tobytes())


def with_worker_id(payload):
    """Kopie einer Worker-Nachricht mit ``worker_id`` (D3-Vorbereitung; Nicht-dicts unverändert)."""
    if isinstance(payload, dict):
        return {**payload, "worker_id": settings.WORKER_ID}
    return payload


def crop_norm(frame: np.ndarray, bbox, margin: float = 0.0, region: tuple[float, float] = (0.0, 1.0)) -> np.ndarray | None:
    """Crop a normalized bbox [x1,y1,x2,y2] (optionally only a vertical sub-region) with margin."""
    h, w = frame.shape[:2]
    x1, y1, x2, y2 = bbox
    bh = y2 - y1
    y1, y2 = y1 + bh * region[0], y1 + bh * region[1]
    mx, my = (x2 - x1) * margin, (y2 - y1) * margin
    X1 = int(max(0, (x1 - mx) * w)); X2 = int(min(w, (x2 + mx) * w))
    Y1 = int(max(0, (y1 - my) * h)); Y2 = int(min(h, (y2 + my) * h))
    if X2 - X1 < 4 or Y2 - Y1 < 4:
        return None
    return np.ascontiguousarray(frame[Y1:Y2, X1:X2])


def point_in_polygon(x: float, y: float, poly) -> bool:
    inside = False
    n = len(poly)
    if n < 3:
        return False
    j = n - 1
    for i in range(n):
        xi, yi = poly[i][0], poly[i][1]
        xj, yj = poly[j][0], poly[j][1]
        if (yi > y) != (yj > y):
            xin = (xj - xi) * (y - yi) / ((yj - yi) or 1e-12) + xi
            if x < xin:
                inside = not inside
        j = i
    return inside


_COLORS = {
    "person": (80, 175, 76), "car": (243, 150, 33), "truck": (243, 150, 33), "bus": (243, 150, 33),
    "motorcycle": (0, 152, 255), "bicycle": (0, 152, 255),
    "dog": (176, 39, 156), "cat": (176, 39, 156), "bird": (176, 39, 156), "horse": (176, 39, 156),
}


def draw_objects(frame: np.ndarray, objects: list[dict]) -> np.ndarray:
    img = frame.copy()
    h, w = img.shape[:2]
    th = max(1, int(round(min(w, h) / 360)))
    fs = max(0.45, min(w, h) / 900)
    for o in objects:
        x1, y1, x2, y2 = o["bbox"]
        p1 = (int(x1 * w), int(y1 * h)); p2 = (int(x2 * w), int(y2 * h))
        color = _COLORS.get(o["label"], (0, 200, 255))
        cv2.rectangle(img, p1, p2, color, th + 1)
        text = f"{o['label']} {o['score']:.2f}"
        ident = o.get("identity")
        if ident and ident.get("display_name"):
            text += f" {ident['display_name']}"
        plate = (o.get("attributes") or {}).get("plate_text")
        if plate and not ident:
            text += f" [{plate}]"
        (tw, tht), bl = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, fs, th)
        ty = max(p1[1], tht + bl + 2)
        cv2.rectangle(img, (p1[0], ty - tht - bl - 2), (p1[0] + tw + 4, ty), color, -1)
        cv2.putText(img, text, (p1[0] + 2, ty - bl), cv2.FONT_HERSHEY_SIMPLEX, fs, (255, 255, 255), th, cv2.LINE_AA)
    return img
