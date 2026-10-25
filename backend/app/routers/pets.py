"""Haustier-Tagebuch – REST (W2: Gerüst K2, Umsetzung C3). Vertrag: CONTRACT.md W2.F."""
from typing import Optional

from fastapi import APIRouter, Depends, Query

from ..auth import require_user

router = APIRouter(prefix="/api/pets", tags=["pets"], dependencies=[Depends(require_user)])


@router.get("/visits")
def list_visits(identity_id: Optional[int] = None, purpose: Optional[str] = None, camera_id: Optional[int] = None,
                since: Optional[str] = None, until: Optional[str] = None, limit: int = Query(100, ge=1, le=1000),
                offset: int = Query(0, ge=0), signed: bool = False):
    """C3: {items:[PetVisit], total}."""
    return {"items": [], "total": 0}


@router.get("/summary")
def summary(days: int = Query(14, ge=1, le=90)):
    """C3: [{identity, label, purposes:{food|water|litter:{today, normal, last_ts, daily}}}]."""
    return []
