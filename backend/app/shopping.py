"""Einkaufsliste (C4) – Gerüst aus K3, Umsetzung: A3+C4.

Vertrag: CONTRACT.md W3.F. Vorrats-Sensoren (`supply`) rufen `on_supply_state(sensor, state)`; bei
`shopping_list_auto` → offener Eintrag bei low/out, erledigt bei ok. WS `shopping_list`, MQTT extern
`{prefix}/shopping_list` (retain). REST in `routers/sensors.py` (`/api/shopping-list*`).
"""
from __future__ import annotations

from typing import Any


def on_supply_state(sensor: dict[str, Any], state: str) -> None:
    """Vorrats-Sensor hat einen bestätigten Zustand (A3/C4)."""
    return None
