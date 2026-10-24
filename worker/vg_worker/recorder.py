"""Cheap pre-event ring buffer: ffmpeg `-c copy` segment muxer into tmpfs + clip export.

Segments are MPEG-TS (robust against abrupt kills, stream copy only). Each segment file is named
by its wall-clock start (strftime), so clips can be cut by time without extra bookkeeping.
"""
from __future__ import annotations

import glob
import logging
import os
import shutil
import subprocess
import threading
import time
from collections import deque

from . import settings
from .util import abs_media_path, rtsp_input_args

log = logging.getLogger("vg.recorder")


class RingRecorder:
    def __init__(self, name: str, url: str):
        self.name = name
        self.url = url
        self.dir = os.path.join(settings.BUFFER_DIR, name)
        self.state = "starting"
        self._stop = threading.Event()
        self._proc: subprocess.Popen | None = None
        self._thread = threading.Thread(target=self._run, name=f"rec-{name}", daemon=True)

    def start(self):
        if os.path.isdir(self.dir):
            shutil.rmtree(self.dir, ignore_errors=True)
        os.makedirs(self.dir, exist_ok=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        p = self._proc
        if p and p.poll() is None:
            try:
                p.terminate()
                p.wait(3)
            except Exception:  # noqa: BLE001
                try:
                    p.kill()
                except Exception:  # noqa: BLE001
                    pass

    def join(self, timeout=5.0):
        self._thread.join(timeout)
        shutil.rmtree(self.dir, ignore_errors=True)

    # -- segment muxer ------------------------------------------------------
    def _cmd(self) -> list[str]:
        cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin"]
        if self.url.startswith("rtsp"):
            cmd += rtsp_input_args(self.url)
        elif not self.url.startswith(("http", "rtmp")):
            cmd += ["-re", "-stream_loop", "-1"]
        cmd += ["-fflags", "+genpts", "-i", self.url,
                "-map", "0:v:0", "-map", "0:a:0?", "-c", "copy",
                "-f", "segment", "-segment_time", f"{settings.BUFFER_SEGMENT_S:g}",
                "-segment_format", "mpegts", "-reset_timestamps", "1", "-strftime", "1",
                os.path.join(self.dir, "%s.ts")]
        return cmd

    def _run(self):
        backoff = 1.0
        while not self._stop.is_set():
            t0 = time.time()
            err: deque[str] = deque(maxlen=5)
            try:
                self._proc = subprocess.Popen(self._cmd(), stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
                self.state = "online"
                cleaner = threading.Thread(target=self._clean_loop, args=(self._proc,), daemon=True)
                cleaner.start()
                for line in iter(self._proc.stderr.readline, b""):
                    s = line.decode(errors="replace").strip()
                    if s:
                        err.append(s)
                self._proc.wait()
            except Exception as e:  # noqa: BLE001
                err.append(str(e))
            if self._stop.is_set():
                break
            self.state = "offline"
            log.warning("[%s] segment recorder exited (%s): %s", self.name,
                        self._proc.returncode if self._proc else "?", " | ".join(err))
            if time.time() - t0 > 30:
                backoff = 1.0
            self._stop.wait(backoff)
            backoff = min(backoff * 2, 30.0)

    def _segments(self) -> list[tuple[float, str]]:
        segs = []
        for p in glob.glob(os.path.join(self.dir, "*.ts")):
            try:
                segs.append((float(os.path.basename(p)[:-3]), p))
            except ValueError:
                continue
        segs.sort()
        return segs

    def _clean_loop(self, proc):
        while proc.poll() is None and not self._stop.is_set():
            self._stop.wait(2.0)
            cutoff = time.time() - settings.BUFFER_KEEP_S - settings.BUFFER_SEGMENT_S
            for ts, p in self._segments()[:-2]:
                if ts < cutoff:
                    try:
                        os.remove(p)
                    except OSError:
                        pass

    # -- clip export --------------------------------------------------------
    def export_clip(self, rel_path: str, start: float, end: float) -> str:
        """Concat all segments overlapping [start, end] into an mp4 (stream copy). Blocks."""
        # wait until the segment covering `end` is closed (a newer segment exists) – max 2 segments extra
        deadline = time.time() + settings.BUFFER_SEGMENT_S * 3 + 5
        while time.time() < deadline:
            segs = self._segments()
            if segs and segs[-1][0] > end:
                break
            time.sleep(0.25)
        segs = self._segments()
        if not segs:
            raise RuntimeError("ring buffer empty (camera offline?)")
        chosen = []
        for i, (ts, p) in enumerate(segs):
            nxt = segs[i + 1][0] if i + 1 < len(segs) else time.time()
            if nxt <= start or ts > end:
                continue
            if i == len(segs) - 1:
                continue  # still being written
            chosen.append(p)
        if not chosen:
            # fall back to everything completed
            chosen = [p for _, p in segs[:-1]]
        if not chosen:
            raise RuntimeError("no completed segments in buffer")
        full = abs_media_path(rel_path)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        lst = os.path.join(self.dir, f"concat-{os.getpid()}-{threading.get_ident()}.txt")
        # hardlink/copy segments first so the cleaner cannot delete them mid-concat
        tmpdir = os.path.join(self.dir, f"clip-{threading.get_ident()}-{int(time.time()*1000)}")
        os.makedirs(tmpdir, exist_ok=True)
        try:
            local = []
            for p in chosen:
                q = os.path.join(tmpdir, os.path.basename(p))
                try:
                    os.link(p, q)
                except OSError:
                    try:
                        shutil.copy(p, q)
                    except OSError:
                        continue
                local.append(q)
            if not local:
                raise RuntimeError("segments vanished")
            with open(lst, "w") as f:
                for q in local:
                    f.write(f"file '{q}'\n")
            tmp_out = full + ".part.mp4"
            cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
                   "-f", "concat", "-safe", "0", "-i", lst,
                   "-c", "copy", "-movflags", "+faststart", tmp_out]
            r = subprocess.run(cmd, capture_output=True, timeout=120)
            if r.returncode != 0 or not os.path.exists(tmp_out):
                # retry with timestamp regeneration + AAC-less output (audio codecs like pcm_alaw can't go to mp4)
                cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
                       "-fflags", "+genpts", "-f", "concat", "-safe", "0", "-i", lst,
                       "-map", "0:v:0", "-c", "copy", "-movflags", "+faststart", tmp_out]
                r = subprocess.run(cmd, capture_output=True, timeout=120)
            if r.returncode != 0:
                raise RuntimeError(f"ffmpeg concat failed: {r.stderr.decode(errors='replace')[-300:]}")
            os.replace(tmp_out, full)
            return rel_path
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)
            try:
                os.remove(lst)
            except OSError:
                pass
