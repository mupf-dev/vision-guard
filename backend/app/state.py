"""Flüchtiger Laufzeitzustand (Kamera-Status, Worker-Info, MQTT-Verbindung)."""
import threading
import time
from datetime import datetime, timezone
from typing import Any, Optional

STATUS_STALE_S = 15.0
WORKER_STALE_S = 15.0

_lock = threading.Lock()
camera_status: dict[int, dict[str, Any]] = {}   # camera_id -> letzte Statusnachricht + _rx
worker_system: dict[str, Any] = {}
worker_rx: float = 0.0
mqtt_connected: bool = False


def set_camera_status(camera_id: int, msg: dict[str, Any]) -> None:
    with _lock:
        camera_status[camera_id] = {**msg, "_rx": time.time()}


def set_worker_system(msg: dict[str, Any]) -> None:
    global worker_rx
    with _lock:
        worker_system.clear()
        worker_system.update(msg)
        worker_rx = time.time()


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
    base = {"state": "offline", "fps": 0.0, "width": None, "height": None, "inference_ms": None, "last_seen": None}
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


def worker_info() -> dict[str, Any]:
    with _lock:
        sysmsg = dict(worker_system)
        rx = worker_rx
    online = bool(rx) and (time.time() - rx) <= WORKER_STALE_S
    return {
        "online": online,
        "gpu": sysmsg.get("gpu") or {},
        "models": sysmsg.get("models") or {},
        "last_seen": _iso_from_unix(sysmsg.get("ts") or rx or None),
    }
