"""Interne Endpunkte für den Worker (von nginx gesperrt)."""
import logging

from fastapi import APIRouter, Depends
from fastapi.concurrency import run_in_threadpool
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import go2rtc, identity, settings_store
from ..auth import require_internal
from ..db import SessionLocal, get_db
from ..models import Camera, Identity
from ..schemas import IdentifyIn, IdentifyOut

# Jeder interne Router hängt an require_internal (heute ohne Wirkung, später Worker-Key-Prüfung – D3).
router = APIRouter(prefix="/api/internal", tags=["internal"], dependencies=[Depends(require_internal)])
log = logging.getLogger("vg.internal")


def resolve_inventory_vocab(labels: list[str] | None, camera_id: int | None = None,
                            label_min_scores: dict | None = None, instances: dict | None = None) -> list[dict]:
    """Vokabular einer Kamera: inventory_labels gefiltert (Reihenfolge der Settings), [] = alles.

    Sind *alle* gewählten Labels inzwischen aus dem Vokabular entfernt, gilt wie bei [] das ganze Vokabular
    (sonst würde der Worker still nichts erkennen, während die Oberfläche die Kamera als aktiv zeigt)."""
    vocab = settings_store.get().get("inventory_vocabulary") or []
    wanted = set(labels or [])

    lms = label_min_scores or {}
    inst = instances or {}

    def entry(e: dict) -> dict:
        # W2: Kamera-Schwelle je Label (D2) vor Vokabular-Wert; instances = Zahl der Exemplare (B4)
        ms = lms.get(e["label"])
        return {"label": e["label"], "name_de": e["name_de"], "prompts": list(e.get("prompts") or []),
                "min_score": float(ms) if ms is not None else e.get("min_score"),
                "instances": int(inst.get(e["label"], 0))}

    out = [entry(e) for e in vocab if not wanted or e["label"] in wanted]
    if wanted and not out:
        log.warning("Kamera %s: keines der gewählten Inventar-Labels (%s) steht noch im Vokabular – "
                    "verwende das ganze Vokabular", camera_id, ", ".join(sorted(wanted)))
        out = [entry(e) for e in vocab]
    return out


@router.get("/cameras")
def internal_cameras(db: Session = Depends(get_db)):
    out = []
    review_on = bool(settings_store.get().get("review_enabled", True))
    instances = dict(db.execute(select(Identity.item_label, func.count()).where(
        Identity.kind == "item", Identity.item_label.is_not(None)).group_by(Identity.item_label)).all())
    for c in db.scalars(select(Camera).where(Camera.enabled.is_(True)).order_by(Camera.id)).all():
        main = go2rtc.rtsp_url(go2rtc.stream_name(c.id))
        detect = go2rtc.rtsp_url(go2rtc.detect_stream_name(c.id)) if c.detect_url else main
        out.append({
            "id": c.id, "name": c.name, "enabled": c.enabled,
            "detect_url_rtsp": detect, "main_rtsp": main, "detect_fps": c.detect_fps,
            "labels": list(c.labels or []), "min_score": c.min_score,
            "face_recognition": c.face_recognition, "plate_recognition": c.plate_recognition,
            "pet_recognition": c.pet_recognition, "zones": list(c.zones or []),
            "room": c.room,
            "inventory_enabled": bool(c.inventory_enabled),
            "inventory_interval_s": c.inventory_interval_s if c.inventory_interval_s is not None else 3,
            "inventory_min_score": c.inventory_min_score if c.inventory_min_score is not None else 0.35,
            "inventory_tiling": True if c.inventory_tiling is None else bool(c.inventory_tiling),
            "inventory_vocab": resolve_inventory_vocab(c.inventory_labels, c.id,
                                                       dict(c.inventory_label_min_scores or {}), instances),
            "health_enabled": True if c.health_enabled is None else bool(c.health_enabled),
            # W2
            "pose_enabled": bool(c.pose_enabled),
            "gesture_enabled": bool(c.gesture_enabled),
            "inventory_suppress": list(c.inventory_suppress or []),
            "inventory_review": review_on,
        })
    return out


@router.get("/settings")
def internal_settings():
    from ..notifications import mask_settings
    return mask_settings(settings_store.get())   # Push-Zugangsdaten braucht der Worker nicht


def _identify(body: IdentifyIn) -> dict:
    with SessionLocal() as s:
        return identity.identify(s, body)


@router.post("/identify", response_model=IdentifyOut)
async def identify_ep(body: IdentifyIn):
    return await run_in_threadpool(_identify, body)
