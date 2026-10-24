"""Erzeugt den Bild-/Fragensatz für `vlm/bench.py` aus den frei verfügbaren Testvideos (keine echten Kamerabilder).

    python -m vg_worker.tests_feasibility.vlmset --videos /videos/raw --out /data/vlmset

Ausgabe: JPEGs + `questions.json` = [{image, question, truth: "yes"|"no", source}]. Die Wahrheit kommt aus
Frame-Labels (UR Fall), der Aktion (UTD-MHAD) bzw. dem Produktiv-Detektor (YOLO11s, conf ≥ 0,6 für „ja“,
kein Treffer ≥ 0,2 für „nein“). Zusätzlich synthetische Licht-Fragen (Bild stark abgedunkelt = Licht aus).
Vor der Auswertung von Hand sichten (die Bilder sind klein).
"""
from __future__ import annotations

import argparse
import csv
import glob
import json
import os
import random

import cv2
import numpy as np

from .common import grab_frame, read_video_frames


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--videos", default="/videos/raw")
    ap.add_argument("--rtsp", default="rtsp://testcam:8554/cam1,rtsp://testcam:8554/cam2")
    ap.add_argument("--detector", default="/models/yolo11s.pt")
    ap.add_argument("--out", default="/data/vlmset")
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    rnd = random.Random(11)
    from ultralytics import YOLO
    det = YOLO(a.detector)
    qs = []

    def save(img, name):
        cv2.imwrite(os.path.join(a.out, name), img, [cv2.IMWRITE_JPEG_QUALITY, 92])
        return name

    def detect(img):
        r = det.predict(img, imgsz=640, conf=0.2, verbose=False)[0]
        out = {}
        for c, s in zip(r.boxes.cls.cpu().numpy().astype(int), r.boxes.conf.cpu().numpy()):
            out[det.names[c]] = max(out.get(det.names[c], 0), float(s))
        return out

    # UR Fall: liegt / steht
    ur = os.path.join(a.videos, "urfall")
    lab: dict = {}
    for fn in ("urfall-cam0-falls.csv",):
        for row in csv.reader(open(os.path.join(ur, fn))):
            lab.setdefault(row[0], {})[int(row[1])] = int(row[2])
    for seq in ("fall-03", "fall-08", "fall-15", "fall-22"):
        fr = [(i, f) for i, _t, f in read_video_frames(os.path.join(ur, f"{seq}-cam0.mp4"), crop=(320, 0, 640, 240))]
        lying = [f for i, f in fr if lab[seq].get(i + 1) == 1]
        stand = [f for i, f in fr if lab[seq].get(i + 1) == -1]
        if lying:
            n = save(cv2.resize(lying[len(lying) // 2], (640, 480)), f"{seq}_lying.jpg")
            qs.append({"image": n, "question": "Is a person lying on the floor?", "truth": "yes", "source": "urfall"})
        if stand:
            n = save(cv2.resize(stand[len(stand) // 2], (640, 480)), f"{seq}_stand.jpg")
            qs.append({"image": n, "question": "Is a person lying on the floor?", "truth": "no", "source": "urfall"})
    # UTD-MHAD: Arme verschränkt / Hand gehoben
    utd = os.path.join(a.videos, "utd")
    for act, q, truth in (("a6_s2", "Are the person's arms crossed over the chest?", "yes"),
                          ("a23_s2", "Are the person's arms crossed over the chest?", "no"),
                          ("a3_s3", "Is the person raising a hand above the shoulder?", "yes"),
                          ("a24_s3", "Is the person raising a hand above the shoulder?", "no")):
        p = os.path.join(utd, f"{act}_t1_color.avi")
        if os.path.exists(p):
            fr = [f for _i, _t, f in read_video_frames(p)]
            n = save(fr[len(fr) // 2], f"utd_{act}.jpg")
            qs.append({"image": n, "question": q, "truth": truth, "source": "utd"})
    # Regal / Flaschen
    for name, q, truth in (("store-aisle-detection.mp4", "Is this a store shelf with products?", "yes"),
                           ("bottle-detection.mp4", "Are there bottles in the image?", "yes")):
        p = os.path.join(a.videos, name)
        if os.path.exists(p):
            fr = [f for _i, _t, f in read_video_frames(p, every=25)]
            n = save(fr[len(fr) // 2], name.replace(".mp4", ".jpg"))
            qs.append({"image": n, "question": q, "truth": truth, "source": "intel"})
    # testcam: Person ja/nein, Auto ja/nein, Licht an/aus (synthetisch)
    urls = [u for u in a.rtsp.split(",") if u]
    want = {"person_yes": None, "person_no": None, "car_yes": None, "car_no": None}
    for u in urls:
        for _ in range(40):
            try:
                f = grab_frame(u)
            except Exception:  # noqa: BLE001
                break
            d = detect(f)
            if "cam1" in u:
                if d.get("person", 0) >= 0.6 and want["person_yes"] is None:
                    want["person_yes"] = f
                if "person" not in d and want["person_no"] is None:
                    want["person_no"] = f
            else:
                if d.get("car", 0) >= 0.6 and want["car_yes"] is None:
                    want["car_yes"] = f
                if "car" not in d and "truck" not in d and want["car_no"] is None:
                    want["car_no"] = f
            if all(v is not None for k, v in want.items() if (k.startswith("person") == ("cam1" in u))):
                break
    for k, f in want.items():
        if f is None:
            continue
        what, truth = k.split("_")
        n = save(f, f"testcam_{k}.jpg")
        q = "Is there a person in the image?" if what == "person" else "Is there a car in the image?"
        qs.append({"image": n, "question": q, "truth": truth, "source": "testcam"})
    base = want.get("person_yes") if want.get("person_yes") is not None else want.get("person_no")
    if base is not None:
        n = save(base, "light_on.jpg")
        qs.append({"image": n, "question": "Is the light on in this room?", "truth": "yes", "source": "synthetic"})
        dark = np.clip(base.astype(np.float32) * 0.06, 0, 255).astype(np.uint8)
        n = save(dark, "light_off.jpg")
        qs.append({"image": n, "question": "Is the light on in this room?", "truth": "no", "source": "synthetic"})
    # Rotes Rechteck (Farbe/Ort)
    canvas = np.full((480, 640, 3), 235, np.uint8)
    cv2.rectangle(canvas, (380, 260), (520, 400), (30, 30, 220), -1)
    n = save(canvas, "red_box.jpg")
    qs.append({"image": n, "question": "Is there a red box in the image?", "truth": "yes", "source": "synthetic"})
    qs.append({"image": n, "question": "Is there a blue box in the image?", "truth": "no", "source": "synthetic"})
    json.dump(qs, open(os.path.join(a.out, "questions.json"), "w"), indent=1)
    print(len(qs), "Fragen ->", a.out)


if __name__ == "__main__":
    main()
