"""Identitäts-Service: Wiedererkennung von Gesichtern, Tieren (Embeddings) und Kennzeichen (Text)."""
from __future__ import annotations

import logging
import os
import re
import shutil
import threading
import uuid
from typing import Any, Optional

import numpy as np
from rapidfuzz.distance import Levenshtein
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from . import settings_store
from .models import KV, Event, Identity, IdentitySample, utcnow
from .serializers import identity_json
from .util import media_path
from .ws import hub

log = logging.getLogger("vg.identity")

MAX_SAMPLES = 20
_lock = threading.RLock()

# Embedding-Cache je Art: (Matrix normierter Embeddings [n,d], identity_ids [n]) – None = neu aufbauen
_cache: dict[str, Optional[tuple[np.ndarray, np.ndarray]]] = {"face": None, "pet": None}


def invalidate(kind: Optional[str] = None) -> None:
    with _lock:
        for k in ([kind] if kind in _cache else list(_cache)):
            _cache[k] = None


def _normalize(v: np.ndarray) -> np.ndarray:
    n = np.linalg.norm(v, axis=-1, keepdims=True)
    n[n == 0] = 1.0
    return v / n


def _matrix(s: Session, kind: str) -> Optional[tuple[np.ndarray, np.ndarray]]:
    cached = _cache.get(kind)
    if cached is not None:
        return cached
    rows = s.execute(
        select(IdentitySample.identity_id, IdentitySample.embedding)
        .join(Identity, Identity.id == IdentitySample.identity_id)
        .where(Identity.kind == kind, IdentitySample.embedding.is_not(None))
    ).all()
    # Nur Embeddings der häufigsten Dimension verwenden (Modellwechsel robust behandeln)
    dims: dict[int, int] = {}
    for _, emb in rows:
        dims[len(emb)] = dims.get(len(emb), 0) + 1
    if not dims:
        _cache[kind] = (np.zeros((0, 0), dtype=np.float32), np.zeros((0,), dtype=np.int64))
        return _cache[kind]
    d = max(dims, key=dims.get)
    ids = [iid for iid, emb in rows if len(emb) == d]
    mat = np.asarray([emb for _, emb in rows if len(emb) == d], dtype=np.float32)
    _cache[kind] = (_normalize(mat), np.asarray(ids, dtype=np.int64))
    return _cache[kind]


def normalize_plate(text: Optional[str]) -> str:
    return re.sub(r"[\s\-_.·:]+", "", (text or "").upper())


def _next_number(s: Session, kind: str) -> int:
    key = f"counter_{kind}"
    row = s.get(KV, key, with_for_update=True)
    n = int(row.value) + 1 if row and row.value is not None else 1
    if row:
        row.value = n
    else:
        s.add(KV(key=key, value=n))
    return n


def _copy_crop(identity_id: int, crop_path: Optional[str]) -> Optional[str]:
    if not crop_path:
        return None
    try:
        src = media_path(crop_path)
        if not os.path.isfile(src):
            return None
        ext = os.path.splitext(src)[1] or ".jpg"
        rel = f"identities/{identity_id}/{uuid.uuid4().hex}{ext}"
        dst = media_path(rel)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copyfile(src, dst)
        return rel
    except Exception as e:  # noqa: BLE001
        log.warning("Crop konnte nicht kopiert werden (%s): %s", crop_path, e)
        return None


def _remove_media(rel: Optional[str]) -> None:
    if not rel:
        return
    try:
        os.remove(media_path(rel))
    except (OSError, ValueError):
        pass


def _add_sample(s: Session, ident: Identity, emb: Optional[list[float]], plate_text: Optional[str],
                crop_path: Optional[str], quality: float) -> bool:
    """Fügt ein Sample hinzu; behält max. MAX_SAMPLES mit bester Qualität. True wenn hinzugefügt."""
    samples = s.scalars(
        select(IdentitySample).where(IdentitySample.identity_id == ident.id).order_by(IdentitySample.quality.asc())
    ).all()
    if len(samples) >= MAX_SAMPLES:
        worst = samples[0]
        if quality <= (worst.quality or 0):
            return False
        _remove_media(worst.image_path)
        s.delete(worst)
    img = _copy_crop(ident.id, crop_path)
    s.add(IdentitySample(identity_id=ident.id, embedding=emb, plate_text=plate_text, image_path=img,
                         quality=float(quality or 0)))
    return True


def prune_samples(s: Session, identity_id: int) -> None:
    samples = s.scalars(
        select(IdentitySample).where(IdentitySample.identity_id == identity_id)
        .order_by(IdentitySample.quality.desc(), IdentitySample.id.desc())
    ).all()
    for smp in samples[MAX_SAMPLES:]:
        _remove_media(smp.image_path)
        s.delete(smp)


def _link_event(s: Session, camera_id: Optional[int], track_id: Optional[int], identity_id: int) -> Optional[Event]:
    if camera_id is None or track_id is None:
        return None
    ev = s.scalar(
        select(Event).where(Event.camera_id == camera_id, Event.track_id == track_id, Event.active.is_(True))
        .order_by(Event.start_ts.desc()).limit(1)
    )
    if ev and ev.identity_id != identity_id:
        ev.identity_id = identity_id
        return ev
    return None


def identify(s: Session, req: Any) -> dict[str, Any]:
    """Kern von POST /api/internal/identify. req: schemas.IdentifyIn."""
    settings = settings_store.get()
    kind = req.kind
    quality = float(req.quality or 0)
    with _lock:
        ident: Optional[Identity] = None
        score = 0.0
        is_new = False
        emb_list: Optional[list[float]] = None
        plate_disp: Optional[str] = None

        if kind in ("face", "pet"):
            if not req.embedding:
                return {"identity_id": None, "display_name": None, "name": None, "score": 0.0, "is_new": False}
            vec = np.asarray(req.embedding, dtype=np.float32)
            if not np.all(np.isfinite(vec)) or float(np.linalg.norm(vec)) == 0.0:
                return {"identity_id": None, "display_name": None, "name": None, "score": 0.0, "is_new": False}
            emb_list = [float(x) for x in vec]
            thr = float(settings["face_match_threshold"] if kind == "face" else settings["pet_match_threshold"])
            mat, ids = _matrix(s, kind)
            if mat.shape[0] and mat.shape[1] == vec.shape[0]:
                sims = mat @ _normalize(vec[None, :])[0]
                best = int(np.argmax(sims))
                if float(sims[best]) >= thr:
                    ident = s.get(Identity, int(ids[best]))
                    score = float(sims[best])
        else:  # plate
            norm = normalize_plate(req.plate_text)
            if not norm:
                return {"identity_id": None, "display_name": None, "name": None, "score": 0.0, "is_new": False}
            plate_disp = re.sub(r"\s+", " ", (req.plate_text or "").strip().upper())
            maxd = int(settings["plate_max_distance"])
            best_d = None
            for iid, pn in s.execute(select(Identity.id, Identity.plate_norm).where(Identity.kind == "plate")).all():
                if not pn:
                    continue
                d = Levenshtein.distance(norm, pn, score_cutoff=maxd)
                if d <= maxd and (best_d is None or d < best_d):
                    best_d, ident = d, s.get(Identity, iid)
                    if d == 0:
                        break
            if ident is not None:
                score = 1.0 - (best_d or 0) / max(len(norm), 1)

        now = utcnow()
        if ident is None:
            is_new = True
            number = 0 if kind == "plate" else _next_number(s, kind)
            ident = Identity(kind=kind, auto_number=number, first_seen=now, last_seen=now, notes="")
            if kind == "plate":
                ident.plate_text = plate_disp
                ident.plate_norm = normalize_plate(req.plate_text)
            s.add(ident)
            s.flush()
            score = 1.0 if kind == "plate" else score
            _add_sample(s, ident, emb_list, plate_disp, req.crop_path, quality)
        else:
            ident.last_seen = now
            _add_sample(s, ident, emb_list, plate_disp, req.crop_path, quality)
            if kind == "plate" and plate_disp and normalize_plate(plate_disp) == ident.plate_norm:
                ident.plate_text = plate_disp

        ev = _link_event(s, req.camera_id, req.track_id, ident.id)
        s.commit()
        if kind in ("face", "pet"):
            invalidate(kind)

    if is_new:
        hub.broadcast({"type": "identity", "identity": identity_json(s, ident)})
    if ev is not None:
        from .events import notify_event_changed  # zyklischen Import vermeiden
        notify_event_changed(ev.id)
    return {"identity_id": ident.id, "display_name": ident.display_name, "name": ident.name,
            "score": round(score, 4), "is_new": is_new}


def delete_identity_files(identity_id: int) -> None:
    try:
        shutil.rmtree(media_path(f"identities/{identity_id}"), ignore_errors=True)
    except ValueError:
        pass


def merge(s: Session, src: Identity, dst: Identity) -> Identity:
    with _lock:
        samples = s.scalars(select(IdentitySample).where(IdentitySample.identity_id == src.id)).all()
        for smp in samples:
            if smp.image_path:
                try:
                    new_rel = f"identities/{dst.id}/{os.path.basename(smp.image_path)}"
                    dst_abs = media_path(new_rel)
                    os.makedirs(os.path.dirname(dst_abs), exist_ok=True)
                    shutil.move(media_path(smp.image_path), dst_abs)
                    smp.image_path = new_rel
                except Exception as e:  # noqa: BLE001
                    log.warning("Sample-Datei nicht verschoben: %s", e)
            smp.identity_id = dst.id
        s.execute(update(Event).where(Event.identity_id == src.id).values(identity_id=dst.id))
        if not dst.name and src.name:
            dst.name = src.name
        if src.notes and src.notes not in (dst.notes or ""):
            dst.notes = ((dst.notes or "") + ("\n" if dst.notes else "") + src.notes).strip()
        if src.first_seen and (not dst.first_seen or src.first_seen < dst.first_seen):
            dst.first_seen = src.first_seen
        if src.last_seen and (not dst.last_seen or src.last_seen > dst.last_seen):
            dst.last_seen = src.last_seen
        s.flush()
        prune_samples(s, dst.id)
        s.expire(src)
        s.delete(src)
        s.commit()
        delete_identity_files(src.id)
        invalidate()
    return dst
