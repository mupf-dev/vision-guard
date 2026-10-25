"""Armgesten (D1) – regelbasiert auf COCO-Keypoints (keine Finger, keine Handformen).

Gesten (CONTRACT W2.C): `arms_up` (beide Hände über dem Kopf), `arm_left`/`arm_right` (linker/rechter Arm der
Person seitlich waagerecht ausgestreckt), `arms_cross` (Unterarme **über Schulterhöhe** gekreuzt, „X über dem
Kopf“). Bewusst nicht „Arme vor der Brust verschränkt“ (Messung W0): das ist eine alltägliche Ruhehaltung und
würde bei einer Halte-Geste ständig auslösen.

Eine Geste gilt, wenn sie über `hold_s` in ≥ 80 % der Pose-Beobachtungen erkannt wurde (mind. 3 Beobachtungen
und die Beobachtungen müssen ≥ 90 % des Fensters überspannen). Danach 5 s Sperre je Track (alle Gesten).
"""
from __future__ import annotations

import math
from collections import deque

import numpy as np

from .posture import KP_CONF, LEL, LHIP, LSH, LWR, NOSE, REL, RHIP, RSH, RWR

GESTURES = ("arms_up", "arm_left", "arm_right", "arms_cross")
SHARE = 0.8
COOLDOWN_S = 5.0
MIN_OBS = 3


def classify(kp: np.ndarray, kc: np.ndarray) -> set[str]:
    """Gesten eines Einzelbilds (Bildkoordinaten, y nach unten)."""
    g: set[str] = set()

    def ok(*ix: int) -> bool:
        return all(kc[i] >= KP_CONF for i in ix)

    if not ok(LSH, RSH):
        return g
    sw = float(math.hypot(*(kp[LSH] - kp[RSH])))       # Schulterbreite als Maßstab
    if sw < 4:
        return g
    sh_y = float((kp[LSH][1] + kp[RSH][1]) / 2)
    cx = float((kp[LSH][0] + kp[RSH][0]) / 2)
    head_y = float(kp[NOSE][1]) if kc[NOSE] >= KP_CONF else sh_y - 0.6 * sw
    # Person muss aufrecht sein (sonst sind „oben/seitlich“ nicht definiert, z. B. liegend)
    if ok(LHIP, RHIP):
        hip_y = float((kp[LHIP][1] + kp[RHIP][1]) / 2)
        if hip_y - sh_y < 0.6 * sw:
            return g

    lw_ok, rw_ok = ok(LWR), ok(RWR)
    # gekreuzt über Schulterhöhe: Handgelenke jeweils auf der anderen Körperseite, nah beieinander
    if lw_ok and rw_ok:
        side = float(np.sign(kp[LSH][0] - kp[RSH][0])) or 1.0     # +1: linke Schulter rechts im Bild
        lw, rw = kp[LWR], kp[RWR]
        crossed = (np.sign(lw[0] - cx) == -side) and (np.sign(rw[0] - cx) == side)
        high = lw[1] < sh_y - 0.2 * sw and rw[1] < sh_y - 0.2 * sw
        close = abs(float(lw[0] - rw[0])) < 1.0 * sw
        if crossed and high and close:
            g.add("arms_cross")
    # beide Hände über dem Kopf (Ellbogen, soweit sichtbar, über den Schultern)
    if lw_ok and rw_ok and "arms_cross" not in g:
        up = kp[LWR][1] < head_y and kp[RWR][1] < head_y
        elbows = all(kp[e][1] < sh_y + 0.1 * sw for e in (LEL, REL) if kc[e] >= KP_CONF)
        if up and elbows:
            g.add("arms_up")
    # Arm seitlich waagerecht: Handgelenk etwa auf Schulterhöhe, weit außen, Ellbogen dazwischen
    for w, s, e, name in ((LWR, LSH, LEL, "arm_left"), (RWR, RSH, REL, "arm_right")):
        if not ok(w, s):
            continue
        dx = float(kp[w][0] - kp[s][0])
        outward = float(np.sign(kp[s][0] - cx))           # Richtung von der Körpermitte zur Schulter
        level = abs(float(kp[w][1] - kp[s][1])) < 0.6 * sw
        far = abs(float(kp[w][0] - cx)) > 1.5 * sw and np.sign(dx) == outward
        elbow_ok = kc[e] < KP_CONF or abs(float(kp[e][1] - kp[s][1])) < 0.6 * sw
        if level and far and elbow_ok:
            g.add(name)
    return g


class GestureState:
    """Halte-Logik je Track: Beobachtungen der letzten `hold_s` Sekunden."""

    def __init__(self):
        self.obs: deque[tuple[float, frozenset[str]]] = deque()
        self.blocked_until = 0.0

    def add(self, ts: float, gestures: set[str], hold_s: float) -> tuple[str, float, float] | None:
        """Beobachtung hinzufügen; liefert (geste, score, gehaltene Sekunden), sobald eine Geste gilt."""
        self.obs.append((ts, frozenset(gestures)))
        while self.obs and ts - self.obs[0][0] > hold_s + 0.5:
            self.obs.popleft()
        if ts < self.blocked_until:
            return None
        win = [(t, g) for t, g in self.obs if ts - t <= hold_s + 1e-6]
        if len(win) < MIN_OBS:
            return None
        span = win[-1][0] - win[0][0]
        if span < 0.9 * hold_s:
            return None
        best = None
        for name in GESTURES:
            hits = [t for t, g in win if name in g]
            share = len(hits) / len(win)
            if share >= SHARE and gestures and name in gestures:
                held = ts - hits[0]
                if best is None or share > best[1]:
                    best = (name, round(share, 3), round(held, 2))
        if best is not None:
            self.blocked_until = ts + COOLDOWN_S
            self.obs.clear()
        return best
