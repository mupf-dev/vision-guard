from typing import Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Response, UploadFile
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import embed_client, item_identity, settings_store
from .. import identity as ident_svc
from ..auth import require_user
from ..db import get_db
from ..models import Identity, IdentitySample, InventoryItem, utcnow
from .. import identity_bulk
from ..schemas import (BulkDeleteIn, BulkMergeIn, IdentityCreateIn, IdentityPatch, MergeIn, SampleFromInventoryIn,
                       iso, media_url)
from ..serializers import identities_json, identity_json
from ..util import safe_remove
from ..ws import hub
from .events import query_events

router = APIRouter(prefix="/api/identities", tags=["identities"], dependencies=[Depends(require_user)])


def _get(db: Session, iid: int) -> Identity:
    i = db.get(Identity, iid)
    if i is None:
        raise HTTPException(404, "Identität nicht gefunden")
    return i


@router.get("")
def list_identities(db: Session = Depends(get_db), kind: Optional[str] = None, named: Optional[bool] = None,
                    locked: Optional[bool] = None, with_suggestion: bool = False):
    q = select(Identity)
    if kind:
        q = q.where(Identity.kind == kind)
    if locked is not None:
        q = q.where(Identity.learning_locked.is_(locked))
    if named is True:
        q = q.where(Identity.name.is_not(None), Identity.name != "")
    elif named is False:
        q = q.where((Identity.name.is_(None)) | (Identity.name == ""))
    items = list(db.scalars(q.order_by(Identity.last_seen.desc())).all())
    data = identities_json(db, items)
    if with_suggestion:
        best = ident_svc.best_suggestions(db, items)
        for d in data:
            d["suggestion"] = best.get(d["id"])
    return data


@router.post("", status_code=201)
def create_identity(body: IdentityCreateIn, db: Session = Depends(get_db)):
    """W2 (B4): eigenes Exemplar anlegen (nur `kind:"item"`); Fotos danach über `/samples`."""
    vocab = {e.get("label") for e in (settings_store.get().get("inventory_vocabulary") or [])}
    if body.item_label not in vocab:
        raise HTTPException(422, f"Unbekannter Gegenstand: {body.item_label}")
    now = utcnow()
    i = Identity(kind="item", item_label=body.item_label, name=body.name.strip() or None,
                 auto_number=ident_svc._next_number(db, "item"), first_seen=now, last_seen=now, notes="")
    db.add(i)
    db.commit()
    db.refresh(i)
    item_identity.changed()
    data = identity_json(db, i)
    hub.broadcast({"type": "identity", "identity": data})
    return data


# ---------------------------------------------------------------- Identitäten 07 (Paket A, CONTRACT ID.B/ID.C)
# Diese Routen müssen VOR den Routen mit `/{iid}` stehen (sonst passt z. B. GET /merges auf GET /{iid} → 422).


def _bulk_call(fn, *args, **kw):
    try:
        return fn(*args, **kw)
    except identity_bulk.BulkError as e:
        raise HTTPException(e.status, e.detail) from None
    except NotImplementedError:
        raise HTTPException(501, "Noch nicht umgesetzt") from None


@router.post("/bulk-delete")
def bulk_delete(body: BulkDeleteIn, db: Session = Depends(get_db)):
    return {"deleted": _bulk_call(identity_bulk.bulk_delete, db, body.ids)}


@router.post("/bulk-merge")
def bulk_merge(body: BulkMergeIn, db: Session = Depends(get_db)):
    target, merged, merge_ids = _bulk_call(identity_bulk.bulk_merge, db, body.ids, body.into_id, actor="bulk")
    return {"target": identity_json(db, target), "merged": merged, "merge_ids": merge_ids}


@router.get("/merges")
def list_merges(db: Session = Depends(get_db), identity_id: Optional[int] = None, actor: Optional[str] = None,
                undone: Optional[bool] = None, limit: int = Query(50, ge=1, le=500), offset: int = Query(0, ge=0)):
    return _bulk_call(identity_bulk.list_merges, db, identity_id=identity_id, actor=actor, undone=undone,
                      limit=limit, offset=offset)


@router.post("/merges/{merge_id}/undo")
def undo_merge(merge_id: int, db: Session = Depends(get_db)):
    restored, target = _bulk_call(identity_bulk.undo_merge, db, merge_id)
    return {"restored": identity_json(db, restored), "target": identity_json(db, target)}


def _parse_bbox(v: Optional[str]) -> Optional[list[float]]:
    if v is None or not v.strip():
        return None
    try:
        b = [float(x) for x in v.split(",")]
    except ValueError:
        raise HTTPException(422, "bbox muss \"x1,y1,x2,y2\" (normiert) sein") from None
    if len(b) != 4 or not all(0.0 <= x <= 1.0 for x in b) or b[2] <= b[0] or b[3] <= b[1]:
        raise HTTPException(422, "bbox muss \"x1,y1,x2,y2\" (normiert) sein")
    return b


def _sample_call(fn, db: Session, i: Identity, *args):
    try:
        out = fn(db, i, *args)
    except embed_client.EmbedUnavailable as e:
        raise HTTPException(503, "Analyse-Dienst nicht erreichbar" if "ausgelastet" not in str(e) else str(e)) from None
    except embed_client.EmbedError as e:
        raise HTTPException(422, f"Bild nicht auswertbar: {e}") from None
    except item_identity.SampleError as e:
        raise HTTPException(422, str(e)) from None
    db.refresh(i)
    hub.broadcast({"type": "identity", "identity": identity_json(db, i)})
    return out


@router.post("/{iid}/samples", status_code=201)
def upload_sample(iid: int, file: UploadFile = File(...), bbox: Optional[str] = Form(None),
                  db: Session = Depends(get_db)):
    """W2 (B4): Foto (jpeg/png/webp ≤ 10 MB) als Sample eines eigenen Gegenstands; optional `bbox` „x1,y1,x2,y2“."""
    i = _get(db, iid)
    box = _parse_bbox(bbox)
    data = file.file.read(item_identity.MAX_UPLOAD_BYTES + 1)
    return _sample_call(item_identity.add_upload_sample, db, i, data, box)


@router.post("/{iid}/samples/from-inventory", status_code=201)
def sample_from_inventory(iid: int, body: SampleFromInventoryIn, db: Session = Depends(get_db)):
    """W2 (B4): aktuellen Crop eines erkannten Gegenstands als Sample übernehmen."""
    i = _get(db, iid)
    item = db.get(InventoryItem, body.inventory_item_id)
    if item is None:
        raise HTTPException(404, "Gegenstand nicht gefunden")
    return _sample_call(item_identity.add_inventory_sample, db, i, item)


@router.get("/{iid}")
def get_identity(iid: int, db: Session = Depends(get_db)):
    return identity_json(db, _get(db, iid))


@router.patch("/{iid}")
def patch_identity(iid: int, body: IdentityPatch, db: Session = Depends(get_db)):
    i = _get(db, iid)
    fields = body.model_fields_set
    if "name" in fields:
        name = (body.name or "").strip()
        i.name = name or None
    if "notes" in fields:
        i.notes = body.notes or ""
    if "learning_locked" in fields and body.learning_locked is not None:
        i.learning_locked = bool(body.learning_locked)
    db.commit()
    if i.kind == "item":
        item_identity.invalidate()
    data = identity_json(db, i)
    hub.broadcast({"type": "identity", "identity": data})
    return data


@router.delete("/{iid}", status_code=204)
def delete_identity(iid: int, db: Session = Depends(get_db)):
    i = _get(db, iid)
    kind = i.kind
    if kind == "item":   # Zuordnungen der Gegenstände lösen
        from sqlalchemy import update
        db.execute(update(InventoryItem).where(InventoryItem.identity_id == iid)
                   .values(identity_id=None, identity_score=None))
    if kind == "pet":   # W2 (C3): Tagebuch-Besuche bleiben als „unbekanntes Tier“
        from ..pet_diary import on_identity_deleted
        on_identity_deleted(db, iid)
    db.delete(i)
    db.commit()
    ident_svc.delete_identity_files(iid)
    ident_svc.invalidate(kind)
    if kind == "item":
        item_identity.changed()
    hub.broadcast({"type": "identity", "identity": {"id": iid, "kind": kind, "deleted": True}})
    return Response(status_code=204)


@router.post("/{iid}/merge")
def merge_identity(iid: int, body: MergeIn, db: Session = Depends(get_db)):
    if body.into_id == iid:
        raise HTTPException(422, "Quelle und Ziel sind identisch")
    src = _get(db, iid)
    dst = _get(db, body.into_id)
    if src.kind != dst.kind:
        raise HTTPException(422, "Nur Identitäten gleicher Art können zusammengeführt werden")
    try:
        dst, src_deleted = ident_svc.merge(db, src, dst, body.sample_ids)
    except ident_svc.MergeError as e:
        db.rollback()
        raise HTTPException(422, str(e)) from e
    if dst.kind == "item":
        if src_deleted:
            from sqlalchemy import update
            db.execute(update(InventoryItem).where(InventoryItem.identity_id == iid).values(identity_id=dst.id))
            db.commit()
        item_identity.changed()
    data = identity_json(db, dst)
    if src_deleted:
        hub.broadcast({"type": "identity", "identity": {"id": iid, "kind": data["kind"], "deleted": True,
                                                         "merged_into": dst.id}})
        hub.broadcast({"type": "identity", "identity": data})
    else:
        # Teil-Merge: Quelle bleibt mit restlichen Samples und ihren Events bestehen
        hub.broadcast({"type": "identity", "identity": data})
        hub.broadcast({"type": "identity", "identity": identity_json(db, _get(db, iid))})
    return data


@router.get("/{iid}/suggestions")
def identity_suggestions(iid: int, db: Session = Depends(get_db), limit: int = Query(3, ge=1, le=20)):
    return ident_svc.suggestions(db, _get(db, iid), limit)


@router.get("/{iid}/samples")
def list_samples(iid: int, db: Session = Depends(get_db)):
    _get(db, iid)
    rows = db.scalars(select(IdentitySample).where(IdentitySample.identity_id == iid)
                      .order_by(IdentitySample.quality.desc(), IdentitySample.id.desc())).all()
    return [{"id": s.id, "image_url": media_url(s.image_path), "created_at": iso(s.created_at),
             "quality": s.quality, "manual": bool(s.manual),
             "negative": bool(s.negative), "source": s.source or "auto",
             # Identitäten 07 (ID.D): Bildmerkmale, bei Altdaten null
             "face_size": s.face_size, "yaw": s.yaw, "pitch": s.pitch, "sharpness": s.sharpness,
             "day_night": s.day_night, "source_res": s.source_res, "camera_id": s.camera_id} for s in rows]


@router.delete("/{iid}/samples/{sid}", status_code=204)
def delete_sample(iid: int, sid: int, db: Session = Depends(get_db)):
    i = _get(db, iid)
    smp = db.get(IdentitySample, sid)
    if smp is None or smp.identity_id != iid:
        raise HTTPException(404, "Sample nicht gefunden")
    path = smp.image_path
    db.delete(smp)
    db.commit()
    safe_remove(path)
    ident_svc.invalidate(i.kind)
    if i.kind == "item":
        item_identity.changed()
    hub.broadcast({"type": "identity", "identity": identity_json(db, i)})
    return Response(status_code=204)


@router.get("/{iid}/events")
def identity_events(iid: int, db: Session = Depends(get_db), limit: int = Query(50, ge=1, le=1000),
                    offset: int = Query(0, ge=0)):
    _get(db, iid)
    return query_events(db, identity_id=iid, limit=limit, offset=offset)
