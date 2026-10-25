"""GPU-Lease (W3, K3): ein Dienst mit großem Modell (A2: vlm) leiht sich die GPU vom Worker.

Vertrag: CONTRACT.md „Nachträge: Welle 3“, W3.C. Topics (alle unter vg/internal/gpu/):

    request  {request_id, holder, need_mb, max_s}      Dienst → Worker
    granted  {request_id, holder, ok, free_mb, need_mb, unloaded, until, ms, error}   Worker → Dienst
    release  {request_id, holder}                      Dienst → Worker
    released {request_id, holder, reason}              Worker → Dienst (release|expired)

Der Worker entlädt dabei alle registrierten entladbaren Modelle (Inventar/YOLOE, ab B1 SigLIP-Bildturm), ruft
``torch.cuda.empty_cache()`` und misst den freien Speicher per NVML. Detektion, Gesicht/Kennzeichen/Tier,
DINOv2-Embedding und Pose bleiben geladen.

Registrieren (Fach-Module):

    pipeline.lease.register("history", pause=engine.pause_gpu, resume=engine.resume_gpu)

``pause(holder) -> bool`` entlädt blockierend (≤ 8 s) und liefert True, wenn das Modell aus dem VRAM ist;
``resume()`` erlaubt das Neuladen (lazy beim nächsten Takt).

Referenz-Client (für den vlm-Dienst und zum Prüfen): ``LeaseClient`` unten bzw. ``python -m vg_worker.leasetest``.
"""
from __future__ import annotations

import json
import logging
import threading
import time
import uuid
from dataclasses import dataclass
from typing import Callable, Optional

from . import gpu

log = logging.getLogger("vg.gpu_lease")

TOPIC_REQUEST = "vg/internal/gpu/request"
TOPIC_GRANTED = "vg/internal/gpu/granted"
TOPIC_RELEASE = "vg/internal/gpu/release"
TOPIC_RELEASED = "vg/internal/gpu/released"

DEFAULT_MAX_S = 300.0
MIN_MAX_S, MAX_MAX_S = 10.0, 1800.0
PAUSE_TIMEOUT_S = 8.0          # je Modell; der Dienst wartet insgesamt höchstens 10 s auf granted

Publish = Callable[[str, dict, int, bool], None]


@dataclass
class _Unloadable:
    name: str
    pause: Callable[[str], bool]
    resume: Callable[[], None]


class GpuLeaseManager:
    def __init__(self, publish: Publish):
        self.publish = publish
        self._models: list[_Unloadable] = []
        self._lock = threading.RLock()
        self._lease: Optional[dict] = None
        self._busy = threading.Lock()          # serialisiert Entladen/Neuladen
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self.stats = {"grants": 0, "denied": 0, "expired": 0}

    # ------------------------------------------------------------------ API
    def register(self, name: str, pause: Callable[[str], bool], resume: Callable[[], None]) -> None:
        with self._lock:
            self._models = [m for m in self._models if m.name != name] + [_Unloadable(name, pause, resume)]

    def active(self) -> bool:
        with self._lock:
            return self._lease is not None

    def status(self) -> Optional[dict]:
        """Für vg/internal/system.gpu_lease."""
        with self._lock:
            if self._lease is None:
                return None
            lz = self._lease
            return {"request_id": lz["request_id"], "holder": lz["holder"], "since": round(lz["since"], 3),
                    "until": round(lz["until"], 3), "need_mb": lz["need_mb"], "free_mb": lz.get("free_mb")}

    def start(self) -> None:
        if self._thread is None:
            self._thread = threading.Thread(target=self._expiry_loop, name="gpu-lease", daemon=True)
            self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self.active():
            self._end("expired")

    # ------------------------------------------------------------------ MQTT (aus dem MQTT-Thread aufrufen)
    def on_message(self, topic: str, payload: dict, retained: bool = False) -> None:
        if retained:
            return       # nie auf alte retained Anfragen reagieren
        if topic == TOPIC_REQUEST:
            threading.Thread(target=self._handle_request, args=(payload,), name="gpu-lease-req", daemon=True).start()
        elif topic == TOPIC_RELEASE:
            threading.Thread(target=self._handle_release, args=(payload,), name="gpu-lease-rel", daemon=True).start()

    # ------------------------------------------------------------------ intern
    def _handle_request(self, msg: dict) -> None:
        rid = str(msg.get("request_id") or "")[:64]
        holder = str(msg.get("holder") or "?")[:40]
        if not rid:
            return
        try:
            need = max(0, int(msg.get("need_mb") or 0))
        except (TypeError, ValueError):
            need = 0
        try:
            max_s = float(msg.get("max_s") or DEFAULT_MAX_S)
        except (TypeError, ValueError):
            max_s = DEFAULT_MAX_S
        max_s = max(MIN_MAX_S, min(MAX_MAX_S, max_s))
        t0 = time.time()
        with self._lock:
            cur = self._lease
            if cur is not None and cur["holder"] != holder:
                self.stats["denied"] += 1
                self._reply(rid, holder, ok=False, need=need, unloaded=[], until=None, t0=t0,
                            error=f"busy: GPU-Lease von {cur['holder']}")
                return
            if cur is not None:      # Verlängern (gleiche oder neue request_id desselben Halters)
                cur["request_id"] = rid
                cur["until"] = time.time() + max_s
                cur["need_mb"] = need
                free = gpu.free_mb()
                cur["free_mb"] = free
                ok = free is None or free >= need
                self._reply(rid, holder, ok=ok, need=need, unloaded=list(cur["unloaded"]), until=cur["until"],
                            t0=t0, free=free, error=None if ok else f"zu wenig VRAM: {free} < {need}")
                return
            self._lease = {"request_id": rid, "holder": holder, "need_mb": need, "since": t0,
                           "until": t0 + max_s, "unloaded": [], "free_mb": None}
        log.info("GPU-Lease angefragt von %s (%s MB, max. %.0f s)", holder, need, max_s)
        unloaded: list[str] = []
        with self._busy:
            for m in list(self._models):
                try:
                    if m.pause(holder):
                        unloaded.append(m.name)
                    else:
                        log.warning("GPU-Lease: %s nicht rechtzeitig entladen", m.name)
                except Exception:  # noqa: BLE001
                    log.exception("GPU-Lease: Entladen von %s fehlgeschlagen", m.name)
            _empty_cache()
            time.sleep(0.2)              # NVML zeigt freigegebenen Speicher leicht verzögert
            free = gpu.free_mb()
        ok = free is None or free >= need
        with self._lock:
            if self._lease is None or self._lease["request_id"] != rid:
                return                   # inzwischen freigegeben
            self._lease["unloaded"] = unloaded
            self._lease["free_mb"] = free
            until = self._lease["until"]
        self.stats["grants"] += 1
        log.info("GPU-Lease für %s: %s entladen, frei %s MB (Bedarf %s MB) in %.0f ms", holder,
                 ", ".join(unloaded) or "nichts", free, need, (time.time() - t0) * 1000)
        self._reply(rid, holder, ok=ok, need=need, unloaded=unloaded, until=until, t0=t0, free=free,
                    error=None if ok else f"zu wenig VRAM: {free} < {need}")

    def _handle_release(self, msg: dict) -> None:
        rid = str(msg.get("request_id") or "")
        holder = str(msg.get("holder") or "")
        with self._lock:
            cur = self._lease
            if cur is None or (rid and rid != cur["request_id"] and holder != cur["holder"]):
                return
        self._end("release")

    def _end(self, reason: str) -> None:
        with self._lock:
            cur = self._lease
            self._lease = None
        if cur is None:
            return
        with self._busy:
            for m in list(self._models):
                try:
                    m.resume()
                except Exception:  # noqa: BLE001
                    log.exception("GPU-Lease: Freigeben von %s fehlgeschlagen", m.name)
        if reason == "expired":
            self.stats["expired"] += 1
        log.info("GPU-Lease von %s beendet (%s) nach %.0f s", cur["holder"], reason, time.time() - cur["since"])
        self.publish(TOPIC_RELEASED, {"request_id": cur["request_id"], "holder": cur["holder"], "reason": reason},
                     1, False)

    def _reply(self, rid: str, holder: str, *, ok: bool, need: int, unloaded: list[str], until: Optional[float],
               t0: float, free: Optional[int] = None, error: Optional[str] = None) -> None:
        self.publish(TOPIC_GRANTED, {"request_id": rid, "holder": holder, "ok": bool(ok), "free_mb": free,
                                     "need_mb": need, "unloaded": unloaded,
                                     "until": round(until, 3) if until else None,
                                     "ms": round((time.time() - t0) * 1000, 1), "error": error}, 1, False)

    def _expiry_loop(self) -> None:
        while not self._stop.wait(1.0):
            with self._lock:
                expired = self._lease is not None and time.time() > self._lease["until"]
            if expired:
                self._end("expired")


def _empty_cache() -> None:
    import gc
    gc.collect()
    try:
        import torch
        if torch.cuda.is_available():
            torch.cuda.synchronize()
            torch.cuda.empty_cache()
    except Exception:  # noqa: BLE001
        pass


# ============================================================================ Referenz-Client
class LeaseClient:
    """Kleiner synchroner Client (paho) – Vorlage für den vlm-Dienst (A2) und für ``leasetest``.

        lc = LeaseClient(host, port, holder="vlm")
        g = lc.acquire(need_mb=3000, max_s=300, timeout=10)   # dict aus granted oder None (Zeitlimit → 503)
        ...
        lc.release()
    """

    def __init__(self, host: str, port: int = 1883, holder: str = "vlm"):
        import paho.mqtt.client as mqtt

        self.holder = holder
        self.request_id: Optional[str] = None
        self._granted: dict[str, dict] = {}
        self._released: dict[str, dict] = {}
        self._cv = threading.Condition()
        self.c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f"vg-lease-{holder}-{uuid.uuid4().hex[:6]}")
        self.c.on_message = self._on_message
        self.c.connect(host, port, keepalive=30)
        self.c.subscribe([(TOPIC_GRANTED, 1), (TOPIC_RELEASED, 1)])
        self.c.loop_start()

    def _on_message(self, client, userdata, msg):
        try:
            p = json.loads(msg.payload or b"{}")
        except ValueError:
            return
        with self._cv:
            if msg.topic == TOPIC_GRANTED:
                self._granted[str(p.get("request_id"))] = p
            elif msg.topic == TOPIC_RELEASED:
                self._released[str(p.get("request_id"))] = p
            self._cv.notify_all()

    def acquire(self, need_mb: int, max_s: float = DEFAULT_MAX_S, timeout: float = 10.0) -> Optional[dict]:
        rid = self.request_id or uuid.uuid4().hex
        self.request_id = rid
        with self._cv:
            self._granted.pop(rid, None)
        self.c.publish(TOPIC_REQUEST, json.dumps({"request_id": rid, "holder": self.holder, "need_mb": int(need_mb),
                                                  "max_s": float(max_s)}), qos=1)
        deadline = time.time() + timeout
        with self._cv:
            while rid not in self._granted:
                left = deadline - time.time()
                if left <= 0:
                    return None
                self._cv.wait(left)
            return self._granted[rid]

    def release(self, timeout: float = 5.0) -> Optional[dict]:
        rid = self.request_id
        if not rid:
            return None
        self.c.publish(TOPIC_RELEASE, json.dumps({"request_id": rid, "holder": self.holder}), qos=1)
        deadline = time.time() + timeout
        with self._cv:
            while rid not in self._released:
                left = deadline - time.time()
                if left <= 0:
                    break
                self._cv.wait(left)
            res = self._released.get(rid)
        self.request_id = None
        return res

    def close(self) -> None:
        self.c.loop_stop()
        self.c.disconnect()
