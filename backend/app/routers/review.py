"""Rückfragen („Bitte bestätigen“) – REST (W2: Gerüst K2, Ausbau D2).

Vertrag: CONTRACT.md „Nachträge: Welle 2“, W2.D. Fertig: Liste, Einzelabruf, Beantworten (Scope write).
D2 ergänzt: `/review/thresholds` (Inhalt), signierte Antwort-Links `/review/a/{id}` (eigener Router ohne Login,
in main.py einhängen), `signed=1` wirkt bereits über die Middleware.
"""
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import review as review_svc
from ..auth import current_principal, require_user
from ..db import get_db
from ..models import ReviewRequest
from ..schemas import ReviewAnswerIn

router = APIRouter(prefix="/api/review", tags=["review"], dependencies=[Depends(require_user)])

STATUSES = ("open", "yes", "no", "skipped", "expired")


@router.get("")
def list_reviews(db: Session = Depends(get_db), status: Optional[str] = "open", kind: Optional[str] = None,
                 camera_id: Optional[int] = None, limit: int = Query(50, ge=1, le=500),
                 offset: int = Query(0, ge=0), signed: bool = False):
    q = select(ReviewRequest)
    if status:
        sts = [x.strip() for x in status.split(",") if x.strip()]
        bad = [x for x in sts if x not in STATUSES]
        if bad:
            raise HTTPException(422, f"Unbekannter Status: {', '.join(bad)}")
        q = q.where(ReviewRequest.status.in_(sts))
    if kind:
        q = q.where(ReviewRequest.kind.in_([x.strip() for x in kind.split(",") if x.strip()]))
    if camera_id is not None:
        q = q.where(ReviewRequest.camera_id == camera_id)
    total = db.scalar(select(func.count()).select_from(q.subquery())) or 0
    if status == "open":
        q = q.order_by(ReviewRequest.priority.desc(), ReviewRequest.created_at.desc())
    else:
        q = q.order_by(ReviewRequest.created_at.desc())
    rows = db.scalars(q.limit(limit).offset(offset)).all()
    return {"items": [review_svc.review_json(r) for r in rows], "total": total, **review_svc.counts(db)}


@router.get("/thresholds")
def thresholds(camera_id: Optional[int] = None):
    """D2: [{camera_id, label, base, current, auto, yes, no}] – bis zur Umsetzung leer."""
    return []


@router.get("/{review_id}")
def get_review(review_id: int, db: Session = Depends(get_db)):
    r = db.get(ReviewRequest, review_id)
    if r is None:
        raise HTTPException(404, "Rückfrage nicht gefunden")
    return review_svc.review_json(r)


@router.post("/{review_id}/answer")
def answer_review(review_id: int, body: ReviewAnswerIn, request: Request, db: Session = Depends(get_db)):
    p = current_principal(request)
    source = "ui" if p is not None and p.kind == "session" else (body.source or "api")
    try:
        return review_svc.answer(db, review_id, body.answer, source=source, correct_label=body.correct_label)
    except review_svc.ReviewError as e:
        raise HTTPException(e.status, str(e)) from None
