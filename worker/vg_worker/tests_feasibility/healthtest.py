"""Machbarkeitstest C5 Kamera-Gesundheit (nur CPU): verdeckt / verschoben / unscharf / eingefroren / dunkel.

    # 1) Synthetisch: Störungen auf echte Frames anwenden, Erkennungsrate und Fehlalarme messen
    python -m vg_worker.tests_feasibility.healthtest synth --src cam_1,cam_2,cam_3 --frames 40 --every 3 [--out /out]
    # 2) Beobachten: echte Kamera über Stunden (Fehlalarme, z. B. IR-Umschaltung in der Nacht); nur Kennzahlen, keine Bilder
    python -m vg_worker.tests_feasibility.healthtest observe --src cam_3 --hours 6 --every 10 [--out /out]

Bilder kommen über go2rtc (`/api/frame.jpeg?src=`), werden nur im Speicher verarbeitet und nie gespeichert.
Die Messfunktionen (`metrics`, `compare`, `HealthState`) sind als Vorlage für `worker/vg_worker/frame_metrics.py`
und `health.py` gedacht (alles auf 320 px Breite, Graustufen, numpy/OpenCV, < 5 ms je Frame).
"""
from __future__ import annotations

import argparse
import json
import os
import random
import time
from collections import defaultdict

import cv2
import numpy as np

from .common import grab_frame

W = 320  # Analysebreite


# ------------------------------------------------------------------ Kennzahlen
def prep(frame: np.ndarray) -> np.ndarray:
    h, w = frame.shape[:2]
    g = cv2.cvtColor(cv2.resize(frame, (W, int(h * W / w)), interpolation=cv2.INTER_AREA), cv2.COLOR_BGR2GRAY)
    return g


def metrics(frame: np.ndarray) -> dict:
    g = prep(frame)
    hsv = cv2.cvtColor(cv2.resize(frame, (W, g.shape[0]), interpolation=cv2.INTER_AREA), cv2.COLOR_BGR2HSV)
    edges = cv2.Canny(g, 50, 150)
    return {
        "g": g,
        "brightness": float(g.mean()),
        "contrast": float(g.std()),
        "sharpness": float(cv2.Laplacian(g, cv2.CV_64F).var()) / (float(g.std()) ** 2 + 1.0),
        "edge_density": float((edges > 0).mean()),
        "saturation": float(hsv[:, :, 1].mean()),  # ≈ 0 bei IR-Nachtbild
        # Anteil fast einfarbiger Blöcke (16×16) – hoch bei Abdeckung
        "flat_share": float(_flat_share(g)),
    }


def _flat_share(g: np.ndarray, b: int = 16) -> float:
    h, w = g.shape
    g = g[: h - h % b, : w - w % b].astype(np.float32)
    blocks = g.reshape(g.shape[0] // b, b, g.shape[1] // b, b).std(axis=(1, 3))
    return float((blocks < 4.0).mean())


def compare(ref: dict, cur: dict) -> dict:
    """Vergleich mit Referenz: Verschiebung (Phasenkorrelation auf Kantenbildern), Struktur-Ähnlichkeit."""
    a = cv2.GaussianBlur(ref["g"].astype(np.float32), (3, 3), 0)
    b = cv2.GaussianBlur(cur["g"].astype(np.float32), (3, 3), 0)
    # Helligkeitsunabhängig: Gradientenbetrag, normiert
    ga = cv2.magnitude(cv2.Sobel(a, cv2.CV_32F, 1, 0), cv2.Sobel(a, cv2.CV_32F, 0, 1))
    gb = cv2.magnitude(cv2.Sobel(b, cv2.CV_32F, 1, 0), cv2.Sobel(b, cv2.CV_32F, 0, 1))
    ga = (ga - ga.mean()) / (ga.std() + 1e-6)
    gb = (gb - gb.mean()) / (gb.std() + 1e-6)
    win = cv2.createHanningWindow(ga.shape[::-1], cv2.CV_32F)
    (dx, dy), resp = cv2.phaseCorrelate(ga, gb, win)
    ncc = float((ga * gb).mean())  # 1 = gleiche Struktur am gleichen Ort
    return {"shift": float(np.hypot(dx / ga.shape[1], dy / ga.shape[0])), "shift_resp": float(resp),
            "ncc": ncc, "sharp_ratio": cur["sharpness"] / max(ref["sharpness"], 1e-6),
            "diff": float(np.abs(ref["g"].astype(np.int16) - cur["g"].astype(np.int16)).mean())}


def classify(ref: dict, cur: dict, prev: dict | None, base_resp: float = 0.6) -> tuple[set[str], dict]:
    """base_resp = typische Phasenkorrelations-Antwort der Kamera ohne Störung (Bewegung im Bild senkt sie)."""
    c = compare(ref, cur)
    p: set[str] = set()
    flat_gain = cur["flat_share"] - ref["flat_share"]
    c["flat_gain"] = flat_gain
    if flat_gain > 0.35 or cur["contrast"] < 0.25 * ref["contrast"]:
        p.add("covered")
    elif c["sharp_ratio"] < 0.45:
        p.add("blur")
    elif (c["shift"] > 0.02 and c["shift_resp"] > 0.04) or c["shift_resp"] < 0.5 * base_resp:
        p.add("moved")
    if prev is not None:
        d = float(np.abs(prev["g"].astype(np.int16) - cur["g"].astype(np.int16)).mean())
        c["prev_diff"] = d
        if d < 0.05:  # bitgleich bis auf Rundung -> Standbild; echtes Sensorrauschen liegt deutlich darüber
            p.add("frozen")
    if cur["brightness"] < 12:
        p.add("dark")
    return p, c


class HealthState:
    """Hysterese: Problem gilt erst nach `after_s` am Stück, endet nach `clear_s` ohne Befund.
    Zwei Referenzen (Tag/Farbe und Nacht/IR), gewählt über die Sättigung."""

    def __init__(self, after_s: float = 60, clear_s: float = 30):
        self.after_s, self.clear_s = after_s, clear_s
        self.refs: dict[str, dict] = {}
        self.since: dict[str, float] = {}
        self.last_ok: dict[str, float] = {}
        self.active: set[str] = set()
        self.prev = None
        self.base_resp: dict[str, float] = {}

    def mode(self, m: dict) -> str:
        return "ir" if m["saturation"] < 8 else "day"

    def update(self, m: dict, ts: float) -> tuple[set[str], set[str], dict]:
        mode = self.mode(m)
        if mode not in self.refs:
            self.refs[mode] = m  # erste Aufnahme je Modus = Referenz (im Produkt: Knopf „Referenz neu setzen“)
        found, c = classify(self.refs[mode], m, self.prev, self.base_resp.get(mode, 0.6))
        if not (found - {"dark", "frozen"}):  # gleitende Basis nur aus unauffälligen Frames
            b = self.base_resp.get(mode, c["shift_resp"])
            self.base_resp[mode] = 0.9 * b + 0.1 * c["shift_resp"]
        self.prev = m
        started, ended = set(), set()
        for t in found:
            self.since.setdefault(t, ts)
            if t not in self.active and ts - self.since[t] >= self.after_s:
                self.active.add(t)
                started.add(t)
        for t in list(self.since):
            if t not in found:
                self.last_ok.setdefault(t, ts)
                if ts - self.last_ok[t] >= self.clear_s:
                    self.since.pop(t, None)
                    self.last_ok.pop(t, None)
                    if t in self.active:
                        self.active.discard(t)
                        ended.add(t)
            else:
                self.last_ok.pop(t, None)
        c["mode"] = mode
        return started, ended, c


# ------------------------------------------------------------------ Störungen
def perturb(img: np.ndarray, kind: str, rnd: random.Random) -> np.ndarray:
    h, w = img.shape[:2]
    if kind.startswith("shift"):
        f = float(kind[5:]) / 100
        dx = int(w * f) * rnd.choice([-1, 1])
        M = np.float32([[1, 0, dx], [0, 1, 0]])
        return cv2.warpAffine(img, M, (w, h), borderMode=cv2.BORDER_REFLECT)
    if kind.startswith("rot"):
        M = cv2.getRotationMatrix2D((w / 2, h / 2), float(kind[3:]) * rnd.choice([-1, 1]), 1.0)
        return cv2.warpAffine(img, M, (w, h), borderMode=cv2.BORDER_REFLECT)
    if kind.startswith("blur"):
        s = float(kind[4:])
        return cv2.GaussianBlur(img, (0, 0), s * w / 640)
    if kind == "cover":  # Hand/Tuch: fast ganzflächig dunkel, leicht ungleichmäßig
        out = np.full_like(img, rnd.randint(5, 40))
        noise = np.random.default_rng(rnd.randint(0, 1 << 30)).normal(0, 2, img.shape)
        return np.clip(out + noise, 0, 255).astype(np.uint8)
    if kind == "cover_partial":  # 60 % der Fläche verdeckt
        out = img.copy()
        out[:, : int(w * 0.6)] = rnd.randint(5, 40)
        return out
    if kind == "light_down":
        return np.clip(img.astype(np.float32) * 0.55, 0, 255).astype(np.uint8)
    if kind == "light_up":
        return np.clip(img.astype(np.float32) * 1.35 + 10, 0, 255).astype(np.uint8)
    if kind == "ir":  # Nachtbild: Graustufen, Kontrast anders
        g = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        g = cv2.equalizeHist(g)
        return cv2.cvtColor(g, cv2.COLOR_GRAY2BGR)
    if kind == "noise":
        n = np.random.default_rng(rnd.randint(0, 1 << 30)).normal(0, 6, img.shape)
        return np.clip(img + n, 0, 255).astype(np.uint8)
    return img


POSITIVE = {"shift5": "moved", "shift10": "moved", "shift20": "moved", "rot5": "moved", "rot10": "moved",
            "blur3": "blur", "blur6": "blur", "cover": "covered", "cover_partial": "covered"}
NEGATIVE = ["none", "light_down", "light_up", "ir_same_ref", "noise"]


def run_synth(a) -> dict:
    rnd = random.Random(3)
    res: dict = {}
    timings = []
    for src in a.src.split(","):
        frames = []
        for _ in range(a.frames):
            try:
                frames.append(grab_frame(src))
            except Exception as e:  # noqa: BLE001
                print(src, "Fehler:", e, flush=True)
            time.sleep(a.every)
        if len(frames) < 3:
            continue
        ref = metrics(frames[0])
        ir_ref = metrics(perturb(frames[0], "ir", rnd))
        base = float(np.median([compare(ref, metrics(f))["shift_resp"] for f in frames[1:4]]))
        counts: dict = defaultdict(lambda: defaultdict(int))
        stats: dict = defaultdict(list)
        frozen_hits = frozen_fp = 0
        for i, f in enumerate(frames[1:], 1):
            prev = metrics(frames[i - 1])
            # eingefroren: gleiches Bild nochmal (im Produkt kommt es so von go2rtc/ffmpeg)
            fz, _ = classify(ref, metrics(f), metrics(f), base)
            frozen_hits += "frozen" in fz
            for kind in list(POSITIVE) + NEGATIVE:
                if kind == "ir_same_ref":
                    t0 = time.perf_counter()
                    cur = metrics(perturb(f, "ir", rnd))
                    found, _c = classify(ir_ref, cur, None, base)
                else:
                    t0 = time.perf_counter()
                    cur = metrics(perturb(f, kind, rnd) if kind != "none" else f)
                    found, _c = classify(ref, cur, prev if kind == "none" else None, base)
                    if kind == "none":
                        frozen_fp += "frozen" in found
                        found = found - {"frozen"}
                timings.append((time.perf_counter() - t0) * 1000)
                found = found - {"dark"}
                stats[kind].append((_c["ncc"], _c["shift"], _c["shift_resp"], _c["sharp_ratio"], _c["flat_gain"]))
                exp = POSITIVE.get(kind)
                counts[kind]["n"] += 1
                if exp:
                    counts[kind]["hit"] += (exp in found) or (kind == "cover_partial" and bool(found))
                    counts[kind]["other"] += bool(found - {exp})
                else:
                    counts[kind]["false_alarm"] += bool(found)
                    for t in found:
                        counts[kind][f"fa_{t}"] += 1
        n = len(frames) - 1
        res[src] = {k: dict(v) for k, v in counts.items()}
        res[src]["base_resp"] = round(base, 3)
        res[src]["median_ncc_shift_resp_sharp_flat"] = {k: [round(float(x), 3) for x in np.median(np.array(v), axis=0)]
                                                      for k, v in stats.items()}
        res[src]["frozen"] = {"n": n, "hit": frozen_hits, "false_alarm_on_live": frozen_fp}
        print(src, json.dumps(res[src]), flush=True)
    res["ms_per_frame_median"] = round(float(np.median(timings)), 2) if timings else None
    return res


def run_observe(a) -> dict:
    st = HealthState(after_s=a.after_s)
    t_end = time.time() + a.hours * 3600
    log = open(os.path.join(a.out, f"health_observe_{a.src}.jsonl"), "a")
    n = errs = 0
    events = []
    raw_hits = defaultdict(int)
    modes = defaultdict(int)
    while time.time() < t_end:
        ts = time.time()
        try:
            m = metrics(grab_frame(a.src))
        except Exception as e:  # noqa: BLE001
            errs += 1
            log.write(json.dumps({"ts": ts, "error": str(e)[:200]}) + "\n")
            time.sleep(a.every)
            continue
        started, ended, c = st.update(m, ts)
        n += 1
        modes[c["mode"]] += 1
        found, _ = classify(st.refs[c["mode"]], m, None, st.base_resp.get(c["mode"], 0.6))
        for t in found:
            raw_hits[t] += 1
        rec = {"ts": round(ts, 1), "mode": c["mode"], "b": round(m["brightness"], 1), "sat": round(m["saturation"], 1),
               "sharp": round(m["sharpness"], 1), "flat": round(m["flat_share"], 3), "ncc": round(c["ncc"], 3),
               "shift": round(c["shift"], 4), "prev_diff": round(c.get("prev_diff", -1), 3),
               "raw": sorted(found), "active": sorted(st.active)}
        log.write(json.dumps(rec) + "\n")
        log.flush()
        for t in started:
            events.append({"ts": ts, "type": t, "event": "start"})
            print(time.strftime("%H:%M:%S"), "START", t, json.dumps(rec), flush=True)
        for t in ended:
            events.append({"ts": ts, "type": t, "event": "end"})
            print(time.strftime("%H:%M:%S"), "ENDE", t, flush=True)
        time.sleep(max(0.0, a.every - (time.time() - ts)))
    return {"src": a.src, "samples": n, "errors": errs, "hours": a.hours, "every_s": a.every, "after_s": a.after_s,
            "alarms": events, "raw_hits": dict(raw_hits), "modes": dict(modes)}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=["synth", "observe"])
    ap.add_argument("--src", default="cam_1,cam_2")
    ap.add_argument("--frames", type=int, default=40)
    ap.add_argument("--every", type=float, default=3.0)
    ap.add_argument("--hours", type=float, default=6.0)
    ap.add_argument("--after-s", type=float, default=60.0)
    ap.add_argument("--out", default="/out")
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    rep = run_synth(a) if a.mode == "synth" else run_observe(a)
    fn = os.path.join(a.out, f"healthtest_{a.mode}{'_' + a.src if a.mode == 'observe' else ''}.json")
    with open(fn, "w") as f:
        json.dump(rep, f, indent=1, ensure_ascii=False)
    print(json.dumps(rep, indent=1, ensure_ascii=False)[:4000])


if __name__ == "__main__":
    main()
