"""In-Memory-Abbild der Kameras (für MQTT-/Regel-Verarbeitung ohne DB-Zugriff)."""
import threading
from typing import Any, Optional

from sqlalchemy import select

from .db import SessionLocal
from .models import Camera
from .util import slugify

_lock = threading.Lock()
_cams: dict[int, dict[str, Any]] = {}


def reload() -> None:
    with SessionLocal() as s:
        rows = s.scalars(select(Camera)).all()
        data = {
            c.id: {
                "id": c.id,
                "name": c.name,
                "slug": slugify(c.name),
                "enabled": c.enabled,
                "labels": list(c.labels or []),
                "zones": {z.get("id"): z.get("name") or z.get("id") for z in (c.zones or []) if isinstance(z, dict)},
            }
            for c in rows
        }
    with _lock:
        _cams.clear()
        _cams.update(data)


def get(camera_id: int) -> Optional[dict[str, Any]]:
    with _lock:
        c = _cams.get(camera_id)
        return dict(c) if c else None


def all() -> list[dict[str, Any]]:  # noqa: A001
    with _lock:
        return [dict(c) for c in _cams.values()]
