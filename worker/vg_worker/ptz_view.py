"""PTZ-Sicht je Kamera (CONTRACT „Nachträge: PTZ“, PTZ.D): `vg/internal/ptz/{camera_id}` vom Backend (retain).

Zonen, Gegenstände, Sensoren und Gesundheitsprüfung gelten nur für die Grundposition. Steht eine PTZ-Kamera
woanders oder bewegt sich, werden diese Auswertungen ausgesetzt. Kameras ohne Nachricht gelten als „home“.
"""
from __future__ import annotations

import threading
import time
from typing import Any

_lock = threading.Lock()
_views: dict[int, dict[str, Any]] = {}


def update(camera_id: int, msg: dict[str, Any] | None) -> None:
    with _lock:
        if not msg:
            _views.pop(camera_id, None)
            return
        prev = _views.get(camera_id)
        home = msg.get("view") == "home" and not msg.get("moving")
        was_home = prev is not None and prev.get("view") == "home" and not prev.get("moving")
        since = (prev or {}).get("home_since", 0.0) if was_home else (time.time() if home else 0.0)
        _views[camera_id] = {"view": msg.get("view"), "moving": bool(msg.get("moving")), "mode": msg.get("mode"),
                             "home_since": since}


def at_home(camera_id: int) -> bool:
    v = _views.get(camera_id)
    return v is None or (v.get("view") == "home" and not v.get("moving"))


def home_since(camera_id: int) -> float:
    """Wanduhr, seit der die Kamera (wieder) in der Grundposition steht; 0 = immer (keine PTZ-Kamera).
    Bilder, die davor aufgenommen wurden (z. B. ein älterer Keyframe), gehören noch zur alten Ausrichtung."""
    v = _views.get(camera_id)
    return float(v.get("home_since") or 0.0) if v else 0.0


def snapshot() -> dict[int, dict[str, Any]]:
    with _lock:
        return {k: dict(v) for k, v in _views.items()}
