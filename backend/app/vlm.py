"""Frag die Kamera (A2) – Gerüst aus K3, Umsetzung: A2.

Vertrag: CONTRACT.md „Nachträge: Welle 3“, W3.E (+ W3.C GPU-Lease). Verdrahtung ist fertig:
- Router `routers/ask.py` (`POST /api/cameras/{id}/ask`, `GET /api/ask`, `DELETE /api/ask/{id}`) ruft `ask(...)`.
- `main.py` ruft `tick()` jede Sekunde (selbst drosseln; z. B. `vlm /health` alle 10 s → `models.vlm`).
- `retention.py` ruft `retention(now)` stündlich (ask_log + Dateien unter ask/ nach `retention_days_ask`).
- Dienst `vlm` (Compose-Profil `vlm`) unter `config.vlm_url`; Cloud-Fallback in `cloud_vlm.py`.
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Optional

log = logging.getLogger("vg.vlm")


class AskError(Exception):
    """Fehler mit HTTP-Status und deutscher Meldung (Router übersetzt in HTTPException)."""

    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


def ask(camera_id: int, question: str, *, region: Optional[list[float]] = None, engine: str = "auto",
        source: str = "ui", token_name: Optional[str] = None) -> dict[str, Any]:
    """Frage an das aktuelle Bild der Kamera → AskResult (W3.E). A2: hier umsetzen (blockierend)."""
    raise AskError(503, "Bildmodell nicht verfügbar (Frag die Kamera ist noch nicht umgesetzt)")


def status() -> str:
    """`off|loading|ready|error: …` für `/system.worker.models.vlm` (A2)."""
    return "off"


def tick() -> None:
    """Jede Sekunde vom Backend-Ticker (Thread). A2: selbst drosseln."""
    return None


def retention(now: datetime) -> int:
    """Stündlich aus retention.py: ask_log + Bilder älter als `retention_days_ask` löschen (A2)."""
    return 0
