"""Exemplare eigener Gegenstände (B4) – Gerüst aus K2, Umsetzung: B4.

Vertrag: CONTRACT.md „Nachträge: Welle 2“, W2.D. Verdrahtung ist fertig:
- `identity.identify()` leitet `POST /api/internal/identify` mit `kind:"item"` hierher (`identify_item`).
- Embeddings für Uploads/Inventar-Crops: `embed_client.request("item", path=…, bbox=…, augment=True)`
  (geteiltes DINOv2 im Worker, `vg/internal/embed`).

B4 ergänzt hier: Abgleich nur innerhalb `item_label`, Negativ-Samples, Lernen ab Schwelle + 0,10,
`item_min_px`, Upload (`POST /identities/{id}/samples`), Übernahme aus dem Inventar, Zuordnung
`inventory_items.identity_id/identity_score` und `InventoryItem.identity` in `inventory.item_json`.
"""
from __future__ import annotations

import logging
from typing import Any

from sqlalchemy.orm import Session

log = logging.getLogger("vg.item_identity")

NO_MATCH: dict[str, Any] = {"identity_id": None, "display_name": None, "name": None, "score": 0.0, "is_new": False}


def identify_item(s: Session, req: Any) -> dict[str, Any]:
    """Kern von `POST /api/internal/identify` für `kind:"item"` (req: schemas.IdentifyIn mit label, item_id).
    Legt nie eine neue Identität an; ohne Treffer `identity_id: None`."""
    return dict(NO_MATCH)
