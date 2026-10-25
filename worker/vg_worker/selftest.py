"""Standalone self-test: run the full pipeline on one or more streams without backend/MQTT.

    python -m vg_worker.selftest rtsp://testcam:8554/cam1 [rtsp://testcam:8554/cam2 ...]
        [--seconds 20] [--fps 5] [--no-face] [--no-plate] [--no-pet] [--clip] [--replicate N]
        [--inventory] [--inventory-interval 3] [--vocab datei.json]

--inventory schaltet die Inventar-Pipeline (Gegenstände) für die erste Kamera ein, z. B.
    python -m vg_worker.selftest rtsp://go2rtc:8554/cam_3 --inventory --seconds 60

Identification calls are answered by an in-memory fake (cosine matching), messages are counted.
"""
from __future__ import annotations

import argparse
import logging
import os
import threading
import time
from collections import Counter

import numpy as np

from . import settings


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("urls", nargs="+")
    ap.add_argument("--seconds", type=float, default=20)
    ap.add_argument("--fps", type=float, default=5)
    ap.add_argument("--replicate", type=int, default=1, help="open every URL N times (load test)")
    ap.add_argument("--no-face", action="store_true")
    ap.add_argument("--no-plate", action="store_true")
    ap.add_argument("--no-pet", action="store_true")
    ap.add_argument("--clip", action="store_true", help="also test snapshot + clip export")
    ap.add_argument("--verbose", action="store_true")
    ap.add_argument("--inventory", action="store_true", help="inventory pipeline on the first camera")
    ap.add_argument("--inventory-interval", type=float, default=3.0)
    ap.add_argument("--no-tiling", action="store_true")
    ap.add_argument("--health", action="store_true",
                    help="Kamera-Gesundheit (C5) mitprüfen; Referenzbilder dann nur in einem temporären Ordner")
    ap.add_argument("--vocab", help="JSON file: list of {label, name_de, prompts, min_score}")
    a = ap.parse_args()
    logging.basicConfig(level=logging.DEBUG if a.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    import torch
    print(f"torch {torch.__version__}  cuda available={torch.cuda.is_available()}  "
          f"device={torch.cuda.get_device_name(0) if torch.cuda.is_available() else '-'}")
    import onnxruntime as ort
    print(f"onnxruntime {ort.__version__} providers={ort.get_available_providers()}")

    from .gpu import gpu_info
    from .pipeline import Pipeline
    # Kamera-Gesundheit: Selbsttest-Kameras haben IDs 1..N wie echte Kameras – Referenzbilder dürfen nie im
    # gemeinsamen Medienordner landen (sie würden health/cam<N>_*.jpg echter Kameras überschreiben)
    if a.health:
        import tempfile
        settings.MEDIA_DIR = tempfile.mkdtemp(prefix="vg-selftest-media-")
        print(f"health checks on, media (references) in {settings.MEDIA_DIR}")
    else:
        settings.HEALTH_ENABLED = False

    counts: Counter = Counter()
    labels_started: Counter = Counter()
    cmd_results: list[dict] = []
    max_objects = Counter()
    inventory_last: dict[int, dict] = {}
    inventory_changes: Counter = Counter()
    lock = threading.Lock()

    def publish(topic: str, payload: dict, qos: int = 0, retain: bool = False):
        kind = topic.split("/")[2]
        with lock:
            counts[kind] += 1
            if kind == "track":
                counts[f"track_{payload['type']}"] += 1
                if payload["type"] == "start":
                    labels_started[payload["label"]] += 1
                if payload["type"] == "update" and payload.get("identity"):
                    counts["track_update_with_identity"] += 1
            elif kind == "detections":
                max_objects[payload["camera_id"]] = max(max_objects[payload["camera_id"]], len(payload["objects"]))
            elif kind == "cmd_result":
                cmd_results.append(payload)
            elif kind == "inventory":
                if payload.get("stopped"):
                    counts["inventory_stopped"] += 1
                    return
                if not payload.get("snapshot_path") and payload["camera_id"] in inventory_last:
                    payload = dict(payload, snapshot_path=inventory_last[payload["camera_id"]].get("snapshot_path"))
                inventory_last[payload["camera_id"]] = payload
                for ch in payload.get("changes") or []:
                    inventory_changes[ch["type"]] += 1

    # fake backend identify: cosine matching, new identity if below threshold
    db: dict[str, list[tuple[int, np.ndarray | str]]] = {"face": [], "pet": [], "plate": []}
    idc = [0]
    id_calls: Counter = Counter()

    def identify(body: dict):
        kind = body["kind"]
        with lock:
            id_calls[kind] += 1
            assert os.path.exists(os.path.join(settings.MEDIA_DIR, body["crop_path"])), body["crop_path"]
            if kind == "plate":
                for iid, t in db[kind]:
                    if t == body["plate_text"]:
                        return {"identity_id": iid, "display_name": f"Kennzeichen {t}", "name": None, "score": 1.0, "is_new": False}
                idc[0] += 1
                db[kind].append((idc[0], body["plate_text"]))
                return {"identity_id": idc[0], "display_name": f"Kennzeichen {body['plate_text']}", "name": None, "score": 1.0, "is_new": True}
            emb = np.asarray(body["embedding"], dtype=np.float32)
            assert abs(float(np.linalg.norm(emb)) - 1.0) < 1e-3, "embedding not normalized"
            thr = 0.45 if kind == "face" else 0.8
            best = (None, -1.0)
            for iid, e in db[kind]:
                s = float(np.dot(e, emb))
                if s > best[1]:
                    best = (iid, s)
            if best[0] is not None and best[1] >= thr:
                return {"identity_id": best[0], "display_name": f"{kind} #{best[0]}", "name": None, "score": best[1], "is_new": False}
            idc[0] += 1
            db[kind].append((idc[0], emb))
            return {"identity_id": idc[0], "display_name": f"{kind} #{idc[0]}", "name": None, "score": best[1], "is_new": True}

    p = Pipeline(publish, identify)
    t0 = time.time()
    p.load_detector()
    print(f"detector loaded in {time.time() - t0:.1f}s: {p.detector_status}")
    cams = []
    cid = 0
    for _ in range(a.replicate):
        for u in a.urls:
            cid += 1
            cams.append({"id": cid, "name": f"test{cid}", "enabled": True, "detect_url_rtsp": u, "main_rtsp": u,
                         "detect_fps": a.fps, "labels": [], "min_score": 0.5,
                         "face_recognition": not a.no_face, "plate_recognition": not a.no_plate,
                         "pet_recognition": not a.no_pet,
                         "zones": [{"id": "left", "name": "links", "polygon": [[0, 0], [0.5, 0], [0.5, 1], [0, 1]]}]})
    if a.inventory:
        if a.vocab:
            import json
            with open(a.vocab, encoding="utf-8") as fh:
                vocab = json.load(fh)
        else:
            vocab = [{"label": lab, "name_de": de, "prompts": pr, "min_score": None} for lab, de, pr in (
                ("lamp", "Lampe", ["lamp"]), ("book", "Buch", ["book"]), ("remote", "Fernbedienung", ["remote control"]),
                ("table", "Tisch", ["table", "dining table"]), ("mug", "Tasse", ["mug", "cup"]),
                ("phone", "Handy", ["cell phone", "smartphone"]), ("keys", "Schlüssel", ["keys", "key ring"]),
                ("eyeglasses", "Brille", ["eyeglasses"]), ("bottle", "Flasche", ["bottle"]),
                ("plant", "Pflanze", ["potted plant"]))]
        cams[0].update({"inventory_enabled": True, "inventory_interval_s": a.inventory_interval,
                        "inventory_min_score": 0.35, "inventory_tiling": not a.no_tiling, "inventory_vocab": vocab})
    p.apply_config(cams)
    p.start()
    # give identification models time to load (they load lazily in background)
    t_start = time.time()
    gpu_peak = 0
    while time.time() - t_start < a.seconds:
        time.sleep(5)
        g = gpu_info()
        gpu_peak = max(gpu_peak, g.get("mem_used") or 0)
        st = p.statuses()
        cam_s = ", ".join(f"{k}: {v['state']} {v['fps']}fps {v['inference_ms']}ms" for k, v in st.items())
        print(f"[{time.time() - t_start:5.1f}s] perf={p.perf()} gpu={g} cams={{{cam_s}}}")
        print(f"         models={p.models_status()}")
        print(f"         msgs={dict(counts)} id_calls={dict(id_calls)} id_stats={p.identifier.stats}")
        if a.inventory:
            with lock:
                inv = dict(inventory_last)
            for cid_, m in inv.items():
                its = ", ".join(f"{i['label']}:{i['state'][0]}{i['score']:.2f}" for i in m["items"])
                print(f"         inventory cam {cid_}: {m['width']}x{m['height']} every {m['interval_s']}s "
                      f"model={m['model']} [{its}] changes={dict(inventory_changes)}")

    if a.clip:
        first = cams[0]["id"]
        day = time.strftime("%Y-%m-%d")
        p.command(first, {"action": "snapshot", "request_id": "s1", "path": f"snapshots/{day}/selftest.jpg"})
        p.command(first, {"action": "clip", "request_id": "c1", "path": f"clips/{day}/selftest.mp4", "pre_s": 5, "post_s": 3})
        time.sleep(3 + settings.BUFFER_SEGMENT_S * 2 + 4)
        print("cmd_results:", cmd_results)
        clip = os.path.join(settings.MEDIA_DIR, f"clips/{day}/selftest.mp4")
        if os.path.exists(clip):
            import subprocess
            out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration:stream=codec_name,width,height",
                                  "-of", "compact", clip], capture_output=True, text=True).stdout
            print(f"clip {clip}: {os.path.getsize(clip)} bytes {out.strip()}")
        for c in cams:
            cam = p.cameras.get(c["id"]) if hasattr(p, "cameras") else None
            d = cam.recorder.dir if cam is not None and cam.recorder else ""
            if os.path.isdir(d):
                fs = sorted(os.listdir(d))
                print(f"ring buffer cam_{c['id']}: {len(fs)} files, {sum(os.path.getsize(os.path.join(d, f)) for f in fs if os.path.isfile(os.path.join(d, f)))/1e6:.1f} MB")

    p.stop()
    elapsed = time.time() - t_start
    print("\n===== SELFTEST SUMMARY =====")
    print(f"duration          : {elapsed:.1f}s, cameras: {len(cams)}")
    print(f"frames analysed   : {p.frames_total}  ({p.frames_total / elapsed:.1f} fps total)")
    h = list(p.batch_hist)
    if h:
        print(f"batch inference   : avg {np.mean([m for *_, m in h]):.1f} ms, avg batch {np.mean([b for _, b, _ in h]):.2f}, "
              f"per frame {np.sum([m for *_, m in h]) / max(1, np.sum([b for _, b, _ in h])):.1f} ms")
    print(f"GPU               : {gpu_info().get('name')}  peak mem {gpu_peak} MiB  (torch device: {p.detector.device}, fp16={p.detector.half})")
    print(f"tracks started    : {counts['track_start']} {dict(labels_started)}; ended {counts['track_end']}; updates {counts['track_update']}")
    print(f"max objects/frame : {dict(max_objects)}")
    print(f"faces found       : {p.identifier.stats['face_found']}, identify calls {dict(id_calls)}, identities {[(k, len(v)) for k, v in db.items()]}")
    print(f"identify stats    : {p.identifier.stats}")
    print(f"models            : {p.models_status()}")
    if a.inventory:
        print(f"inventory perf    : {p.inventory.perf()}  changes {dict(inventory_changes)}")
        for cid_, m in inventory_last.items():
            for i in m["items"]:
                print(f"  cam {cid_} {i['state']:9s} {i['label']:12s} {i['score']:.2f} bbox={i['bbox']} hits={i['hits']} "
                      f"crop={i['crop_path']}")
            print(f"  snapshot (letzte Änderung): {m.get('snapshot_path')}")
    os._exit(0)


if __name__ == "__main__":
    main()
