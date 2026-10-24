"""Standalone self-test: run the full pipeline on one or more streams without backend/MQTT.

    python -m vg_worker.selftest rtsp://testcam:8554/cam1 [rtsp://testcam:8554/cam2 ...]
        [--seconds 20] [--fps 5] [--no-face] [--no-plate] [--no-pet] [--clip] [--replicate N]

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

    counts: Counter = Counter()
    labels_started: Counter = Counter()
    cmd_results: list[dict] = []
    max_objects = Counter()
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
    os._exit(0)


if __name__ == "__main__":
    main()
