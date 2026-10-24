from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

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
        raise HTTPException(404, "Regel nicht gefunden")
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
