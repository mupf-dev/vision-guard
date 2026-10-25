"""Bildkennzahlen auf der CPU (C5 Kamera-Gesundheit, später A3 virtuelle Sensoren).

Alles auf 320 px Breite in Graustufen, numpy/OpenCV, ca. 6 ms je Bild (gemessen in Welle 0,
`docs/plan/roadmap/messungen.md` Abschnitt 4). Vorlage: `tests_feasibility/healthtest.py`.

- `metrics(frame)`  → Kennzahlen eines Bildes (Helligkeit, Kontrast, normierte Schärfe, flache Blöcke, Sättigung)
- `compare(ref, cur)` → Vergleich mit einer Referenz (Verschiebung per Phasenkorrelation der Gradientenbilder,
  Struktur-Korrelation, Schärfeverhältnis)
- `classify(ref, cur, prev, base_resp)` → Rohbefunde `dark|blur|covered|frozen|moved` eines einzelnen Prüfbildes
- `frame_diff(a, b)` → mittlere Absolutdifferenz zweier Prüfbilder (eingefroren)

Schwellen aus der Messung W0 (Empfehlungen C5): `covered` = flache Blöcke +35 %-Punkte gegenüber Referenz oder
Kontrast < 25 % der Referenz; `blur` = normierte Schärfe < 0,45 × Referenz; `moved` = Verschiebung > 2 % bei
Antwort > 0,04 oder Antwort < 0,5 × gleitende Basis; `frozen` = Differenz zum vorigen Prüfbild < 0,05;
`dark` = Mittelwert < 25 und 95-Perzentil < 60 (Entwurf C5; IR-Nachtbild bleibt deutlich heller).
"""
from __future__ import annotations

from typing import Any, Optional

import cv2
import numpy as np

WIDTH = 320                 # Analysebreite

# Schwellen (siehe Modul-Doku)
DARK_MEAN = 25.0
DARK_P95 = 60.0
COVER_FLAT_GAIN = 0.35
COVER_CONTRAST_RATIO = 0.25
BLUR_RATIO = 0.45
MOVED_SHIFT = 0.02
MOVED_MIN_RESP = 0.04
MOVED_RESP_DROP = 0.5
FROZEN_DIFF = 0.05
IR_SATURATION = 8.0         # mittlere Sättigung darunter = Graustufen-/IR-Bild („night“)
# Referenz nur aus „normalen“ Bildern anlegen (nicht dunkel, nicht weitgehend einfarbig)
REF_MIN_CONTRAST = 5.0
REF_MAX_FLAT = 0.85
# Abdeckung ohne Referenz (z. B. Kamera schon beim ersten Start abgeklebt): fast nur flache Blöcke, kaum Kontrast
COVER_ABS_FLAT = 0.92
COVER_ABS_CONTRAST = 4.0


def _resize(frame: np.ndarray) -> np.ndarray:
    h, w = frame.shape[:2]
    if w == WIDTH:
        return frame
    return cv2.resize(frame, (WIDTH, max(1, int(round(h * WIDTH / w)))), interpolation=cv2.INTER_AREA)


def _flat_share(g: np.ndarray, b: int = 16) -> float:
    """Anteil fast einfarbiger 16×16-Blöcke (hoch bei Abdeckung)."""
    h, w = g.shape
    g = g[: h - h % b, : w - w % b].astype(np.float32)
    if g.size == 0:
        return 0.0
    blocks = g.reshape(g.shape[0] // b, b, g.shape[1] // b, b).std(axis=(1, 3))
    return float((blocks < 4.0).mean())


def metrics(frame: np.ndarray) -> dict[str, Any]:
    """Kennzahlen eines BGR-Bildes. `g` (Graustufen 320 px) bleibt für Vergleiche im Ergebnis."""
    small = _resize(frame)
    g = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
    hsv = cv2.cvtColor(small, cv2.COLOR_BGR2HSV)
    contrast = float(g.std())
    return {
        "g": g,
        "brightness": float(g.mean()),
        "p95": float(np.percentile(g, 95)),
        "contrast": contrast,
        # Laplace-Varianz normiert auf den Kontrast → weitgehend helligkeitsunabhängig
        "sharpness": float(cv2.Laplacian(g, cv2.CV_64F).var()) / (contrast ** 2 + 1.0),
        "saturation": float(hsv[:, :, 1].mean()),
        "flat_share": _flat_share(g),
    }


def mode_of(m: dict[str, Any]) -> str:
    """'night' (Graustufen/IR) oder 'day' (Farbe)."""
    return "night" if m["saturation"] < IR_SATURATION else "day"


def usable_as_reference(m: dict[str, Any]) -> bool:
    return (m["brightness"] >= DARK_MEAN or m["p95"] >= DARK_P95) and m["contrast"] >= REF_MIN_CONTRAST \
        and m["flat_share"] <= REF_MAX_FLAT


def _grad(g: np.ndarray) -> np.ndarray:
    a = cv2.GaussianBlur(g.astype(np.float32), (3, 3), 0)
    m = cv2.magnitude(cv2.Sobel(a, cv2.CV_32F, 1, 0), cv2.Sobel(a, cv2.CV_32F, 0, 1))
    return (m - m.mean()) / (m.std() + 1e-6)


def compare(ref: dict[str, Any], cur: dict[str, Any]) -> dict[str, float]:
    """Vergleich mit der Referenz. Größen müssen übereinstimmen (sonst wird `cur` angepasst)."""
    rg, cg = ref["g"], cur["g"]
    if rg.shape != cg.shape:
        cg = cv2.resize(cg, (rg.shape[1], rg.shape[0]), interpolation=cv2.INTER_AREA)
    ga, gb = _grad(rg), _grad(cg)
    win = cv2.createHanningWindow(ga.shape[::-1], cv2.CV_32F)
    (dx, dy), resp = cv2.phaseCorrelate(ga, gb, win)
    sx, sy = dx / ga.shape[1], dy / ga.shape[0]
    return {"shift": float(np.hypot(sx, sy)), "shift_x": float(sx), "shift_y": float(sy),
            "shift_resp": float(resp), "ncc": float((ga * gb).mean()),
            "sharp_ratio": cur["sharpness"] / max(ref["sharpness"], 1e-6),
            "flat_gain": cur["flat_share"] - ref["flat_share"],
            "contrast_ratio": cur["contrast"] / max(ref["contrast"], 1e-6)}


def frame_diff(a: dict[str, Any], b: dict[str, Any]) -> float:
    ga, gb = a["g"], b["g"]
    if ga.shape != gb.shape:
        return 255.0
    return float(np.abs(ga.astype(np.int16) - gb.astype(np.int16)).mean())


def classify(ref: Optional[dict[str, Any]], cur: dict[str, Any], prev: Optional[dict[str, Any]],
             base_resp: Optional[float]) -> tuple[dict[str, float], dict[str, float]]:
    """Rohbefunde eines Prüfbildes → ({type: value}, Vergleichswerte).

    Vorrang: `dark` vor `covered` vor `blur` vor `moved` (je Bild höchstens eins davon); `frozen` zusätzlich.
    Ohne Referenz werden nur `dark` und `frozen` geprüft. `base_resp` = gleitende typische Korrelationsantwort
    der Kamera (None = noch unbekannt → Kriterium „Antwort eingebrochen“ ruht)."""
    found: dict[str, float] = {}
    c: dict[str, float] = {}
    if prev is not None:
        d = frame_diff(prev, cur)
        c["diff"] = d
        if d < FROZEN_DIFF:
            found["frozen"] = round(d, 4)
    if cur["brightness"] < DARK_MEAN and cur["p95"] < DARK_P95:
        found["dark"] = round(cur["brightness"], 1)
        return found, c
    if ref is None:
        if cur["flat_share"] >= COVER_ABS_FLAT and cur["contrast"] < COVER_ABS_CONTRAST:
            found["covered"] = round(cur["flat_share"], 3)
        return found, c
    c.update(compare(ref, cur))
    if c["flat_gain"] > COVER_FLAT_GAIN or c["contrast_ratio"] < COVER_CONTRAST_RATIO:
        found["covered"] = round(max(c["flat_gain"], 0.0), 3)
    elif c["sharp_ratio"] < BLUR_RATIO:
        found["blur"] = round(c["sharp_ratio"], 3)
    elif (c["shift"] > MOVED_SHIFT and c["shift_resp"] > MOVED_MIN_RESP) or \
            (base_resp is not None and c["shift_resp"] < MOVED_RESP_DROP * base_resp):
        found["moved"] = round(c["shift"], 4)
    return found, c
