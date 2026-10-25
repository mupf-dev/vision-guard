from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import identity as ident_svc
from ..auth import require_user
from ..db import get_db
from ..models import Identity, IdentitySample
from ..schemas import IdentityPatch, MergeIn, iso, media_url
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
    data = identity_json(db, i)
    hub.broadcast({"type": "identity", "identity": data})
    return data


@router.delete("/{iid}", status_code=204)
def delete_identity(iid: int, db: Session = Depends(get_db)):
    i = _get(db, iid)
    kind = i.kind
    if kind == "pet":   # W2 (C3): Tagebuch-Besuche bleiben als „unbekanntes Tier“
        from ..pet_diary import on_identity_deleted
        on_identity_deleted(db, iid)
    db.delete(i)
    db.commit()
    ident_svc.delete_identity_files(iid)
    ident_svc.invalidate(kind)
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
             "negative": bool(s.negative), "source": s.source or "auto"} for s in rows]


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
    hub.broadcast({"type": "identity", "identity": identity_json(db, i)})
    return Response(status_code=204)


@router.get("/{iid}/events")
def identity_events(iid: int, db: Session = Depends(get_db), limit: int = Query(50, ge=1, le=1000),
                    offset: int = Query(0, ge=0)):
    _get(db, iid)
    return query_events(db, identity_id=iid, limit=limit, offset=offset)
