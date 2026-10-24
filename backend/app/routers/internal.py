"""Interne Endpunkte für den Worker (von nginx gesperrt)."""
from fastapi import APIRouter, Depends
from fastapi.concurrency import run_in_threadpool
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import go2rtc, identity, settings_store
from ..db import SessionLocal, get_db
from ..models import Camera
from ..schemas import IdentifyIn, IdentifyOut

router = APIRouter(prefix="/api/internal", tags=["internal"])


@router.get("/cameras")
def internal_cameras(db: Session = Depends(get_db)):
    out = []
    for c in db.scalars(select(Camera).where(Camera.enabled.is_(True)).order_by(Camera.id)).all():
        main = go2rtc.rtsp_url(go2rtc.stream_name(c.id))
        detect = go2rtc.rtsp_url(go2rtc.detect_stream_name(c.id)) if c.detect_url else main
        out.append({
            "id": c.id, "name": c.name, "enabled": c.enabled,
            "detect_url_rtsp": detect, "main_rtsp": main, "detect_fps": c.detect_fps,
            "labels": list(c.labels or []), "min_score": c.min_score,
            "face_recognition": c.face_recognition, "plate_recognition": c.plate_recognition,
            "pet_recognition": c.pet_recognition, "zones": list(c.zones or []),
        })
    return out


@router.get("/settings")
def internal_settings():
    return settings_store.get()


def _identify(body: IdentifyIn) -> dict:
    with SessionLocal() as s:
        return identity.identify(s, body)


@router.post("/identify", response_model=IdentifyOut)
async def identify_ep(body: IdentifyIn):
    return await run_in_threadpool(_identify, body)
