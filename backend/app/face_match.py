"""Identitäten 07 (Paket C): Gesichts-Abgleich und Galerie-Auswahl – CONTRACT ID.D/ID.F.

Gemeinsame Bausteine für `identity.identify` (Paket C) und den Nachtlauf (`nightly.py`, Paket D):

- `top_k_scores(mat, ids, vec, k=3)` → `{identity_id: score}`: Score je Identität = Mittel der besten `k`
  Bild-Ähnlichkeiten (Kosinus, Embeddings L2-normiert; bei < k Bildern das Mittel der vorhandenen).
- `decide(scores, thresholds, margin)` → `(identity_id | None, score, second_score, decision)` mit
  `decision ∈ {"match", "none", "ambiguous"}`: Treffer, wenn `score ≥ Schwelle der Identität` und
  `score − second_score ≥ margin`.
- `threshold_for(ident, settings)` → `ident.match_threshold` ?? `face_match_threshold` bzw. `pet_match_threshold`.
- `sample_limit(ident, settings)` → `face_max_samples_named` für benannte Gesichter, sonst `identity.MAX_SAMPLES`.
- `select_gallery(samples, limit)` → IDs der zu behaltenden Bilder: manuelle immer, sonst k-Center über die
  Embeddings (Start: bestes Bild), Tag/Nacht getrennt anteilig; Profile/unscharfe Bilder zuletzt.

Noch Gerüst – Umsetzung in Paket C.
"""
from __future__ import annotations

from typing import Any, Optional

import numpy as np

TOP_K = 3


def threshold_for(ident: Any, settings: dict[str, Any]) -> float:
    if getattr(ident, "match_threshold", None) is not None:
        return float(ident.match_threshold)
    kind = getattr(ident, "kind", "face")
    return float(settings["face_match_threshold"] if kind == "face" else settings["pet_match_threshold"])


def sample_limit(ident: Any, settings: dict[str, Any]) -> int:
    from .identity import MAX_SAMPLES
    if getattr(ident, "kind", None) == "face" and getattr(ident, "name", None):
        return int(settings.get("face_max_samples_named", 40))
    return MAX_SAMPLES


def top_k_scores(mat: np.ndarray, ids: np.ndarray, vec: np.ndarray, k: int = TOP_K) -> dict[int, float]:
    raise NotImplementedError("Paket C")


def decide(scores: dict[int, float], thresholds: dict[int, float], margin: float
           ) -> tuple[Optional[int], float, Optional[float], str]:
    raise NotImplementedError("Paket C")


def select_gallery(samples: list[Any], limit: int) -> list[int]:
    raise NotImplementedError("Paket C")
