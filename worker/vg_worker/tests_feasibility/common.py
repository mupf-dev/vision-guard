"""Gemeinsame Helfer der Machbarkeitstests: VRAM-Messung, Zeitmessung, Bilder holen.

VRAM wird auf zwei Ebenen gemessen:
- **Gerät (NVML/`mem_get_info`)**: belegter Speicher der ganzen GPU inkl. produktivem Worker und eigenem
  CUDA-Kontext. Ein Hintergrund-Thread tastet alle 20 ms ab und merkt sich die Spitze. Das ist die Zahl, die gegen
  das Budget (Dauerlast ≤ 4,5 GB, Spitze ≤ 5,6 GB) geprüft wird.
- **torch**: `max_memory_allocated`/`max_memory_reserved` dieses Prozesses (ohne CUDA-Kontext).
Unter WSL liefert NVML keine Werte je Prozess; deshalb wird der Gerätewert vor dem Laden als Basis genommen.
"""
from __future__ import annotations

import os
import statistics
import threading
import time

MiB = 1024 * 1024


class VramProbe:
    def __init__(self, interval: float = 0.02):
        self.interval = interval
        self.peak = 0
        self._stop = threading.Event()
        self._t: threading.Thread | None = None
        self._h = None
        try:
            import pynvml
            pynvml.nvmlInit()
            self._nv = pynvml
            self._h = pynvml.nvmlDeviceGetHandleByIndex(0)
        except Exception:
            self._nv = None

    def used(self) -> int:
        if self._h is not None:
            return int(self._nv.nvmlDeviceGetMemoryInfo(self._h).used)
        import torch
        free, total = torch.cuda.mem_get_info()
        return int(total - free)

    def total(self) -> int:
        if self._h is not None:
            return int(self._nv.nvmlDeviceGetMemoryInfo(self._h).total)
        import torch
        return int(torch.cuda.mem_get_info()[1])

    def gpu_name(self) -> str:
        if self._h is not None:
            n = self._nv.nvmlDeviceGetName(self._h)
            return n.decode() if isinstance(n, bytes) else n
        return "?"

    def _run(self):
        while not self._stop.is_set():
            try:
                self.peak = max(self.peak, self.used())
            except Exception:
                pass
            time.sleep(self.interval)

    def start(self):
        self.peak = self.used()
        self._stop.clear()
        self._t = threading.Thread(target=self._run, daemon=True)
        self._t.start()
        return self

    def reset_peak(self):
        self.peak = self.used()

    def stop(self) -> int:
        self._stop.set()
        if self._t:
            self._t.join(1)
        return self.peak


def mib(x: float | int | None) -> float | None:
    return None if x is None else round(x / MiB, 1)


def torch_mem() -> dict:
    import torch
    if not torch.cuda.is_available():
        return {}
    return {"alloc_peak_mib": mib(torch.cuda.max_memory_allocated()),
            "reserved_peak_mib": mib(torch.cuda.max_memory_reserved()),
            "reserved_now_mib": mib(torch.cuda.memory_reserved())}


def timeit(fn, n: int = 20, warmup: int = 3, sync: bool = True) -> dict:
    import torch
    for _ in range(warmup):
        fn()
    if sync and torch.cuda.is_available():
        torch.cuda.synchronize()
    ts = []
    for _ in range(n):
        t0 = time.perf_counter()
        fn()
        if sync and torch.cuda.is_available():
            torch.cuda.synchronize()
        ts.append((time.perf_counter() - t0) * 1000)
    ts.sort()
    return {"median_ms": round(statistics.median(ts), 2), "p90_ms": round(ts[int(0.9 * (len(ts) - 1))], 2),
            "min_ms": round(ts[0], 2), "n": n}


def grab_frame(src: str, timeout: float = 15.0):
    """Standbild über go2rtc (volle Auflösung) als BGR-Array. `src` = go2rtc-Streamname oder rtsp://-URL."""
    import cv2
    import numpy as np
    import requests
    if src.startswith("rtsp://"):
        cap = cv2.VideoCapture(src, cv2.CAP_FFMPEG)
        try:
            for _ in range(int(timeout * 10)):
                ok, img = cap.read()
                if ok:
                    return img
                time.sleep(0.1)
        finally:
            cap.release()
        raise RuntimeError(f"kein Bild von {src}")
    base = os.environ.get("VG_GO2RTC_URL", "http://go2rtc:1984").rstrip("/")
    r = requests.get(f"{base}/api/frame.jpeg", params={"src": src}, timeout=timeout)
    r.raise_for_status()
    img = cv2.imdecode(np.frombuffer(r.content, np.uint8), cv2.IMREAD_COLOR)
    if img is None:
        raise RuntimeError(f"kein Bild von {src}")
    return img


def read_video_frames(path: str, every: int = 1, max_frames: int | None = None, crop: tuple | None = None):
    """Liefert (index, t_s, frame) aus einer Videodatei. crop=(x1,y1,x2,y2) in Pixeln."""
    import cv2
    cap = cv2.VideoCapture(path)
    fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
    i = n = 0
    while True:
        ok, f = cap.read()
        if not ok:
            break
        if i % every == 0:
            if crop:
                x1, y1, x2, y2 = crop
                f = f[y1:y2, x1:x2]
            yield i, i / fps, f
            n += 1
            if max_frames and n >= max_frames:
                break
        i += 1
    cap.release()
