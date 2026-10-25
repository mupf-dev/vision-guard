"""Identitäten 07 (Paket B): Gesichtsausschnitte aus der vollen Auflösung des Hauptstroms – CONTRACT ID.D/ID.G.

Der Analysestrom wird auf `VG_MAX_FRAME_WIDTH` (1920) herunterskaliert; Gesichter wären dort bei `cam_3`
(2880×1616) nur 2/3 so groß. Machbarkeitstest (docs/plan/07-identitaeten-fortschritt.md): ein **zusätzlicher**
ffmpeg-Leser auf dem Hauptstrom in voller Auflösung mit wenigen Bildern/s kostet ≈ 13 % eines CPU-Kerns
(+ 1,5 % fürs Lesen der Pipe) und ≈ 110 MB RSS; NVDEC spart kaum CPU. Deshalb:

- Je Kamera mit wirksamem `face_fullres` (aus `/api/internal/cameras`) ein `FullResReader`, der **nur auf
  Anforderung** läuft: Der Gesichts-Dispatcher ruft `demand()` auf, sobald eine Person getrackt wird; ohne Anforderung
  beendet sich der Leser nach `FACE_FULLRES_IDLE_S`. Ein Wechsel von `face_fullres` startet nur diesen Leser,
  nie den ffmpeg-Prozess des Analysestroms.
- Die letzten `FACE_FULLRES_KEEP` Bilder liegen im RAM (je ≈ 14 MB bei 2880×1616); `frame_near(ts)` liefert das
  zeitlich nächste Bild (Worker-Wanduhr wie `Frame.ts`), höchstens `FACE_FULLRES_MAX_DT_S` entfernt – sonst `None`
  und der Dispatcher schneidet wie bisher aus dem Analysebild.
- Bringt der Hauptstrom keine höhere Auflösung als das Analysebild (≤ 1,15×), läuft kein Leser (`no_gain`).
- Fehler (Strom weg, ffmpeg bricht ab) setzen nur `last_error`; Wiederanlauf mit Backoff bei erneuter Anforderung.
"""
from __future__ import annotations

import logging
import subprocess
import threading
import time
from collections import deque
from dataclasses import dataclass

import numpy as np

from . import settings
from .ingest import probe
from .util import rtsp_input_args

log = logging.getLogger("vg.fullres")


@dataclass
class FullFrame:
    ts: float
    image: np.ndarray       # BGR, volle Auflösung – NUR LESEN


class FullResReader:
    def __init__(self, camera_id: int, url: str):
        self.camera_id = camera_id
        self.url = url
        self.width = 0
        self.height = 0
        self.state = "idle"            # idle | starting | running | no_gain | error
        self.last_error = ""
        self.detect_width = 0          # Breite des Analysebilds (für no_gain)
        self._frames: deque[FullFrame] = deque(maxlen=max(2, settings.FACE_FULLRES_KEEP))
        self._lock = threading.Lock()
        self._last_demand = 0.0
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._proc: subprocess.Popen | None = None
        self._retry_at = 0.0
        self._backoff = 5.0
        self.starts = 0

    # -- öffentlich ---------------------------------------------------------------------------------
    def demand(self, detect_width: int = 0) -> None:
        """Bilder werden gebraucht (Person im Bild). Startet den Leser bei Bedarf (nicht blockierend)."""
        now = time.time()
        self._last_demand = now
        if detect_width:
            self.detect_width = detect_width
        if self.state == "no_gain" or self._stop.is_set():
            return
        t = self._thread
        if (t is None or not t.is_alive()) and now >= self._retry_at:
            self._thread = threading.Thread(target=self._run, name=f"fullres-{self.camera_id}", daemon=True)
            self.state = "starting"
            self._thread.start()

    def frame_near(self, ts: float, max_dt: float | None = None) -> FullFrame | None:
        max_dt = settings.FACE_FULLRES_MAX_DT_S if max_dt is None else max_dt
        with self._lock:
            frames = list(self._frames)
        best = None
        for f in frames:
            d = abs(f.ts - ts)
            if d <= max_dt and (best is None or d < abs(best.ts - ts)):
                best = f
        return best

    def active(self) -> bool:
        return self.state in ("starting", "running")

    def stop(self) -> None:
        self._stop.set()
        self._kill()

    # -- intern -------------------------------------------------------------------------------------
    def _kill(self):
        p = self._proc
        if p is not None and p.poll() is None:
            try:
                p.kill()
            except Exception:  # noqa: BLE001
                pass

    def _run(self):
        try:
            if not self.width:
                info = probe(self.url)
                if not info:
                    raise RuntimeError("Hauptstrom nicht erreichbar")
                self.width, self.height = info[0], info[1]
            if self.detect_width and self.width <= self.detect_width * 1.15:
                self.state = "no_gain"
                log.info("camera %s: main stream %dx%d is not larger than the analysis frame – full resolution "
                         "face crops not needed", self.camera_id, self.width, self.height)
                return
            self._read()
            self._backoff = 5.0
        except Exception as e:  # noqa: BLE001
            self.last_error = str(e)[:200]
            self.state = "error"
            self._retry_at = time.time() + self._backoff
            self._backoff = min(self._backoff * 2, 120.0)
            log.warning("camera %s: full resolution reader failed: %s", self.camera_id, e)
            return
        finally:
            with self._lock:
                self._frames.clear()
        if self.state != "error":
            self.state = "idle"

    def _read(self):
        w, h = self.width, self.height
        n = w * h * 3
        cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin"]
        if self.url.startswith("rtsp"):
            cmd += rtsp_input_args(self.url)
        elif not self.url.startswith(("http", "rtmp")):
            cmd += ["-re", "-stream_loop", "-1"]
        cmd += ["-fflags", "nobuffer", "-flags", "low_delay", "-i", self.url, "-an", "-sn", "-dn",
                "-vf", f"fps={settings.FACE_FULLRES_FPS:g}", "-pix_fmt", "bgr24", "-f", "rawvideo", "pipe:1"]
        self.starts += 1
        log.info("camera %s: full resolution reader on (%dx%d @ %g fps)", self.camera_id, w, h,
                 settings.FACE_FULLRES_FPS)
        self._proc = proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, bufsize=n * 2)
        try:   # größere Pipe: ~16× weniger read()-Aufrufe (und GIL-Wechsel) je 14-MB-Bild
            import fcntl
            fcntl.fcntl(proc.stdout.fileno(), fcntl.F_SETPIPE_SZ, 1 << 20)
        except Exception:  # noqa: BLE001
            pass
        last = [time.time()]

        def _watch():
            while proc.poll() is None:
                time.sleep(1.0)
                idle = time.time() - self._last_demand
                if self._stop.is_set() or idle > settings.FACE_FULLRES_IDLE_S:
                    self._kill()
                    return
                if time.time() - last[0] > max(settings.FIRST_FRAME_TIMEOUT_S, 10.0):
                    self.last_error = "Hauptstrom liefert keine Bilder"
                    self._kill()
                    return
        threading.Thread(target=_watch, daemon=True, name=f"fullres-wd-{self.camera_id}").start()
        got = 0
        try:
            while not self._stop.is_set():
                buf = proc.stdout.read(n)
                if not buf or len(buf) < n:
                    break
                now = time.time()
                last[0] = now
                img = np.frombuffer(buf, dtype=np.uint8).reshape(h, w, 3)
                with self._lock:
                    self._frames.append(FullFrame(now, img))
                got += 1
                if got == 1:
                    self.state = "running"
                    self.last_error = ""
        finally:
            self._kill()
            try:
                proc.wait(5)
            except Exception:  # noqa: BLE001
                pass
        log.info("camera %s: full resolution reader off (%d frames)", self.camera_id, got)
        if got == 0 and time.time() - self._last_demand <= settings.FACE_FULLRES_IDLE_S and not self._stop.is_set():
            raise RuntimeError(self.last_error or "keine Bilder vom Hauptstrom")


class FullResManager:
    """Leser je Kamera nach Konfiguration; Kennzahlen für perf.face."""

    def __init__(self):
        self.readers: dict[int, FullResReader] = {}
        self.lock = threading.Lock()
        self.crop_ms: deque[tuple[float, float]] = deque(maxlen=2000)     # (ts, ms)

    def configure(self, cameras: list[dict]) -> None:
        want = {}
        for c in cameras:
            if c.get("face_fullres") and c.get("face_recognition"):
                url = c.get("main_rtsp") or c.get("detect_url_rtsp")
                if url:
                    want[int(c["id"])] = url
        stop = []
        with self.lock:
            for cid in list(self.readers):
                if cid not in want or self.readers[cid].url != want[cid]:
                    stop.append(self.readers.pop(cid))
            for cid, url in want.items():
                if cid not in self.readers:
                    self.readers[cid] = FullResReader(cid, url)
        for r in stop:
            r.stop()

    def get(self, cid: int) -> FullResReader | None:
        return self.readers.get(cid)

    def note_crop(self, ms: float) -> None:
        self.crop_ms.append((time.time(), ms))

    def status_text(self) -> str:
        with self.lock:
            rs = list(self.readers.values())
        if not rs:
            return "off"
        errs = [r for r in rs if r.state == "error"]
        if errs and len(errs) == len(rs):
            return f"error: {errs[0].last_error or 'Leser ausgefallen'}"[:200]
        return "on"

    def perf(self) -> tuple[int, float]:
        now = time.time()
        d = [m for t, m in list(self.crop_ms) if now - t <= 60]
        return len(d), (round(sum(d) / len(d), 2) if d else 0.0)

    def stop(self) -> None:
        with self.lock:
            rs = list(self.readers.values())
            self.readers.clear()
        for r in rs:
            r.stop()
