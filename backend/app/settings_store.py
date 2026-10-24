"""Settings (in DB-Tabelle kv unter 'settings', im Speicher gecacht)."""
import threading
from typing import Any

from sqlalchemy.orm import Session

from .config import DEFAULT_SETTINGS
from .db import SessionLocal
from .models import KV
from .schemas import SettingsModel

_lock = threading.Lock()
_cache: dict[str, Any] = dict(DEFAULT_SETTINGS)


def load() -> dict[str, Any]:
    with SessionLocal() as s:
        row = s.get(KV, "settings")
        data = dict(DEFAULT_SETTINGS)
        if row and isinstance(row.value, dict):
            data.update({k: v for k, v in row.value.items() if k in DEFAULT_SETTINGS})
        data = SettingsModel(**data).model_dump()
        if not row:
            s.add(KV(key="settings", value=data))
            s.commit()
    with _lock:
        _cache.clear()
        _cache.update(data)
    return dict(data)


def get() -> dict[str, Any]:
    with _lock:
        return dict(_cache)


def save(s: Session, data: dict[str, Any]) -> dict[str, Any]:
    data = SettingsModel(**data).model_dump()
    row = s.get(KV, "settings")
    if row:
        row.value = data
    else:
        s.add(KV(key="settings", value=data))
    s.commit()
    with _lock:
        _cache.clear()
        _cache.update(data)
    return dict(data)
