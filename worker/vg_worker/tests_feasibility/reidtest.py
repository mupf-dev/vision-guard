"""C2 Machbarkeitstest: Personen-Wiedererkennung (OSNet x0_25 MSMT17 als ONNX) – Qualität, Schwelle, Zeit, VRAM.

Daten: Market-1501 (Test-Teil: `query/`, `bounding_box_test/`; 6 Kameras, nicht im Training von OSNet-MSMT17 →
fairer Domänenwechsel). Nur lokal zur Messung, nicht im Repo.

Messungen:
1. Klassisch: Rank-1 und mAP (Query gegen Galerie, gleiche Kamera derselben Person ausgeschlossen).
2. Paare: Kosinus gleiche Person/andere Kamera vs. andere Person → TPR bei FPR 1 % / 5 %.
3. **Haushalt-Simulation** (die eigentliche Frage für C2): Gruppen aus k Personen; je Person eine Galerie aus bis zu
   5 Bildern einer Kamera (wie die RAM-Galerie im Backend), Sonde = Bild derselben Person aus einer anderen Kamera
   bzw. Bild einer Person außerhalb der Gruppe („Fremder“). Bei Schwelle t: richtig verknüpft / falsch verknüpft /
   als neu angelegt.
4. Zeit je Ausschnitt (CPU und CUDA), VRAM (NVML) – CUDA nur unter `flock ~/vg-gpu.lock`.
Optional `--dino`: dasselbe mit dem DINOv2 der Tiererkennung (Alternative ohne neues Modell).

    python -m vg_worker.tests_feasibility.reidtest --market /data/Market-1501-v15.09.15 [--device cpu|cuda] [--dino]
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import random
import re
import time

import cv2
import numpy as np

_RX = re.compile(r"^(-?\d+)_c(\d)s\d_\d+_\d+\.jpg$")


def load_split(root: str, sub: str, limit_ids: set[int] | None = None):
    items = []
    for p in sorted(glob.glob(os.path.join(root, sub, "*.jpg"))):
        m = _RX.match(os.path.basename(p))
        if not m:
            continue
        pid, cam = int(m.group(1)), int(m.group(2))
        if pid <= 0:                                  # -1 = Störbild, 0 = Hintergrund
            continue
        if limit_ids is not None and pid not in limit_ids:
            continue
        items.append((p, pid, cam))
    return items


def embed_all(fn, paths: list[str], bs: int = 32) -> tuple[np.ndarray, float]:
    out, t_total = [], 0.0
    for i in range(0, len(paths), bs):
        ims = [cv2.imread(p) for p in paths[i:i + bs]]
        t0 = time.perf_counter()
        out.append(fn(ims))
        t_total += time.perf_counter() - t0
    return np.concatenate(out), t_total / max(1, len(paths)) * 1000.0


def rank1_map(q, qp, qc, g, gp, gc):
    sims = q @ g.T
    r1, aps = 0, []
    for i in range(len(q)):
        valid = ~((gp == qp[i]) & (gc == qc[i]))
        s, m = sims[i][valid], (gp[valid] == qp[i])
        if not m.any():
            continue
        order = np.argsort(-s)
        mm = m[order]
        r1 += int(mm[0])
        hits = np.cumsum(mm)
        prec = hits[mm] / (np.nonzero(mm)[0] + 1)
        aps.append(prec.mean())
    return r1 / len(aps), float(np.mean(aps))


def pair_stats(e, pid, cam, n=200000, seed=1):
    rng = np.random.default_rng(seed)
    N = len(e)
    a, b = rng.integers(0, N, n), rng.integers(0, N, n)
    same = (pid[a] == pid[b]) & (cam[a] != cam[b])
    diff = pid[a] != pid[b]
    s = np.einsum("ij,ij->i", e[a], e[b])
    pos, neg = s[same], s[diff]
    out = {"pos_n": int(len(pos)), "neg_n": int(len(neg)),
           "pos_median": round(float(np.median(pos)), 3), "neg_median": round(float(np.median(neg)), 3)}
    for fpr in (0.01, 0.05):
        t = float(np.quantile(neg, 1 - fpr))
        out[f"thr_fpr{int(fpr * 100)}"] = round(t, 3)
        out[f"tpr_fpr{int(fpr * 100)}"] = round(float((pos >= t).mean()), 3)
    return out


def household(e, pid, cam, k=4, groups=400, gal_n=5, thresholds=(0.5, 0.55, 0.6, 0.65, 0.7, 0.75, 0.8), seed=2):
    """Gruppen aus k Personen. Galerie je Person: ≤ gal_n Bilder einer Kamera, Sonden: andere Kamera (bekannt) bzw.
    fremde Person. Ergebnis je Schwelle: Anteile richtig/falsch verknüpft/neu."""
    rng = random.Random(seed)
    by_pid: dict[int, dict[int, list[int]]] = {}
    for i, (p, c) in enumerate(zip(pid, cam)):
        by_pid.setdefault(int(p), {}).setdefault(int(c), []).append(i)
    multi = [p for p, cs in by_pid.items() if len(cs) >= 2]
    res = {t: {"known_ok": 0, "known_wrong": 0, "known_new": 0, "stranger_wrong": 0, "stranger_new": 0} for t in thresholds}
    nk = ns = 0
    for _ in range(groups):
        grp = rng.sample(multi, k + 1)
        members, stranger = grp[:k], grp[k]
        gal, probes = {}, []
        for p in members:
            cams = list(by_pid[p])
            gc = rng.choice(cams)
            gal[p] = e[by_pid[p][gc][:gal_n]]
            oc = rng.choice([c for c in cams if c != gc])
            probes.append((p, rng.choice(by_pid[p][oc])))
        sc = rng.choice(list(by_pid[stranger]))
        probes.append((None, rng.choice(by_pid[stranger][sc])))
        for truth, idx in probes:
            best_p, best_s = None, -1.0
            for p, G in gal.items():
                s = float((G @ e[idx]).max())
                if s > best_s:
                    best_p, best_s = p, s
            if truth is None:
                ns += 1
            else:
                nk += 1
            for t in thresholds:
                r = res[t]
                if truth is None:
                    r["stranger_wrong" if best_s >= t else "stranger_new"] += 1
                elif best_s < t:
                    r["known_new"] += 1
                else:
                    r["known_ok" if best_p == truth else "known_wrong"] += 1
    out = {}
    for t, r in res.items():
        out[str(t)] = {"richtig_verknuepft": round(r["known_ok"] / nk, 3),
                       "falsch_verknuepft": round(r["known_wrong"] / nk, 3),
                       "bekannt_als_neu": round(r["known_new"] / nk, 3),
                       "fremder_verknuepft": round(r["stranger_wrong"] / ns, 3)}
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--market", required=True)
    ap.add_argument("--device", default="cpu")
    ap.add_argument("--ids", type=int, default=0, help="nur die ersten n Personen (0 = alle 750)")
    ap.add_argument("--dino", action="store_true")
    ap.add_argument("--no-flip", action="store_true")
    ap.add_argument("--out", default="/out/reidtest.json")
    a = ap.parse_args()

    from vg_worker import reid as R
    ids = None
    q_all = load_split(a.market, "query")
    if a.ids:
        ids = set(sorted({p for _, p, _ in q_all})[: a.ids])
    q = load_split(a.market, "query", ids)
    g = load_split(a.market, "bounding_box_test", ids)
    print(f"query {len(q)}  gallery {len(g)}  ids {len({p for _, p, _ in q})}")
    results = {"query": len(q), "gallery": len(g)}

    probe = None
    if a.device.startswith("cuda"):
        try:
            from vg_worker.tests_feasibility.common import VramProbe
            probe = VramProbe()
            base = probe.used()
            probe.start()
        except Exception as e:  # noqa: BLE001
            print("VRAM probe n/a:", e)
    t0 = time.time()
    model = R.ReidModel(a.device, path=R.ensure_weights())
    results["load_s"] = round(time.time() - t0, 2)
    results["device"] = model.device
    variants = [("osnet_flip", lambda ims: model.embed(ims, flip=True))]
    if not a.no_flip:
        variants.append(("osnet", lambda ims: model.embed(ims, flip=False)))
    # Zeit je Einzelausschnitt (Batch 1, wie bei wenigen Personen im Betrieb)
    im = cv2.imread(q[0][0])
    for f in (False, True):
        for _ in range(5):
            model.embed([im], flip=f)
        t1 = time.perf_counter()
        for _ in range(50):
            model.embed([im], flip=f)
        results[f"ms_batch1{'_flip' if f else ''}"] = round((time.perf_counter() - t1) / 50 * 1000, 2)
    if a.dino:
        import timm
        import torch
        dev = "cuda" if a.device.startswith("cuda") and torch.cuda.is_available() else "cpu"
        m = timm.create_model("vit_small_patch14_dinov2.lvd142m", pretrained=True, num_classes=0, img_size=224).eval().to(dev)
        if dev == "cuda":
            m.half()
        mean = torch.tensor([0.485, 0.456, 0.406], device=dev).view(1, 3, 1, 1)
        std = torch.tensor([0.229, 0.224, 0.225], device=dev).view(1, 3, 1, 1)

        def dino(ims):
            xs = []
            for im in ims:
                h, w = im.shape[:2]
                s = max(h, w)
                c = np.zeros((s, s, 3), np.uint8)
                c[(s - h) // 2:(s - h) // 2 + h, (s - w) // 2:(s - w) // 2 + w] = im
                xs.append(cv2.resize(c, (224, 224))[:, :, ::-1])
            with torch.inference_mode():
                t = torch.from_numpy(np.stack(xs)).to(dev).permute(0, 3, 1, 2).float() / 255.0
                t = (t - mean) / std
                if dev == "cuda":
                    t = t.half()
                f = torch.nn.functional.normalize(m(t).float(), dim=1)
            return f.cpu().numpy()
        variants.append(("dinov2_pet", dino))

    for name, fn in variants:
        qe, ms_q = embed_all(fn, [p for p, _, _ in q])
        ge, ms_g = embed_all(fn, [p for p, _, _ in g])
        qp = np.array([p for _, p, _ in q]); qc = np.array([c for _, _, c in q])
        gp = np.array([p for _, p, _ in g]); gc = np.array([c for _, _, c in g])
        r1, mAP = rank1_map(qe, qp, qc, ge, gp, gc)
        allE = np.concatenate([qe, ge]); allP = np.concatenate([qp, gp]); allC = np.concatenate([qc, gc])
        res = {"rank1": round(r1, 4), "mAP": round(mAP, 4), "ms_per_crop_batch32": round((ms_q + ms_g) / 2, 2),
               "pairs": pair_stats(allE, allP, allC),
               "household_k4": household(allE, allP, allC, k=4),
               "household_k8": household(allE, allP, allC, k=8, seed=3)}
        results[name] = res
        print(name, json.dumps(res, indent=1))
    if probe is not None:
        probe.stop()
        results["vram_device_base_mb"] = round(base / 2**20)
        results["vram_device_peak_mb"] = round(probe.peak / 2**20)
        results["vram_delta_mb"] = round((probe.peak - base) / 2**20)
    print(json.dumps({k: v for k, v in results.items() if not isinstance(v, dict)}, indent=1))
    os.makedirs(os.path.dirname(a.out), exist_ok=True)
    with open(a.out, "w") as f:
        json.dump(results, f, indent=1)


if __name__ == "__main__":
    main()
