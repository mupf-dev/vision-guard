"""Identitäten 07 (Paket B): Qualitätsmerkmale eines Gesichts – CONTRACT ID.A/ID.F.

- `head_pose(kps) -> (yaw, pitch)`: grobe Kopfdrehung/Nicken in Grad aus den 5 InsightFace-Punkten
  (Augen, Nase, Mundwinkel).
- `sharpness(aligned_112) -> float`: Laplace-Varianz des ausgerichteten 112×112-Gesichts (roh).
- `day_night(img) -> "day" | "night"`: Graubild/IR-Erkennung (geringe Farbsättigung).
- `quality(det_score, size, yaw, pitch, sharp) -> float` (0..1): Erkennungs-Konfidenz × Größe × Frontalität ×
  Schärfe; Profile (|yaw| groß) und unscharfe Bilder bekommen niedrige Werte.

Noch Gerüst – Umsetzung in Paket B.
"""
from __future__ import annotations
