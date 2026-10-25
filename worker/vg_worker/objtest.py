"""Machbarkeitstest Inventar: YOLOE-11 mit Text-Prompts an einer echten Kamera (ohne Backend/MQTT).

    python -m vg_worker.objtest [--src cam_3] [--models s,m] [--frames 5] [--interval 2]
        [--modes full640,full1280,tiles2x2_960,tiles3x2_640] [--conf 0.05] [--out /out]

Holt Standbilder in voller Auflösung über go2rtc (`/api/frame.jpeg?src=...`), lädt YOLOE-11 (Gewichte und
MobileCLIP-Textencoder einmalig nach /models/yoloe), setzt das Standard-Vokabular (50 Gegenstände) als Text-Prompts
und vergleicht Ganzbild- und Kachel-Varianten (eigene Kachelung + NMS). Gemessen werden ms/Durchlauf,
VRAM-Spitze (torch) und Treffer je Label mit Score. Annotierte Bilder und ein JSON-Bericht landen in --out.
Die Text-Embeddings werden auf der CPU berechnet und unter /models/yoloe/pe_*.pt gecacht (kein Textencoder im VRAM).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import time
from collections import defaultdict

import numpy as np

from . import settings

# Kopie des Standard-Vokabulars (backend/app/config.py: DEFAULT_INVENTORY_VOCABULARY), label -> Prompts
VOCAB: dict[str, list[str]] = {
    "keys": ["keys", "key ring"], "wallet": ["wallet", "purse"], "phone": ["cell phone", "smartphone"],
    "eyeglasses": ["eyeglasses"], "sunglasses": ["sunglasses"], "wristwatch": ["wristwatch"],
    "headphones": ["headphones", "earbuds case"], "backpack": ["backpack"], "handbag": ["handbag"],
    "umbrella": ["umbrella"], "medicine": ["pill bottle", "medicine box"], "jacket": ["jacket", "coat"],
    "remote": ["remote control"], "laptop": ["laptop"], "tablet": ["tablet computer"],
    "charger": ["charger", "charging cable"], "game_controller": ["game controller"], "tv": ["television"],
    "flashlight": ["flashlight"], "mug": ["mug", "cup"], "glass": ["drinking glass"], "bottle": ["bottle"],
    "plate": ["plate"], "bowl": ["bowl"], "fork": ["fork"], "spoon": ["spoon"], "knife": ["knife"],
    "pot": ["cooking pot"], "pan": ["frying pan"], "cutting_board": ["cutting board"],
    "kettle": ["electric kettle"], "table": ["table", "dining table"], "chair": ["chair"],
    "sofa": ["sofa", "couch"], "bed": ["bed"], "rug": ["rug", "carpet"], "pillow": ["pillow", "cushion"],
    "blanket": ["blanket"], "lamp": ["lamp"], "potted_plant": ["potted plant"], "book": ["book"],
    "pen": ["pen"], "scissors": ["scissors"], "tool": ["hammer", "screwdriver"],
    "parcel": ["cardboard box", "parcel"], "toy": ["toy", "stuffed animal"], "ball": ["ball"],
    "shoes": ["shoes"], "helmet": ["helmet"], "trash_can": ["trash can"],
}

MODES = {
    # name: [(tiles_x, tiles_y, imgsz), ...] – mehrere Teile = Kombination (Ganzbild + Kacheln), per NMS vereint
    "full640": [(1, 1, 640)],
    "full1280": [(1, 1, 1280)],
    "tiles2x2_960": [(2, 2, 960)],
    "tiles3x2_640": [(3, 2, 640)],
    "full1280+2x2_960": [(1, 1, 1280), (2, 2, 960)],
    "full960+3x2_640": [(1, 1, 960), (3, 2, 640)],
}
OVERLAP = 0.15


def grab_frame(src: str):
    import cv2
    import requests
    base = os.environ.get("VG_GO2RTC_URL", "http://go2rtc:1984").rstrip("/")
    r = requests.get(f"{base}/api/frame.jpeg", params={"src": src}, timeout=15)
    r.raise_for_status()
    img = cv2.imdecode(np.frombuffer(r.content, np.uint8), cv2.IMREAD_COLOR)
    if img is None:
        raise RuntimeError("Standbild nicht dekodierbar")
    return img


def tile_boxes(w: int, h: int, nx: int, ny: int, overlap: float = OVERLAP):
    """Kacheln (x1,y1,x2,y2) mit Überlappung, die das Bild vollständig abdecken."""
    if nx == 1 and ny == 1:
        return [(0, 0, w, h)]
    tw = int(np.ceil(w / (nx - (nx - 1) * overlap)))
    th = int(np.ceil(h / (ny - (ny - 1) * overlap)))
    out = []
    for iy in range(ny):
        for ix in range(nx):
            x1 = 0 if nx == 1 else round(ix * (w - tw) / (nx - 1))
            y1 = 0 if ny == 1 else round(iy * (h - th) / (ny - 1))
            out.append((x1, y1, min(w, x1 + tw), min(h, y1 + th)))
    return out


def load_yoloe(size: str, prompts: list[str]):
    import torch
    from ultralytics import YOLOE
    d = os.path.join(settings.MODELS_DIR, "yoloe")
    os.makedirs(d, exist_ok=True)
    name = f"yoloe-11{size}-seg.pt"
    cwd = os.getcwd()
    os.chdir(d)  # ultralytics lädt bloße Dateinamen (Gewichte, mobileclip_blt.ts) ins CWD
    try:
        t0 = time.time()
        model = YOLOE(name)
        key = hashlib.sha1(("\n".join(prompts)).encode()).hexdigest()[:12]
        pe_path = os.path.join(d, f"pe_{size}_{key}.pt")
        if os.path.exists(pe_path):
            pe = torch.load(pe_path, map_location="cpu")
            src = "Cache"
        else:
            model.model.cpu()
            pe = model.get_text_pe(prompts).cpu()   # Textencoder auf CPU, nicht gecacht
            torch.save(pe, pe_path)
            src = "berechnet"
        model.set_classes(prompts, pe)
        print(f"YOLOE-11{size}: geladen in {time.time() - t0:.1f}s, {len(prompts)} Prompts ({src})")
    finally:
        os.chdir(cwd)
    return model


def _predict_part(model, img, nx, ny, imgsz, conf, device, half, edge_filter):
    import torch
    h, w = img.shape[:2]
    tiles = tile_boxes(w, h, nx, ny)
    crops = [img[y1:y2, x1:x2] for (x1, y1, x2, y2) in tiles]
    res = model.predict(crops, imgsz=imgsz, conf=conf, iou=0.5, device=device, half=half,
                        verbose=False, retina_masks=False, max_det=300)
    bx, sc, cl = [], [], []
    for (x1, y1, x2, y2), r in zip(tiles, res):
        if r.boxes is None or len(r.boxes) == 0:
            continue
        b = r.boxes.xyxy.clone()
        if edge_filter and len(tiles) > 1:
            # an einer inneren Kachelkante abgeschnittene Boxen verwerfen (große Objekte liefert das Ganzbild)
            m = 4
            cut = torch.zeros(len(b), dtype=torch.bool, device=b.device)
            if x1 > 0:
                cut |= b[:, 0] <= m
            if y1 > 0:
                cut |= b[:, 1] <= m
            if x2 < w:
                cut |= b[:, 2] >= (x2 - x1) - m
            if y2 < h:
                cut |= b[:, 3] >= (y2 - y1) - m
            keep = ~cut
            b = b[keep]
            s_, c_ = r.boxes.conf[keep], r.boxes.cls[keep]
        else:
            s_, c_ = r.boxes.conf, r.boxes.cls
        b[:, [0, 2]] += x1
        b[:, [1, 3]] += y1
        bx.append(b)
        sc.append(s_)
        cl.append(c_)
    return bx, sc, cl


def run_mode(model, img, mode: str, conf: float, device: str, half: bool):
    """-> (boxes Nx4 in Bildkoordinaten, scores N, cls N, ms)."""
    import torch
    from torchvision.ops import batched_nms
    parts = MODES[mode]
    torch.cuda.synchronize()
    t0 = time.perf_counter()
    bx, sc, cl = [], [], []
    for nx, ny, imgsz in parts:
        b_, s_, c_ = _predict_part(model, img, nx, ny, imgsz, conf, device, half, edge_filter=len(parts) > 1)
        bx += b_
        sc += s_
        cl += c_
    if bx:
        b = torch.cat(bx)
        s = torch.cat(sc)
        c = torch.cat(cl)
        if len(parts) > 1 or parts[0][:2] != (1, 1):
            keep = batched_nms(b.float(), s.float(), c.long(), 0.5)
            b, s, c = b[keep], s[keep], c[keep]
        out = (b.cpu().numpy(), s.cpu().numpy(), c.cpu().numpy().astype(int))
    else:
        out = (np.zeros((0, 4)), np.zeros(0), np.zeros(0, int))
    torch.cuda.synchronize()
    ms = (time.perf_counter() - t0) * 1000
    return (*out, ms)


def annotate(img, boxes, scores, labels, path: str, thr: float):
    import cv2
    im = img.copy()
    s = max(1.0, img.shape[1] / 1600)
    for b, sc, lb in zip(boxes, scores, labels):
        if sc < thr:
            continue
        x1, y1, x2, y2 = map(int, b)
        col = (0, 200, 0) if sc >= 0.3 else (0, 200, 255)
        cv2.rectangle(im, (x1, y1), (x2, y2), col, max(2, int(2 * s)))
        cv2.putText(im, f"{lb} {sc:.2f}", (x1 + 3, max(20, y1 - 6)), cv2.FONT_HERSHEY_SIMPLEX, 0.7 * s, col,
                    max(1, int(2 * s)))
    cv2.imwrite(path, im, [cv2.IMWRITE_JPEG_QUALITY, 85])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", default="cam_3", help="go2rtc-Streamname")
    ap.add_argument("--models", default="s,m")
    ap.add_argument("--frames", type=int, default=5)
    ap.add_argument("--interval", type=float, default=2.0)
    ap.add_argument("--modes", default=",".join(MODES))
    ap.add_argument("--conf", type=float, default=0.05, help="Untergrenze; Auswertung zusätzlich bei 0.15/0.25/0.4")
    ap.add_argument("--repeat", type=int, default=3, help="Durchläufe je Bild und Modus (Zeitmessung)")
    ap.add_argument("--out", default="/tmp/objtest")
    ap.add_argument("--focus", default="", help="kommagetrennte Labels: alle Boxen ab --conf ausgeben")
    ap.add_argument("--extra", default="", help="zusätzliche Prompts 'label=prompt;label=prompt' (Prompt-Varianten testen)")
    ap.add_argument("--image", action="append", default=[], help="lokales Bild statt go2rtc (mehrfach)")
    a = ap.parse_args()
    logging.basicConfig(level=logging.WARNING)
    os.makedirs(a.out, exist_ok=True)

    import cv2
    import torch
    torch.set_num_threads(settings.TORCH_THREADS)
    device = settings.DEVICE if torch.cuda.is_available() else "cpu"
    half = device.startswith("cuda")
    print(f"torch {torch.__version__} device={torch.cuda.get_device_name(0) if half else 'cpu'}")

    for kv in [x for x in a.extra.split(";") if x.strip()]:
        lb, p = kv.split("=", 1)
        VOCAB.setdefault(lb.strip(), []).append(p.strip())
    focus = {x.strip() for x in a.focus.split(",") if x.strip()}
    prompts, p2l = [], []
    for lb, ps in VOCAB.items():
        for p in ps:
            prompts.append(p)
            p2l.append(lb)

    # Bilder holen
    frames = []
    for p in a.image:
        frames.append(cv2.imread(p))
    for i in range(a.frames if not a.image else 0):
        if i:
            time.sleep(a.interval)
        frames.append(grab_frame(a.src))
    h, w = frames[0].shape[:2]
    print(f"{len(frames)} Bilder {w}x{h} von {a.src if not a.image else 'Datei'}")
    cv2.imwrite(os.path.join(a.out, "frame0.jpg"), frames[0], [cv2.IMWRITE_JPEG_QUALITY, 90])

    report = {"source": a.src, "width": w, "height": h, "frames": len(frames), "prompts": len(prompts),
              "results": []}
    modes = [m for m in a.modes.split(",") if m]
    for size in [s.strip() for s in a.models.split(",") if s.strip()]:
        model = load_yoloe(size, prompts)
        # Warm-up je Modus (CUDA-Kernel, Autotune)
        for m in modes:
            run_mode(model, frames[0], m, a.conf, device, half)
        for m in modes:
            torch.cuda.empty_cache()
            torch.cuda.reset_peak_memory_stats()
            times = []
            per_label = defaultdict(list)       # label -> [max score je Bild]
            boxes_by_label = defaultdict(list)  # label -> [(frame, score, bbox normiert)]
            first = None
            for fi, img in enumerate(frames):
                for _ in range(a.repeat):
                    b, s, c, ms = run_mode(model, img, m, a.conf, device, half)
                    times.append(ms)
                labels = [p2l[i] for i in c]
                if focus and fi == 0:
                    for bb, ss, ci in zip(b, s, c):
                        if p2l[ci] in focus:
                            print(f"   [fokus {m}] {p2l[ci]:10s} '{prompts[ci]}' {ss:.3f} "
                                  f"[{bb[0] / w:.3f}, {bb[1] / h:.3f}, {bb[2] / w:.3f}, {bb[3] / h:.3f}]")
                if fi == 0:
                    first = (b, s, labels)
                best = {}
                for bb, ss, lb in zip(b, s, labels):
                    if ss > best.get(lb, (0,))[0]:
                        best[lb] = (float(ss), [round(float(bb[0]) / w, 3), round(float(bb[1]) / h, 3),
                                                round(float(bb[2]) / w, 3), round(float(bb[3]) / h, 3)])
                for lb, (ss, nb) in best.items():
                    per_label[lb].append(ss)
                    boxes_by_label[lb].append((fi, round(ss, 3), nb))
            peak = torch.cuda.max_memory_allocated() / 2**20
            res = {
                "model": f"yoloe-11{size}-seg", "mode": m, "parts": MODES[m],
                "ms_median": round(float(np.median(times)), 1), "ms_p90": round(float(np.percentile(times, 90)), 1),
                "vram_peak_mib": round(peak, 0),
                "labels": {lb: {"frames": len(v), "max": round(max(v), 3), "mean": round(float(np.mean(v)), 3),
                                "boxes": boxes_by_label[lb][:3]}
                           for lb, v in sorted(per_label.items(), key=lambda kv: -max(kv[1]))},
            }
            report["results"].append(res)
            annotate(frames[0], *first, os.path.join(a.out, f"yoloe11{size}_{m}.jpg"), thr=0.15)
            print(f"\n== YOLOE-11{size} {m}: {res['ms_median']} ms (p90 {res['ms_p90']}), "
                  f"VRAM-Spitze {res['vram_peak_mib']:.0f} MiB")
            for lb, st in res["labels"].items():
                if st["max"] >= 0.1:
                    print(f"   {lb:16s} in {st['frames']}/{len(frames)} Bildern  max {st['max']:.2f}  "
                          f"mittel {st['mean']:.2f}  bbox {st['boxes'][0][2]}")
        del model
        torch.cuda.empty_cache()

    try:
        from .gpu import gpu_info
        report["gpu_after"] = gpu_info()
    except Exception as e:  # noqa: BLE001
        report["gpu_after"] = str(e)
    with open(os.path.join(a.out, "report.json"), "w") as f:
        json.dump(report, f, indent=1, ensure_ascii=False)
    print(f"\nBericht: {os.path.join(a.out, 'report.json')}")


if __name__ == "__main__":
    main()
