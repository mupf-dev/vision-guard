"""Flüchtiger Laufzeitzustand (Kamera-Status, Worker-Info, MQTT-Verbindung)."""
import threading
import time
from datetime import datetime, timezone
from typing import Any, Optional

STATUS_STALE_S = 15.0
WORKER_STALE_S = 15.0

_lock = threading.Lock()
camera_status: dict[int, dict[str, Any]] = {}   # camera_id -> letzte Statusnachricht + _rx
# D3-Vorbereitung: letzte `system`-Nachricht je worker_id (Nachrichten ohne worker_id gelten als "local").
# Heute gibt es genau einen Worker; /system.worker zeigt den lokalen (bzw. zuletzt gemeldeten) Worker.
LOCAL_WORKER_ID = "local"
worker_systems: dict[str, dict[str, Any]] = {}   # worker_id -> letzte system-Nachricht + _rx
mqtt_connected: bool = False
# W1 C5: Gesundheitsstatus je Kamera ({state:"ok|warn|unknown", problems:[{type, since, text}]}), gesetzt von health.py
camera_health: dict[int, dict[str, Any]] = {}


def set_camera_status(camera_id: int, msg: dict[str, Any]) -> None:
    with _lock:
        camera_status[camera_id] = {**msg, "_rx": time.time()}


def set_camera_health(camera_id: int, health: Optional[dict[str, Any]]) -> None:
    with _lock:
        if health is None:
            camera_health.pop(camera_id, None)
        else:
            camera_health[camera_id] = dict(health)


def worker_id_of(msg: dict[str, Any], topic_worker_id: Optional[str] = None) -> str:
    """worker_id einer Worker-Nachricht (Feld vor Topic-Segment, Default "local")."""
    wid = msg.get("worker_id") or topic_worker_id or LOCAL_WORKER_ID
    return str(wid)[:64]


def set_worker_system(msg: dict[str, Any], topic_worker_id: Optional[str] = None) -> None:
    wid = worker_id_of(msg, topic_worker_id)
    with _lock:
        worker_systems[wid] = {**msg, "worker_id": wid, "_rx": time.time()}


def _iso_from_unix(ts: Optional[float]) -> Optional[str]:
    if not ts:
        return None
    try:
        return datetime.fromtimestamp(float(ts), timezone.utc).isoformat().replace("+00:00", "Z")
    except Exception:
        return None


def status_for(camera_id: int, enabled: bool = True) -> dict[str, Any]:
    """Status-Objekt laut Contract für eine Kamera."""
    with _lock:
        s = dict(camera_status.get(camera_id) or {})
        health = camera_health.get(camera_id)
    base = {"state": "offline", "fps": 0.0, "width": None, "height": None, "inference_ms": None, "last_seen": None,
            "health": dict(health) if health else None}
    if s:
        rx = s.pop("_rx", 0)
        ts = s.get("ts") or rx
        base.update({
            "state": s.get("state") or "online",
            "fps": s.get("fps", 0.0),
            "width": s.get("width"),
            "height": s.get("height"),
            "inference_ms": s.get("inference_ms"),
            "last_seen": _iso_from_unix(ts),
        })
        if time.time() - rx > STATUS_STALE_S:
            base["state"] = "offline"
            base["fps"] = 0.0
    if not enabled:
        base["state"] = "disabled"
        base["fps"] = 0.0
    return base


def _worker_dict(sysmsg: dict[str, Any]) -> dict[str, Any]:
    rx = sysmsg.get("_rx") or 0.0
    online = bool(rx) and (time.time() - rx) <= WORKER_STALE_S and sysmsg.get("online", True) is not False
    return {
        "online": online,
        "gpu": sysmsg.get("gpu") or {},
        "models": sysmsg.get("models") or {},
        "last_seen": _iso_from_unix(sysmsg.get("ts") or rx or None),
        # W3 (K3): Leistungsdaten und GPU-Lease des Workers
        "perf": sysmsg.get("perf") or {},
        "gpu_lease": sysmsg.get("gpu_lease"),
    }


def worker_info(worker_id: Optional[str] = None) -> dict[str, Any]:
    """Status eines Workers für /system.worker: der angefragte; sonst bevorzugt ein online-Worker
    ("local" vor anderen), danach der mit der jüngsten Meldung (`ts`). Ein retained Altbestand eines
    umbenannten Workers verdeckt so nicht den laufenden."""
    with _lock:
        if worker_id is not None:
            sysmsg = dict(worker_systems.get(worker_id) or {})
        elif worker_systems:
            now = time.time()

            def rank(m: dict[str, Any]):
                online = now - (m.get("_rx") or 0.0) <= WORKER_STALE_S and m.get("online", True) is not False
                return (online, m.get("worker_id") == LOCAL_WORKER_ID, m.get("ts") or m.get("_rx") or 0.0)

            sysmsg = dict(max(worker_systems.values(), key=rank))
        else:
            sysmsg = {}
    return _worker_dict(sysmsg)


def workers_info() -> dict[str, dict[str, Any]]:
    """Alle bekannten Worker (worker_id -> Status wie worker_info); Grundlage für D3 `/system.workers`."""
    with _lock:
        msgs = {wid: dict(m) for wid, m in worker_systems.items()}
    return {wid: _worker_dict(m) for wid, m in sorted(msgs.items())}
