from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import i18n
from .. import rules as rules_mod
from ..auth import require_user
from ..db import get_db
from ..models import Rule
from ..schemas import RuleIn
from ..serializers import rule_json
from ..sync import rules_changed

router = APIRouter(prefix="/api/rules", tags=["rules"], dependencies=[Depends(require_user)])


def _get(db: Session, rule_id: int) -> Rule:
    r = db.get(Rule, rule_id)
    if r is None:
        raise HTTPException(404, i18n.t("errors.rule_not_found"))
    return r


def _apply(r: Rule, body: RuleIn) -> None:
    for k, v in body.model_dump(mode="json").items():
        setattr(r, k, v)


@router.get("")
def list_rules(db: Session = Depends(get_db)):
    return [rule_json(r) for r in db.scalars(select(Rule).order_by(Rule.id)).all()]


@router.post("", status_code=201)
def create_rule(body: RuleIn, db: Session = Depends(get_db)):
    r = Rule()
    _apply(r, body)
    db.add(r)
    db.commit()
    db.refresh(r)
    rules_changed()
    return rule_json(r)


@router.post("/check")
def check_rule(body: RuleIn):
    """A1: Bedingungen eines (auch ungespeicherten) Regel-Entwurfs jetzt prüfen – ohne Aktionen auszuführen.
    → {ok, reason, details:[{type, ok, reason}], placeholders:{items,item_place,weather}, item_ids, preview}"""
    rule = {"id": 0, **body.model_dump(mode="json")}
    res = rules_mod.evaluate_conditions(
        rule, rules_mod.CondContext(rule=rule, now=datetime.now(timezone.utc)), all_details=True)
    details = []
    for d in res.details:
        spec = rules_mod.CONDITIONS.get(d["type"])
        if spec is not None and spec.track_only:
            d = {**d, "ok": None, "reason": i18n.t("rules.check.track_only")}
        details.append(d)
    ok = all(d["ok"] is not False for d in details)
    ph = {"items": "", "item_place": "", "weather": "", **{k: v for k, v in res.ctx.items()
                                                         if k in ("items", "item_place", "weather")}}
    ctx = {**ph, "rule": rule["name"]}
    preview = None
    push = next((a for a in rule.get("actions") or [] if a.get("type") == "push"), None)
    if push is not None:
        preview = {"title": rules_mod.render(push.get("title") or "{rule}", ctx),
                   "message": rules_mod.render(push.get("message") or "", ctx)}
    return {"ok": ok, "reason": "; ".join(d["reason"] for d in details if d.get("reason")),
            "details": details, "placeholders": ph, "item_ids": res.item_ids if ok else [],
            "preview": preview}


@router.get("/{rule_id}")
def get_rule(rule_id: int, db: Session = Depends(get_db)):
    return rule_json(_get(db, rule_id))


@router.put("/{rule_id}")
def update_rule(rule_id: int, body: RuleIn, db: Session = Depends(get_db)):
    r = _get(db, rule_id)
    _apply(r, body)
    db.commit()
    db.refresh(r)
    rules_changed()
    return rule_json(r)


@router.delete("/{rule_id}", status_code=204)
def delete_rule(rule_id: int, db: Session = Depends(get_db)):
    db.delete(_get(db, rule_id))
    db.commit()
    rules_changed()
    return Response(status_code=204)
