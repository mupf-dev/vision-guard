"""B3 Tagesbericht – Bericht bauen, speichern, versenden (gemeinsam für Scheduler und REST)."""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .. import settings_store
from ..config import config
from ..db import SessionLocal
from ..models import Identity, Report, utcnow
from ..schemas import iso
from ..ws import hub
from . import deliver as _deliver
from .facts import collect_facts
from .render import period_text, render_llm, render_template, sections

log = logging.getLogger("vg.report")


def report_json(r: Report, full: bool = False) -> dict[str, Any]:
    data = {"id": r.id, "kind": r.kind, "period_start": iso(r.period_start), "period_end": iso(r.period_end),
            "created_at": iso(r.created_at), "text": r.text, "generator": r.generator,
            "delivery": dict(r.delivery or {})}
    if full:
        data["html"] = r.html
        data["facts"] = dict(r.facts or {})
    return data


def known_names(db: Session) -> list[str]:
    """Alle vergebenen Namen (Personen, Tiere, Kennzeichen, Exemplare) – für die Plausibilitätsprüfung."""
    return [n for n in db.scalars(select(Identity.name).where(Identity.name.is_not(None)).distinct()).all() if n]


def build(db: Session, since: datetime, until: datetime, *, kind: str = "manual") -> dict[str, Any]:
    """Bericht (ungespeichert): {kind, period_start, period_end, facts, text, html, generator}."""
    facts = collect_facts(db, since, until)
    st = settings_store.get()
    if st.get("report_generator") == "llm" and (st.get("llm_provider") or "none") != "none":
        text, html, generator, meta = render_llm(facts, known_names(db), kind)
        facts["generation"] = meta
    else:
        text, html = render_template(facts, kind)
        generator = "template"
        if st.get("report_generator") == "llm":
            facts["generation"] = {"requested": "llm", "ok": False,
                                   "error": "Kein Sprachmodell eingerichtet (Einstellungen → KI-Sprachmodell)"}
    facts["sections"] = [{"key": k, "title": t, "text": s} for k, t, s in sections(facts)]
    return {"kind": kind, "period_start": since, "period_end": until, "facts": facts, "text": text, "html": html,
            "generator": generator}


def preview_json(data: dict[str, Any]) -> dict[str, Any]:
    return {"id": None, "kind": data["kind"], "period_start": iso(data["period_start"]),
            "period_end": iso(data["period_end"]), "created_at": iso(utcnow()), "text": data["text"],
            "html": data["html"], "generator": data["generator"], "delivery": {}, "facts": data["facts"]}


def title_of(r: dict[str, Any]) -> str:
    head, _ = period_text(r.get("facts") or {"period": {"end": r.get("period_end")}})
    return f"Tagesbericht {head}" if r.get("kind") == "daily" else f"Bericht {head}"


def generate(since: datetime, until: datetime, *, kind: str = "manual", period_key: Optional[str] = None,
             deliver: bool = False) -> Optional[dict[str, Any]]:
    """Bericht bauen, speichern, optional versenden; WS `report`. None = `period_key` existiert schon."""
    with SessionLocal() as db:
        if period_key and db.scalar(select(Report.id).where(Report.period_key == period_key)) is not None:
            return None
        data = build(db, since, until, kind=kind)
        r = Report(kind=kind, period_key=period_key, period_start=data["period_start"],
                   period_end=data["period_end"], created_at=utcnow(), facts=data["facts"], text=data["text"],
                   html=data["html"], generator=data["generator"], delivery={})
        db.add(r)
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            log.info("Bericht %s existiert bereits", period_key)
            return None
        db.refresh(r)
        out = report_json(r, full=True)
    log.info("Bericht %s (%s, %s) erzeugt: %d Zeichen", out["id"], kind, out["generator"], len(out["text"]))
    if deliver:
        facts = out.get("facts") or {}
        date = (facts.get("period") or {}).get("date") or until.astimezone(config.tz).date().isoformat()
        delivery = _deliver.deliver(out, date=date, counts=facts.get("counts") or {}, title=title_of(out))
        with SessionLocal() as db:
            r = db.get(Report, out["id"])
            if r is not None:
                r.delivery = delivery
                db.commit()
        out["delivery"] = delivery
    hub.broadcast({"type": "report", "report": {k: v for k, v in out.items() if k != "facts"}})
    return out


def utc(dt: datetime) -> datetime:
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)
