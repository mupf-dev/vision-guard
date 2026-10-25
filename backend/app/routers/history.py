"""Zeitmaschine – REST (W3/B1). Vertrag: CONTRACT.md W3.G."""
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.concurrency import run_in_threadpool

from .. import history
from ..auth import require_user

router = APIRouter(prefix="/api/history", tags=["history"], dependencies=[Depends(require_user)])

NO_VECTOR = "Zeitmaschine nicht verfügbar (Datenbank ohne pgvector)"


def _utc(dt: Optional[datetime]) -> Optional[datetime]:
    if dt is None:
        return None
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt


@router.get("/search")
async def search(q: str = Query(min_length=1, max_length=200), camera_id: Optional[int] = None,
                 room: Optional[str] = None, since: Optional[datetime] = None, until: Optional[datetime] = None,
                 limit: int = Query(10, ge=1, le=50),
                 signed: bool = Query(False, description="thumb_url signieren (Middleware)")):
    """Textsuche („roter Karton im Flur“) → `{query, queries, model, items: HistoryEpisode[]}`, beste zuerst.
    `queries` = tatsächlich kodierte Texte (Original + englische Fassung)."""
    if not history.enabled():
        raise HTTPException(503, NO_VECTOR)
    q = q.strip()
    if not q:
        raise HTTPException(422, "Suchtext fehlt")
    try:
        return await run_in_threadpool(history.search, q, camera_id=camera_id, room=room, since=_utc(since),
                                       until=_utc(until), limit=limit)
    except history.SearchUnavailable as e:
        raise HTTPException(503, str(e)) from e


@router.get("/frames")
async def frames(camera_id: int, around: datetime, span_min: int = Query(10, ge=1, le=240),
                 signed: bool = Query(False, description="thumb_url signieren (Middleware)")):
    """Filmstreifen: gespeicherte Bilder einer Kamera um `around` (± `span_min`)."""
    if not history.enabled():
        raise HTTPException(503, NO_VECTOR)
    return await run_in_threadpool(history.frames, camera_id, _utc(around), span_min)


@router.get("/stats")
async def stats():
    """`{enabled, frames, vectors, thumb_bytes, oldest, by_camera, model}`."""
    return await run_in_threadpool(history.stats)


@router.delete("")
async def delete_history(camera_id: Optional[int] = None, before: Optional[datetime] = None):
    """Verlauf löschen (Scope `write`): alles, nur eine Kamera und/oder nur Bilder vor `before`. → `{deleted}`."""
    if not history.enabled():
        raise HTTPException(503, NO_VECTOR)
    n = await run_in_threadpool(history.delete_frames, camera_id, _utc(before))
    return {"deleted": n}
