"""Identitäten 07 (Paket B): Qualitätsmerkmale eines Gesichts – CONTRACT ID.A/ID.F.

- `head_pose(kps) -> (yaw, pitch)`: grobe Kopfdrehung/Nicken in Grad aus den 5 InsightFace-Punkten
  (linkes Auge, rechtes Auge, Nase, linker/rechter Mundwinkel – Bildkoordinaten).
- `sharpness(aligned_112) -> float`: Laplace-Varianz des ausgerichteten 112×112-Gesichts (roh).
- `day_night(img) -> "day" | "night"`: Graubild/IR-Erkennung (geringe Farbsättigung).
- `quality(det_score, size, yaw, pitch, sharp) -> float` (0..1): Erkennungs-Konfidenz × Größe × Frontalität ×
  Schärfe; Profile (|yaw| groß) und unscharfe Bilder bekommen niedrige Werte.

Alles reine CPU-Rechnung auf wenigen Punkten bzw. einem 112×112-Bild (< 0,2 ms je Gesicht).
"""
from __future__ import annotations

import math

import cv2
import numpy as np

# Geometrie eines Durchschnittsgesichts (nur Verhältnisse zählen):
# Nasenspitze liegt ≈ 0,9 × halber Augenabstand vor der Augenebene, ≈ 0,36 × (Augen–Mund-Abstand) vor der
# Augen/Mund-Ebene. In der ArcFace-Vorlage (112×112) liegt die Nase bei 49,6 % zwischen Augen- und Mundlinie.
_NOSE_DEPTH_EYES = 0.9
_NOSE_DEPTH_EYE_MOUTH = 0.36
_PITCH_P0 = 0.496

# Laplace-Varianz (112×112, innerer Bereich) ab der ein Gesicht als scharf gilt. Gemessen an den gespeicherten
# Gesichtsbildern dieser Installation (nur lesend, 200 Bilder): Gesichter < 90 px Median 12–22 (hochskaliert, weich),
# 90–130 px Median 224, > 130 px Median 490. Unschärfe senkt die Qualität höchstens auf die Hälfte (SHARP_FLOOR),
# damit die Skala zu den Backend-Grenzen (face_new_min_quality 0,35 / face_sample_min_quality 0,30) passt –
# kleine Gesichter bestraft schon der Größenfaktor.
SHARP_REF = 60.0
SHARP_FLOOR = 0.5


def head_pose(kps) -> tuple[float, float]:
    """(yaw, pitch) in Grad. yaw > 0: Kopf zur Bildseite des rechten Auges gedreht; pitch > 0: Kinn hoch.
    Nur eine grobe Schätzung (±10°), reicht zum Aussortieren von Profilen und starkem Nicken."""
    p = np.asarray(kps, dtype=np.float64).reshape(5, 2)
    le, re, nose, lm, rm = p
    # Rollwinkel herausrechnen (Augenlinie waagerecht drehen)
    d = re - le
    ang = math.atan2(d[1], d[0])
    c, s = math.cos(-ang), math.sin(-ang)
    rot = np.array([[c, -s], [s, c]])
    q = (p - (le + re) / 2.0) @ rot.T
    le, re, nose, lm, rm = q
    left = nose[0] - le[0]
    right = re[0] - nose[0]
    tot = left + right
    if tot <= 1e-6:
        yaw = 90.0
    else:
        t = (left - right) / tot                      # = (n/e)·tan(yaw)
        yaw = math.degrees(math.atan(t / _NOSE_DEPTH_EYES))
        # Nase außerhalb der Augen → mindestens ~60° (Profil)
        if left <= 0 or right <= 0:
            yaw = math.copysign(max(abs(yaw), 60.0), t if t != 0 else 1.0)
    eye_y = 0.0
    mouth_y = (lm[1] + rm[1]) / 2.0
    span = mouth_y - eye_y
    if span <= 1e-6:
        pitch = 0.0
    else:
        pr = (nose[1] - eye_y) / span                  # = p0 − (n/L)·tan(pitch)
        pitch = math.degrees(math.atan((_PITCH_P0 - pr) / _NOSE_DEPTH_EYE_MOUTH))
    return (round(float(max(-90.0, min(90.0, yaw))), 1), round(float(max(-90.0, min(90.0, pitch))), 1))


def sharpness(aligned_112: np.ndarray) -> float:
    """Laplace-Varianz des ausgerichteten Gesichts (BGR oder Grau, 112×112); größer = schärfer."""
    g = aligned_112 if aligned_112.ndim == 2 else cv2.cvtColor(aligned_112, cv2.COLOR_BGR2GRAY)
    # nur der innere Bereich (Augen/Nase/Mund) – Hintergrundkanten am Rand zählen nicht
    g = g[16:104, 16:96]
    return round(float(cv2.Laplacian(g, cv2.CV_64F).var()), 1)


def day_night(img: np.ndarray) -> str:
    """'night', wenn das Bild praktisch farblos ist (IR-Nachtsicht liefert Graubilder), sonst 'day'."""
    try:
        small = cv2.resize(img, (32, 32), interpolation=cv2.INTER_AREA)
        hsv = cv2.cvtColor(small, cv2.COLOR_BGR2HSV)
        return "night" if float(hsv[:, :, 1].mean()) < 12.0 else "day"
    except Exception:  # noqa: BLE001
        return "day"


def pose_factor(yaw: float | None, pitch: float | None) -> float:
    """1 bei frontal; 30° Drehung ≈ 0,75, 45° ≈ 0,5, ≥ 70° ≈ 0 (Profil). Nicken zählt schwächer."""
    f = 1.0
    if yaw is not None:
        f *= max(0.0, math.cos(math.radians(min(90.0, abs(yaw))))) ** 2
    if pitch is not None:
        f *= max(0.0, math.cos(math.radians(min(90.0, abs(pitch)))))
    return f


def sharp_factor(sharp: float | None) -> float:
    """SHARP_FLOOR (ganz unscharf) .. 1 (≥ SHARP_REF), linear dazwischen."""
    if sharp is None:
        return 1.0
    return float(SHARP_FLOOR + (1.0 - SHARP_FLOOR) * min(1.0, max(0.0, sharp) / SHARP_REF))


def quality(det_score: float, size: float, yaw: float | None, pitch: float | None, sharp: float | None) -> float:
    """Gesamtqualität 0..1. `size` = kürzere Seite des Gesichts in Originalpixeln (Bild, aus dem geschnitten wurde).
    Größenfaktor wie bisher (min(1, size/112)), damit die Skala zu den Backend-Grenzen passt
    (face_new_min_quality 0,35, face_sample_min_quality 0,30)."""
    size_f = min(1.0, max(0.0, float(size)) / 112.0)
    q = float(det_score) * size_f * pose_factor(yaw, pitch) * sharp_factor(sharp)
    return round(max(0.0, min(1.0, q)), 4)
