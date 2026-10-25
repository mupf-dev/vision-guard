"""Haustier-Tagebuch – REST (W2/C3). Vertrag: CONTRACT.md W2.F."""
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.concurrency import run_in_threadpool
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import pet_diary
from ..auth import require_user
from ..db import get_db
from ..models import PetVisit

router = APIRouter(prefix="/api/pets", tags=["pets"], dependencies=[Depends(require_user)])


@router.get("/visits")
def list_visits(identity_id: Optional[int] = Query(None, description="Tier-Identität; 0 = unbekannte Tiere"),
                purpose: Optional[str] = Query(None, description="kommagetrennt: food,water,litter"),
                camera_id: Optional[int] = None, label: Optional[str] = None,
                since: Optional[datetime] = None, until: Optional[datetime] = None,
                limit: int = Query(100, ge=1, le=1000), offset: int = Query(0, ge=0),
                signed: bool = Query(False, description="thumb_url signieren (Middleware)"),
                db: Session = Depends(get_db)):
    """{items:[PetVisit], total} – neueste zuerst."""
    q = select(PetVisit)
    if identity_id is not None:
        q = q.where(PetVisit.identity_id.is_(None)) if identity_id == 0 else q.where(PetVisit.identity_id == identity_id)
    purposes = [p.strip() for p in (purpose or "").split(",") if p.strip()]
    if purposes:
        bad = [p for p in purposes if p not in pet_diary.PURPOSES]
        if bad:
            raise HTTPException(422, f"Unbekannter Zweck: {', '.join(bad)} (erlaubt: food, water, litter)")
        q = q.where(PetVisit.purpose.in_(purposes))
    if camera_id is not None:
        q = q.where(PetVisit.camera_id == camera_id)
    if label:
        q = q.where(PetVisit.label == label)
    if since is not None:
        q = q.where(PetVisit.start_ts >= since)
    if until is not None:
        q = q.where(PetVisit.start_ts <= until)
    total = db.scalar(select(func.count()).select_from(q.subquery())) or 0
    rows = list(db.scalars(q.order_by(PetVisit.start_ts.desc(), PetVisit.id.desc()).limit(limit).offset(offset)))
    return {"items": pet_diary.visits_json(db, rows), "total": total}


@router.get("/summary")
async def summary(days: int = Query(14, ge=1, le=90)):
    """[{identity, label, name, purposes:{food|water|litter:{today, normal, last_ts, daily, …}}}]."""
    return await run_in_threadpool(pet_diary.summary, days)
