"""Per-camera multi-object tracking (supervision ByteTrack) + track lifecycle per CONTRACT.md."""
from __future__ import annotations

import logging
import os
import threading
import time
from collections import Counter
from dataclasses import dataclass, field

import numpy as np
import supervision as sv

from . import settings
from .util import crop_norm, new_media_path, point_in_polygon, write_jpeg

log = logging.getLogger("vg.tracking")

# Track IDs must be unique per camera across worker restarts and camera restarts (the backend keys
# events by camera+track_id). One process-wide counter seeded from the clock (< 2^31 for Postgres INTEGER).
_ID_LOCK = threading.Lock()
_ID_NEXT = [(int(time.time()) % 1_000_000) * 1000 + 1]


def _next_track_id() -> int:
    with _ID_LOCK:
        v = _ID_NEXT[0]
        _ID_NEXT[0] = v + 1 if v < 2_000_000_000 else 1
        return v


@dataclass
class Track:
    tid: int
    ext_id: int
    first_seen: float
    last_seen: float
    bbox: list[float]
    score: float
    best_score: float = 0.0
    hits: int = 0                       # detections with score >= min_score
    label_votes: Counter = field(default_factory=Counter)
    label: str = ""
    confirmed: bool = False
    zones: list[str] = field(default_factory=list)
    entered: list[str] = field(default_factory=list)   # accumulated since last message
    left: list[str] = field(default_factory=list)
    identity: dict | None = None
    attributes: dict = field(default_factory=dict)
    # best crop
    best_quality: float = 0.0
    best_crop: np.ndarray | None = None
    best_crop_bbox: list[float] | None = None
    crop_path: str | None = None
    crop_dirty: bool = False
    last_crop_write: float = 0.0
    # messaging
    dirty_reason: set = field(default_factory=set)
    last_update_msg: float = 0.0
    # identification bookkeeping (shared with identify.Identifier via `ids`)
    ids: dict = field(default_factory=lambda: {"lock": threading.Lock(), "face_sent": 0, "face_quality": 0.0,
                                               "face_last": 0.0, "plate_reads": {}, "plate_sent": set(),
                                               "plate_last": 0.0, "plate_tries": 0, "pet_done": False})
    pending: int = 0                    # outstanding identification jobs/HTTP calls
    ended: bool = False
    # Pose (C1): zuletzt per Track-update gemeldete Bewegung und Zeitpunkt
    motion_sent: float | None = None
    motion_sent_ts: float = 0.0
    released: bool = False              # Nachlauf beendet (an der Stelle steht jetzt jemand)

    def obj(self) -> dict:
        return {"track_id": self.tid, "label": self.label, "score": round(self.score, 3),
                "bbox": [round(v, 4) for v in self.bbox], "zones": list(self.zones),
                "identity": self.identity, "attributes": dict(self.attributes)}


def _iou(a, b) -> float:
    ix = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = ix * iy
    if inter <= 0:
        return 0.0
    ua = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / ua if ua > 0 else 0.0


def _overlap_of(a, b) -> float:
    """Anteil der Fläche von a, der in b liegt."""
    ix = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    area = max(1e-12, (a[2] - a[0]) * (a[3] - a[1]))
    return ix * iy / area


def _center_dist(a, b) -> float:
    return float(np.hypot((a[0] + a[2] - b[0] - b[2]) / 2.0, (a[1] + a[3] - b[1] - b[3]) / 2.0))


class CameraTracker:
    """Owns ByteTrack + Track objects for one camera. Thread-safe via self.lock."""

    def __init__(self, camera_id: int, fps: float):
        self.camera_id = camera_id
        self.fps = max(1.0, float(fps))
        self.lock = threading.RLock()
        self.tracks: dict[int, Track] = {}         # by public tid
        self.by_ext: dict[int, int] = {}           # bytetrack id -> tid
        self._activation = None
        self.cfg: dict = {}
        self.last_objects: list[dict] = []
        self.last_frame_size = (0, 0)
        self.tracker: sv.ByteTrack | None = None
        self.last_ts = 0.0
        # C1: Nachlauf für liegende Personen (Settings fall_track_grace_s); wirkt nur mit pose_enabled
        self.lying_grace_s = settings.LYING_GRACE_S

    # ------------------------------------------------------------------ config
    def configure(self, cfg: dict):
        with self.lock:
            self.cfg = cfg
            act = max(0.1, float(cfg.get("min_score") or 0.5) - 0.2)
            if self.tracker is None or act != self._activation or float(cfg.get("detect_fps") or 5) != self.fps:
                self.fps = max(1.0, float(cfg.get("detect_fps") or 5))
                self._activation = act
                # max_time_lost = frame_rate/30*lost_track_buffer frames ~= TRACK_END_S seconds
                self.tracker = sv.ByteTrack(track_activation_threshold=act,
                                            lost_track_buffer=int(30 * settings.TRACK_END_S),
                                            minimum_matching_threshold=0.8,
                                            frame_rate=int(round(self.fps)),
                                            minimum_consecutive_frames=1)
                self.by_ext.clear()

    def _zones_of(self, bbox) -> list[str]:
        # Fußpunkt knapp ins Bild ziehen: am unteren Rand abgeschnittene Personen (y2 = 1,0) lagen sonst nie in
        # einer Zone, die bis zum Bildrand reicht (Punkt-in-Polygon ist am Rand „außen“) – z. B. Sofa-Ausschluss
        x = min(max((bbox[0] + bbox[2]) / 2.0, 0.001), 0.999)
        y = min(max(bbox[3], 0.001), 0.999)
        out = []
        for z in self.cfg.get("zones") or []:
            poly = z.get("polygon") or []
            if poly and point_in_polygon(x, y, poly):
                out.append(str(z.get("id")))
        return out

    # ------------------------------------------------------------------ main step
    def process(self, ts: float, frame: np.ndarray, dets: list[tuple[str, float, list[float]]]):
        """Feed one analysed frame. Returns (detections_payload, [track messages], [new_confirmed/active tracks])."""
        with self.lock:
            cfg = self.cfg
            labels = set(cfg.get("labels") or [])
            min_score = float(cfg.get("min_score") or 0.5)
            h, w = frame.shape[:2]
            self.last_frame_size = (w, h)
            floor = max(settings.DETECT_MIN_CONF, min(0.1, min_score))
            dets = [d for d in dets if (not labels or d[0] in labels) and d[1] >= floor]
            lab_list = sorted({d[0] for d in dets})
            if dets:
                xyxy = np.array([[d[2][0] * w, d[2][1] * h, d[2][2] * w, d[2][3] * h] for d in dets], dtype=np.float32)
                conf = np.array([d[1] for d in dets], dtype=np.float32)
                cls = np.array([lab_list.index(d[0]) for d in dets], dtype=int)
                sd = sv.Detections(xyxy=xyxy, confidence=conf, class_id=cls)
            else:
                sd = sv.Detections.empty()
            tracked = self.tracker.update_with_detections(sd)

            msgs: list[dict] = []
            objects: list[dict] = []
            touched: list[Track] = []
            cur_ext = {int(x) for x in tracked.tracker_id} if len(tracked) else set()
            for k in range(len(tracked)):
                ext = int(tracked.tracker_id[k])
                x1, y1, x2, y2 = (float(v) for v in tracked.xyxy[k])
                bbox = [max(0.0, x1 / w), max(0.0, y1 / h), min(1.0, x2 / w), min(1.0, y2 / h)]
                score = float(tracked.confidence[k])
                label = lab_list[int(tracked.class_id[k])] if len(lab_list) else "object"
                tid = self.by_ext.get(ext)
                tr = self.tracks.get(tid) if tid is not None else None
                if tr is None or tr.ended:
                    # nur eine wieder gefundene *liegende* Person anhängen (Box breiter als ~hoch), sonst neuer Track
                    flat = (bbox[2] - bbox[0]) * w > 1.0 * (bbox[3] - bbox[1]) * h
                    held = self._held_lying(bbox, ts, cur_ext) if label == "person" and flat else None
                    if held is not None:
                        # C1: liegende Person nach Track-Abriss wiedergefunden → derselbe Track (Liegedauer bleibt)
                        log.info("camera %s: lying track %s re-attached after %.1fs", self.camera_id, held.tid,
                                 ts - held.last_seen)
                        tr = held
                        tr.ext_id = ext
                        self.by_ext[ext] = tr.tid
                    else:
                        tid = _next_track_id()
                        tr = Track(tid=tid, ext_id=ext, first_seen=ts, last_seen=ts, bbox=bbox, score=score)
                        self.tracks[tid] = tr
                        self.by_ext[ext] = tid
                tr.last_seen = ts
                tr.bbox = bbox
                tr.score = score
                if score >= min_score:
                    tr.hits += 1
                    tr.best_score = max(tr.best_score, score)
                tr.label_votes[label] += score
                if not tr.confirmed:
                    tr.label = tr.label_votes.most_common(1)[0][0]
                # zones
                zs = self._zones_of(bbox)
                if zs != tr.zones:
                    if tr.confirmed:
                        for z in zs:
                            if z not in tr.zones:
                                tr.entered.append(z)
                        for z in tr.zones:
                            if z not in zs:
                                tr.left.append(z)
                        tr.dirty_reason.add("zone")
                    tr.zones = zs
                # best crop (score * area), from the full-resolution frame
                if score >= min_score:
                    area = (bbox[2] - bbox[0]) * (bbox[3] - bbox[1])
                    q = score * area
                    if q > tr.best_quality * 1.1:
                        c = crop_norm(frame, bbox, margin=0.08)
                        if c is not None:
                            tr.best_quality = q
                            tr.best_crop = c
                            tr.best_crop_bbox = bbox
                            tr.crop_dirty = True
                # confirmation
                if not tr.confirmed and tr.hits >= settings.TRACK_CONFIRM_HITS:
                    tr.confirmed = True
                    tr.label = tr.label_votes.most_common(1)[0][0]
                    tr.entered = list(tr.zones)
                    self._write_crop(tr, ts)
                    msgs.append(self._msg(tr, "start", ts))
                if tr.confirmed:
                    objects.append(tr.obj())
                    touched.append(tr)

            self._release_stood_up(touched, w, h)

            # updates: zone change / identity / attributes immediately, better crop at most 1/s
            for tr in touched:
                if tr.dirty_reason or (tr.crop_dirty and ts - tr.last_crop_write >= settings.TRACK_UPDATE_MIN_S
                                       and ts - tr.last_update_msg >= settings.TRACK_UPDATE_MIN_S):
                    if tr.crop_dirty and ts - tr.last_crop_write >= settings.TRACK_UPDATE_MIN_S:
                        self._write_crop(tr, ts)
                    msgs.append(self._msg(tr, "update", ts))

            self.last_objects = objects
            self.last_ts = ts
            det_msg = {"camera_id": self.camera_id, "ts": round(ts, 3), "width": w, "height": h, "objects": objects}
            return det_msg, msgs, touched

    # ------------------------------------------------------------------ expiry
    def expire(self, now: float, force: bool = False) -> list[dict]:
        """End tracks without hits for TRACK_END_S (waiting a bit for pending identification)."""
        msgs = []
        with self.lock:
            for tid, tr in list(self.tracks.items()):
                idle = now - tr.last_seen
                end_s = settings.TRACK_END_S
                if self._lying_hold(tr):
                    end_s = max(end_s, self.lying_grace_s)
                if not force and idle < end_s:
                    # identity/attributes resolved while object is not visible in the current frame
                    if tr.confirmed and tr.dirty_reason & {"identity", "attributes", "pose"}:
                        msgs.append(self._msg(tr, "update", now))
                    continue
                if not tr.confirmed:
                    del self.tracks[tid]
                    continue
                if not force and tr.pending > 0 and idle < settings.TRACK_END_S + 6.0:
                    continue
                tr.ended = True
                if tr.crop_dirty:
                    self._write_crop(tr, now)
                tr.left = list(dict.fromkeys(tr.left + tr.zones))
                msgs.append(self._msg(tr, "end", now))
                del self.tracks[tid]
            for ext, tid in list(self.by_ext.items()):
                if tid not in self.tracks:
                    del self.by_ext[ext]
        return msgs

    # ------------------------------------------------------------------ identification results
    def set_identity(self, tid: int, identity: dict | None, attributes: dict | None = None):
        with self.lock:
            tr = self.tracks.get(tid)
            if tr is None:
                return
            if identity and tr.attributes.get("identity_source") == "reid":
                # W4 (C2): eigene Erkennung (Gesicht) hat Vorrang vor der über ReID weitergegebenen Identität
                tr.attributes["identity_source"] = identity.get("kind") or "face"
                tr.dirty_reason.add("attributes")
            if identity and identity != tr.identity:
                tr.identity = identity
                tr.dirty_reason.add("identity")
            if attributes:
                new = {**tr.attributes, **attributes}
                if new != tr.attributes:
                    tr.attributes = new
                    tr.dirty_reason.add("attributes")

    # ------------------------------------------------------------------ ReID (C2)
    def set_reid(self, tid: int, presence_key: str, identity: dict | None = None):
        """Antwort von /api/internal/reid übernehmen: `presence_key` als Attribut; eine über ReID weitergegebene
        Identität nur, wenn der Track noch keine eigene hat (`identity_source: "reid"`)."""
        with self.lock:
            tr = self.tracks.get(tid)
            if tr is None or tr.ended:
                return
            a = tr.attributes
            if presence_key and a.get("presence_key") != presence_key:
                a["presence_key"] = presence_key
                tr.dirty_reason.add("attributes")
            if identity and identity.get("id") is not None and tr.identity is None:
                tr.identity = {"id": identity["id"], "display_name": identity.get("display_name"),
                               "name": identity.get("name"), "kind": identity.get("kind") or "face"}
                a["identity_source"] = "reid"
                tr.dirty_reason.add("identity")

    # ------------------------------------------------------------------ Pose (C1/D1)
    def _lying_hold(self, tr: Track) -> bool:
        return (bool(self.cfg.get("pose_enabled")) and self.lying_grace_s > 0 and tr.confirmed and not tr.released
                and tr.label == "person" and tr.attributes.get("posture") == "lying")

    def _held_lying(self, bbox: list[float], ts: float, cur_ext: set[int]) -> Track | None:
        """Liegender Track ohne aktuelle Detektion an (fast) derselben Stelle."""
        best, best_d = None, 1e9
        for tr in self.tracks.values():
            if tr.ended or not self._lying_hold(tr) or ts - tr.last_seen < 0.05:
                continue
            if tr.ext_id in cur_ext:
                continue          # ByteTrack führt den Track in diesem Bild selbst
            d = _center_dist(bbox, tr.bbox)
            if _iou(bbox, tr.bbox) >= 0.2 or d < 0.1:
                if d < best_d:
                    best, best_d = tr, d
        return best

    def _release_stood_up(self, visible: list[Track], w: int, h: int):
        """Gehaltener Liegend-Track (gerade nicht detektiert) und an seiner Stelle steht/sitzt jetzt eine Person
        (aufrechte Box deckt ≥ 30 % der Liegend-Box ab) → Person ist vermutlich aufgestanden: Nachlauf beenden."""
        held = [t for t in self.tracks.values() if t.confirmed and not t.ended and t not in visible
                and self._lying_hold(t)]
        if not held:
            return
        for v in visible:
            if v.label != "person" or v.attributes.get("posture") == "lying":
                continue
            if (v.bbox[2] - v.bbox[0]) * w >= 0.8 * (v.bbox[3] - v.bbox[1]) * h:
                continue
            for t in held:
                if _overlap_of(t.bbox, v.bbox) >= 0.3 and not t.released:
                    log.info("camera %s: lying track %s released (person upright at the same place, track %s)",
                             self.camera_id, t.tid, v.tid)
                    t.released = True

    def person_tracks(self) -> list[dict]:
        """Bestätigte Personen-Tracks für die Pose-Stufe (`held` = gerade nicht detektiert, liegend gehalten)."""
        with self.lock:
            out = []
            for tr in self.tracks.values():
                if not tr.confirmed or tr.ended or tr.label != "person":
                    continue
                held = tr.last_seen < self.last_ts - 1e-3
                if held and not self._lying_hold(tr):
                    continue
                out.append({"tid": tr.tid, "bbox": list(tr.bbox), "zones": list(tr.zones),
                            "identity": tr.identity, "held": held})
            return out

    def set_pose(self, tid: int, posture: str, since: float, motion: float | None, changed: bool):
        """Geglättete Haltung übernehmen. Track-update bei Haltungswechsel sofort, bei deutlich geänderter
        Bewegung höchstens alle 3 s (für `max_motion` im Backend)."""
        with self.lock:
            tr = self.tracks.get(tid)
            if tr is None or tr.ended:
                return
            a = tr.attributes
            if posture != "unknown" and (changed or a.get("posture") != posture):
                if a.get("posture") != posture:
                    a["posture"] = posture
                    a["posture_since"] = round(since, 3)
                    tr.dirty_reason.add("attributes")
            if motion is not None:
                m = round(float(motion), 3)
                a["motion"] = m
                ts = self.last_ts or time.time()
                if (tr.motion_sent is None or abs(m - tr.motion_sent) > 0.02) and ts - tr.motion_sent_ts >= 3.0:
                    tr.motion_sent, tr.motion_sent_ts = m, ts
                    tr.dirty_reason.add("pose")

    def pose_keepalive(self, box: list[float], ts: float):
        """Pose-Modell sieht eine Person, die der Detektor nicht liefert: liegenden Track an dieser Stelle halten
        (nur wenn die Pose-Box selbst flach ist – eine aufgestandene Person hält den Liegend-Track nicht)."""
        fw, fh = self.last_frame_size
        if fw and fh and (box[2] - box[0]) * fw <= 1.0 * (box[3] - box[1]) * fh:
            return
        with self.lock:
            for tr in self.tracks.values():
                if tr.ended or not self._lying_hold(tr):
                    continue
                if _iou(box, tr.bbox) >= 0.2 or _center_dist(box, tr.bbox) < 0.1:
                    if ts > tr.last_seen:
                        tr.last_seen = ts
                    return

    def change_pending(self, tid: int, delta: int):
        with self.lock:
            tr = self.tracks.get(tid)
            if tr is not None:
                tr.pending = max(0, tr.pending + delta)

    # ------------------------------------------------------------------ helpers
    def _write_crop(self, tr: Track, ts: float):
        if tr.best_crop is None:
            return
        try:
            if not tr.crop_path:
                tr.crop_path = new_media_path("crops", "jpg", tr.first_seen)
            write_jpeg(tr.crop_path, tr.best_crop)
            tr.crop_dirty = False
            tr.last_crop_write = ts
        except Exception as e:  # noqa: BLE001
            log.warning("crop write failed: %s", e)

    def _msg(self, tr: Track, typ: str, ts: float) -> dict:
        m = {"type": typ, "camera_id": self.camera_id, "track_id": tr.tid, "label": tr.label,
             "score": round(tr.score, 3), "ts": round(ts, 3), "first_seen": round(tr.first_seen, 3),
             "last_seen": round(tr.last_seen, 3), "zones": list(tr.zones),
             "entered_zones": list(dict.fromkeys(tr.entered)), "left_zones": list(dict.fromkeys(tr.left)),
             "identity": tr.identity, "attributes": dict(tr.attributes), "crop_path": tr.crop_path,
             "best_score": round(max(tr.best_score, tr.score), 3)}
        tr.entered.clear()
        tr.left.clear()
        tr.dirty_reason.clear()
        tr.last_update_msg = ts
        return m

    def snapshot_objects(self) -> list[dict]:
        with self.lock:
            return list(self.last_objects)

    def reset(self):
        with self.lock:
            self.tracks.clear()
            self.by_ext.clear()
            self.tracker = None
            self.configure(self.cfg)
