"""Machbarkeitstest C1/D1: Pose (YOLO11-pose) für Sturz/Liegen und Armgesten.

    python -m vg_worker.tests_feasibility.posetest --videos /videos/raw --models yolo11n-pose.pt,yolo11s-pose.pt \
        [--weights-dir /wmodels] [--detector /models/yolo11s.pt] [--rtsp rtsp://testcam:8554/cam1] [--out /out]

Teile:
1. **Last/VRAM:** Batch von 16 Personen-Ausschnitten (256 px) und Ganzbild 640; ms je Batch, VRAM-Spitze des
   Geräts (inkl. produktivem Worker) und Zuwachs gegenüber vorher; Hochrechnung GPU-Anteil bei 16 Kameras × 5 fps.
2. **Liegen/Sturz** auf UR Fall Detection (Kwolek & Kępski 2014, CC BY-NC-SA 4.0): 30 Stürze + 40 Alltagsszenen,
   Kamera 0 (rechte Bildhälfte = RGB, 320×240), Wahrheit je Frame aus `urfall-cam0-{falls,adls}.csv`
   (-1 = nicht liegend, 0 = fällt, 1 = liegt). Vergleich: (a) Pose Ganzbild, (b) Pose auf Detektor-Ausschnitt,
   (c) nur Box-Seitenverhältnis des Detektors (ohne Pose). Zusätzlich: Wird die liegende Person vom
   Produktiv-Detektor (YOLO11s, conf 0,5 wie `min_score`) überhaupt gefunden (Track-Abriss)?
3. **Gesten** auf UTD-MHAD (Chen et al. 2015, nur Forschung/privat): Arme verschränken (a6), Winken (a3),
   Wischen links/rechts (a1/a2) gegen Negativ-Aktionen (Klatschen, Bizeps-Curl, Joggen, Gehen, Hinsetzen/Aufstehen).
   Regelbasierter Klassifikator auf Keypoints; eine Geste gilt, wenn sie in ≥ 80 % der Frames über 1,0 s erkannt wird.
"""
from __future__ import annotations

import argparse
import csv
import glob
import json
import os
import re
import time
from collections import defaultdict

import numpy as np

from .common import VramProbe, mib, timeit, torch_mem, read_video_frames

# COCO-Keypoints
NOSE, LSH, RSH, LEL, REL, LWR, RWR, LHIP, RHIP, LKN, RKN, LANK, RANK = 0, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16
KP_CONF = 0.3


# ------------------------------------------------------------------ Haltung
def torso_angle(kp: np.ndarray, kc: np.ndarray) -> float | None:
    """Winkel (Grad) zwischen Rumpf (Schultermitte→Hüftmitte) und der Senkrechten. None = zu unsicher."""
    pts_s = [kp[i] for i in (LSH, RSH) if kc[i] >= KP_CONF]
    pts_h = [kp[i] for i in (LHIP, RHIP) if kc[i] >= KP_CONF]
    if not pts_s or not pts_h:
        return None
    s = np.mean(pts_s, axis=0)
    h = np.mean(pts_h, axis=0)
    v = h - s
    n = float(np.hypot(*v))
    if n < 1e-6:
        return None
    return float(np.degrees(np.arccos(min(1.0, abs(v[1]) / n))))


def body_angle(kp: np.ndarray, kc: np.ndarray) -> float | None:
    """Winkel Kopf/Schultern→Knöchel bzw. Knie zur Senkrechten (robuster bei verdeckten Hüften)."""
    top = [kp[i] for i in (NOSE, LSH, RSH) if kc[i] >= KP_CONF]
    low = [kp[i] for i in (LANK, RANK, LKN, RKN) if kc[i] >= KP_CONF]
    if not top or not low:
        return None
    v = np.mean(low, axis=0) - np.mean(top, axis=0)
    n = float(np.hypot(*v))
    if n < 1e-6:
        return None
    return float(np.degrees(np.arccos(min(1.0, abs(v[1]) / n))))


def lying_from_pose(kp, kc, box) -> bool | None:
    a = torso_angle(kp, kc)
    b = body_angle(kp, kc)
    vals = [x for x in (a, b) if x is not None]
    if vals:
        return max(vals) > 55.0
    return lying_from_box(box)


def lying_from_box(box) -> bool | None:
    if box is None:
        return None
    w, h = box[2] - box[0], box[3] - box[1]
    return (w / max(h, 1e-6)) > 1.3


# ------------------------------------------------------------------ Gesten
def classify_gesture(kp: np.ndarray, kc: np.ndarray) -> set[str]:
    """Regelbasiert auf Bildkoordinaten (Pixel). Gibt eine Menge erkannter Gesten zurück."""
    g: set[str] = set()
    ok = lambda *ix: all(kc[i] >= KP_CONF for i in ix)  # noqa: E731
    if not ok(LSH, RSH):
        return g
    sw = float(np.hypot(*(kp[LSH] - kp[RSH])))  # Schulterbreite als Maßstab
    if sw < 5:
        return g
    head_y = kp[NOSE][1] if kc[NOSE] >= KP_CONF else min(kp[LSH][1], kp[RSH][1]) - 0.6 * sw
    # Hand über dem Kopf (je Seite) / beide Arme hoch
    up = [ok(w) and kp[w][1] < head_y for w in (LWR, RWR)]
    if any(up):
        g.add("hand_up")
    if all(up):
        g.add("arms_up")
    # Arme verschränkt: beide Handgelenke vor dem Rumpf auf der jeweils anderen Körperseite, unter den Schultern
    if ok(LWR, RWR, LHIP, RHIP):
        cx = (kp[LSH][0] + kp[RSH][0]) / 2
        sy = (kp[LSH][1] + kp[RSH][1]) / 2
        hy = (kp[LHIP][1] + kp[RHIP][1]) / 2
        lw, rw = kp[LWR], kp[RWR]
        # Bild gespiegelt: linke Schulter (Person) liegt rechts im Bild -> Vorzeichen über Schulterlage bestimmen
        side = np.sign(kp[LSH][0] - kp[RSH][0]) or 1.0
        crossed = (np.sign(lw[0] - cx) == -side) and (np.sign(rw[0] - cx) == side)
        in_band = sy < lw[1] < hy and sy < rw[1] < hy
        close = abs(lw[0] - rw[0]) < 1.2 * sw
        if crossed and in_band and close:
            g.add("arms_cross")
    # Arm seitlich ausgestreckt: Handgelenk auf Schulterhöhe, weit außen (> 1,2 × Schulterbreite vom Schultermittelpunkt)
    cx = (kp[LSH][0] + kp[RSH][0]) / 2
    for w, s, name in ((LWR, LSH, "arm_side"), (RWR, RSH, "arm_side")):
        if ok(w, s) and abs(kp[w][1] - kp[s][1]) < 0.6 * sw and abs(kp[w][0] - cx) > 1.5 * sw:
            g.add(name)
    return g


def held(frames_flags: list[bool], fps: float, hold_s: float = 1.0, share: float = 0.8) -> bool:
    n = max(1, int(round(hold_s * fps)))
    if len(frames_flags) < n:
        return False
    a = np.array(frames_flags, dtype=float)
    c = np.convolve(a, np.ones(n), "valid") / n
    return bool((c >= share).any())


# ------------------------------------------------------------------ Modelle
class Pose:
    def __init__(self, path: str):
        from ultralytics import YOLO
        self.name = os.path.basename(path)
        self.m = YOLO(path)

    def full(self, frame, imgsz=640):
        r = self.m.predict(frame, imgsz=imgsz, conf=0.25, half=True, verbose=False)[0]
        return self._people(r, (0, 0))

    def crops(self, crops: list, imgsz=256):
        if not crops:
            return []
        rs = self.m.predict([c for c, _ in crops], imgsz=imgsz, conf=0.15, half=True, verbose=False)
        out = []
        for r, (_c, off) in zip(rs, crops):
            ppl = self._people(r, off)
            out.append(max(ppl, key=lambda p: p["score"]) if ppl else None)
        return out

    @staticmethod
    def _people(r, off):
        if r.keypoints is None or r.boxes is None or len(r.boxes) == 0:
            return []
        kp = r.keypoints.xy.cpu().numpy()
        kc = r.keypoints.conf.cpu().numpy() if r.keypoints.conf is not None else np.ones(kp.shape[:2])
        bx = r.boxes.xyxy.cpu().numpy()
        sc = r.boxes.conf.cpu().numpy()
        ox, oy = off
        return [{"kp": kp[i] + [ox, oy], "kc": kc[i], "box": bx[i] + [ox, oy, ox, oy], "score": float(sc[i])}
                for i in range(len(bx))]


class Detector:
    def __init__(self, path: str):
        from ultralytics import YOLO
        self.m = YOLO(path)

    def persons(self, frame, conf=0.25, imgsz=640):
        r = self.m.predict(frame, imgsz=imgsz, conf=conf, classes=[0], half=True, verbose=False)[0]
        return [(b.tolist(), float(s)) for b, s in zip(r.boxes.xyxy.cpu().numpy(), r.boxes.conf.cpu().numpy())]


def crop_box(frame, box, margin=0.15):
    H, W = frame.shape[:2]
    x1, y1, x2, y2 = box
    mw, mh = (x2 - x1) * margin, (y2 - y1) * margin
    a, b = int(max(0, x1 - mw)), int(max(0, y1 - mh))
    c, d = int(min(W, x2 + mw)), int(min(H, y2 + mh))
    return frame[b:d, a:c], (a, b)


# ------------------------------------------------------------------ Teil 1: Last
def bench_load(pose_path: str, det: Detector, probe: VramProbe, sample_frames: list) -> dict:
    import torch
    torch.cuda.empty_cache()
    torch.cuda.reset_peak_memory_stats()
    base = probe.used()
    probe.reset_peak()
    t0 = time.time()
    p = Pose(pose_path)
    crops = []
    for f in sample_frames:
        for b, _s in det.persons(f):
            crops.append(crop_box(f, b))
    if not crops:
        crops = [(sample_frames[0], (0, 0))]
    batch = (crops * 16)[:16]
    p.crops(batch[:1])
    load_s = time.time() - t0
    t_crop16 = timeit(lambda: p.crops(batch), n=20)
    t_crop1 = timeit(lambda: p.crops(batch[:1]), n=20)
    t_full = timeit(lambda: p.full(sample_frames[0]), n=20)
    peak = probe.peak
    # 16 Kameras × 5 fps × 1 Person = 80 Ausschnitte/s = 5 Batches à 16
    gpu_share = 5 * t_crop16["median_ms"] / 1000
    res = {"model": p.name, "load_s": round(load_s, 1), "crop16_256": t_crop16, "crop1_256": t_crop1,
           "full640": t_full, "device_before_mib": mib(base), "device_peak_mib": mib(peak),
           "device_delta_mib": mib(peak - base), **torch_mem(),
           "gpu_share_16cam_5fps_1person": round(gpu_share, 3), "n_real_crops": len(crops)}
    del p
    torch.cuda.empty_cache()
    return res


# ------------------------------------------------------------------ Teil 2: Liegen
def load_urfall_labels(d: str) -> dict:
    lab: dict[str, dict[int, int]] = defaultdict(dict)
    for fn in ("urfall-cam0-falls.csv", "urfall-cam0-adls.csv"):
        p = os.path.join(d, fn)
        if not os.path.exists(p):
            continue
        with open(p) as f:
            for row in csv.reader(f):
                if len(row) >= 3:
                    lab[row[0]][int(row[1])] = int(row[2])
    return lab


def eval_lying(pose: Pose, det: Detector, d: str, fps: float = 30.0) -> dict:
    labels = load_urfall_labels(d)
    vids = sorted(glob.glob(os.path.join(d, "*-cam0.mp4")))
    conf_m = {k: defaultdict(int) for k in ("pose_full", "pose_crop", "box_only")}
    det_lying = {"frames": 0, "det_any": 0, "det_prod": 0}
    clips = []
    for v in vids:
        seq = os.path.basename(v).replace("-cam0.mp4", "")
        lab = labels.get(seq, {})
        if not lab:
            continue
        flags = {k: [] for k in conf_m}
        truths = []
        for i, _t, f in read_video_frames(v, crop=(320, 0, 640, 240)):
            y = lab.get(i + 1)
            if y is None:
                continue
            f = np.ascontiguousarray(f)
            persons = det.persons(f, conf=0.25)
            best = max(persons, key=lambda p: p[1]) if persons else None
            if y == 1:
                det_lying["frames"] += 1
                det_lying["det_any"] += bool(persons)
                det_lying["det_prod"] += bool(best and best[1] >= 0.5)
            pf = pose.full(f)
            pf = max(pf, key=lambda p: p["score"]) if pf else None
            pc = pose.crops([crop_box(f, best[0])])[0] if best else None
            pred = {
                "pose_full": lying_from_pose(pf["kp"], pf["kc"], pf["box"]) if pf else None,
                "pose_crop": lying_from_pose(pc["kp"], pc["kc"], best[0]) if pc else (lying_from_box(best[0]) if best else None),
                "box_only": lying_from_box(best[0]) if best else None,
            }
            for k, p in pred.items():
                flags[k].append(bool(p))
                if y in (-1, 1):
                    t = "pos" if y == 1 else "neg"
                    if p is None:
                        conf_m[k][f"{t}_none"] += 1
                    else:
                        conf_m[k][("tp" if p else "fn") if y == 1 else ("fp" if p else "tn")] += 1
            truths.append(y)
        kind = "fall" if seq.startswith("fall") else ("adl_lying" if 1 in truths else "adl")
        clip = {"seq": seq, "kind": kind, "frames": len(truths)}
        for k in conf_m:
            # Ereignis: ≥ 1 s (≥ 80 %) liegend. Das Video endet bei Stürzen oft < 1 s nach dem Aufprall,
            # deshalb zusätzlich 0,5 s.
            clip[f"{k}_1s"] = held(flags[k], fps, 1.0)
            clip[f"{k}_05s"] = held(flags[k], fps, 0.5)
        clips.append(clip)
        print(f"  {seq}: {kind} " + " ".join(f"{k}={clip[k + '_1s']}/{clip[k + '_05s']}" for k in conf_m), flush=True)
    summ = {}
    for k, c in conf_m.items():
        tp, fn, fp, tn = c["tp"], c["fn"], c["fp"], c["tn"]
        summ[k] = {**dict(c), "recall": round(tp / max(tp + fn + c["pos_none"], 1), 3),
                   "specificity": round(tn / max(tn + fp + c["neg_none"], 1), 3),
                   "precision": round(tp / max(tp + fp, 1), 3)}
        for kind in ("fall", "adl", "adl_lying"):
            cs = [x for x in clips if x["kind"] == kind]
            summ[k][f"clips_{kind}"] = len(cs)
            summ[k][f"event_1s_{kind}"] = sum(x[f"{k}_1s"] for x in cs)
            summ[k][f"event_05s_{kind}"] = sum(x[f"{k}_05s"] for x in cs)
    det_lying["recall_any_conf025"] = round(det_lying["det_any"] / max(det_lying["frames"], 1), 3)
    det_lying["recall_conf05"] = round(det_lying["det_prod"] / max(det_lying["frames"], 1), 3)
    return {"per_method": summ, "detector_on_lying_frames": det_lying, "clips": clips}


# ------------------------------------------------------------------ Teil 3: Gesten
UTD_ACTIONS = {1: "swipe_left", 2: "swipe_right", 3: "wave", 4: "clap", 6: "arms_cross", 16: "arm_curl",
               22: "jog", 23: "walk", 24: "sit_to_stand", 25: "stand_to_sit", 17: "tennis_serve", 27: "squat"}
EXPECT = {"arms_cross": "arms_cross", "wave": "hand_up", "swipe_left": "arm_side", "swipe_right": "arm_side",
          "tennis_serve": "hand_up"}


def eval_gestures(pose: Pose, d: str) -> dict:
    vids = sorted(glob.glob(os.path.join(d, "a*_s*_t*_color.avi")))
    res = defaultdict(lambda: defaultdict(lambda: {"clips": 0, "held_1s": 0, "any_frame": 0}))
    for v in vids:
        m = re.match(r"a(\d+)_s(\d+)_t(\d+)", os.path.basename(v))
        act = UTD_ACTIONS.get(int(m.group(1))) if m else None
        if not act:
            continue
        flags = defaultdict(list)
        n = 0
        for _i, _t, f in read_video_frames(v):
            ppl = pose.full(f)
            p = max(ppl, key=lambda x: x["score"]) if ppl else None
            gs = classify_gesture(p["kp"], p["kc"]) if p else set()
            for g in ("arms_cross", "hand_up", "arms_up", "arm_side"):
                flags[g].append(g in gs)
            n += 1
        for g in ("arms_cross", "hand_up", "arms_up", "arm_side"):
            r = res[act][g]
            r["clips"] += 1
            r["held_1s"] += held(flags[g], 30.0, 1.0)
            r["any_frame"] += any(flags[g])
    out = {a: {g: dict(v) for g, v in gd.items()} for a, gd in res.items()}
    # Zusammenfassung: Trefferquote auf der erwarteten Geste, Fehlalarme (held_1s) auf Negativ-Aktionen
    summ = {}
    for g in ("arms_cross", "hand_up", "arm_side"):
        pos = [a for a, e in EXPECT.items() if e == g and a in out]
        neg = [a for a in out if a not in EXPECT]
        tp = sum(out[a][g]["held_1s"] for a in pos)
        np_ = sum(out[a][g]["clips"] for a in pos)
        fp = sum(out[a][g]["held_1s"] for a in neg)
        nn = sum(out[a][g]["clips"] for a in neg)
        summ[g] = {"positives": pos, "hit_held_1s": f"{tp}/{np_}", "false_alarm_held_1s": f"{fp}/{nn}",
                   "hit_any_frame": f"{sum(out[a][g]['any_frame'] for a in pos)}/{np_}"}
    return {"summary": summ, "by_action": out}


# ------------------------------------------------------------------ main
def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--videos", default="/videos/raw")
    ap.add_argument("--models", default="yolo11n-pose.pt,yolo11s-pose.pt")
    ap.add_argument("--weights-dir", default="/wmodels")
    ap.add_argument("--detector", default="/models/yolo11s.pt")
    ap.add_argument("--rtsp", default="")
    ap.add_argument("--parts", default="load,lying,gestures")
    ap.add_argument("--out", default="/out")
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    os.makedirs(a.weights_dir, exist_ok=True)
    os.chdir(a.weights_dir)  # ultralytics lädt fehlende Gewichte ins CWD

    probe = VramProbe().start()
    rep: dict = {"gpu": probe.gpu_name(), "device_total_mib": mib(probe.total()),
                 "device_used_at_start_mib": mib(probe.used()), "started": time.strftime("%Y-%m-%d %H:%M:%S")}
    det = Detector(a.detector)
    # Beispielbilder für die Lastmessung: UR-Fall-Frames (kleine Personen) und optional ein RTSP-Bild
    samples = []
    for v in sorted(glob.glob(os.path.join(a.videos, "urfall", "fall-0[1-4]-cam0.mp4"))):
        for _i, _t, f in read_video_frames(v, every=40, crop=(320, 0, 640, 240)):
            samples.append(np.ascontiguousarray(f))
    if a.rtsp:
        import cv2
        cap = cv2.VideoCapture(a.rtsp)
        ok, f = cap.read()
        if ok:
            samples.insert(0, f)
        cap.release()
    if not samples:
        samples = [np.zeros((480, 640, 3), np.uint8)]

    parts = set(a.parts.split(","))
    for mp in a.models.split(","):
        mr: dict = {}
        print(f"== {mp}", flush=True)
        if "load" in parts:
            mr["load"] = bench_load(mp, det, probe, samples)
            print(json.dumps(mr["load"], indent=1), flush=True)
        pose = Pose(mp)
        if "lying" in parts and os.path.isdir(os.path.join(a.videos, "urfall")):
            mr["lying"] = eval_lying(pose, det, os.path.join(a.videos, "urfall"))
            print(json.dumps(mr["lying"]["per_method"], indent=1), json.dumps(mr["lying"]["detector_on_lying_frames"]), flush=True)
        if "gestures" in parts and os.path.isdir(os.path.join(a.videos, "utd")):
            mr["gestures"] = eval_gestures(pose, os.path.join(a.videos, "utd"))
            print(json.dumps(mr["gestures"]["summary"], indent=1), flush=True)
        rep[mp] = mr
        del pose
    rep["device_peak_total_mib"] = mib(probe.stop())
    with open(os.path.join(a.out, "posetest.json"), "w") as f:
        json.dump(rep, f, indent=1, ensure_ascii=False, default=str)
    print("Bericht:", os.path.join(a.out, "posetest.json"))


if __name__ == "__main__":
    main()
