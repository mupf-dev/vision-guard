"""Identitäts-Service: Wiedererkennung von Gesichtern, Tieren (Embeddings) und Kennzeichen (Text)."""
from __future__ import annotations

import logging
import os
import re
import shutil
import threading
import uuid
from datetime import timedelta
from typing import Any, Optional

import numpy as np
from rapidfuzz.distance import Levenshtein
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from . import settings_store
from .models import KV, Event, Identity, IdentitySample, utcnow
from .schemas import media_url
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
        .where(Identity.kind == kind, IdentitySample.embedding.is_not(None),
               IdentitySample.negative.is_(False))   # W2: Negativbeispiele nie als Treffer
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
    """Fügt ein Sample hinzu; behält max. MAX_SAMPLES mit bester Qualität. True wenn hinzugefügt.

    Automatisch gelernte Samples verdrängen nur andere automatische (manual=false) – per Merge bewusst
    übernommene Bilder bleiben erhalten.
    """
    samples = s.scalars(
        select(IdentitySample).where(IdentitySample.identity_id == ident.id).order_by(IdentitySample.quality.asc())
    ).all()
    if len(samples) >= MAX_SAMPLES:
        auto = [x for x in samples if not x.manual]
        if not auto:
            return False
        worst = auto[0]
        if quality <= (worst.quality or 0):
            return False
        _remove_media(worst.image_path)
        s.delete(worst)
    img = _copy_crop(ident.id, crop_path)
    s.add(IdentitySample(identity_id=ident.id, embedding=emb, plate_text=plate_text, image_path=img,
                         quality=float(quality or 0)))
    return True


def prune_samples(s: Session, identity_id: int) -> None:
    """Kürzt auf MAX_SAMPLES: zuerst fallen automatische Samples (niedrigste Qualität zuerst), dann manuelle."""
    samples = s.scalars(
        select(IdentitySample).where(IdentitySample.identity_id == identity_id)
        .order_by(IdentitySample.manual.desc(), IdentitySample.quality.desc(), IdentitySample.id.desc())
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
    if kind == "item":   # W2 (B4): Exemplare – eigener Abgleich, legt nie neue Identitäten an
        from .item_identity import identify_item
        return identify_item(s, req)
    quality = float(req.quality or 0)
    with _lock:
        ident: Optional[Identity] = None
        score = 0.0
        is_new = False
        added = False
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
            added = _add_sample(s, ident, emb_list, plate_disp, req.crop_path, quality)
        else:
            ident.last_seen = now
            # Lernsperre: erkennen ja (last_seen, Event-Zuordnung), aber kein neues Sample speichern
            added = False if ident.learning_locked else _add_sample(s, ident, emb_list, plate_disp,
                                                                     req.crop_path, quality)
            if kind == "plate" and plate_disp and normalize_plate(plate_disp) == ident.plate_norm:
                ident.plate_text = plate_disp

        ev = _link_event(s, req.camera_id, req.track_id, ident.id)
        s.commit()
        if added and kind in ("face", "pet"):
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


class MergeError(ValueError):
    """Ungültige Merge-Anfrage (→ HTTP 422)."""


def _move_sample_file(smp: IdentitySample, dst_id: int) -> None:
    if not smp.image_path:
        return
    try:
        new_rel = f"identities/{dst_id}/{os.path.basename(smp.image_path)}"
        dst_abs = media_path(new_rel)
        os.makedirs(os.path.dirname(dst_abs), exist_ok=True)
        shutil.move(media_path(smp.image_path), dst_abs)
        smp.image_path = new_rel
    except Exception as e:  # noqa: BLE001
        log.warning("Sample-Datei nicht verschoben: %s", e)


def merge(s: Session, src: Identity, dst: Identity, sample_ids: Optional[list[int]] = None) -> tuple[Identity, bool]:
    """Führt src in dst zusammen. Rückgabe: (Ziel, Quelle gelöscht?).

    sample_ids None oder alle Samples der Quelle → klassischer Merge: alle Samples und Events wandern, Quelle
    wird gelöscht. Echte Teilmenge → nur diese Samples wandern (manual=true), Quelle samt Events bleibt.
    """
    with _lock:
        src_samples = s.scalars(select(IdentitySample).where(IdentitySample.identity_id == src.id)).all()
        if sample_ids is None:
            moving = list(src_samples)
            full = True
            mark_manual = False
        else:
            wanted = set(sample_ids)
            if not wanted:
                raise MergeError("Keine Bilder ausgewählt")
            by_id = {smp.id: smp for smp in src_samples}
            if not wanted.issubset(by_id):
                raise MergeError("Mindestens ein ausgewähltes Bild gehört nicht zur Quelle")
            moving = [by_id[sid] for sid in wanted]
            full = wanted == set(by_id)
            mark_manual = True

        dst_manual = s.scalar(select(func.count()).select_from(IdentitySample).where(
            IdentitySample.identity_id == dst.id, IdentitySample.manual.is_(True))) or 0
        new_manual = sum(1 for smp in moving if mark_manual or smp.manual)
        if dst_manual + new_manual > MAX_SAMPLES:
            raise MergeError(f"max. {MAX_SAMPLES} Samples – erst alte löschen")

        for smp in moving:
            _move_sample_file(smp, dst.id)
            smp.identity_id = dst.id
            if mark_manual:
                smp.manual = True

        if full:
            s.execute(update(Event).where(Event.identity_id == src.id).values(identity_id=dst.id))
            if src.kind == "pet":   # W2 (C3): Besuche im Haustier-Tagebuch wandern mit
                from .pet_diary import on_identity_merged
                on_identity_merged(s, src.id, dst.id)
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
        src_id = src.id
        if full:
            s.expire(src)
            s.delete(src)
        s.commit()
        if full:
            delete_identity_files(src_id)
        invalidate()
    return dst, full


# ---------------------------------------------------------------- Vorschläge „Ist das vielleicht X?“


def _covers(s: Session, ids: list[int]) -> dict[int, str]:
    out: dict[int, str] = {}
    if not ids:
        return out
    rows = s.execute(
        select(IdentitySample.identity_id, IdentitySample.image_path)
        .where(IdentitySample.identity_id.in_(ids), IdentitySample.image_path.is_not(None))
        .order_by(IdentitySample.identity_id, IdentitySample.quality.desc(), IdentitySample.id.desc())
    ).all()
    for iid, path in rows:
        out.setdefault(iid, path)
    return out


def _blocked_pairs(s: Session) -> set[tuple[int, int]]:
    """W2 (D2): Paare (Quelle, Ziel), die in einer Rückfrage `identity_match` mit „Nein“ beantwortet wurden."""
    from .models import ReviewRequest
    out: set[tuple[int, int]] = set()
    for key in s.scalars(select(ReviewRequest.dedup_key).where(
            ReviewRequest.kind == "identity_match", ReviewRequest.status == "no")).all():
        try:
            _, a, b = str(key).split(":")
            out.add((int(a), int(b)))
        except ValueError:
            continue
    return out


def _suggest_for(s: Session, kind: str, src_ids: list[int], limit: int) -> dict[int, list[dict[str, Any]]]:
    """Vorschläge je Quell-ID: benannte Identitäten gleicher Art, Score = max. Kosinus-Ähnlichkeit."""
    result: dict[int, list[dict[str, Any]]] = {sid: [] for sid in src_ids}
    if kind not in ("face", "pet") or not src_ids:
        return result
    settings = settings_store.get()
    thr = float(settings["face_match_threshold"] if kind == "face" else settings["pet_match_threshold"])
    named = {i.id: i for i in s.scalars(select(Identity).where(
        Identity.kind == kind, Identity.name.is_not(None), Identity.name != "")).all()}
    if not named:
        return result
    with _lock:
        mat, ids = _matrix(s, kind)
    if not mat.shape[0]:
        return result
    tmask = np.isin(ids, np.asarray(list(named), dtype=np.int64))
    if not tmask.any():
        return result
    tmat, tids = mat[tmask], ids[tmask]
    uniq = np.unique(tids)
    blocked = _blocked_pairs(s)
    covers: Optional[dict[int, str]] = None
    for sid in src_ids:
        smat = mat[ids == sid]
        if not smat.shape[0]:
            continue
        row_max = (tmat @ smat.T).max(axis=1)          # je Ziel-Sample beste Ähnlichkeit
        cand = []
        for tid in uniq:
            if int(tid) == sid or (sid, int(tid)) in blocked:
                continue
            cand.append((float(row_max[tids == tid].max()), int(tid)))
        cand.sort(reverse=True)
        cand = cand[:limit]
        if cand and covers is None:
            covers = _covers(s, [int(t) for t in uniq])
        for score, tid in cand:
            t = named[tid]
            result[sid].append({
                "identity_id": tid, "display_name": t.display_name, "name": t.name,
                "cover_url": media_url((covers or {}).get(tid)), "score": round(score, 4),
                "likely": score >= thr - 0.10,
            })
    return result


def suggestions(s: Session, ident: Identity, limit: int = 3) -> list[dict[str, Any]]:
    return _suggest_for(s, ident.kind, [ident.id], limit).get(ident.id, [])


def best_suggestions(s: Session, items: list[Identity]) -> dict[int, Optional[dict[str, Any]]]:
    """Für Listen: bester Vorschlag mit likely=true je unbenannter face/pet-Identität, sonst None."""
    out: dict[int, Optional[dict[str, Any]]] = {i.id: None for i in items}
    for kind in ("face", "pet"):
        src = [i.id for i in items if i.kind == kind and not i.name]
        for sid, sugg in _suggest_for(s, kind, src, 1).items():
            if sugg and sugg[0]["likely"]:
                out[sid] = sugg[0]
    return out


# ---------------------------------------------------------------- Aufräumen


def cleanup_orphans(s: Session, days: int) -> list[tuple[int, str]]:
    """Löscht unbenannte, nicht gesperrte Identitäten ohne Events, die seit `days` Tagen nicht gesehen wurden.

    Rückgabe: [(id, kind)] der gelöschten Identitäten.
    """
    cutoff = utcnow() - timedelta(days=days)
    has_events = select(Event.id).where(Event.identity_id == Identity.id).exists()
    with _lock:
        rows = s.scalars(select(Identity).where(
            (Identity.name.is_(None)) | (Identity.name == ""),
            Identity.learning_locked.is_(False),
            Identity.last_seen < cutoff,
            ~has_events,
        )).all()
        gone = [(i.id, i.kind) for i in rows]
        for i in rows:
            s.delete(i)
        if gone:
            s.commit()
            for iid, _ in gone:
                delete_identity_files(iid)
            invalidate()
    for iid, kind in gone:
        hub.broadcast({"type": "identity", "identity": {"id": iid, "kind": kind, "deleted": True}})
    return gone
