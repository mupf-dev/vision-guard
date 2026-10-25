"""Haustier-Tagebuch (C3) – Gerüst aus K2, Umsetzung: C3.

Vertrag: CONTRACT.md „Nachträge: Welle 2“, W2.F. Verdrahtung ist fertig:
- `main.py` importiert dieses Modul und ruft `startup()` beim Start sowie `tick()` jede Sekunde (selbst drosseln).
- Besuche über `events.on_track(callback)` erfassen (in `startup()` registrieren), Zonen-Zweck aus
  `registry.get(camera_id)["zone_purpose"]` (`{zone_id: "food|water|litter"}`).
- Hinweise als Notification `source="pet"` (notifications.create, Ziele `notify_routes.pet`).
- Router: `routers/pets.py` (`/api/pets/visits`, `/api/pets/summary`).
"""
from __future__ import annotations

import logging

log = logging.getLogger("vg.pets")


def startup() -> None:
    """Beim Backend-Start (nach registry/rules). C3: on_track-Hook registrieren."""
    return None


def tick() -> None:
    """Jede Sekunde vom Backend-Ticker (Thread). C3: stündliche Prüfung / 21:00 selbst takten."""
    return None
