"""Identitäten 07 (Paket D): Nachtlauf – Fragmente bündeln, Galerie kuratieren, Schwellen je Person, Aufräumen.

CONTRACT ID.H. Ablauf:
- `tick()` (Backend-Ticker, jede Sekunde, selbst gedrosselt): fällig ab `nightly_time` (Ortszeit), ein Lauf je
  lokalem Tag (`nightly_reports.period_key = "nightly:YYYY-MM-DD"`), Nachholen bis 6 h; `nightly_mode = "off"` → nichts.
- `start(mode, trigger, apply_from=None, idx=None)` → NightlyReport-ID; Lauf in eigenem Thread, höchstens einer
  gleichzeitig (`RunningError` → 409).
- `status()` → `{mode, time, running, running_report_id, last, next_run}`.
- Modus `preview` (Standard dieser Installation): nur berechnen und als Aktionen speichern – **keine** Änderung.
- Modus `apply`: Merges nur Unbenannt → Ziel (nie zwei Benannte) über `identity_bulk.bulk_merge(actor="nightly")`,
  Rückfragen über `review.create(kind="identity_match", …)`, Kuratieren/Schwellen/Aufräumen.
- Score-Berechnung über `face_match` (Paket C).

Noch Gerüst – Umsetzung in Paket D.
"""
from __future__ import annotations

from typing import Any, Optional


class RunningError(RuntimeError):
    """Es läuft bereits ein Nachtlauf (→ HTTP 409)."""


def tick() -> None:
    """Gerüst: noch kein Zeitplan (Paket D)."""
    return None


def start(mode: str, trigger: str, apply_from: Optional[int] = None, idx: Optional[list[int]] = None) -> int:
    raise NotImplementedError("Paket D")


def status() -> dict[str, Any]:
    raise NotImplementedError("Paket D")
