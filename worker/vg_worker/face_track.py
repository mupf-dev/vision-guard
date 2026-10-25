"""Identitäten 07 (Paket B): Gesichter je Track sammeln statt 3× sofort – CONTRACT ID.F/ID.G.

- Über die ganze Track-Dauer gedrosselt Gesichter auswerten (ggf. aus voller Auflösung, `face_fullres` je Kamera)
  und die besten `face_track_faces` (Setting) behalten.
- Früh eine Anfrage `phase:"provisional"` (erstes brauchbares Gesicht), am Track-Ende (bzw. nach 30 s und am Ende
  erneut) `phase:"final"` mit gemitteltem, L2-normiertem Embedding und den Einzelbildern als `samples`.
- Kennzahlen für `vg/internal/system` → `perf.face`.

Noch Gerüst – Umsetzung in Paket B.
"""
from __future__ import annotations
