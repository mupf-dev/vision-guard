"""Per-camera frame ingest via an ffmpeg subprocess (rawvideo bgr24 on stdout).

Always keeps only the *latest* frame (drop instead of queue). Auto-reconnects with backoff.
"""
from __future__ import annotations

import json
import logging
import subprocess
import threading
import time
from collections import deque
from dataclasses import dataclass

import numpy as np

from . import settings
from .util import rtsp_input_args

log = logging.getLogger("vg.ingest")

_HWACCEL_BROKEN = False  # set globally once CUDA decode failed to produce frames


@dataclass
class Frame:
    seq: int
    ts: float           # wall clock (unix seconds) when the frame was received
    image: np.ndarray   # HxWx3 BGR (decode resolution, max VG_MAX_FRAME_WIDTH wide)


def probe(url: str, timeout: float = 15.0) -> tuple[int, int, str] | None:
    """Return (width, height, codec) of the first video stream or None."""
    cmd = ["ffprobe", "-v", "error", *rtsp_input_args(url, timeout),
           "-select_streams", "v:0", "-show_entries", "stream=width,height,codec_name", "-of", "json", url]
    try:
        out = subprocess.run(cmd, capture_output=True, timeout=timeout + 5, check=False)
        st = json.loads(out.stdout or b"{}").get("streams") or []
        if not st or not st[0].get("width"):
            return None
        return int(st[0]["width"]), int(st[0]["height"]), st[0].get("codec_name", "")
    except Exception as e:  # noqa: BLE001
        log.debug("probe %s failed: %s", url, e)
        return None


class FrameSource:
    """Background thread decoding one stream at `fps` frames/s."""

    def __init__(self, name: str, url: str, fps: float):
        self.name = name
        self.url = url
        self.fps = max(0.5, float(fps or 5))
        self.state = "starting"          # starting | online | offline
        self.width = 0                   # source resolution
        self.height = 0
        self.out_w = 0                   # decode resolution
        self.out_h = 0
        self.last_error = ""
        self._lock = threading.Lock()
        self._cond = threading.Condition(self._lock)
        self._latest: Frame | None = None
        self._seq = 0
        self._stop = threading.Event()
        self._proc: subprocess.Popen | None = None
        self._recv_times: deque[float] = deque(maxlen=64)
        self._thread = threading.Thread(target=self._run, name=f"ingest-{name}", daemon=True)

    # -- public -------------------------------------------------------------
    def start(self):
        self._thread.start()

    def stop(self):
        self._stop.set()
        self._kill()
        with self._cond:
            self._cond.notify_all()

    def join(self, timeout=5.0):
        self._thread.join(timeout)

    def latest(self) -> Frame | None:
        with self._lock:
            return self._latest

    def wait_newer(self, seq: int, timeout: float) -> Frame | None:
        with self._cond:
            if self._latest is None or self._latest.seq <= seq:
                self._cond.wait(timeout)
            f = self._latest
            return f if f is not None and f.seq > seq else None

    def recv_fps(self) -> float:
        with self._lock:
            t = list(self._recv_times)
        now = time.time()
        t = [x for x in t if now - x <= 5.0]
        if len(t) < 2:
            return 0.0
        return (len(t) - 1) / max(1e-3, t[-1] - t[0])

    # -- internals ----------------------------------------------------------
    def _kill(self):
        p = self._proc
        if p and p.poll() is None:
            try:
                p.kill()
            except Exception:  # noqa: BLE001
                pass

    def _cmd(self, hw: bool) -> list[str]:
        cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin"]
        if self.url.startswith("rtsp"):
            cmd += rtsp_input_args(self.url)
        elif not self.url.startswith(("http", "rtmp")):
            cmd += ["-re", "-stream_loop", "-1"]   # local file (tests)
        if hw:
            cmd += ["-hwaccel", "cuda"]
        cmd += ["-fflags", "nobuffer", "-flags", "low_delay", "-i", self.url, "-an", "-sn", "-dn",
                "-vf", f"fps={self.fps:g},scale={self.out_w}:{self.out_h}",
                "-pix_fmt", "bgr24", "-f", "rawvideo", "pipe:1"]
        return cmd

    def _set_state(self, st: str):
        if st != self.state:
            log.info("[%s] %s -> %s%s", self.name, self.state, st, f" ({self.last_error})" if st == "offline" and self.last_error else "")
            self.state = st

    def _run(self):
        global _HWACCEL_BROKEN
        backoff = 1.0
        while not self._stop.is_set():
            info = probe(self.url)
            if self._stop.is_set():
                break
            if not info:
                self.last_error = "stream not reachable"
                self._set_state("offline")
                self._stop.wait(backoff)
                backoff = min(backoff * 2, 30.0)
                continue
            self.width, self.height, codec = info
            scale = min(1.0, settings.MAX_FRAME_WIDTH / self.width)
            self.out_w = max(2, int(self.width * scale) // 2 * 2)
            self.out_h = max(2, int(self.height * scale) // 2 * 2)
            use_hw = settings.HWACCEL in ("cuda", "auto") and not _HWACCEL_BROKEN and codec in ("h264", "hevc")
            got = self._read_loop(use_hw)
            if self._stop.is_set():
                break
            if use_hw and got == 0 and settings.HWACCEL == "auto":
                log.warning("[%s] CUDA decode produced no frames – falling back to CPU decode", self.name)
                _HWACCEL_BROKEN = True
                continue
            self._set_state("offline")
            if got > 0:
                backoff = 1.0
            self._stop.wait(backoff)
            backoff = min(backoff * 2, 30.0)
        self._set_state("offline")

    def _read_loop(self, hw: bool) -> int:
        n = self.out_w * self.out_h * 3
        cmd = self._cmd(hw)
        log.debug("[%s] %s", self.name, " ".join(cmd))
        try:
            self._proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, bufsize=n * 2)
        except Exception as e:  # noqa: BLE001
            self.last_error = f"ffmpeg start failed: {e}"
            return 0
        proc = self._proc
        err_lines: deque[str] = deque(maxlen=5)

        def _stderr():
            for line in iter(proc.stderr.readline, b""):
                s = line.decode(errors="replace").strip()
                if s:
                    err_lines.append(s)
        threading.Thread(target=_stderr, daemon=True, name=f"ffmpeg-err-{self.name}").start()

        # watchdog: kill ffmpeg if no frame arrives for STALL_TIMEOUT
        last = [time.time()]
        first = [True]

        def _watch():
            while proc.poll() is None and not self._stop.is_set():
                time.sleep(1.0)
                # erstes Bild darf länger dauern (z. B. YouTube/HLS: go2rtc muss die Quelle erst öffnen)
                limit = max(settings.STALL_TIMEOUT_S, 3.0 / self.fps)
                if first[0]:
                    limit = max(limit, settings.FIRST_FRAME_TIMEOUT_S)
                if time.time() - last[0] > limit:
                    self.last_error = "stream stalled"
                    log.warning("[%s] no frames for %.0fs – restarting ffmpeg", self.name, time.time() - last[0])
                    self._kill()
                    return
        threading.Thread(target=_watch, daemon=True, name=f"ffmpeg-wd-{self.name}").start()

        got = 0
        tokens, t_prev = 1.0, time.time()
        try:
            while not self._stop.is_set():
                buf = proc.stdout.read(n)
                if not buf or len(buf) < n:
                    break
                now = time.time()
                last[0] = now
                first[0] = False
                # Quellen mit unzuverlässigen Zeitstempeln (z. B. HLS/YouTube) liefern Frames schubweise und mehr
                # als detect_fps: per Token-Bucket nach Wanduhr drosseln (toleriert Jitter, verhindert Schübe)
                tokens = min(1.5, tokens + (now - t_prev) * self.fps)
                t_prev = now
                if tokens < 0.8:
                    continue
                tokens -= 1.0
                img = np.frombuffer(buf, dtype=np.uint8).reshape(self.out_h, self.out_w, 3)
                got += 1
                with self._cond:
                    self._seq += 1
                    self._latest = Frame(self._seq, now, img)
                    self._recv_times.append(now)
                    self._cond.notify_all()
                if got == 1:
                    self.last_error = ""
                    self._set_state("online")
        finally:
            self._kill()
            try:
                proc.wait(5)
            except Exception:  # noqa: BLE001
                pass
            if err_lines and not self._stop.is_set():
                self.last_error = err_lines[-1][:200]
                log.warning("[%s] ffmpeg exited (%s): %s", self.name, proc.returncode, " | ".join(err_lines))
        return got
