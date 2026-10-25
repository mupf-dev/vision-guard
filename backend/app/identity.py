"""Identitäts-Service: Wiedererkennung von Gesichtern, Tieren (Embeddings) und Kennzeichen (Text)."""
from __future__ import annotations

import logging
import os
import re
import shutil
import threading
import time as _time
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


def stage_sample_files(moves: list[tuple[IdentitySample, str, str]]) -> tuple[list[str], list[str], set[str]]:
    """Bilddateien beim Umhängen von Samples **vor** dem Commit an den neuen Ort kopieren (Prüfung 07).

    moves = [(Sample, alter Pfad, neuer Pfad)]; `image_path` des Samples steht schon auf dem neuen Pfad. Fehlt die
    Quelldatei, bleibt der neue Pfad (wie bisher). Schlägt das Kopieren fehl, bekommt das Sample den alten Pfad
    zurück. Rückgabe: (neue Kopien – bei Rollback löschen, alte Dateien – nach dem Commit löschen, Ordner, in denen
    noch referenzierte Dateien liegen). So zeigt die DB nach Commit oder Absturz nie auf eine fehlende Datei;
    schlimmstenfalls bleibt eine alte Kopie liegen (Ordner gelöschter Identitäten räumt `sweep_orphan_dirs` weg).
    """
    copied: list[str] = []
    old_files: list[str] = []
    keep_dirs: set[str] = set()
    for smp, old, new in moves:
        try:
            src_abs = media_path(old)
            if not os.path.exists(src_abs):
                continue
            dst_abs = media_path(new)
            os.makedirs(os.path.dirname(dst_abs), exist_ok=True)
            shutil.copy2(src_abs, dst_abs)
            copied.append(new)
            old_files.append(old)
        except Exception as e:  # noqa: BLE001
            log.warning("Bilddatei nicht kopiert (Sample %s): %s", smp.id, e)
            smp.image_path = old
            keep_dirs.add(os.path.dirname(old))
    return copied, old_files, keep_dirs


def sweep_orphan_dirs(min_age_s: float = 3600.0) -> int:
    """Ordner `identities/<id>` ohne Identität (z. B. nach Absturz zwischen Commit und Aufräumen) entfernen.
    Nur Ordner, die seit `min_age_s` unverändert sind (eine gerade angelegte Identität ist evtl. noch nicht
    committet). Rückgabe: Anzahl entfernter Ordner."""
    from .db import SessionLocal
    try:
        base = media_path("identities")
        names = [n for n in os.listdir(base) if n.isdigit()]
    except (OSError, ValueError):
        return 0
    if not names:
        return 0
    with SessionLocal() as s:
        have = set(s.scalars(select(Identity.id).where(Identity.id.in_([int(n) for n in names]))).all())
    now, gone = _time.time(), 0
    for n in names:
        p = os.path.join(base, n)
        try:
            if int(n) in have or now - os.path.getmtime(p) < min_age_s:
                continue
        except OSError:
            continue
        with _lock:   # gegen gleichzeitiges Anlegen/Zurücksetzen (gleiche ID) absichern
            with SessionLocal() as s:
                if s.get(Identity, int(n)) is not None:
                    continue
            shutil.rmtree(p, ignore_errors=True)
            gone += 1
    return gone


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


def prune_samples(s: Session, identity_id: int, limit: Optional[int] = None,
                  defer_remove: Optional[list[str]] = None) -> list[int]:
    """Kürzt auf `limit` (Standard MAX_SAMPLES): zuerst fallen automatische Samples (niedrigste Qualität zuerst),
    dann manuelle. Rückgabe: IDs der entfernten Samples.

    `defer_remove`: Bilddateien nicht sofort löschen, sondern ihre Pfade anhängen (Identitäten 07: Dateioperationen
    erst nach dem Commit).

    Identitäten 07 (ID.D): Gesichter kürzen auf `limit` bzw. `sample_limit` (benannt 40) per Vielfalts-Auswahl
    (`face_match.select_gallery`); manuelle und negative Bilder bleiben, über dem Limit fallen danach manuelle
    nach Qualität (wie bisher).
    """
    ident = s.get(Identity, identity_id)
    if ident is not None and ident.kind == "face":
        return _prune_face(s, ident, limit=limit, defer_remove=defer_remove)
    samples = s.scalars(
        select(IdentitySample).where(IdentitySample.identity_id == identity_id)
        .order_by(IdentitySample.manual.desc(), IdentitySample.quality.desc(), IdentitySample.id.desc())
    ).all()
    gone: list[int] = []
    for smp in samples[(MAX_SAMPLES if limit is None else int(limit)):]:
        if defer_remove is not None:
            if smp.image_path:
                defer_remove.append(smp.image_path)
        else:
            _remove_media(smp.image_path)
        gone.append(smp.id)
        s.delete(smp)
    return gone


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
    if kind == "face":   # Identitäten 07 (Paket C): Top-3-Abgleich, Phasen, Qualitätsgrenzen, Vielfalts-Galerie
        return _identify_face(s, req, settings)
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


# ---------------------------------------------------------------- Identitäten 07 (Paket C): Gesichter
#
# CONTRACT ID.F. Ablauf je Anfrage (nur kind="face"):
#   1. Abgleich: Score je Identität = Mittel der besten 3 Bild-Ähnlichkeiten (face_match.top_k_scores),
#      Schwelle je Person (match_threshold ?? face_match_threshold), Mindestabstand face_match_margin zum
#      Zweitbesten – sonst "ambiguous" (keine Zuordnung, nichts gelernt, Rückfrage identity_match). Ausnahme:
#      Bester und Zweitbester sind untereinander ≥ nightly_auto_merge_min ähnlich (Fragmente einer Person, nicht
#      beide benannt) → Zuordnung (benannte bevorzugt) und Rückfrage.
#   2. Phase: provisional = nur bestehende, nie lernen; final = maßgeblich für das Event (auch beendet, ≤ 10 min),
#      ersetzt bzw. entfernt die Identität und gewinnt gegen spätere Track-Meldungen; single = Altverhalten.
#   3. Neu anlegen nur über face_new_min_quality/face_new_min_size (unbekannte Größe = nicht prüfbar → nur Qualität)
#      und nur, wenn mindestens ein Bild als Beispiel taugt (sonst wäre die neue Identität nie wiedererkennbar).
#   4. Lernen: Bilder ≥ face_sample_min_quality, kein Profil/unscharf (face_match.is_poor), zum Track-Mittel
#      passend; Galerie per Vielfalt (face_match.select_gallery) bis sample_limit; nie bei Lernsperre.

FINAL_TTL_S = 900.0            # so lange gewinnt eine endgültige Zuordnung gegen Track-Meldungen des Workers
FINAL_EVENT_WINDOW_S = 600     # final setzt die Identität des neuesten Events der letzten 10 min
TRACK_CONSISTENCY_MIN = 0.35   # Einzelbild ↔ Track-Mittel: darunter gehört das Bild vermutlich nicht zum Track
AMBIGUOUS_ASK_EVERY_S = 3600.0

_final_lock = threading.Lock()
_final_tracks: dict[tuple[int, int], tuple[float, Optional[dict[str, Any]]]] = {}
_asked_pairs: dict[tuple[int, int], float] = {}


def final_track_identity(camera_id: Optional[int], track_id: Optional[int]) -> tuple[bool, Optional[dict[str, Any]]]:
    """(vorhanden?, Identity-Dict | None) der endgültigen Zuordnung eines Tracks – für events.py: Track-Meldungen
    des Workers mit vorläufiger Identität dürfen eine endgültige Zuordnung nicht überschreiben."""
    if camera_id is None or track_id is None:
        return False, None
    now = _time.time()
    with _final_lock:
        ent = _final_tracks.get((int(camera_id), int(track_id)))
        if ent is None:
            return False, None
        if now - ent[0] > FINAL_TTL_S:
            _final_tracks.pop((int(camera_id), int(track_id)), None)
            return False, None
        return True, ent[1]


def _remember_final(camera_id: Optional[int], track_id: Optional[int], ident: Optional[dict[str, Any]]) -> None:
    if camera_id is None or track_id is None:
        return
    now = _time.time()
    with _final_lock:
        _final_tracks[(int(camera_id), int(track_id))] = (now, ident)
        if len(_final_tracks) > 2000:
            for k in [k for k, v in _final_tracks.items() if now - v[0] > FINAL_TTL_S]:
                _final_tracks.pop(k, None)


def forget_final_identities(ids: list[int], into: Optional[dict[str, Any]] = None) -> None:
    """Nach Sammel-Zusammenführen/-Löschen (Prüfung 07): endgültige Track-Zuordnungen auf entfernte Identitäten
    auf das Ziel umschreiben (`into`) bzw. auf „ohne Identität“ setzen, damit Regeln/Live-Anzeige nicht bis zu
    FINAL_TTL_S eine gelöschte Identität weitertragen."""
    gone = {int(i) for i in ids}
    if not gone:
        return
    with _final_lock:
        for k, (ts, ref) in list(_final_tracks.items()):
            if ref is not None and ref.get("id") in gone:
                _final_tracks[k] = (ts, into)


def _ident_ref(ident: Identity) -> dict[str, Any]:
    return {"id": ident.id, "display_name": ident.display_name, "name": ident.name, "kind": ident.kind}


def _cand(x: Any, fallback_crop: Optional[str] = None) -> Optional[dict[str, Any]]:
    """Gesichts-Kandidat aus IdentifyIn bzw. IdentifySampleIn; None bei ungültigem Embedding."""
    emb = getattr(x, "embedding", None)
    if not emb:
        return None
    v = np.asarray(emb, dtype=np.float32)
    if not np.all(np.isfinite(v)) or float(np.linalg.norm(v)) == 0.0:
        return None
    return {"embedding": [float(e) for e in v], "vec": v / np.linalg.norm(v),
            "crop_path": getattr(x, "crop_path", None) or fallback_crop,
            "quality": float(getattr(x, "quality", 0) or 0), "face_size": getattr(x, "face_size", None),
            "yaw": getattr(x, "yaw", None), "pitch": getattr(x, "pitch", None),
            "sharpness": getattr(x, "sharpness", None), "day_night": getattr(x, "day_night", None),
            "source_res": getattr(x, "source_res", None)}


def _prune_face(s: Session, ident: Identity, extra_keep: Optional[set[int]] = None,
                limit: Optional[int] = None, defer_remove: Optional[list[str]] = None) -> list[int]:
    """Galerie eines Gesichts auf `limit` (Standard sample_limit) kürzen (Vielfalt). Rückgabe: gelöschte Sample-IDs.

    `defer_remove`: Bilddateien nicht sofort löschen, sondern die Pfade anhängen (Löschen nach dem Commit)."""
    from .face_match import sample_limit, select_gallery
    if limit is None:
        limit = sample_limit(ident, settings_store.get())
    pos = [x for x in s.scalars(select(IdentitySample).where(IdentitySample.identity_id == ident.id)).all()
           if not x.negative]
    if len(pos) <= limit:
        return []
    keep = set(select_gallery(pos, limit))
    manual = sorted([x for x in pos if x.manual], key=lambda x: (x.quality or 0, x.id), reverse=True)
    over_manual = {x.id for x in manual[limit:]}
    gone = []
    for smp in pos:
        if (smp.id not in keep or smp.id in over_manual) and smp.id not in (extra_keep or set()):
            if defer_remove is not None:
                if smp.image_path:
                    defer_remove.append(smp.image_path)
            else:
                _remove_media(smp.image_path)
            s.delete(smp)
            gone.append(smp.id)
    return gone


def _learn_face(s: Session, ident: Identity, cands: list[dict[str, Any]], camera_id: Optional[int],
                defer_remove: list[str]) -> bool:
    """Kandidaten in die Galerie übernehmen, soweit die Vielfalts-Auswahl sie behält. True = mind. 1 neu.
    Dateien verdrängter Bilder landen in `defer_remove` (Löschen erst nach dem Commit)."""
    from types import SimpleNamespace

    from .face_match import sample_limit, select_gallery
    if not cands or ident.learning_locked:
        return False
    limit = sample_limit(ident, settings_store.get())
    existing = [x for x in s.scalars(select(IdentitySample).where(IdentitySample.identity_id == ident.id)).all()
                if not x.negative]
    tmp = [SimpleNamespace(id=-(i + 1), embedding=c["embedding"], quality=c["quality"], manual=False,
                           negative=False, day_night=c["day_night"], yaw=c["yaw"], pitch=c["pitch"],
                           sharpness=c["sharpness"]) for i, c in enumerate(cands)]
    keep = set(select_gallery(existing + tmp, limit))
    added = False
    for i, c in enumerate(cands):
        if -(i + 1) not in keep:
            continue
        img = _copy_crop(ident.id, c["crop_path"])
        s.add(IdentitySample(identity_id=ident.id, embedding=c["embedding"], image_path=img,
                             quality=c["quality"], source="auto", face_size=c["face_size"], yaw=c["yaw"],
                             pitch=c["pitch"], sharpness=c["sharpness"], day_night=c["day_night"],
                             source_res=c["source_res"], camera_id=camera_id))
        added = True
    if added:
        manual_over = sorted([x for x in existing if x.manual], key=lambda x: (x.quality or 0, x.id),
                             reverse=True)[limit:]
        for smp in existing:
            if (smp.id not in keep and not smp.manual) or smp in manual_over:
                if smp.image_path:
                    defer_remove.append(smp.image_path)
                s.delete(smp)
    return added


def _set_final_event(s: Session, camera_id: Optional[int], track_id: Optional[int],
                     identity_id: Optional[int]) -> Optional[Event]:
    """final: Identität des neuesten Events (Kamera+Track, letzte 10 min, auch beendet) setzen bzw. entfernen.
    Eine per ReID weitergegebene Identität (attributes.identity_source == "reid") wird nicht entfernt."""
    if camera_id is None or track_id is None:
        return None
    cutoff = utcnow() - timedelta(seconds=FINAL_EVENT_WINDOW_S)
    ev = s.scalar(select(Event).where(Event.camera_id == camera_id, Event.track_id == track_id,
                                      Event.last_seen >= cutoff).order_by(Event.start_ts.desc()).limit(1))
    if ev is None or ev.identity_id == identity_id:
        return None
    if identity_id is None and (ev.attributes or {}).get("identity_source") == "reid":
        return None
    ev.identity_id = identity_id
    return ev


def _ask_ambiguous(s: Session, a_id: Optional[int], b_id: Optional[int], score: float) -> None:
    """Knappe Entscheidung zwischen zwei Identitäten → Rückfrage identity_match („dieselbe Person?“).
    Nie zwischen zwei benannten; Quelle = unbenannte (bei zwei unbenannten die jüngere). Dedup/Limits: review.py,
    zusätzlich höchstens 1×/h je Paar aus diesem Prozess."""
    if a_id is None or b_id is None or a_id == b_id:
        return
    a, b = s.get(Identity, a_id), s.get(Identity, b_id)
    if a is None or b is None or (a.name and b.name):
        return
    if a.name or (not b.name and a.id < b.id):
        src, dst = b, a
    else:
        src, dst = a, b
    key = (src.id, dst.id)
    now = _time.time()
    with _final_lock:
        if now - _asked_pairs.get(key, 0.0) < AMBIGUOUS_ASK_EVERY_S:
            return
        _asked_pairs[key] = now
    if (src.id, dst.id) in _blocked_pairs(s):
        return
    covers = _covers(s, [src.id, dst.id])
    try:
        from . import review
        review.create(
            kind="identity_match", dedup_key=f"idm:{src.id}:{dst.id}",
            question=f"Ist „{src.display_name}“ dieselbe Person wie „{dst.display_name}“?",
            priority=float(score), identity_id=dst.id, source_identity_id=src.id, score=round(float(score), 4),
            crop_path=covers.get(src.id), snapshot_path=covers.get(dst.id),
            data={"identity_kind": "face", "target_name": dst.display_name, "source_name": src.display_name,
                  "reason": "ambiguous"})
    except Exception:  # noqa: BLE001 – Rückfrage ist Beiwerk, die Erkennung darf nicht scheitern
        log.exception("Rückfrage zu knapper Gesichtszuordnung fehlgeschlagen")


def _identify_face(s: Session, req: Any, settings: dict[str, Any]) -> dict[str, Any]:
    from .face_match import decide, is_poor, mean_embedding, pair_score, threshold_for, top_k_scores
    phase = getattr(req, "phase", None) or "single"
    empty = {"identity_id": None, "display_name": None, "name": None, "score": 0.0, "is_new": False,
             "decision": "none", "second_score": None, "threshold": None, "learned": False, "phase": phase}

    cands: list[dict[str, Any]] = []
    if phase == "final":
        cands = [c for c in (_cand(x) for x in (getattr(req, "samples", None) or [])) if c is not None]
    top = _cand(req)
    if top is not None:
        vec = top["vec"]
    else:
        m = mean_embedding([c["embedding"] for c in cands])
        if m is None:
            if phase == "final":   # final ohne Gesicht: Track bleibt (bzw. wird) ohne Identität
                with _lock:
                    ev = _set_final_event(s, req.camera_id, req.track_id, None)
                    s.commit()
                _remember_final(req.camera_id, req.track_id, None)
                if ev is not None:
                    from .events import notify_event_changed
                    notify_event_changed(ev.id)
            return empty
        vec = m
    if not cands and top is not None:
        cands = [top]                      # single bzw. final ohne Einzelbilder (alter Worker)
    cands = [c for c in cands if len(c["embedding"]) == vec.shape[0]]

    margin = float(settings.get("face_match_margin", 0.05))
    new_q = float(settings.get("face_new_min_quality", 0.35))
    new_size = int(settings.get("face_new_min_size", 80))
    smp_q = float(settings.get("face_sample_min_quality", 0.30))

    is_new = learned = False
    ev: Optional[Event] = None
    ask: Optional[tuple[int, int, float]] = None
    to_remove: list[str] = []
    with _lock:
        mat, ids = _matrix(s, "face")
        scores = top_k_scores(mat, ids, vec)
        ranked = sorted(scores, key=scores.get, reverse=True)[:2]
        thr_map: dict[int, float] = {}
        rows = {i.id: i for i in s.scalars(select(Identity).where(Identity.id.in_(ranked))).all()} if ranked else {}
        for iid in ranked:
            if iid in rows:
                thr_map[iid] = threshold_for(rows[iid], settings)
        best_id, score, second, decision = decide(scores, thr_map or {0: float(settings["face_match_threshold"])},
                                                  margin)
        if decision == "ambiguous" and len(ranked) > 1:
            ask = (ranked[0], ranked[1], score)
            # Prüfung (07): Ist der Zweitbeste nur ein weiteres Fragment derselben Person (untereinander
            # ≥ nightly_auto_merge_min ähnlich, nicht beide benannt, Paar nicht mit „Nein“ beantwortet), ist er keine
            # Konkurrenz – sonst blockieren sich die Fragmente einer Person gegenseitig. Zuordnung an die benannte
            # (sofern über ihrer Schwelle), sonst an den Besten; die Rückfrage „dieselbe Person?“ bleibt.
            a, b = rows.get(ranked[0]), rows.get(ranked[1])
            if a is not None and b is not None and not (a.name and b.name) \
                    and pair_score(mat, ids, a.id, b.id) >= float(settings.get("nightly_auto_merge_min", 0.55)) \
                    and not {(a.id, b.id), (b.id, a.id)} & _blocked_pairs(s):
                pick = b if (b.name and not a.name and scores[b.id] >= thr_map.get(b.id, 1.0)) else a
                best_id, score, decision = pick.id, scores[pick.id], "match"
        thr = thr_map.get(best_id) if best_id is not None else float(settings["face_match_threshold"])
        if thr is None:
            thr = float(settings["face_match_threshold"])
        ident: Optional[Identity] = rows.get(best_id) if decision == "match" else None
        if decision == "match" and ident is None:   # Cache veraltet (Identität eben gelöscht)
            decision = "none"

        # Lernkandidaten: Qualität, kein Profil/unscharf, passend zum Track-Mittel
        def learnable(c: dict[str, Any]) -> bool:
            return (c["quality"] >= smp_q and not is_poor(c)
                    and float(np.dot(c["vec"], vec)) >= TRACK_CONSISTENCY_MIN)

        learn = [c for c in cands if learnable(c)] if phase != "provisional" else []

        now = utcnow()
        if decision == "none" and phase != "provisional":
            best_q = max((c["quality"] for c in cands), default=0.0)
            sizes = [int(c["face_size"]) for c in cands if c["face_size"] is not None]
            size_ok = (max(sizes) >= new_size) if sizes else True   # alte Worker: Größe unbekannt
            if best_q >= new_q and size_ok and learn:
                ident = Identity(kind="face", auto_number=_next_number(s, "face"), first_seen=now, last_seen=now,
                                 notes="")
                s.add(ident)
                s.flush()
                is_new = True
                decision = "new"
        if ident is not None:
            ident.last_seen = now
            if learn and not ident.learning_locked:
                if not is_new:
                    # nur Bilder lernen, die auch einzeln zur Person passen (Schutz vor Track-Vertauschung)
                    tm = mat[ids == ident.id] if mat.shape[0] and mat.shape[1] == vec.shape[0] else None
                    if tm is not None and tm.shape[0]:
                        learn = [c for c in learn if float(np.sort(tm @ c["vec"])[-3:].mean()) >= thr - margin]
                learned = _learn_face(s, ident, learn, req.camera_id, to_remove)
            if is_new and not learned:   # Galerie hat nichts übernommen (sollte nicht vorkommen) → verwerfen
                s.delete(ident)
                s.flush()
                ident, is_new, decision = None, False, "none"

        if phase == "final":
            ev = _set_final_event(s, req.camera_id, req.track_id, ident.id if ident is not None else None)
        elif ident is not None:
            has_final, _ = final_track_identity(req.camera_id, req.track_id)
            if not has_final:            # vorläufig/single überschreiben keine endgültige Zuordnung
                ev = _link_event(s, req.camera_id, req.track_id, ident.id)
        s.commit()
        for p in to_remove:
            _remove_media(p)
        if learned:
            invalidate("face")
        ref = _ident_ref(ident) if ident is not None else None

    if phase == "final":
        _remember_final(req.camera_id, req.track_id, ref)
    if is_new and ident is not None:
        hub.broadcast({"type": "identity", "identity": identity_json(s, ident)})
    if ev is not None:
        from .events import notify_event_changed  # zyklischen Import vermeiden
        notify_event_changed(ev.id)
    if ask is not None:
        _ask_ambiguous(s, ask[0], ask[1], ask[2])
    out = dict(empty)
    out.update({"score": round(float(score), 4), "decision": decision,
                "second_score": round(float(second), 4) if second is not None else None,
                "threshold": round(float(thr), 4), "learned": learned})
    if ref is not None:
        out.update({"identity_id": ref["id"], "display_name": ref["display_name"], "name": ref["name"],
                    "is_new": is_new})
    return out


def delete_identity_files(identity_id: int) -> None:
    try:
        shutil.rmtree(media_path(f"identities/{identity_id}"), ignore_errors=True)
    except ValueError:
        pass


class MergeError(ValueError):
    """Ungültige Merge-Anfrage (→ HTTP 422)."""


def merge(s: Session, src: Identity, dst: Identity, sample_ids: Optional[list[int]] = None,
          actor: str = "user") -> tuple[Identity, bool]:
    """Führt src in dst zusammen. Rückgabe: (Ziel, Quelle gelöscht?).

    sample_ids None oder alle Samples der Quelle → klassischer Merge: alle Samples und Events wandern, Quelle
    wird gelöscht. Echte Teilmenge → nur diese Samples wandern (manual=true), Quelle samt Events bleibt.
    Identitäten 07 (ID.B): ein vollständiger Merge wird in `identity_merges` protokolliert (`actor` user|review).
    """
    from .face_match import sample_limit
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
        limit = sample_limit(dst, settings_store.get())
        if dst_manual + new_manual > limit:
            raise MergeError(f"max. {limit} Samples – erst alte löschen")

        if full:   # Verlauf zum Zurücksetzen (vor dem Umhängen erfassen)
            from .identity_bulk import log_merge
            ev_ids = list(s.scalars(select(Event.id).where(Event.identity_id == src.id)).all())
            log_merge(s, src, dst, actor=actor, sample_ids=[smp.id for smp in moving], event_ids=ev_ids)

        moves: list[tuple[IdentitySample, str, str]] = []
        for smp in moving:
            if smp.image_path:
                new_rel = f"identities/{dst.id}/{os.path.basename(smp.image_path)}"
                if new_rel != smp.image_path:
                    moves.append((smp, smp.image_path, new_rel))
                    smp.image_path = new_rel
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
        to_remove: list[str] = []
        pruned = set(prune_samples(s, dst.id, sample_limit(dst, settings_store.get()),   # Name kann neu sein
                                   defer_remove=to_remove))
        # Dateien erst kopieren (gekürzte Bilder nicht), alte nach dem Commit löschen – Prüfung 07
        to_remove += [old for smp, old, _ in moves if smp.id in pruned]   # gekürzt: Datei liegt noch am alten Ort
        moves = [m for m in moves if m[0].id not in pruned]
        copied, old_files, keep_dirs = stage_sample_files(moves)
        src_id = src.id
        if full:
            s.expire(src)
            s.delete(src)
        try:
            s.commit()
        except BaseException:
            s.rollback()
            for p in copied:
                _remove_media(p)
            raise
        for p in old_files + to_remove:
            _remove_media(p)
        if full:
            if f"identities/{src_id}" in keep_dirs:
                try:
                    os.rmdir(media_path(f"identities/{src_id}"))
                except (OSError, ValueError):
                    pass
            else:
                delete_identity_files(src_id)
        invalidate()
    if full:
        forget_final_identities([src_id], into=_ident_ref(dst))
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
            Identity.kind != "item",   # R1: eigene Exemplare haben nie Events – nur der Nutzer löscht sie
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
