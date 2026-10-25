from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..auth import require_user
from ..db import get_db
from ..models import Event
from ..serializers import event_json
from ..util import safe_remove
from ..ws import hub

router = APIRouter(prefix="/api/events", tags=["events"], dependencies=[Depends(require_user)])


def _utc(dt: Optional[datetime]) -> Optional[datetime]:
    if dt is not None and dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def query_events(db: Session, *, camera_id=None, label=None, identity_id=None, since=None, until=None,
                 active=None, has_snapshot=None, has_clip=None, limit=50, offset=0) -> dict:
    conds = []
    if camera_id is not None:
        conds.append(Event.camera_id == camera_id)
    if label:
        labels = [x for x in label.split(",") if x]
        conds.append(Event.label.in_(labels) if len(labels) > 1 else Event.label == labels[0])
    if identity_id is not None:
        conds.append(Event.identity_id == identity_id)
    if since is not None:
        conds.append(Event.start_ts >= _utc(since))
    if until is not None:
        conds.append(Event.start_ts <= _utc(until))
    if active is not None:
        conds.append(Event.active.is_(active))
    if has_snapshot is not None:
        conds.append(Event.snapshot_path.isnot(None) if has_snapshot else Event.snapshot_path.is_(None))
    if has_clip is not None:
        conds.append(Event.clip_path.isnot(None) if has_clip else Event.clip_path.is_(None))
    total = db.scalar(select(func.count()).select_from(Event).where(*conds)) or 0
    rows = db.scalars(select(Event).where(*conds).order_by(Event.start_ts.desc(), Event.id)
                      .limit(limit).offset(offset)).unique().all()
    return {"items": [event_json(e) for e in rows], "total": total}


@router.get("")
def list_events(
    db: Session = Depends(get_db),
    camera_id: Optional[int] = None,
    label: Optional[str] = None,
    identity_id: Optional[int] = None,
    since: Optional[datetime] = None,
    until: Optional[datetime] = None,
    active: Optional[bool] = None,
    has_snapshot: Optional[bool] = None,
    has_clip: Optional[bool] = None,
    limit: int = Query(50, ge=1, le=1000),
    offset: int = Query(0, ge=0),
):
    return query_events(db, camera_id=camera_id, label=label, identity_id=identity_id, since=since,
                        until=until, active=active,
                        has_snapshot=has_snapshot, has_clip=has_clip, limit=limit, offset=offset)


@router.get("/{event_id}")
def get_event(event_id: str, db: Session = Depends(get_db)):
    e = db.get(Event, event_id)
    if e is None:
        raise HTTPException(404, "Event nicht gefunden")
    return event_json(e)


@router.delete("/{event_id}", status_code=204)
def delete_event(event_id: str, db: Session = Depends(get_db)):
    e = db.get(Event, event_id)
    if e is None:
        raise HTTPException(404, "Event nicht gefunden")
    paths = (e.snapshot_path, e.clip_path, e.thumb_path)
    data = event_json(e)
    db.delete(e)
    db.commit()
    for p in paths:
        safe_remove(p)
    hub.broadcast({"type": "event", "action": "delete", "event": data})
    return Response(status_code=204)


@router.post("/{event_id}/ack")
def ack_event(event_id: str, db: Session = Depends(get_db)):
    """W2 (C1): Event quittieren – stoppt Wiederholungen (repeat_s). Idempotent, Scope write."""
    from ..events import processor
    from ..models import utcnow

    e = db.get(Event, event_id)
    if e is None:
        raise HTTPException(404, "Event nicht gefunden")
    if e.acknowledged_at is None:
        e.acknowledged_at = utcnow()
        db.commit()
        processor.notify_event_changed(e.id)   # WS event/update (+ MQTT extern, wie jede Änderung)
    db.refresh(e)
    return event_json(e)
