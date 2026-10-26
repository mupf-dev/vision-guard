"""Rückfragen („Bitte bestätigen“) – REST (W2: Gerüst K2, Ausbau D2).

Vertrag: CONTRACT.md „Nachträge: Welle 2“, W2.D. Liste, Einzelabruf, Beantworten (Scope write), Schwellen je
Kamera+Label (`/review/thresholds`) und signierte Antwort-Links ohne Login (`public_router`: `GET /review/a/{id}`
zeigt eine Bestätigungsseite – kein Beantworten beim Link-Prefetch –, `POST` beantwortet mit `answer_source:"push"`).
`signed=1` wirkt über die Middleware.
"""
import html
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import i18n
from .. import review as review_svc
from .. import review_effects
from ..auth import current_principal, require_user
from ..db import get_db
from ..models import ReviewRequest
from ..schemas import ReviewAnswerIn

router = APIRouter(prefix="/api/review", tags=["review"], dependencies=[Depends(require_user)])

STATUSES = ("open", "yes", "no", "skipped", "expired")


@router.get("")
def list_reviews(db: Session = Depends(get_db), status: Optional[str] = "open", kind: Optional[str] = None,
                 camera_id: Optional[int] = None, limit: int = Query(50, ge=1, le=500),
                 offset: int = Query(0, ge=0), signed: bool = False):
    q = select(ReviewRequest)
    if status:
        sts = [x.strip() for x in status.split(",") if x.strip()]
        bad = [x for x in sts if x not in STATUSES]
        if bad:
            raise HTTPException(422, i18n.t("errors.unknown_status", status=", ".join(bad)))
        q = q.where(ReviewRequest.status.in_(sts))
    if kind:
        q = q.where(ReviewRequest.kind.in_([x.strip() for x in kind.split(",") if x.strip()]))
    if camera_id is not None:
        q = q.where(ReviewRequest.camera_id == camera_id)
    total = db.scalar(select(func.count()).select_from(q.subquery())) or 0
    if status == "open":
        q = q.order_by(ReviewRequest.priority.desc(), ReviewRequest.created_at.desc())
    else:
        q = q.order_by(ReviewRequest.created_at.desc())
    rows = db.scalars(q.limit(limit).offset(offset)).all()
    return {"items": [review_svc.review_json(r) for r in rows], "total": total, **review_svc.counts(db)}


@router.get("/thresholds")
def thresholds(camera_id: Optional[int] = None, db: Session = Depends(get_db)):
    """[{camera_id, label, name_de, base, current, auto, yes, no}] – Schwellen je Kamera+Label mit Antworten oder
    eigenem Wert. `auto:false` = von Hand gesetzt (wird nicht nachgeführt)."""
    return review_effects.thresholds(db, camera_id)


@router.get("/{review_id}")
def get_review(review_id: int, db: Session = Depends(get_db)):
    r = db.get(ReviewRequest, review_id)
    if r is None:
        raise HTTPException(404, i18n.t("errors.review_not_found"))
    return review_svc.review_json(r)


@router.post("/{review_id}/answer")
def answer_review(review_id: int, body: ReviewAnswerIn, request: Request, db: Session = Depends(get_db)):
    p = current_principal(request)
    source = "ui" if p is not None and p.kind == "session" else (body.source or "api")
    try:
        return review_svc.answer(db, review_id, body.answer, source=source, correct_label=body.correct_label)
    except review_svc.ReviewError as e:
        raise HTTPException(e.status, str(e)) from None


# ------------------------------------------------------------------ Antwort-Links ohne Login (Push)

public_router = APIRouter(prefix="/api/review/a", tags=["review"])


def _ans(a: str) -> str:
    return i18n.t("review.page.yes" if a == "yes" else "review.page.no")


def _page(title: str, body: str, status: int = 200) -> HTMLResponse:
    doc = f"""<!doctype html><html lang="{i18n.current()}"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><meta name="robots" content="noindex">
<title>{html.escape(title)} – VisionGuard</title>
<style>body{{font-family:system-ui,sans-serif;background:#0f1115;color:#e8eaed;margin:0;padding:24px;}}
main{{max-width:440px;margin:0 auto;}}h1{{font-size:20px}}p{{color:#aab;line-height:1.5}}
button{{font-size:18px;padding:12px 28px;border-radius:10px;border:0;background:#3b82f6;color:#fff;cursor:pointer}}
button.no{{background:#555}}</style></head><body><main>{body}</main></body></html>"""
    return HTMLResponse(doc, status_code=status, headers={"Cache-Control": "no-store",
                                                          "Referrer-Policy": "no-referrer"})


def _check(review_id: int, a: str, exp: Optional[str], sig: Optional[str]) -> None:
    if not review_effects.verify_answer_sig(review_id, a, exp, sig):
        raise HTTPException(403, i18n.t("errors.link_invalid"))


@public_router.get("/{review_id}", response_class=HTMLResponse)
def answer_page(review_id: int, a: str = "", exp: Optional[str] = None, sig: Optional[str] = None,
                db: Session = Depends(get_db)):
    """Bestätigungsseite (beantwortet noch nicht – Messenger und Mail-Programme rufen Links vorab auf)."""
    try:
        _check(review_id, a, exp, sig)
    except HTTPException:
        return _page(i18n.t("review.page.title_invalid"), f"<h1>{html.escape(i18n.t('review.page.invalid'))}</h1>", 403)
    r = db.get(ReviewRequest, review_id)
    if r is None:
        return _page(i18n.t("review.page.title_unknown"), f"<h1>{html.escape(i18n.t('review.page.gone'))}</h1>", 404)
    q = html.escape(r.question or "")
    if r.status != "open":
        return _page(i18n.t("review.page.title_done"), f"<h1>{q}</h1><p>{html.escape(i18n.t('review.page.done'))}</p>")
    cls = "" if a == "yes" else "no"
    label = html.escape(_ans(a))
    submit = html.escape(i18n.t("review.page.submit", answer=_ans(a)))
    return _page(i18n.t("review.page.title_confirm"), f"""<h1>{q}</h1><p>{html.escape(i18n.t("review.page.answer"))}: <b>{label}</b></p>
<form method="post"><button class="{cls}" type="submit">{submit}</button></form>""")


@public_router.post("/{review_id}")
def answer_by_link(review_id: int, request: Request, a: str = "", exp: Optional[str] = None,
                   sig: Optional[str] = None, db: Session = Depends(get_db)):
    """Beantwortet ohne Login (`answer_source:"push"`); ntfy-Aktion oder Knopf der Bestätigungsseite."""
    _check(review_id, a, exp, sig)
    try:
        out = review_svc.answer(db, review_id, a, source="push")
    except review_svc.ReviewError as e:
        if "text/html" in (request.headers.get("accept") or ""):
            return _page(i18n.t("review.page.title_failed"), f"<h1>{html.escape(str(e))}</h1>", e.status)
        raise HTTPException(e.status, str(e)) from None
    if "text/html" in (request.headers.get("accept") or ""):
        return _page(i18n.t("review.page.title_thanks"),
                     f"<h1>{html.escape(i18n.t('review.page.thanks'))}</h1><p>{html.escape(out['question'])} – "
                     f"<b>{html.escape(_ans(a))}</b> {html.escape(i18n.t('review.page.saved'))}</p>")
    return out
