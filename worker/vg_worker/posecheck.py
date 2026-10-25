"""Messwerkzeug C1/D1: Videodatei offline durch Detektor + Tracker + Pose-Stufe schicken (ohne Backend/MQTT).

    python -m vg_worker.posecheck /videos/sturz.mp4 [--fps 5] [--dwell 5] [--grace 30] [--gestures] [--loops 1]

Läuft synchron Bild für Bild in Videozeit (nicht Echtzeit) mit denselben Klassen wie der Worker (`Detector`,
`CameraTracker`, `PoseEngine._apply`). Ausgabe:
- **Standbild-Abschnitte** des Videos (≥ 5 s fast unverändert) – in `testcam` `sturz.mp4` sind das genau die
  10-s-„liegt danach“-Phasen nach jedem Sturz (Wahrheit für „Sturz erkannt“);
- **Liegen-Episoden** je Track (Haltung `lying` aus dem Worker, inkl. Nachlauf) und ob sie eine Regel mit
  `min_dwell_s = --dwell` ausgelöst hätten; Treffer innerhalb eines Standbild-Abschnitts = erkannter Sturz,
  außerhalb = Fehlalarm-Kandidat (Alltagsszene);
- mit `--gestures`: alle erkannten Gesten mit Videozeit.
"""
from __future__ import annotations

import argparse
import logging
import time

import cv2
import numpy as np

from . import settings


class _Cam:
    def __init__(self, cid: int, cfg: dict):
        from .tracking import CameraTracker
        self.id = cid
        self.cfg = cfg
        self.tracker = CameraTracker(cid, cfg["detect_fps"])
        self.tracker.configure(cfg)


def static_periods(path: str, fps: float, min_s: float = 5.0) -> list[tuple[float, float]]:
    cap = cv2.VideoCapture(path)
    vfps = cap.get(cv2.CAP_PROP_FPS) or 25.0
    step = max(1, int(round(vfps / fps)))
    prev, start, out, i = None, None, [], 0
    while True:
        ok, fr = cap.read()
        if not ok:
            break
        if i % step == 0:
            t = i / vfps
            g = cv2.resize(cv2.cvtColor(fr, cv2.COLOR_BGR2GRAY), (160, 120)).astype(np.float32)
            if prev is not None and float(np.mean(np.abs(g - prev))) < 0.5:
                start = t - 1.0 / fps if start is None else start
            else:
                if start is not None and t - start >= min_s:
                    out.append((start, t))
                start = None
            prev = g
        i += 1
    if start is not None and i / vfps - start >= min_s:
        out.append((start, i / vfps))
    cap.release()
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("video")
    ap.add_argument("--fps", type=float, default=5.0)
    ap.add_argument("--dwell", type=float, default=5.0, help="min_dwell_s der gedachten Sturz-Regel")
    ap.add_argument("--grace", type=float, default=30.0, help="fall_track_grace_s")
    ap.add_argument("--gestures", action="store_true")
    ap.add_argument("--hold", type=float, default=1.0, help="gesture_hold_s")
    ap.add_argument("--min-score", type=float, default=0.5)
    ap.add_argument("--verbose", action="store_true")
    ap.add_argument("--trace", type=float, default=0.0, help="alle n s Zustand aller Tracks ausgeben")
    a = ap.parse_args()
    logging.basicConfig(level=logging.DEBUG if a.verbose else logging.WARNING,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    from .detector import Detector
    from .pose import PoseEngine, PoseJob
    from .inventory import GpuTurn

    statics = static_periods(a.video, a.fps)
    det = Detector()
    gestures: list[tuple[float, dict]] = []
    vt = [0.0]

    def publish(topic, payload, qos=0, retain=False):
        if "/gesture/" in topic:
            gestures.append((vt[0], payload))

    cfg = {"id": 1, "name": "posecheck", "detect_fps": a.fps, "labels": ["person"], "min_score": a.min_score,
           "zones": [], "pose_enabled": True, "gesture_enabled": a.gestures}
    cam = _Cam(1, cfg)
    cam.tracker.lying_grace_s = a.grace
    eng = PoseEngine(publish, GpuTurn(), lambda cid: cam)
    eng.gesture_hold_s = a.hold
    eng.wanted = {1}
    from .pose import PoseModel
    eng.model = PoseModel()
    frame_hits: list[tuple[float, set]] = []
    if a.gestures:
        from . import gestures as gest_mod
        from . import pose as pose_mod
        _orig = gest_mod.classify

        def _rec(kp, kc):
            r = _orig(kp, kc)
            if r:
                frame_hits.append((vt[0], r))
            return r
        pose_mod.gest.classify = _rec

    cap = cv2.VideoCapture(a.video)
    vfps = cap.get(cv2.CAP_PROP_FPS) or 25.0
    step = max(1, int(round(vfps / a.fps)))
    pose_every = 1.0 / (settings.GESTURE_FPS if a.gestures else settings.POSE_FPS)
    last_pose = -1e9
    # Track-Zustand je Zeitpunkt: (videozeit, tid, posture, posture_since)
    lying: dict[int, dict] = {}
    t0 = time.time()
    base = 1_000_000.0     # künstliche Unix-Zeit (Tracker rechnet in Sekunden)
    i = n = 0
    pose_ms = []
    while True:
        ok, fr = cap.read()
        if not ok:
            break
        if i % step:
            i += 1
            continue
        t = i / vfps
        vt[0] = t
        ts = base + t
        dets = det.detect([fr])[0]
        _, msgs, _ = cam.tracker.process(ts, fr, dets)
        if t - last_pose >= pose_every - 1e-6:
            tracks = cam.tracker.person_tracks()
            if tracks:
                last_pose = t
                t1 = time.perf_counter()
                people = eng.model.predict([fr])[0]
                pose_ms.append((time.perf_counter() - t1) * 1000)
                eng._apply(PoseJob(1, ts, fr, tracks, a.gestures), people)
        cam.tracker.expire(ts)
        if a.trace and n % max(1, int(a.trace * a.fps)) == 0:
            with cam.tracker.lock:
                rows = [f"{tr.tid % 1000}:{tr.attributes.get('posture', '-')}"
                        f"[{','.join(f'{v:.2f}' for v in tr.bbox)}]{'H' if tr.last_seen < ts else ''}"
                        for tr in cam.tracker.tracks.values() if tr.confirmed]
            print(f"{t:6.1f}s " + " ".join(rows))
        with cam.tracker.lock:
            for tr in cam.tracker.tracks.values():
                e = lying.setdefault(tr.tid, {"open": False, "episodes": []})
                if tr.attributes.get("posture") == "lying":
                    if not e["open"]:
                        since = float(tr.attributes.get("posture_since") or ts) - base
                        e["episodes"].append([max(0.0, since), t])
                        e["open"] = True
                    e["episodes"][-1][1] = t
                else:
                    e["open"] = False
        i += 1
        n += 1
    cap.release()
    el = time.time() - t0

    episodes = []
    for tid, e in lying.items():
        for s, en in e["episodes"]:
            episodes.append((s, en, tid))
    episodes.sort()

    def in_static(t: float) -> int | None:
        for k, (s, e) in enumerate(statics):
            if s - 1.0 <= t <= e + 1.0:
                return k
        return None

    print(f"\nVideo {a.video}: {n} Bilder à {a.fps} fps in {el:.1f} s, Pose Ø {np.mean(pose_ms) if pose_ms else 0:.1f} ms "
          f"({len(pose_ms)} Aufrufe)")
    print(f"Standbild-Abschnitte (≥ 5 s): {len(statics)}: " + ", ".join(f"{s:.0f}–{e:.0f}s" for s, e in statics))
    fired_static, false = set(), []
    for s, en, tid in episodes:
        fire_at = s + a.dwell
        fires = en >= fire_at
        k = in_static(fire_at) if fires else None
        tag = "—"
        if fires:
            if k is not None:
                fired_static.add(k)
                tag = f"Sturz {k + 1}"
            else:
                tag = "AUSSERHALB Standbild"
                false.append((s, en, tid))
        print(f"  Track {tid}: liegt {s:6.1f}–{en:6.1f}s ({en - s:4.1f} s) → {'löst aus' if fires else 'zu kurz'} [{tag}]")
    print(f"Erkannte Stürze (Auslösung in einem Standbild-Abschnitt, dwell {a.dwell:.0f} s): "
          f"{len(fired_static)}/{len(statics)}")
    print(f"Auslösungen außerhalb der Standbilder (Fehlalarm-Kandidaten): {len(false)}")
    if a.gestures:
        print(f"Einzelbilder mit Geste: {len(frame_hits)}: "
              + ", ".join(f"{t:.1f}s {'+'.join(sorted(g))}" for t, g in frame_hits[:200]))
        print(f"Gesten: {len(gestures)}")
        for t, g in gestures:
            print(f"  {t:6.1f}s Track {g['track_id']}: {g['gesture']} (score {g['score']}, {g['held_s']} s)")


if __name__ == "__main__":
    main()
