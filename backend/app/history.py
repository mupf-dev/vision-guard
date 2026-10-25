"""Zeitmaschine (B1) – Gerüst aus K3, Umsetzung: B1.

Vertrag: CONTRACT.md „Nachträge: Welle 3“, W3.G. Verdrahtung ist fertig:
- `main.py` registriert `handle_worker_frame` für `vg/internal/history/{camera_id}` (Track-Thread) und ruft
  `tick()` jede Sekunde (selbst drosseln).
- Router `routers/history.py` (`/api/history/*`). `retention.py` ruft `retention(now)` stündlich.
- Datenbank: `models.HistoryFrame` (halfvec(768), nur mit pgvector – vorher `db.vector_available()` prüfen),
  Abstand `HistoryFrame.embedding.cosine_distance(vec)`; Textkodierung über
  `embed_client.request("text", text=q, model="siglip2", timeout=5)`.
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Optional

log = logging.getLogger("vg.history")


def enabled() -> bool:
    """Zeitmaschine nutzbar (pgvector aktiv)."""
    from .db import vector_available

    return vector_available()


def handle_worker_frame(camera_id: Optional[int], msg: dict[str, Any]) -> None:
    """`vg/internal/history/{camera_id}`: Vektoren speichern bzw. `last_ts` verlängern (B1)."""
    return None


def tick() -> None:
    """Jede Sekunde vom Backend-Ticker (Thread). B1: selbst drosseln."""
    return None


def retention(now: datetime) -> int:
    """Stündlich aus retention.py: Zeilen + Vorschaubilder älter als `retention_days_history` löschen (B1)."""
    return 0
