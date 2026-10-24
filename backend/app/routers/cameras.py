import logging

from fastapi import APIRouter, Depends, HTTPException, Response
from fastapi.concurrency import run_in_threadpool
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import go2rtc, settings_store
from ..auth import require_user
from ..db import get_db
from ..events import processor
from ..models import Camera
from ..schemas import CameraIn, CameraTestIn, CameraTestOut
from ..secrets_mask import unmask_url
from ..serializers import camera_json
from ..sync import cameras_changed

log = logging.getLogger("vg.cameras")
router = APIRouter(prefix="/api/cameras", tags=["cameras"], dependencies=[Depends(require_user)])


def _get(db: Session, cam_id: int) -> Camera:
    c = db.get(Camera, cam_id)
    if c is None:
        raise HTTPException(404, "Kamera nicht gefunden")
    return c


def _check_inventory_labels(labels: list[str], previous: list[str] | None = None) -> list[str]:
    """Nur Schlüssel aus dem Settings-Vokabular (Reihenfolge wie gesendet, ohne Dubletten). Schlüssel, die
    schon gespeichert waren und inzwischen aus dem Vokabular entfernt wurden, fallen still weg."""
    known = {e.get("label") for e in (settings_store.get().get("inventory_vocabulary") or [])}
    out: list[str] = []
    for lb in labels:
        lb = (lb or "").strip()
        if lb not in known:
            if lb in (previous or []):
                continue
            raise HTTPException(422, f"Unbekannter Gegenstand: {lb}")
        if lb not in out:
            out.append(lb)
    return out


# Später hinzugekommene Felder: bei PUT nur übernehmen, wenn der Client sie mitschickt (ältere/externe API-Clients
# setzen sonst Raum und Inventar still auf die Defaults zurück).
OPTIONAL_UPDATE_FIELDS = ("room", "inventory_enabled", "inventory_interval_s", "inventory_labels",
                          "inventory_min_score", "inventory_tiling")


def _apply(c: Camera, body: CameraIn, update: bool = False) -> None:
    data = body.model_dump()
    if update:
        for k in OPTIONAL_UPDATE_FIELDS:
            if k not in body.model_fields_set:
                data.pop(k, None)
    data["zones"] = [z.model_dump() for z in body.zones]
    # maskierte Passwörter aus der Oberfläche durch die gespeicherten ersetzen
    data["url"] = unmask_url(data["url"], [c.url, c.detect_url])
    data["detect_url"] = unmask_url(data["detect_url"], [c.detect_url, c.url])
    if "inventory_labels" in data:
        data["inventory_labels"] = _check_inventory_labels(data.get("inventory_labels") or [],
                                                           list(c.inventory_labels or []))
    for k, v in data.items():
        setattr(c, k, v)


@router.get("")
def list_cameras(db: Session = Depends(get_db)):
    return [camera_json(c) for c in db.scalars(select(Camera).order_by(Camera.id)).all()]


@router.post("", status_code=201)
async def create_camera(body: CameraIn, db: Session = Depends(get_db)):
    c = Camera()
    _apply(c, body)
    db.add(c)
    db.commit()
    db.refresh(c)
    await cameras_changed()
    return camera_json(c)


@router.post("/test", response_model=CameraTestOut)
async def test_camera(body: CameraTestIn, db: Session = Depends(get_db)):
    stored = [u for c in db.scalars(select(Camera)).all() for u in (c.url, c.detect_url)]
    return await go2rtc.probe(unmask_url(body.url.strip(), stored))


@router.get("/{cam_id}")
def get_camera(cam_id: int, db: Session = Depends(get_db)):
    return camera_json(_get(db, cam_id))


@router.put("/{cam_id}")
async def update_camera(cam_id: int, body: CameraIn, db: Session = Depends(get_db)):
    c = _get(db, cam_id)
    _apply(c, body, update=True)
    db.commit()
    db.refresh(c)
    await cameras_changed()
    return camera_json(c)


@router.delete("/{cam_id}", status_code=204)
async def delete_camera(cam_id: int, db: Session = Depends(get_db)):
    c = _get(db, cam_id)
    db.delete(c)
    db.commit()
    processor.drop_camera(cam_id)
    from ..inventory import memory as inventory_memory
    await run_in_threadpool(inventory_memory.drop_camera, cam_id)
    await cameras_changed()
    return Response(status_code=204)


@router.get("/{cam_id}/snapshot.jpg")
async def snapshot(cam_id: int, db: Session = Depends(get_db)):
    _get(db, cam_id)
    try:
        data = await go2rtc.fetch_frame(go2rtc.stream_name(cam_id), timeout=15)
    except Exception as e:  # noqa: BLE001
        log.info("Standbild Kamera %s nicht verfügbar: %s", cam_id, e)
        raise HTTPException(502, "Kamerabild nicht verfügbar") from e
    return Response(data, media_type="image/jpeg", headers={"Cache-Control": "no-store"})
