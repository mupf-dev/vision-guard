"""Settings, Statistik, System."""
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import settings_store, state
from ..auth import require_user
from ..config import VERSION
from ..db import get_db
from ..models import Camera, Event, Identity
from ..schemas import SettingsPatch, iso
from ..sync import settings_changed

router = APIRouter(prefix="/api", tags=["misc"], dependencies=[Depends(require_user)])


@router.get("/settings")
def get_settings():
    return settings_store.get()


@router.put("/settings")
def put_settings(body: SettingsPatch, db: Session = Depends(get_db)):
    from ..mqtt_bus import bus

    cur = settings_store.get()
    cur.update(body.model_dump(exclude_unset=True, exclude_none=True))
    try:
        new = settings_store.save(db, cur)
    except ValidationError as e:
        raise HTTPException(422, [{k: v for k, v in err.items() if k in ("loc", "msg", "type")}
                                  for err in e.errors()]) from None
    bus.set_prefix(new["mqtt_prefix"])
    settings_changed()
    return new


@router.get("/stats")
def stats(hours: int = Query(24, ge=1, le=24 * 365), db: Session = Depends(get_db)):
    since = datetime.now(timezone.utc) - timedelta(hours=hours)
    by_label = dict(db.execute(select(Event.label, func.count()).where(Event.start_ts >= since)
                               .group_by(Event.label)).all())
    by_camera = {str(k): v for k, v in db.execute(select(Event.camera_id, func.count())
                                                  .where(Event.start_ts >= since).group_by(Event.camera_id)).all()}
    bucket = "hour" if hours <= 72 else "day"
    b = func.date_trunc(bucket, Event.start_ts)
    timeline = [
        {"ts": iso(ts), "label": label, "count": cnt}
        for ts, label, cnt in db.execute(
            select(b, Event.label, func.count()).where(Event.start_ts >= since)
            .group_by(b, Event.label).order_by(b)).all()
    ]
    identities_new = db.scalar(select(func.count()).select_from(Identity).where(Identity.first_seen >= since)) or 0
    return {"by_label": by_label, "by_camera": by_camera, "timeline": timeline,
            "identities_new": identities_new, "hours": hours, "bucket": bucket}


@router.get("/system")
def system(db: Session = Depends(get_db)):
    cams = db.scalars(select(Camera).order_by(Camera.id)).all()
    return {
        "cameras": [{"id": c.id, "name": c.name, "status": state.status_for(c.id, c.enabled)} for c in cams],
        "worker": state.worker_info(),
        "mqtt": {"connected": state.mqtt_connected},
        "version": VERSION,
    }
