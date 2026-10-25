"""Machbarkeitstest B4/D2: Unterscheidet DINOv2 „mein Exemplar“ von „fremdem Exemplar derselben Art“ auf kleinen Crops?

    python -m vg_worker.tests_feasibility.itemtest [--sop /data/sop --fetch 60] [--crops /media/inventory] \
        [--photos /data/own] [--models vit_small_patch14_dinov2.lvd142m] [--out /out]

Datenquellen (es gibt keine Handyfotos eigener Gegenstände – der Nutzer schläft):
1. **Stanford Online Products** (MIT-Lizenz, Spiegel `pawlo2013/StanfordOnlineProducts` auf Hugging Face):
   je Produkt 2–12 Fotos; gleiche Kategorie (Tasse, Wasserkocher, Hefter, Lampe, Stuhl) = „fremde Exemplare“.
   Galerie = 2 Fotos je Produkt in voller Auflösung (≈ Handyfotos beim Anlernen). Anfrage = ein anderes Foto desselben
   bzw. eines fremden Produkts, auf Crop-Größe S (längste Seite 40/60/90/120/200 px) verkleinert und mit
   Kamera-Störungen (JPEG q=60, leichte Unschärfe, Helligkeit) versehen. ROC-AUC eigene vs. fremde je Größe.
   Variante „aug“: Galerie zusätzlich aus verkleinerten/gespiegelten Fassungen der Anlernfotos (Domänenangleich).
2. **Echte Inventar-Crops von cam_3** (`/media/inventory/**/<item_id>_<ts>.jpg`, nur lesen): gleiches `item_id` zu
   verschiedenen Zeiten = gleiches Exemplar; anderes `item_id` = anderer Gegenstand. Zeigt Stabilität bei echtem Licht.
3. Optional `--photos <ordner>/<label>/<exemplar>/*.jpg` (eigene Fotos), gleiche Auswertung wie 1.

Embedding wie im Worker (`identify.PetModel`): quadratisch auffüllen, 224 px, fp16, L2-normiert.
Gemessen werden außerdem ms je Embedding (Batch 1 und 16) und der VRAM-Zuwachs.
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import random
import re
import time
from collections import defaultdict

import numpy as np

from .common import VramProbe, mib, timeit, torch_mem

SOP_CATS = {"mug_final": "mug", "kettle_final": "kettle", "stapler_final": "stapler", "lamp_final": "lamp",
            "chair_final": "chair"}
SIZES = [40, 60, 90, 120, 200]
HF = "https://huggingface.co/datasets/pawlo2013/StanfordOnlineProducts/resolve/main/Stanford_Online_Products"


# ------------------------------------------------------------------ Daten
def fetch_sop(d: str, per_cat: int, seed: int = 1) -> None:
    import requests
    os.makedirs(d, exist_ok=True)
    info = os.path.join(d, "Ebay_info.txt")
    if not os.path.exists(info):
        r = requests.get(f"{HF}/Ebay_info.txt", timeout=60)
        r.raise_for_status()
        open(info, "wb").write(r.content)
    prods: dict[str, list[str]] = defaultdict(list)
    for line in open(info).read().splitlines()[1:]:
        _iid, cid, _sc, path = line.split()
        if path.split("/")[0] in SOP_CATS:
            prods[cid].append(path)
    rnd = random.Random(seed)
    by_cat: dict[str, list[str]] = defaultdict(list)
    for cid, paths in prods.items():
        if len(paths) >= 3:
            by_cat[paths[0].split("/")[0]].append(cid)
    todo = []
    for cat, cids in by_cat.items():
        rnd.shuffle(cids)
        for cid in sorted(cids[:per_cat]):
            for p in prods[cid][:4]:
                todo.append((cid, p))
    from concurrent.futures import ThreadPoolExecutor

    def get(item):
        cid, p = item
        dst = os.path.join(d, SOP_CATS[p.split("/")[0]], cid, os.path.basename(p))
        if os.path.exists(dst):
            return
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        for _ in range(3):
            try:
                r = requests.get(f"{HF}/{p}", timeout=60)
                r.raise_for_status()
                open(dst, "wb").write(r.content)
                return
            except Exception:
                time.sleep(1)

    with ThreadPoolExecutor(16) as ex:
        list(ex.map(get, todo))
    print(f"SOP: {len(todo)} Bilder in {d}", flush=True)


def load_instances(d: str) -> dict[str, dict[str, list[str]]]:
    """<d>/<label>/<exemplar>/*.jpg -> {label: {exemplar: [pfade]}}"""
    out: dict[str, dict[str, list[str]]] = defaultdict(dict)
    for lab in sorted(os.listdir(d)):
        p = os.path.join(d, lab)
        if not os.path.isdir(p):
            continue
        for ex in sorted(os.listdir(p)):
            fs = sorted(glob.glob(os.path.join(p, ex, "*.[jJpP][pPnN][gG]")))
            if len(fs) >= 3:
                out[lab][ex] = fs
    return out


# ------------------------------------------------------------------ Bildstörungen
def degrade(img, size: int, rnd: random.Random):
    import cv2
    h, w = img.shape[:2]
    s = size / max(h, w)
    small = cv2.resize(img, (max(1, int(w * s)), max(1, int(h * s))), interpolation=cv2.INTER_AREA)
    if rnd.random() < 0.5:
        small = cv2.GaussianBlur(small, (3, 3), 0.6)
    a = rnd.uniform(0.75, 1.2)
    small = np.clip(small.astype(np.float32) * a + rnd.uniform(-15, 15), 0, 255).astype(np.uint8)
    ok, buf = cv2.imencode(".jpg", small, [cv2.IMWRITE_JPEG_QUALITY, 60])
    return cv2.imdecode(buf, cv2.IMREAD_COLOR)


def gallery_aug(img):
    import cv2
    outs = [img, cv2.flip(img, 1)]
    h, w = img.shape[:2]
    for size in (60, 120):
        s = size / max(h, w)
        outs.append(cv2.resize(img, (max(1, int(w * s)), max(1, int(h * s))), interpolation=cv2.INTER_AREA))
    return outs


# ------------------------------------------------------------------ Modell
class Embedder:
    def __init__(self, name: str):
        import timm
        import torch
        self.torch = torch
        self.name = name
        kw = {"img_size": 224} if "dinov2" in name else {}
        m = timm.create_model(name, pretrained=True, num_classes=0, **kw).eval().cuda().half()
        cfg = timm.data.resolve_data_config({}, model=m)
        self.m = m
        self.mean = torch.tensor(cfg["mean"], device="cuda").view(1, 3, 1, 1)
        self.std = torch.tensor(cfg["std"], device="cuda").view(1, 3, 1, 1)

    @staticmethod
    def prep(img):
        import cv2
        h, w = img.shape[:2]
        s = max(h, w)
        c = np.zeros((s, s, 3), np.uint8)
        c[(s - h) // 2:(s - h) // 2 + h, (s - w) // 2:(s - w) // 2 + w] = img
        return cv2.resize(c, (224, 224), interpolation=cv2.INTER_AREA)[:, :, ::-1].copy()

    def __call__(self, imgs: list) -> np.ndarray:
        torch = self.torch
        out = []
        for i in range(0, len(imgs), 64):
            x = np.stack([self.prep(im) for im in imgs[i:i + 64]])
            with torch.inference_mode():
                t = torch.from_numpy(x).cuda().permute(0, 3, 1, 2).float() / 255.0
                t = ((t - self.mean) / self.std).half()
                f = torch.nn.functional.normalize(self.m(t).float(), dim=1)
            out.append(f.cpu().numpy())
        return np.concatenate(out) if out else np.zeros((0, 384), np.float32)


def auc(pos: list[float], neg: list[float]) -> float | None:
    if not pos or not neg:
        return None
    from sklearn.metrics import roc_auc_score
    y = [1] * len(pos) + [0] * len(neg)
    return round(float(roc_auc_score(y, list(pos) + list(neg))), 4)


def tpr_at_fpr(pos, neg, fpr=0.05):
    if not pos or not neg:
        return None, None
    thr = float(np.quantile(neg, 1 - fpr))
    return round(float(np.mean(np.array(pos) > thr)), 3), round(thr, 3)


# ------------------------------------------------------------------ Auswertung Exemplare
def eval_instances(emb: Embedder, inst: dict, seed: int = 7) -> dict:
    import cv2
    rnd = random.Random(seed)
    res: dict = {}
    for lab, exs in inst.items():
        names = sorted(exs)
        if len(names) < 4:
            continue
        imgs = {e: [cv2.imread(p) for p in exs[e]] for e in names}
        imgs = {e: [i for i in v if i is not None] for e, v in imgs.items()}
        names = [e for e in names if len(imgs[e]) >= 3]
        gal_plain = {e: emb(imgs[e][:2]) for e in names}
        gal_aug = {e: emb([a for im in imgs[e][:2] for a in gallery_aug(im)]) for e in names}
        res[lab] = {"exemplars": len(names)}
        for size in SIZES + ["full"]:
            q_imgs, q_owner = [], []
            for e in names:
                for im in imgs[e][2:]:
                    q_imgs.append(im if size == "full" else degrade(im, size, rnd))
                    q_owner.append(e)
            q = emb(q_imgs)
            for gname, gal in (("plain", gal_plain), ("aug", gal_aug)):
                pos, neg = [], []
                for qi, owner in enumerate(q_owner):
                    for e in names:
                        s = float((gal[e] @ q[qi]).max())
                        (pos if e == owner else neg).append(s)
                t, thr = tpr_at_fpr(pos, neg)
                # Identifikation: wäre das eigene Exemplar unter allen Exemplaren dieses Labels Platz 1?
                top1 = np.mean([max(names, key=lambda e: float((gal[e] @ q[qi]).max())) == owner
                                for qi, owner in enumerate(q_owner)])
                res[lab][f"{size}_{gname}"] = {"auc": auc(pos, neg), "tpr@fpr5": t, "thr@fpr5": thr,
                                               "top1": round(float(top1), 3), "pos_mean": round(float(np.mean(pos)), 3),
                                               "neg_mean": round(float(np.mean(neg)), 3), "n_q": len(q_owner)}
        print(lab, {k: (v["auc"] if isinstance(v, dict) else v) for k, v in res[lab].items()}, flush=True)
    # Mittel über Labels
    summ = {}
    for size in SIZES + ["full"]:
        for g in ("plain", "aug"):
            vals = [r[f"{size}_{g}"]["auc"] for r in res.values() if f"{size}_{g}" in r]
            tprs = [r[f"{size}_{g}"]["tpr@fpr5"] for r in res.values() if f"{size}_{g}" in r]
            if vals:
                summ[f"{size}_{g}"] = {"auc_mean": round(float(np.mean(vals)), 3), "auc_min": min(vals),
                                       "tpr@fpr5_mean": round(float(np.mean(tprs)), 3)}
    return {"by_label": res, "summary": summ}


def eval_real_crops(emb: Embedder, d: str) -> dict:
    import cv2
    files = sorted(glob.glob(os.path.join(d, "**", "*.jpg"), recursive=True))
    groups: dict[str, list[str]] = defaultdict(list)
    for f in files:
        m = re.match(r"([0-9a-f-]{36})_(\d+)\.jpg$", os.path.basename(f))
        if m:
            groups[m.group(1)].append(f)
    groups = {k: v for k, v in groups.items() if len(v) >= 2}
    if len(groups) < 2:
        return {"note": "zu wenige Crops"}
    keys, imgs, sizes = [], [], []
    for k, fs in groups.items():
        for f in fs:
            im = cv2.imread(f)
            if im is None:
                continue
            keys.append(k)
            imgs.append(im)
            sizes.append(max(im.shape[:2]))
    E = emb(imgs)
    S = E @ E.T
    pos, neg = [], []
    pos_by_size = defaultdict(list)
    for i in range(len(keys)):
        for j in range(i + 1, len(keys)):
            if keys[i] == keys[j]:
                pos.append(float(S[i, j]))
                b = "<60" if min(sizes[i], sizes[j]) < 60 else ("60-120" if min(sizes[i], sizes[j]) <= 120 else ">120")
                pos_by_size[b].append(float(S[i, j]))
            else:
                neg.append(float(S[i, j]))
    return {"items": len(groups), "crops": len(keys), "crop_px_median": int(np.median(sizes)),
            "crop_px_min": int(min(sizes)), "auc_same_vs_other_item": auc(pos, neg),
            "same_item_sim": {"mean": round(float(np.mean(pos)), 3), "p05": round(float(np.quantile(pos, 0.05)), 3)},
            "other_item_sim": {"mean": round(float(np.mean(neg)), 3), "p95": round(float(np.quantile(neg, 0.95)), 3)},
            "same_item_sim_by_size": {k: round(float(np.mean(v)), 3) for k, v in pos_by_size.items()},
            "note": "Andere Gegenstände sind meist andere Labels – leichter als 'fremdes Exemplar gleicher Art'."}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sop", default="/data/sop")
    ap.add_argument("--fetch", type=int, default=60, help="Produkte je Kategorie laden (0 = nicht laden)")
    ap.add_argument("--crops", default="/media/inventory")
    ap.add_argument("--photos", default="")
    ap.add_argument("--models", default="vit_small_patch14_dinov2.lvd142m")
    ap.add_argument("--out", default="/out")
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    if a.fetch:
        fetch_sop(a.sop, a.fetch)
    import torch
    probe = VramProbe().start()
    rep: dict = {"gpu": probe.gpu_name(), "device_used_at_start_mib": mib(probe.used()),
                 "started": time.strftime("%Y-%m-%d %H:%M:%S")}
    for name in a.models.split(","):
        torch.cuda.empty_cache()
        torch.cuda.reset_peak_memory_stats()
        base = probe.used()
        probe.reset_peak()
        emb = Embedder(name)
        dummy = [np.random.randint(0, 255, (80, 60, 3), np.uint8) for _ in range(16)]
        r: dict = {"embed_b1": timeit(lambda: emb(dummy[:1]), n=30), "embed_b16": timeit(lambda: emb(dummy), n=10)}
        r["device_delta_mib"] = mib(probe.peak - base)
        r.update(torch_mem())
        print(name, json.dumps(r), flush=True)
        if os.path.isdir(a.sop):
            r["sop"] = eval_instances(emb, load_instances(a.sop))
            print(json.dumps(r["sop"]["summary"], indent=1), flush=True)
        if a.photos and os.path.isdir(a.photos):
            r["own_photos"] = eval_instances(emb, load_instances(a.photos))
        if os.path.isdir(a.crops):
            r["cam_crops"] = eval_real_crops(emb, a.crops)
            print(json.dumps(r["cam_crops"], indent=1), flush=True)
        r["device_delta_after_eval_mib"] = mib(probe.peak - base)
        rep[name] = r
        del emb
    probe.stop()
    with open(os.path.join(a.out, "itemtest.json"), "w") as f:
        json.dump(rep, f, indent=1, ensure_ascii=False)
    print("Bericht:", os.path.join(a.out, "itemtest.json"))


if __name__ == "__main__":
    main()
