"""Tagesberichte – REST (W2: Gerüst K2, Umsetzung B3). Vertrag: CONTRACT.md W2.G."""
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..auth import require_user
from ..db import get_db
from ..models import Report
from ..schemas import ReportGenerateIn, iso

router = APIRouter(prefix="/api/reports", tags=["reports"], dependencies=[Depends(require_user)])


def report_json(r: Report, full: bool = False) -> dict:
    data = {"id": r.id, "kind": r.kind, "period_start": iso(r.period_start), "period_end": iso(r.period_end),
            "created_at": iso(r.created_at), "text": r.text, "generator": r.generator,
            "delivery": dict(r.delivery or {})}
    if full:
        data["html"] = r.html
        data["facts"] = dict(r.facts or {})
    return data


@router.get("")
def list_reports(db: Session = Depends(get_db), limit: int = Query(30, ge=1, le=365), offset: int = Query(0, ge=0)):
    total = db.scalar(select(func.count()).select_from(Report)) or 0
    rows = db.scalars(select(Report).order_by(Report.period_start.desc(), Report.id.desc())
                      .limit(limit).offset(offset)).all()
    return {"items": [report_json(r) for r in rows], "total": total}


@router.get("/preview")
def preview(since: str | None = None, until: str | None = None):
    """B3: Vorschau ohne Speichern (Scope read)."""
    raise HTTPException(501, "Tagesbericht ist noch nicht umgesetzt")


@router.post("/generate")
def generate(body: ReportGenerateIn):
    """B3: Bericht erzeugen und speichern (Scope write)."""
    raise HTTPException(501, "Tagesbericht ist noch nicht umgesetzt")


@router.get("/{report_id}")
def get_report(report_id: int, db: Session = Depends(get_db)):
    r = db.get(Report, report_id)
    if r is None:
        raise HTTPException(404, "Bericht nicht gefunden")
    return report_json(r, full=True)


@router.delete("/{report_id}", status_code=204)
def delete_report(report_id: int, db: Session = Depends(get_db)):
    r = db.get(Report, report_id)
    if r is None:
        raise HTTPException(404, "Bericht nicht gefunden")
    db.delete(r)
    db.commit()
