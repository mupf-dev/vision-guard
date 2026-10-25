"""Settings, Statistik, System."""
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import settings_store, state
from ..auth import require_user
from ..config import VERSION, config
from ..db import get_db, vector_available, vector_version
from ..models import Camera, Event, Identity
from ..notifications import mask_settings, unmask_push_targets
from ..schemas import SettingsPatch, iso
from ..sync import settings_changed

router = APIRouter(prefix="/api", tags=["misc"], dependencies=[Depends(require_user)])


def _out(data: dict) -> dict:
    """Push-Zugangsdaten nur als "***"; dazu read-only, ob LLM-Schlüssel per Env gesetzt sind (W2)."""
    out = mask_settings(data)
    out["llm_key_configured"] = {"openai_compat": bool(config.llm_api_key),
                                 "anthropic": bool(config.anthropic_api_key)}
    return out


@router.get("/settings")
def get_settings():
    return _out(settings_store.get())


@router.put("/settings")
def put_settings(body: SettingsPatch, db: Session = Depends(get_db)):
    from ..mqtt_bus import bus

    cur = settings_store.get()
    patch = body.model_dump(exclude_unset=True, exclude_none=True)
    if "push_targets" in patch:   # "***" → gespeicherter Wert desselben Ziels (gleiche id)
        patch["push_targets"] = unmask_push_targets(patch["push_targets"], list(cur.get("push_targets") or []))
    cur.update(patch)
    try:
        new = settings_store.save(db, cur)
    except ValidationError as e:
        raise HTTPException(422, [{k: v for k, v in err.items() if k in ("loc", "msg", "type")}
                                  for err in e.errors()]) from None
    bus.set_prefix(new["mqtt_prefix"])
    settings_changed()
    return _out(new)


@router.post("/settings/llm_test")
def llm_test():
    """W2: gespeicherten LLM-Anbieter mit einer kurzen Anfrage prüfen (Scope write, kostet ggf. Tokens)."""
    from .. import llm_client

    try:
        r = llm_client.complete([{"role": "user", "content": "Antworte nur mit dem Wort: OK"}], max_tokens=10,
                                temperature=0)
    except llm_client.LLMError as e:
        return {"ok": False, "error": str(e), "text": None, "model": None, "ms": None}
    return {"ok": True, "error": None, "text": r.text[:200], "model": r.model, "ms": r.ms}


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
        # W3 (K3): pgvector (B1 Zeitmaschine)
        "db": {"vector": vector_available(), "vector_version": vector_version()},
        "version": VERSION,
    }
