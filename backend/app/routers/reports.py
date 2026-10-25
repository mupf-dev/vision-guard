"""Tagesberichte – REST (W2, B3). Vertrag: CONTRACT.md W2.G.

`GET` braucht Scope `read`, `POST /generate` Scope `write` (Methodenregel in `require_user`).
`signed=1` signiert die `*_url`-Felder in den Fakten (zentral über `SignedUrlsMiddleware`).
"""
import asyncio
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..auth import require_user
from ..db import SessionLocal, get_db
from ..inventory_diff import WhenError, parse_when
from ..models import Report
from ..report import service
from ..schemas import ReportGenerateIn

router = APIRouter(prefix="/api/reports", tags=["reports"], dependencies=[Depends(require_user)])

MAX_SPAN = timedelta(days=31)

report_json = service.report_json   # Rückwärtskompatibel (Gerüst K2)


def _period(since: Optional[str], until: Optional[str]) -> tuple[datetime, datetime]:
    """Zeitraum aus B2-Zeitangaben (ISO, 3h, today, yesterday …); Default heute 00:00 bis jetzt."""
    now = datetime.now(timezone.utc)
    try:
        t0 = parse_when(since or "today", now)
        t1 = parse_when(until, now) if until else now
    except WhenError as e:
        raise HTTPException(422, str(e)) from None
    t1 = min(t1, now)
    if t0 >= t1:
        raise HTTPException(422, "„since“ muss vor „until“ bzw. vor jetzt liegen")
    if t1 - t0 > MAX_SPAN:
        raise HTTPException(422, "Zeitraum höchstens 31 Tage")
    return t0, t1


@router.get("")
def list_reports(db: Session = Depends(get_db), limit: int = Query(30, ge=1, le=365), offset: int = Query(0, ge=0)):
    total = db.scalar(select(func.count()).select_from(Report)) or 0
    rows = db.scalars(select(Report).order_by(Report.period_end.desc(), Report.id.desc())
                      .limit(limit).offset(offset)).all()
    return {"items": [service.report_json(r) for r in rows], "total": total}


def _preview(t0: datetime, t1: datetime) -> dict:
    with SessionLocal() as db:
        return service.preview_json(service.build(db, t0, t1, kind="manual"))


@router.get("/preview")
async def preview(since: Optional[str] = None, until: Optional[str] = None,
                  signed: Optional[int] = Query(None, description="1 = Bild-URLs in den Fakten signieren")):
    """Bericht für den Zeitraum ohne Speichern (Default heute 00:00 bis jetzt). Mit Sprachmodell bis
    `llm_timeout_s`."""
    t0, t1 = _period(since, until)
    return await asyncio.to_thread(_preview, t0, t1)


@router.post("/generate")
async def generate(body: ReportGenerateIn):
    """Bericht erzeugen und speichern (`kind: "manual"`), mit `deliver: true` auch über alle Kanäle versenden."""
    t0, t1 = _period(body.since, body.until)
    out = await asyncio.to_thread(service.generate, t0, t1, kind="manual", deliver=body.deliver)
    if out is None:   # nur bei period_key möglich – hier nie
        raise HTTPException(409, "Bericht existiert bereits")
    return out


@router.get("/{report_id}")
def get_report(report_id: int, db: Session = Depends(get_db),
               signed: Optional[int] = Query(None, description="1 = Bild-URLs in den Fakten signieren")):
    r = db.get(Report, report_id)
    if r is None:
        raise HTTPException(404, "Bericht nicht gefunden")
    return service.report_json(r, full=True)


@router.delete("/{report_id}", status_code=204)
def delete_report(report_id: int, db: Session = Depends(get_db)):
    r = db.get(Report, report_id)
    if r is None:
        raise HTTPException(404, "Bericht nicht gefunden")
    db.delete(r)
    db.commit()
