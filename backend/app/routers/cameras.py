import logging

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from fastapi.concurrency import run_in_threadpool
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import go2rtc, settings_store
from .. import health as camera_health
from ..auth import require_user
from ..db import get_db
from ..events import processor
from ..models import Camera
from ..media_sign import sign_url
from ..schemas import CameraIn, CameraTestIn, CameraTestOut, HealthReferenceIn
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
                          "inventory_min_score", "inventory_tiling", "health_enabled",
                          # W2
                          "pose_enabled", "gesture_enabled", "inventory_label_min_scores", "inventory_suppress",
                          # W3
                          "vlm_cloud_allowed", "history_enabled", "history_interval_s", "history_tiling",
                          # W4
                          "reid_enabled")


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
    if "inventory_label_min_scores" in data:   # W2 (D2): nur Vokabular-Schlüssel (gespeicherte Altlasten fallen weg)
        scores = data.get("inventory_label_min_scores") or {}
        keep = set(_check_inventory_labels(list(scores), list((c.inventory_label_min_scores or {}).keys())))
        data["inventory_label_min_scores"] = {k: v for k, v in scores.items() if k in keep}
    if "inventory_suppress" in data:
        data["inventory_suppress"] = [x.model_dump() for x in body.inventory_suppress]
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
    await run_in_threadpool(camera_health.drop_camera, cam_id)
    from .. import history as time_machine     # B1: Zeitmaschinen-Bilder der Kamera entfernen
    await run_in_threadpool(time_machine.drop_camera, cam_id)
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


# ------------------------------------------------------------------ Kamera-Gesundheit (C5)

@router.get("/{cam_id}/health")
def camera_health_detail(cam_id: int, hours: int = Query(168, ge=1, le=2160),
                         signed: bool = Query(False, description="Referenzbild-URLs signieren"),
                         db: Session = Depends(get_db)):
    """Zustand, Referenzbilder und Zeitleiste der Probleme (`camera_health_log`) einer Kamera."""
    c = _get(db, cam_id)
    refs = camera_health.reference_urls(cam_id)
    if signed:
        refs = {k: sign_url(v) for k, v in refs.items()}
    return {"camera_id": cam_id, "enabled": True if c.health_enabled is None else bool(c.health_enabled),
            "current": camera_health.current(cam_id), "reference_urls": refs,
            "log": camera_health.log_entries(cam_id, hours)}


@router.post("/{cam_id}/health/reference", status_code=202)
def camera_health_reference(cam_id: int, body: HealthReferenceIn | None = None, db: Session = Depends(get_db)):
    """Referenzbild aus dem aktuellen Bild neu setzen (Worker-Kommando `health_reference`).

    Wartet bis zu 8 s auf das Ergebnis: `ok` true/false (mit `error`), `null` = Worker hat noch nicht geantwortet."""
    c = _get(db, cam_id)
    if not c.enabled:
        raise HTTPException(409, "Kamera ist deaktiviert")
    return camera_health.request_reference(cam_id, (body or HealthReferenceIn()).mode)
