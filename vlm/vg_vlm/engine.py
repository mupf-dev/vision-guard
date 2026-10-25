"""Modellverwaltung des vlm-Dienstes: lazy laden (mit GPU-Lease), Warteschlange 1, Zeitlimit, Entladen im Leerlauf.

Das Modell läuft in einem **Kindprozess** (spawn). Gründe:
- Beim Entladen endet der Prozess, damit wird der gesamte VRAM inkl. CUDA-Kontext (≈ 0,1–0,3 GB) wieder frei –
  ein `del model; empty_cache()` im selben Prozess ließe den Kontext stehen.
- Ein hängendes oder abgestürztes Modell (OOM, Kernel-Fehler) lässt sich hart beenden, ohne den Dienst zu verlieren.

Ablauf einer Anfrage (`infer`): höchstens 1 laufende + 1 wartende Anfrage, sonst `Busy` (→ 429). Ist kein Modell
geladen: Settings lesen → GPU-Lease anfragen (`need_mb`, max. 10 s, sonst `Unavailable` → 503) → Kindprozess
starten, Speichergrenze = freier VRAM laut Worker − Reserve → Modell laden. Jede Anfrage verlängert die Lease.
Nach `vlm_idle_unload_s` ohne Anfrage: Kindprozess beenden, Lease zurückgeben (`release`).
"""
from __future__ import annotations

import io
import logging
import multiprocessing as mp
import os
import threading
import time
from typing import Any, Optional

import httpx

from . import models as M
from .lease import LeaseClient

log = logging.getLogger("vg.vlm.engine")

BACKEND_URL = os.environ.get("VG_BACKEND_URL", "http://backend:8000").rstrip("/")
MQTT_HOST = os.environ.get("VG_MQTT_HOST", "mosquitto")
MQTT_PORT = int(os.environ.get("VG_MQTT_PORT", "1883"))
INFER_TIMEOUT_S = float(os.environ.get("VG_VLM_INFER_TIMEOUT_S", "20"))
LOAD_TIMEOUT_S = float(os.environ.get("VG_VLM_LOAD_TIMEOUT_S", "180"))
LEASE_TIMEOUT_S = 10.0
RESERVE_MB = 300               # Abstand zur Speichergrenze (Worker-Spitzen, NVML-Verzögerung)
SETTINGS_TTL_S = 30.0
MAX_SIDE = 2880                # größere Bilder vorher verkleinern (cam_3 hat 2880×1616)

DEFAULT_SETTINGS = {"vlm_enabled": True, "vlm_model": "moondream2-int4", "vlm_idle_unload_s": 120,
                    "vlm_need_mb": 3000}


class Busy(Exception):
    """Warteschlange voll → 429."""


class Unavailable(Exception):
    """Modell nicht verfügbar → 503 (deutsche Meldung)."""


# ============================================================================ Kindprozess
def _child(conn, key: str, cap_mb: Optional[int]) -> None:  # pragma: no cover - läuft im eigenen Prozess
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s vlm-child %(message)s")
    try:
        import torch
        if not torch.cuda.is_available():
            conn.send(("error", "keine GPU verfügbar"))
            return
        torch.cuda.init()
        if cap_mb:
            total = torch.cuda.mem_get_info()[1]
            frac = max(0.05, min(1.0, cap_mb * 1024 * 1024 / total))
            torch.cuda.set_per_process_memory_fraction(frac)
        t0 = time.time()
        model = M.build(key)
        conn.send(("ready", round(time.time() - t0, 2), round(torch.cuda.memory_reserved() / 2**20)))
    except Exception as e:  # noqa: BLE001
        conn.send(("error", f"Laden: {type(e).__name__}: {str(e)[:300]}"))
        return
    while True:
        try:
            msg = conn.recv()
        except (EOFError, OSError):
            return
        if not msg or msg[0] == "quit":
            return
        if msg[0] == "ask":
            _, img_bytes, prompt, max_tokens = msg
            try:
                from PIL import Image
                img = Image.open(io.BytesIO(img_bytes)).convert("RGB")
                t = time.time()
                ans = model.ask(img, prompt, max_tokens)
                conn.send(("ok", ans, round((time.time() - t) * 1000, 1),
                           round(torch.cuda.max_memory_reserved() / 2**20)))
            except Exception as e:  # noqa: BLE001
                conn.send(("error", f"{type(e).__name__}: {str(e)[:300]}"))


# ============================================================================ Hilfen
def parse_yes_no(answer: str) -> str:
    a = (answer or "").strip().lower().lstrip("*\"'`- ")
    first = "".join(ch for ch in a.split(maxsplit=1)[0] if ch.isalpha()) if a else ""
    if first in ("yes", "yeah", "yep", "ja", "true", "correct"):
        return "yes"
    if first in ("no", "nope", "nein", "false"):
        return "no"
    return "unknown"


def build_prompt(question: str, mode: str) -> str:
    q = (question or "").strip()
    if mode == "yesno":
        return f"{q} Answer with yes or no first, then explain in one short sentence."
    return q


def prepare_image(data: bytes, region: Optional[list[float]]) -> bytes:
    """Ausschnitt (normiert x1,y1,x2,y2) und Verkleinerung; liefert JPEG-Bytes."""
    from PIL import Image
    with Image.open(io.BytesIO(data)) as im:
        im = im.convert("RGB")
        if region:
            x1, y1, x2, y2 = (max(0.0, min(1.0, float(v))) for v in region)
            if x2 - x1 >= 0.01 and y2 - y1 >= 0.01:
                im = im.crop((round(x1 * im.width), round(y1 * im.height),
                              round(x2 * im.width), round(y2 * im.height)))
        if max(im.size) > MAX_SIDE:
            im.thumbnail((MAX_SIDE, MAX_SIDE))
        out = io.BytesIO()
        im.save(out, "JPEG", quality=90)
        return out.getvalue()


def nvml_free_mb() -> Optional[int]:
    try:
        import pynvml
        pynvml.nvmlInit()
        h = pynvml.nvmlDeviceGetHandleByIndex(0)
        mi = pynvml.nvmlDeviceGetMemoryInfo(h)
        return int(mi.free / 2**20)
    except Exception:  # noqa: BLE001
        return None


# ============================================================================ Engine
class Engine:
    def __init__(self) -> None:
        self.state = "off"
        self.model_key: Optional[str] = None
        self.vram_mb: Optional[int] = None
        self.last_used: Optional[float] = None
        self.loaded_at: Optional[float] = None
        self.last_error: Optional[str] = None
        self.download: dict[str, str] = {}             # key → "läuft|fertig|Fehler: …"
        self.stats = {"answers": 0, "loads": 0, "unloads": 0, "busy": 0, "errors": 0}
        self._proc: Optional[mp.Process] = None
        self._conn = None
        self._run = threading.Lock()                   # genau eine Inferenz/Ladung zugleich
        self._gate = threading.Lock()
        self._pending = 0
        self._settings: dict[str, Any] = dict(DEFAULT_SETTINGS)
        self._settings_ts = 0.0
        self._stop = threading.Event()
        self._ctx = mp.get_context("spawn")
        self.lease = LeaseClient(MQTT_HOST, MQTT_PORT, holder="vlm", on_lost=self._lease_lost)
        threading.Thread(target=self._idle_loop, name="vlm-idle", daemon=True).start()
        threading.Thread(target=self._predownload, name="vlm-download", daemon=True).start()

    # ------------------------------------------------------------------ Settings
    def settings(self, force: bool = False) -> dict[str, Any]:
        now = time.time()
        if force or now - self._settings_ts > SETTINGS_TTL_S:
            try:
                r = httpx.get(f"{BACKEND_URL}/api/internal/settings", timeout=5)
                if r.status_code == 200:
                    st = r.json()
                    self._settings = {k: st.get(k, v) for k, v in DEFAULT_SETTINGS.items()}
                    self._settings_ts = now
            except httpx.HTTPError as e:
                log.debug("Settings nicht abrufbar: %s", e)
                self._settings_ts = now - SETTINGS_TTL_S + 5    # in 5 s erneut versuchen
        return self._settings

    def _idle_s(self) -> float:
        try:
            return max(30.0, float(self.settings().get("vlm_idle_unload_s") or 120))
        except (TypeError, ValueError):
            return 120.0

    # ------------------------------------------------------------------ Status
    def health(self) -> dict[str, Any]:
        st = self.settings()
        key = self.model_key or st.get("vlm_model")
        return {"state": self.state, "model": key, "vram_mb": self.vram_mb,
                "last_used": round(self.last_used, 3) if self.last_used else None,
                "enabled": bool(st.get("vlm_enabled", True)), "lease": self.lease.held,
                "mqtt": self.lease.connected, "download": self.download.get(str(key)),
                "last_error": self.last_error, "stats": dict(self.stats)}

    # ------------------------------------------------------------------ Laden/Entladen
    def _predownload(self) -> None:
        """Gewichte des eingestellten Modells beim Start ins Volume laden (CPU, kein VRAM)."""
        for _ in range(30):
            st = self.settings(force=True)
            if self._settings_ts:
                break
            time.sleep(5)
        if not st.get("vlm_enabled", True):
            return
        self._ensure_download(str(st.get("vlm_model") or DEFAULT_SETTINGS["vlm_model"]))

    def _ensure_download(self, key: str) -> None:
        if key not in M.MODELS or self.download.get(key) in ("läuft", "fertig"):
            return
        self.download[key] = "läuft"
        t0 = time.time()
        try:
            M.download(key)
            self.download[key] = "fertig"
            log.info("Gewichte %s bereit (%.0f s)", key, time.time() - t0)
        except Exception as e:  # noqa: BLE001
            self.download[key] = f"Fehler: {type(e).__name__}"
            log.warning("Download %s fehlgeschlagen: %s", key, e)

    def _lease_lost(self, msg: dict) -> None:
        log.warning("GPU-Lease vom Worker beendet (%s) – entlade Modell", msg.get("reason"))
        threading.Thread(target=self._kill_child, name="vlm-lost", daemon=True).start()

    def _kill_child(self) -> None:
        proc, conn = self._proc, self._conn
        self._proc, self._conn = None, None
        if proc is not None:
            try:
                if conn is not None:
                    conn.send(("quit",))
            except Exception:  # noqa: BLE001
                pass
            proc.join(5)
            if proc.is_alive():
                proc.kill()
                proc.join(5)
            self.stats["unloads"] += 1
        if conn is not None:
            try:
                conn.close()
            except Exception:  # noqa: BLE001
                pass
        self.model_key = None
        self.vram_mb = None
        self.loaded_at = None
        if not self.state.startswith("error"):
            self.state = "off"

    def unload(self, reason: str = "idle") -> None:
        """Kindprozess beenden und die GPU zurückgeben."""
        had = self._proc is not None
        self._kill_child()
        if self.lease.held:
            self.lease.release()
        if had:
            log.info("Modell entladen (%s), GPU-Lease zurückgegeben", reason)

    def _need_mb(self, key: str) -> int:
        try:
            need = int(self.settings().get("vlm_need_mb") or 3000)
        except (TypeError, ValueError):
            need = 3000
        if key == M.FALLBACK:          # kleines Modell braucht weniger als die Moondream-Vorgabe
            need = min(need, M.NEED_MB[key])
        return need

    def _ensure_loaded(self) -> None:
        st = self.settings()
        if not st.get("vlm_enabled", True):
            raise Unavailable("Lokales Bildmodell ist ausgeschaltet (Einstellungen → Frag die Kamera)")
        key = str(st.get("vlm_model") or DEFAULT_SETTINGS["vlm_model"])
        if key not in M.MODELS:
            raise Unavailable(f"Unbekanntes Bildmodell: {key}")
        if self._proc is not None and self._proc.is_alive() and \
                (self.model_key == key or (self._fallback_ok and self.model_key == M.FALLBACK)):
            return
        if self._proc is not None:
            self._kill_child()           # anderes Modell eingestellt oder Prozess tot – Lease bleibt
        if self.download.get(key) == "läuft":
            raise Unavailable("Bildmodell wird gerade heruntergeladen – bitte später erneut fragen")
        self.state = "loading"
        self.last_error = None
        idle = self._idle_s()
        need = self._need_mb(key)
        t0 = time.time()
        g = self.lease.acquire(need, max_s=min(1800.0, idle + 60.0), timeout=LEASE_TIMEOUT_S)
        if g is None:
            self.state = "off"
            self.last_error = "GPU-Lease: keine Antwort vom Worker binnen 10 s"
            self.lease.request_id = None
            raise Unavailable("Bildmodell nicht verfügbar (Worker antwortet nicht auf die GPU-Anfrage)")
        free = g.get("free_mb")
        self._fallback_ok = False
        if not g.get("ok"):
            err = str(g.get("error") or "")
            if err.startswith("busy"):
                self.state = "off"
                self.last_error = err
                raise Unavailable("Bildmodell nicht verfügbar (GPU gerade von einem anderen Dienst belegt)")
            # zu wenig VRAM: Lease läuft – kleineres Modell versuchen, sonst zurückgeben
            if key != M.FALLBACK and free is not None and free >= M.NEED_MB[M.FALLBACK]:
                log.warning("Nur %s MB frei (Bedarf %s) – nutze %s", free, need, M.FALLBACK)
                key = M.FALLBACK
                self._fallback_ok = True
            else:
                self.lease.release()
                self.state = "off"
                self.last_error = err
                raise Unavailable(f"Bildmodell nicht verfügbar (zu wenig Grafikspeicher: {free} MB frei)")
        cap = int(free - RESERVE_MB) if isinstance(free, (int, float)) and free else None
        parent, child = self._ctx.Pipe()
        proc = self._ctx.Process(target=_child, args=(child, key, cap), name=f"vlm-{key}", daemon=True)
        proc.start()
        child.close()
        self._proc, self._conn = proc, parent
        if not parent.poll(LOAD_TIMEOUT_S):
            self.last_error = f"Laden von {key} dauerte länger als {LOAD_TIMEOUT_S:.0f} s"
            self.unload("timeout")
            self.state = "error: Zeitlimit beim Laden"
            raise Unavailable("Bildmodell nicht verfügbar (Laden dauert zu lange)")
        try:
            msg = parent.recv()
        except (EOFError, OSError):
            msg = ("error", "Kindprozess beendet")
        if msg[0] != "ready":
            self.last_error = str(msg[1])[:300]
            self.unload("error")
            self.state = f"error: {self.last_error[:120]}"
            self.stats["errors"] += 1
            log.warning("Laden von %s fehlgeschlagen: %s", key, self.last_error)
            raise Unavailable("Bildmodell nicht verfügbar (Laden fehlgeschlagen)")
        self.model_key = key
        self.vram_mb = msg[2]
        self.loaded_at = time.time()
        self.last_used = time.time()
        self.state = "ready"
        self.stats["loads"] += 1
        log.info("%s geladen in %.1f s (Modell %.1f s, torch %s MB, frei laut Worker %s MB, Grenze %s MB, "
                 "entladen: %s)", key, time.time() - t0, msg[1], msg[2], free, cap, ",".join(g.get("unloaded") or []))

    _fallback_ok = False

    # ------------------------------------------------------------------ Anfrage
    def infer(self, image: bytes, question: str, region: Optional[list[float]] = None, mode: str = "query",
              max_tokens: Optional[int] = None) -> dict[str, Any]:
        with self._gate:
            if self._pending >= 2:
                self.stats["busy"] += 1
                raise Busy()
            self._pending += 1
        try:
            if not self._run.acquire(timeout=INFER_TIMEOUT_S):
                self.stats["busy"] += 1
                raise Busy()
            try:
                return self._infer_locked(image, question, region, mode, max_tokens)
            finally:
                self._run.release()
        finally:
            with self._gate:
                self._pending -= 1

    def _infer_locked(self, image: bytes, question: str, region, mode: str, max_tokens) -> dict[str, Any]:
        t0 = time.time()
        img = prepare_image(image, region)       # vor dem Laden: ungültiges Bild → ValueError (400)
        self._ensure_loaded()
        # Lease verlängern (die Antwort darf „zu wenig VRAM“ melden – das Modell ist ja schon geladen)
        if self.lease.held:
            self.lease.acquire(self._need_mb(self.model_key or ""), max_s=min(1800.0, self._idle_s() + 60.0),
                               timeout=2.0)
        if not self.lease.held:
            # Lease verloren (Worker-Neustart/Ablauf) – neu anfragen, damit YOLOE entladen bleibt
            g = self.lease.acquire(self._need_mb(self.model_key or ""), max_s=min(1800.0, self._idle_s() + 60.0),
                                   timeout=LEASE_TIMEOUT_S)
            if g is None or str(g.get("error") or "").startswith("busy"):
                self.unload("lease")
                raise Unavailable("Bildmodell nicht verfügbar (GPU-Lease verloren)")
        prompt = build_prompt(question, mode)
        mt = int(max_tokens or (48 if mode == "yesno" else 120))
        try:
            self._conn.send(("ask", img, prompt, mt))
            ok = self._conn.poll(INFER_TIMEOUT_S)
            msg = self._conn.recv() if ok else None
        except (EOFError, OSError, AttributeError):
            msg = None
            ok = False
        if not ok or msg is None:
            self.stats["errors"] += 1
            self.last_error = "Zeitlimit oder Absturz bei der Antwort"
            self.unload("timeout")
            raise Unavailable("Bildmodell nicht verfügbar (keine Antwort binnen Zeitlimit)")
        if msg[0] != "ok":
            self.stats["errors"] += 1
            self.last_error = str(msg[1])[:300]
            log.warning("Inferenz fehlgeschlagen: %s", self.last_error)
            if "out of memory" in self.last_error.lower():
                self.unload("oom")
            raise Unavailable("Bildmodell nicht verfügbar (Fehler bei der Antwort)")
        answer = str(msg[1]).strip()
        self.vram_mb = max(self.vram_mb or 0, int(msg[3] or 0))
        self.last_used = time.time()
        self.stats["answers"] += 1
        return {"answer": answer, "yes_no": parse_yes_no(answer), "ms": round((time.time() - t0) * 1000, 1),
                "model_ms": msg[2], "model": self.model_key}

    # ------------------------------------------------------------------ Leerlauf
    def _idle_loop(self) -> None:
        while not self._stop.wait(5.0):
            try:
                if self._proc is None:
                    continue
                if self._proc is not None and not self._proc.is_alive():
                    log.warning("Kindprozess unerwartet beendet")
                    with self._run:
                        self.unload("crash")
                        self.state = "error: Modellprozess abgestürzt"
                    continue
                if self.last_used and time.time() - self.last_used > self._idle_s():
                    if self._run.acquire(timeout=0.1):
                        try:
                            if self.last_used and time.time() - self.last_used > self._idle_s():
                                self.unload("idle")
                        finally:
                            self._run.release()
            except Exception:  # noqa: BLE001
                log.exception("Leerlauf-Prüfung fehlgeschlagen")

    def shutdown(self) -> None:
        self._stop.set()
        try:
            self.unload("shutdown")
        finally:
            self.lease.close()
