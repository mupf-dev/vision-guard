"""Zeitmaschine – REST (W3/B1). Vertrag: CONTRACT.md W3.G. Gerüst aus K3, Umsetzung: B1."""
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from .. import history
from ..auth import require_user
from ..db import get_db

router = APIRouter(prefix="/api/history", tags=["history"], dependencies=[Depends(require_user)])

NO_VECTOR = "Zeitmaschine nicht verfügbar (Datenbank ohne pgvector)"


@router.get("/search")
def search(q: str = Query(min_length=1, max_length=200), camera_id: Optional[int] = None,
           room: Optional[str] = None, since: Optional[datetime] = None, until: Optional[datetime] = None,
           limit: int = Query(10, ge=1, le=50),
           signed: bool = Query(False, description="thumb_url signieren (Middleware)")):
    """{query, model, items: HistoryEpisode[]} (B1)."""
    if not history.enabled():
        raise HTTPException(503, NO_VECTOR)
    raise HTTPException(501, "Noch nicht umgesetzt (Welle 3, B1)")


@router.get("/stats")
def stats(db: Session = Depends(get_db)):
    """{enabled, frames, vectors, thumb_bytes, oldest, by_camera} (B1)."""
    return {"enabled": history.enabled(), "frames": 0, "vectors": 0, "thumb_bytes": 0, "oldest": None,
            "by_camera": {}}
