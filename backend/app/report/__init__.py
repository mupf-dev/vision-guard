"""Tagesbericht (B3) – Gerüst aus K2, Umsetzung: B3 (Paket `backend/app/report/`).

Vertrag: CONTRACT.md „Nachträge: Welle 2“, W2.G. Verdrahtung ist fertig:
- `main.py` ruft `tick()` jede Sekunde im Thread auf (Scheduler: minütlich prüfen, idempotent über
  `reports.period_key`, Nachholen bis 6 h).
- LLM nur über `backend/app/llm_client.py` (`configured()`, `complete()`), Test ohne Kosten mit
  `scripts/llm_mock.py`.
- Router: `routers/reports.py`.
Vorschlag für die Aufteilung (Entwurf b2-b3): `facts.py`, `render.py`, `deliver.py`, `scheduler.py`.
"""
from __future__ import annotations


def tick() -> None:
    """Jede Sekunde vom Backend-Ticker (Thread). B3: Scheduler."""
    return None
