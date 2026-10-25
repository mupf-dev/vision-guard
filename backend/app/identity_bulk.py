"""Identitäten 07 (Paket A): Sammel-Löschen, Sammel-Zusammenführen, Verlauf und Zurücksetzen – CONTRACT ID.B/ID.C.

- `bulk_delete(s, ids)` → gelöschte IDs. Eine Transaktion unter `identity._lock`; Art-Hooks (item/pet) wie beim
  Einzel-Löschen; Bilder nach dem Commit löschen; eine gebündelte WS-Nachricht
  `{"type":"identities","action":"deleted","ids":[…],"items":[{"id","kind"}]}`.
- `bulk_merge(s, ids, into_id, *, actor="bulk", score=None, report_id=None)` → `(Ziel, gemergte IDs, merge_ids)`.
  Immer vollständig; Zeilen `FOR UPDATE` in ID-Reihenfolge; kumulatives Limit manueller Bilder
  (`face_match.sample_limit`); Dateien nach dem Commit verschieben (Fehler → alten Pfad zurückschreiben);
  je Quelle ein `IdentityMerge`; WS `{"type":"identities","action":"merged","ids":[…],"into_id":N,"kind":…}` + Ziel.
  Wird auch vom Nachtlauf (`actor="nightly"`, `score`, `report_id`) genutzt.
- `log_merge(s, src, dst, *, actor, score=None, report_id=None, sample_ids, event_ids)` → IdentityMerge
  (auch vom Einzel-Merge `identity.merge` und der Rückfrage „Ja“ aufzurufen).
- `list_merges(s, identity_id=None, actor=None, undone=None, limit=50, offset=0)` → `{items, total}`.
- `undo_merge(s, merge_id)` → `(wiederhergestellte Identität, Ziel)`; Fehler als `BulkError(status, detail)`.

Noch Gerüst – Umsetzung in Paket A.
"""
from __future__ import annotations

from typing import Any, Optional

from sqlalchemy.orm import Session


class BulkError(ValueError):
    """Fehler mit HTTP-Status (404/409/422) und deutscher Meldung."""

    def __init__(self, status: int, detail: str):
        super().__init__(detail)
        self.status = status
        self.detail = detail


def bulk_delete(s: Session, ids: list[int]) -> list[int]:
    raise NotImplementedError("Paket A")


def bulk_merge(s: Session, ids: list[int], into_id: int, *, actor: str = "bulk", score: Optional[float] = None,
               report_id: Optional[int] = None) -> tuple[Any, list[int], list[int]]:
    raise NotImplementedError("Paket A")


def list_merges(s: Session, identity_id: Optional[int] = None, actor: Optional[str] = None,
                undone: Optional[bool] = None, limit: int = 50, offset: int = 0) -> dict[str, Any]:
    raise NotImplementedError("Paket A")


def undo_merge(s: Session, merge_id: int) -> tuple[Any, Any]:
    raise NotImplementedError("Paket A")
