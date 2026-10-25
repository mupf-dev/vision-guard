"""Virtuelle Sensoren (A3+C4) – Gerüst aus K3, Umsetzung: A3+C4.

Vertrag: CONTRACT.md „Nachträge: Welle 3“, W3.F. Verdrahtung ist fertig:
- `main.py` registriert `handle_worker_values` für `vg/internal/sensor/{camera_id}` (Track-Thread), ruft
  `startup()` beim Start und `tick()` jede Sekunde (selbst drosseln: inventory_count/vlm-Sensoren, stale).
- `routers/internal.py` liefert je Kamera `sensors` aus `internal_sensors(db)` (fertig).
- Router `routers/sensors.py` (`/api/sensors*`, `/api/shopping-list*`).
- `retention.py` ruft `retention(now)` stündlich (sensor_history nach `retention_days_sensors`).
- Bestätigter Zustandswechsel → `events.processor.sensor_changed(sensor_json, old, new)` (Regeln, fertig),
  WS `sensor`, MQTT extern `{prefix}/sensor/{slug}`; Vorräte → `shopping.py`.
"""
from __future__ import annotations

import logging
from collections import defaultdict
from datetime import datetime
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

log = logging.getLogger("vg.sensors")

# Arten, die der Worker misst (CPU bzw. geteiltes DINOv2); inventory_count und vlm rechnet das Backend
WORKER_KINDS = ("brightness", "blink", "reference", "fill")
KINDS = ("brightness", "blink", "reference", "inventory_count", "fill", "vlm")
BINARY_STATES = ("on", "off", "unknown")
LEVEL_STATES = ("ok", "low", "out", "unknown")


def internal_sensors(db: Session) -> dict[int, list[dict[str, Any]]]:
    """{camera_id: [{id, kind, region, params, interval_s, change_threshold, references:[{id, state, path}]}]} für
    `GET /api/internal/cameras` – nur aktive Sensoren der Worker-Arten."""
    from .models import SensorReference, VirtualSensor

    out: dict[int, list[dict[str, Any]]] = defaultdict(list)
    sensors = db.scalars(select(VirtualSensor).where(VirtualSensor.enabled.is_(True),
                                                     VirtualSensor.kind.in_(WORKER_KINDS))
                         .order_by(VirtualSensor.id)).all()
    if not sensors:
        return {}
    refs: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for r in db.scalars(select(SensorReference).where(SensorReference.sensor_id.in_([s.id for s in sensors]))
                        .order_by(SensorReference.id)).all():
        refs[r.sensor_id].append({"id": r.id, "state": r.state, "path": r.image_path})
    for s in sensors:
        out[s.camera_id].append({"id": s.id, "kind": s.kind, "region": list(s.region or []),
                                 "params": dict(s.params or {}), "interval_s": float(s.interval_s or 10),
                                 "change_threshold": float(s.change_threshold or 0.08),
                                 "references": refs.get(s.id, [])})
    return dict(out)


def handle_worker_values(camera_id: Optional[int], msg: dict[str, Any]) -> None:
    """`vg/internal/sensor/{camera_id}` (Track-Thread): Messwerte → Hysterese → Zustand (A3)."""
    return None


def startup() -> None:
    """Beim Backend-Start (A3: z. B. retained Zustände nach extern publizieren)."""
    return None


def tick() -> None:
    """Jede Sekunde vom Backend-Ticker (Thread). A3: selbst drosseln."""
    return None


def retention(now: datetime) -> int:
    """Stündlich aus retention.py: sensor_history älter als `retention_days_sensors` löschen (A3)."""
    return 0
