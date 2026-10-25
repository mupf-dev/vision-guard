"""Machbarkeitstest B1 Zeitmaschine: Text→Bild-Suche mit SigLIP 2 vs. MobileCLIP2 (open_clip), VRAM, Zeit, Dedupe.

    python -m vg_worker.tests_feasibility.histtest bench [--models siglip2,mobileclip2] [--videos /videos/raw] [--out /out]
    python -m vg_worker.tests_feasibility.histtest dedupe --src cam_3 --minutes 60 --every 30 [--device cpu]

bench:
- Bildsammlung (~300 Frames) aus den Testvideos: UR Fall (Büro/Wohnung, Person liegt bzw. steht), UTD-MHAD (Arme
  verschränkt, Winken), Intel store-aisle (Regal), bottle-detection (Flaschen), testcam cam1 (Menschen gehen) und
  cam2 (Straße). Wahrheit aus der Quelle (Frame-Labels) und dem Produktiv-Detektor YOLO11s (Objekte).
- 11 Anfragen jeweils **deutsch und englisch**; Kennzahlen: Hit@5, Precision@5, Average Precision; getrennt nach Sprache.
- Zeit je Bild (Batch 1 und 8, fp16) und je Text, VRAM-Zuwachs des Bild-Turms (Gerät + torch).
dedupe:
- Kamera alle `--every` s abtasten (nur im Speicher), Kosinus-Abstand zum zuletzt *gespeicherten* Embedding;
  Anteil der Frames unter der Schwelle (0,02/0,05/0,08/0,12) = Dedupe-Quote (Ziel ≈ 80 %).
"""
from __future__ import annotations

import argparse
import csv
import glob
import json
import os
import random
import time
from collections import defaultdict

import numpy as np

from .common import VramProbe, grab_frame, mib, read_video_frames, timeit, torch_mem

MODELS = {
    "siglip2": ("ViT-B-16-SigLIP2-256", "webli"),
    "mobileclip2": ("MobileCLIP2-S0", "dfndr2b"),
}

# (id, deutsch, englisch, Relevanz-Funktion über Tags)
QUERIES = [
    ("lying", "eine Person liegt auf dem Boden", "a person lying on the floor", lambda t: "person_lying" in t),
    ("bicycle", "ein Fahrrad", "a bicycle", lambda t: "det:bicycle" in t),
    ("car", "ein Auto auf der Straße", "a car on the street", lambda t: "det:car" in t and "street" in t),
    ("shelf", "ein Supermarktregal mit Waren", "a supermarket shelf with products", lambda t: "shelf" in t),
    ("bottles", "Flaschen", "bottles", lambda t: "bottles" in t or "det:bottle" in t),
    ("crossed", "eine Person mit verschränkten Armen", "a person with crossed arms", lambda t: "arms_crossed" in t),
    ("wave", "eine Person winkt mit der Hand", "a person waving their hand", lambda t: "waving" in t),
    ("office", "ein Raum mit Schreibtisch und Bürostühlen", "a room with a desk and office chairs", lambda t: "office" in t),
    ("walking", "Menschen gehen durch einen Gang", "people walking through a hallway", lambda t: "walking" in t),
    ("bus", "ein Bus", "a bus", lambda t: "det:bus" in t),
    ("empty", "ein leerer Raum ohne Menschen", "an empty room without people", lambda t: "indoor" in t and "det:person" not in t),
]


def collect(videos: str, rtsp: list[str], rnd: random.Random) -> list[tuple[np.ndarray, set[str], str]]:
    out = []
    ur = os.path.join(videos, "urfall")
    labels = defaultdict(dict)
    for fn in ("urfall-cam0-falls.csv", "urfall-cam0-adls.csv"):
        p = os.path.join(ur, fn)
        if os.path.exists(p):
            for row in csv.reader(open(p)):
                labels[row[0]][int(row[1])] = int(row[2])
    for v in sorted(glob.glob(os.path.join(ur, "fall-*-cam0.mp4")))[:30]:
        seq = os.path.basename(v)[:-9]
        fr = [(i, f) for i, _t, f in read_video_frames(v, crop=(320, 0, 640, 240))]
        lying = [x for x in fr if labels[seq].get(x[0] + 1) == 1]
        stand = [x for x in fr if labels[seq].get(x[0] + 1) == -1]
        for i, f in rnd.sample(lying, min(2, len(lying))):
            out.append((f, {"person_lying", "office", "indoor"}, f"{seq}#{i}"))
        for i, f in rnd.sample(stand, min(1, len(stand))):
            out.append((f, {"office", "indoor"}, f"{seq}#{i}"))
    utd = os.path.join(videos, "utd")
    for act, tag, n in (("a6", "arms_crossed", 3), ("a3", "waving", 3), ("a23", "", 2), ("a4", "", 2)):
        for v in sorted(glob.glob(os.path.join(utd, f"{act}_s*_t1_color.avi"))):
            fr = [f for _i, _t, f in read_video_frames(v)]
            if not fr:
                continue
            # Geste ist in der Mitte des Clips am deutlichsten
            mid = fr[len(fr) // 3: 2 * len(fr) // 3]
            for f in rnd.sample(mid, min(n, len(mid))):
                out.append((f, {tag, "indoor"} - {""}, f"utd:{os.path.basename(v)}"))
    for name, tags, n in (("store-aisle-detection.mp4", {"shelf", "indoor"}, 30), ("bottle-detection.mp4", {"bottles"}, 20)):
        p = os.path.join(videos, name)
        if os.path.exists(p):
            fr = [f for _i, _t, f in read_video_frames(p, every=5)]
            for f in rnd.sample(fr, min(n, len(fr))):
                out.append((f, set(tags), name))
    for url, tags in zip(rtsp, ({"walking", "indoor"}, {"street"})):
        for k in range(50):
            try:
                out.append((grab_frame(url), set(tags), f"{url}#{k}"))
            except Exception as e:  # noqa: BLE001
                print("RTSP", url, e, flush=True)
                break
            time.sleep(1.2)
    return out


def tag_with_detector(items, det_path: str):
    from ultralytics import YOLO
    m = YOLO(det_path)
    names = m.names
    for f, tags, _src in items:
        r = m.predict(f, imgsz=640, conf=0.45, half=True, verbose=False)[0]
        for c in r.boxes.cls.cpu().numpy().astype(int):
            tags.add(f"det:{names[c]}")
    del m


class Clip:
    def __init__(self, key: str, device: str = "cuda"):
        import open_clip
        import torch
        self.torch = torch
        arch, pre = MODELS[key]
        self.key, self.device = key, device
        model, _, preprocess = open_clip.create_model_and_transforms(arch, pretrained=pre)
        model.eval()
        # Text-Turm auf der CPU lassen, Bild-Turm auf die GPU (wie für den Worker geplant)
        self.text_model = model
        self.preprocess = preprocess
        self.tok = open_clip.get_tokenizer(arch)
        if device == "cuda":
            self.visual = model.visual.half().cuda()
        else:
            self.visual = model.visual
        self.logit_bias = getattr(model, "logit_bias", None)

    def images(self, frames: list[np.ndarray]) -> np.ndarray:
        from PIL import Image
        torch = self.torch
        xs = torch.stack([self.preprocess(Image.fromarray(f[:, :, ::-1])) for f in frames])
        with torch.inference_mode():
            xs = xs.to(self.device)
            if self.device == "cuda":
                xs = xs.half()
            e = self.visual(xs).float()
        return torch.nn.functional.normalize(e, dim=-1).cpu().numpy()

    def texts(self, qs: list[str]) -> np.ndarray:
        torch = self.torch
        with torch.inference_mode():
            t = self.tok(qs)
            e = self.text_model.encode_text(t).float()
        return torch.nn.functional.normalize(e, dim=-1).numpy()


def ap_at(scores: np.ndarray, rel: np.ndarray) -> float:
    order = np.argsort(-scores)
    r = rel[order]
    if r.sum() == 0:
        return float("nan")
    prec = np.cumsum(r) / (np.arange(len(r)) + 1)
    return float((prec * r).sum() / r.sum())


def bench(a) -> dict:
    import torch
    rnd = random.Random(5)
    items = collect(a.videos, a.rtsp.split(",") if a.rtsp else [], rnd)
    print(f"{len(items)} Bilder gesammelt", flush=True)
    tag_with_detector(items, a.detector)
    torch.cuda.empty_cache()
    probe = VramProbe().start()
    rep: dict = {"images": len(items), "device_used_at_start_mib": mib(probe.used()),
                 "tag_counts": {}}
    for q in QUERIES:
        rep["tag_counts"][q[0]] = int(sum(q[3](t) for _f, t, _s in items))
    print("relevante Bilder je Anfrage:", rep["tag_counts"], flush=True)
    frames = [f for f, _t, _s in items]
    for key in a.models.split(","):
        torch.cuda.empty_cache()
        torch.cuda.reset_peak_memory_stats()
        base = probe.used()
        probe.reset_peak()
        t0 = time.time()
        clip = Clip(key)
        load_s = time.time() - t0
        E = np.concatenate([clip.images(frames[i:i + 8]) for i in range(0, len(frames), 8)])
        r: dict = {"load_s": round(load_s, 1), "dim": int(E.shape[1]),
                   "img_b1": timeit(lambda: clip.images(frames[:1]), n=20),
                   "img_b8": timeit(lambda: clip.images(frames[:8]), n=10),
                   "text_cpu_1": timeit(lambda: clip.texts(["ein rotes Paket im Flur"]), n=10, sync=False),
                   "device_delta_mib": mib(probe.peak - base), **torch_mem()}
        res = {}
        for lang_i, lang in ((1, "de"), (2, "en")):
            T = clip.texts([q[lang_i] for q in QUERIES])
            S = T @ E.T
            per = {}
            for qi, q in enumerate(QUERIES):
                rel = np.array([q[3](t) for _f, t, _s in items], dtype=float)
                if rel.sum() == 0:
                    continue
                top = np.argsort(-S[qi])[:5]
                per[q[0]] = {"hit5": bool(rel[top].any()), "p5": float(rel[top].mean()), "ap": round(ap_at(S[qi], rel), 3)}
            res[lang] = {"per_query": per,
                         "hit5": round(float(np.mean([v["hit5"] for v in per.values()])), 3),
                         "p5": round(float(np.mean([v["p5"] for v in per.values()])), 3),
                         "map": round(float(np.nanmean([v["ap"] for v in per.values()])), 3)}
        r["retrieval"] = res
        print(key, json.dumps({k: v for k, v in r.items() if k != "retrieval"}), flush=True)
        print(key, {lang: {k: v for k, v in x.items() if k != "per_query"} for lang, x in res.items()}, flush=True)
        for lang in res:
            print("  ", lang, {q: (v["hit5"], round(v["p5"], 1), v["ap"]) for q, v in res[lang]["per_query"].items()}, flush=True)
        rep[key] = r
        del clip
        torch.cuda.empty_cache()
    rep["device_peak_total_mib"] = mib(probe.stop())
    return rep


def dedupe(a) -> dict:
    clip = Clip(a.models.split(",")[0], device=a.device)
    thr = [0.02, 0.05, 0.08, 0.12]
    last = {t: None for t in thr}
    stored = {t: 0 for t in thr}
    n = 0
    dists = []
    prev = None
    t_end = time.time() + a.minutes * 60
    while time.time() < t_end:
        ts = time.time()
        try:
            e = clip.images([grab_frame(a.src)])[0]
        except Exception as ex:  # noqa: BLE001
            print("Fehler", ex, flush=True)
            time.sleep(a.every)
            continue
        n += 1
        if prev is not None:
            dists.append(float(1 - e @ prev))
        prev = e
        for t in thr:
            if last[t] is None or 1 - float(e @ last[t]) >= t:
                last[t] = e
                stored[t] += 1
        time.sleep(max(0.0, a.every - (time.time() - ts)))
    return {"src": a.src, "model": a.models.split(",")[0], "frames": n, "every_s": a.every,
            "stored": stored, "dedupe_share": {str(t): round(1 - stored[t] / max(n, 1), 3) for t in thr},
            "consecutive_dist": {"median": round(float(np.median(dists)), 4) if dists else None,
                                 "p90": round(float(np.quantile(dists, 0.9)), 4) if dists else None}}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=["bench", "dedupe"])
    ap.add_argument("--models", default="siglip2,mobileclip2")
    ap.add_argument("--videos", default="/videos/raw")
    ap.add_argument("--rtsp", default="rtsp://testcam:8554/cam1,rtsp://testcam:8554/cam2")
    ap.add_argument("--detector", default="/models/yolo11s.pt")
    ap.add_argument("--src", default="cam_3")
    ap.add_argument("--minutes", type=float, default=60)
    ap.add_argument("--every", type=float, default=30)
    ap.add_argument("--device", default="cpu")
    ap.add_argument("--out", default="/out")
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    rep = bench(a) if a.mode == "bench" else dedupe(a)
    with open(os.path.join(a.out, f"histtest_{a.mode}.json"), "w") as f:
        json.dump(rep, f, indent=1, ensure_ascii=False)
    print(json.dumps({k: v for k, v in rep.items() if not isinstance(v, dict) or k in ("dedupe_share", "stored")}, indent=1))


if __name__ == "__main__":
    main()
