"""Frag die Kamera – REST (W3/A2). Vertrag: CONTRACT.md W3.E.

Eigener Router OHNE router-weites `require_user`: `POST /cameras/{id}/ask` verlangt nur Scope `read`
(Ausnahme, siehe `auth.READ_POST_EXCEPTIONS`) und hat ein Rate-Limit von 10/min je Key.
"""
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import vlm
from ..auth import require_read_limited, require_user
from ..db import get_db
from ..models import AskLog, Camera
from ..util import safe_remove

router = APIRouter(prefix="/api", tags=["ask"])

ASK_PER_MIN = 10


class AskIn(BaseModel):
    question: str = Field(min_length=1, max_length=300)
    region: Optional[list[float]] = Field(None, min_length=4, max_length=4)
    engine: Literal["auto", "local", "cloud"] = "auto"

    @field_validator("question")
    @classmethod
    def _question(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("Frage darf nicht leer sein")
        return v

    @field_validator("region")
    @classmethod
    def _region(cls, v: Optional[list[float]]) -> Optional[list[float]]:
        if v is None:
            return None
        if not all(-0.001 <= c <= 1.001 for c in v) or v[2] <= v[0] or v[3] <= v[1]:
            raise ValueError("region muss normiert [x1,y1,x2,y2] sein")
        return [round(min(1.0, max(0.0, float(c))), 4) for c in v]


@router.post("/cameras/{cam_id}/ask")
async def ask_camera(cam_id: int, body: AskIn, request: Request,
                     _user: str = Depends(require_read_limited("ask", ASK_PER_MIN)),
                     db: Session = Depends(get_db)):
    """Frage zum aktuellen Bild → AskResult. Scope `read` genügt; 10/min je Key. Fragen am besten englisch."""
    c = db.get(Camera, cam_id)
    if c is None:
        raise HTTPException(404, "Kamera nicht gefunden")
    if not c.enabled:
        raise HTTPException(409, "Kamera ist deaktiviert")
    p = getattr(request.state, "principal", None)
    source = "api" if p is not None and p.kind == "token" else "ui"
    if request.headers.get("x-vg-source") == "mcp" and source == "api":
        source = "mcp"
    try:
        return await run_in_threadpool(vlm.ask, cam_id, body.question, region=body.region, engine=body.engine,
                                       source=source, token_name=getattr(p, "token_name", None))
    except vlm.AskError as e:
        headers = {"Retry-After": "5"} if e.status == 429 else None
        raise HTTPException(e.status, e.message, headers=headers) from None


@router.get("/ask", dependencies=[Depends(require_user)])
def ask_log(camera_id: Optional[int] = None, limit: int = Query(20, ge=1, le=200), offset: int = Query(0, ge=0),
            signed: bool = Query(False, description="snapshot_url signieren (Middleware)"),
            db: Session = Depends(get_db)):
    """{items: AskResult[], total} – beantwortete Fragen, neueste zuerst."""
    q = select(AskLog).where(AskLog.error.is_(None))
    if camera_id is not None:
        q = q.where(AskLog.camera_id == camera_id)
    total = db.scalar(select(func.count()).select_from(q.subquery())) or 0
    rows = db.scalars(q.order_by(AskLog.ts.desc(), AskLog.id.desc()).limit(limit).offset(offset)).all()
    names = dict(db.execute(select(Camera.id, Camera.name)).all())
    return {"items": [vlm.ask_json(a, names.get(a.camera_id)) for a in rows], "total": int(total)}


@router.delete("/ask/{ask_id}", status_code=204, dependencies=[Depends(require_user)])
def delete_ask(ask_id: int, db: Session = Depends(get_db)):
    a = db.get(AskLog, ask_id)
    if a is None:
        raise HTTPException(404, "Frage nicht gefunden")
    path = a.snapshot_path
    db.delete(a)
    db.commit()
    safe_remove(path)
