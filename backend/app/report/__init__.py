"""Tagesbericht (B3) – Paket `backend/app/report/`. Vertrag: CONTRACT.md „Nachträge: Welle 2“, W2.G.

- `facts.py`     – Fakten deterministisch aus der DB (Ereignisse, Personen, Fahrzeuge, Tiere, Gegenstände (B2),
                   Regeln, Hinweise, Kamerastatus).
- `render.py`    – Text: Satzbausteine (Standard) oder Sprachmodell über `llm_client` mit Plausibilitätsprüfung
                   und Rückfall `template (Fallback)`.
- `deliver.py`   – Versand: MQTT `{prefix}/report`, Push (Notification `source:"report"`), Webhook, Mail.
- `scheduler.py` – `tick()` (vom Backend-Ticker jede Sekunde), genau ein Bericht je Tag, Nachholen bis 6 h.
- `service.py`   – bauen/speichern/versenden, gemeinsam für Scheduler und `routers/reports.py`.
Test ohne Kosten: `scripts/llm_mock.py` (OpenAI-kompatibel, Modi `echo`, `fixed`, `invent`, `error`, `slow`).
"""
from __future__ import annotations

from .scheduler import tick

__all__ = ["tick"]
