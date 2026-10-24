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

    def obj(self) -> dict:
        return {"track_id": self.tid, "label": self.label, "score": round(self.score, 3),
                "bbox": [round(v, 4) for v in self.bbox], "zones": list(self.zones),
                "identity": self.identity, "attributes": dict(self.attributes)}


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
        x = (bbox[0] + bbox[2]) / 2.0
        y = bbox[3]
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
            for k in range(len(tracked)):
                ext = int(tracked.tracker_id[k])
                x1, y1, x2, y2 = (float(v) for v in tracked.xyxy[k])
                bbox = [max(0.0, x1 / w), max(0.0, y1 / h), min(1.0, x2 / w), min(1.0, y2 / h)]
                score = float(tracked.confidence[k])
                label = lab_list[int(tracked.class_id[k])] if len(lab_list) else "object"
                tid = self.by_ext.get(ext)
                tr = self.tracks.get(tid) if tid is not None else None
                if tr is None or tr.ended:
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

            # updates: zone change / identity / attributes immediately, better crop at most 1/s
            for tr in touched:
                if tr.dirty_reason or (tr.crop_dirty and ts - tr.last_crop_write >= settings.TRACK_UPDATE_MIN_S
                                       and ts - tr.last_update_msg >= settings.TRACK_UPDATE_MIN_S):
                    if tr.crop_dirty and ts - tr.last_crop_write >= settings.TRACK_UPDATE_MIN_S:
                        self._write_crop(tr, ts)
                    msgs.append(self._msg(tr, "update", ts))

            self.last_objects = objects
            det_msg = {"camera_id": self.camera_id, "ts": round(ts, 3), "width": w, "height": h, "objects": objects}
            return det_msg, msgs, touched

    # ------------------------------------------------------------------ expiry
    def expire(self, now: float, force: bool = False) -> list[dict]:
        """End tracks without hits for TRACK_END_S (waiting a bit for pending identification)."""
        msgs = []
        with self.lock:
            for tid, tr in list(self.tracks.items()):
                idle = now - tr.last_seen
                if not force and idle < settings.TRACK_END_S:
                    # identity/attributes resolved while object is not visible in the current frame
                    if tr.confirmed and tr.dirty_reason & {"identity", "attributes"}:
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
            if identity and identity != tr.identity:
                tr.identity = identity
                tr.dirty_reason.add("identity")
            if attributes:
                new = {**tr.attributes, **attributes}
                if new != tr.attributes:
                    tr.attributes = new
                    tr.dirty_reason.add("attributes")

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
