"""Haltung (C1) aus COCO-Keypoints und Personen-Box: standing | sitting | lying | unknown.

Grundlage: Machbarkeitstest `tests_feasibility/posetest.py` (messungen.md §2):
- „liegt“ = Box-Seitenverhältnis (Breite/Höhe in Pixeln) > 1,3 **oder** Rumpf-/Körperwinkel > 55° zur Senkrechten.
  Die Box allein hatte 0 Fehlalarme auf 24 Alltagsclips (Präzision 1,0), Pose erhöht den Recall.
- Einzelbilder sind verrauscht → `PostureSmoother` glättet über ein Zeitfenster (Standard 2 s, Mehrheit ≥ 60 %).
- Bewegung (`motion`): mittlere Verschiebung sicherer Keypoints zwischen zwei Pose-Beobachtungen, geteilt durch die
  Box-Diagonale, je Sekunde, gleitend gemittelt. 0 = regungslos; Gehen liegt typischerweise bei 0,1–0,5.

Alle Koordinaten in Pixeln (oder einheitlich normiert – nur Verhältnisse und Winkel zählen, die Box-Seiten
müssen aber in Pixeln vorliegen, damit das Seitenverhältnis stimmt).
"""
from __future__ import annotations

import math
from collections import deque

import numpy as np

# COCO-Keypoints
NOSE, LEYE, REYE, LEAR, REAR = 0, 1, 2, 3, 4
LSH, RSH, LEL, REL, LWR, RWR = 5, 6, 7, 8, 9, 10
LHIP, RHIP, LKN, RKN, LANK, RANK = 11, 12, 13, 14, 15, 16
KP_CONF = 0.3

LYING_ASPECT = 1.3          # Box breiter als hoch → liegt (Messung: Präzision 1,0)
LYING_ANGLE = 55.0          # Rumpf/Körper zur Senkrechten
UPRIGHT_ANGLE = 30.0        # Rumpf aufrecht
THIGH_SITTING = 50.0        # Oberschenkel (Hüfte→Knie) flacher als das → sitzt
THIGH_STANDING = 30.0       # Oberschenkel steiler als das → steht

UPRIGHT_ASPECT = 0.8        # Box deutlich höher als breit → liegt sicher nicht

POSTURES = ("standing", "sitting", "lying", "unknown")
# Einzelbild-Beobachtung zusätzlich: "upright" = sicher nicht liegend, aber stehen/sitzen unklar (beendet „liegt“)


def _mean(kp: np.ndarray, kc: np.ndarray, idx) -> np.ndarray | None:
    pts = [kp[i] for i in idx if kc[i] >= KP_CONF]
    return np.mean(pts, axis=0) if pts else None


def _angle_to_vertical(a: np.ndarray | None, b: np.ndarray | None) -> float | None:
    if a is None or b is None:
        return None
    v = b - a
    n = float(math.hypot(float(v[0]), float(v[1])))
    if n < 1e-6:
        return None
    return float(math.degrees(math.acos(min(1.0, abs(float(v[1])) / n))))


def torso_angle(kp: np.ndarray, kc: np.ndarray) -> float | None:
    """Winkel (Grad) Schultermitte→Hüftmitte zur Senkrechten. None = zu unsicher."""
    return _angle_to_vertical(_mean(kp, kc, (LSH, RSH)), _mean(kp, kc, (LHIP, RHIP)))


def body_angle(kp: np.ndarray, kc: np.ndarray) -> float | None:
    """Winkel Kopf/Schultern→Knie/Knöchel zur Senkrechten (robuster bei verdeckten Hüften)."""
    return _angle_to_vertical(_mean(kp, kc, (NOSE, LSH, RSH)), _mean(kp, kc, (LANK, RANK, LKN, RKN)))


def thigh_angle(kp: np.ndarray, kc: np.ndarray) -> float | None:
    """Winkel Hüfte→Knie zur Senkrechten (Mittel beider Seiten, soweit sicher)."""
    vals = []
    for h, k in ((LHIP, LKN), (RHIP, RKN)):
        if kc[h] >= KP_CONF and kc[k] >= KP_CONF:
            a = _angle_to_vertical(kp[h], kp[k])
            if a is not None:
                vals.append(a)
    return float(np.mean(vals)) if vals else None


def box_aspect(box) -> float | None:
    if box is None:
        return None
    w, h = float(box[2] - box[0]), float(box[3] - box[1])
    if w <= 0 or h <= 0:
        return None
    return w / h


def classify(kp: np.ndarray | None, kc: np.ndarray | None, box) -> str:
    """Haltung eines Einzelbilds: standing | sitting | lying | upright | unknown. `kp`/`kc` dürfen None sein (Pose
    hat die Person nicht gefunden) – dann zählt nur die Box (liegt, aufrecht bzw. unknown)."""
    asp = box_aspect(box)
    if asp is not None and asp > LYING_ASPECT:
        return "lying"
    if kp is None or kc is None:
        return "upright" if asp is not None and asp < UPRIGHT_ASPECT * 0.75 else "unknown"
    ta = torso_angle(kp, kc)
    ba = body_angle(kp, kc)
    angles = [a for a in (ta, ba) if a is not None]
    if angles and max(angles) > LYING_ANGLE:
        return "lying"
    if ta is None:
        return "unknown"
    th = thigh_angle(kp, kc)
    if ta <= UPRIGHT_ANGLE + 15:
        if th is not None and th >= THIGH_SITTING:
            return "sitting"
        if th is not None and th <= THIGH_STANDING:
            return "standing" if ta <= UPRIGHT_ANGLE else "unknown"
        # Knie verdeckt (Tisch, Bildrand): sehr schmale Box → steht
        if asp is not None and asp < 0.45 and ta <= UPRIGHT_ANGLE:
            return "standing"
        if ta <= UPRIGHT_ANGLE or (asp is not None and asp < UPRIGHT_ANGLE):
            return "upright"
    return "unknown"


def motion_step(prev_kp: np.ndarray | None, prev_kc: np.ndarray | None, kp: np.ndarray | None,
                kc: np.ndarray | None, box, dt: float) -> float | None:
    """Mittlere Keypoint-Verschiebung / Box-Diagonale je Sekunde zwischen zwei Beobachtungen."""
    if prev_kp is None or kp is None or prev_kc is None or kc is None or dt <= 0 or box is None:
        return None
    ok = (prev_kc >= KP_CONF) & (kc >= KP_CONF)
    if int(ok.sum()) < 4:
        return None
    diag = math.hypot(float(box[2] - box[0]), float(box[3] - box[1]))
    if diag < 1e-6:
        return None
    d = np.linalg.norm(kp[ok] - prev_kp[ok], axis=1)
    return float(np.median(d)) / diag / dt


class PostureSmoother:
    """Glättet Einzelbild-Haltungen einer Person über `window_s` (Mehrheit ≥ `share`). `posture_since` =
    Zeitpunkt (Unix-s) des letzten Wechsels der geglätteten Haltung. `unknown` verdrängt eine bekannte Haltung
    nicht (Pose verliert liegende Personen oft – der Zustand soll dann erhalten bleiben)."""

    def __init__(self, window_s: float = 2.0, share: float = 0.6):
        self.window_s = window_s
        self.share = share
        self.obs: deque[tuple[float, str]] = deque()
        self.posture = "unknown"
        self.since = 0.0
        self.motion: float | None = None
        self._prev: tuple[float, np.ndarray, np.ndarray] | None = None

    def add(self, ts: float, posture: str, kp: np.ndarray | None = None, kc: np.ndarray | None = None,
            box=None) -> bool:
        """Beobachtung hinzufügen. Rückgabe True, wenn sich die geglättete Haltung geändert hat."""
        self.obs.append((ts, posture))
        while self.obs and ts - self.obs[0][0] > self.window_s:
            self.obs.popleft()
        if kp is not None and kc is not None:
            if self._prev is not None:
                m = motion_step(self._prev[1], self._prev[2], kp, kc, box, ts - self._prev[0])
                if m is not None:
                    m = min(m, 5.0)
                    self.motion = m if self.motion is None else 0.6 * self.motion + 0.4 * m
            self._prev = (ts, kp, kc)
        known = [p for _, p in self.obs if p != "unknown"]
        if not known:
            return False
        best = max(set(known), key=known.count)
        if best == "upright":
            # sicher nicht (mehr) liegend: beendet „liegt“, lässt stehen/sitzen unverändert
            if self.posture == "lying" and known.count(best) / len(self.obs) >= self.share and len(self.obs) >= 2:
                self.posture, self.since = "unknown", ts
                return True
            return False
        # Anteil an allen Beobachtungen im Fenster (unknown zählt mit, damit ein einzelner Treffer nicht reicht),
        # Ausnahme: der Wechsel nach „liegt“ braucht nur die Mehrheit der bekannten Beobachtungen (Pose verliert
        # liegende Personen häufig, die Box-Regel ist präzise)
        n_all = len(self.obs)
        ratio = known.count(best) / n_all
        if best == "lying":
            ratio = known.count(best) / len(known) if len(known) >= 2 else 0.0
        if best != self.posture and ratio >= self.share and len(self.obs) >= 2:
            self.posture = best
            self.since = ts
            return True
        return False
