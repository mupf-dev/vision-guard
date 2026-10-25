"""Hinweise (Notifications) und Push-Test – CONTRACT.md „Nachträge: Welle 1“."""
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from fastapi.concurrency import run_in_threadpool
from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import notifications, push, settings_store
from ..auth import require_user
from ..db import get_db
from ..models import Notification
from ..schemas import PushTarget, PushTestIn
from ..serializers import notification_json

router = APIRouter(prefix="/api", tags=["notifications"], dependencies=[Depends(require_user)])


def _csv(v: Optional[str]) -> list[str]:
    return [x.strip() for x in (v or "").split(",") if x.strip()]


@router.get("/notifications")
def list_notifications(
    source: Optional[str] = Query(None, description="kommagetrennt: rule,health,pet,review,report,sensor"),
    level: Optional[str] = Query(None, description="kommagetrennt: info,warning,alarm"),
    rule_id: Optional[int] = None,
    camera_id: Optional[int] = None,
    since: Optional[datetime] = None,
    until: Optional[datetime] = None,
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    signed: bool = Query(False, description="image_url signieren (Middleware)"),
    db: Session = Depends(get_db),
):
    q = select(Notification)
    if _csv(source):
        q = q.where(Notification.source.in_(_csv(source)))
    if _csv(level):
        q = q.where(Notification.level.in_(_csv(level)))
    if rule_id is not None:
        q = q.where(Notification.rule_id == rule_id)
    if camera_id is not None:
        q = q.where(Notification.camera_id == camera_id)
    if since is not None:
        q = q.where(Notification.ts >= since)
    if until is not None:
        q = q.where(Notification.ts <= until)
    total = db.scalar(select(func.count()).select_from(q.subquery())) or 0
    rows = db.scalars(q.order_by(Notification.ts.desc(), Notification.id.desc()).limit(limit).offset(offset)).all()
    return {"items": [notification_json(n) for n in rows], "total": total}


@router.get("/notifications/{notification_id}")
def get_notification(notification_id: int, signed: bool = False, db: Session = Depends(get_db)):
    n = db.get(Notification, notification_id)
    if n is None:
        raise HTTPException(404, "Hinweis nicht gefunden")
    return notification_json(n)


@router.delete("/notifications/{notification_id}", status_code=204)
async def delete_notification(notification_id: int):
    if not await run_in_threadpool(notifications.delete, notification_id):
        raise HTTPException(404, "Hinweis nicht gefunden")
    return Response(status_code=204)


@router.post("/settings/push_test")
async def push_test(body: PushTestIn):
    """Testnachricht an ein Push-Ziel: gespeichert (`target`) oder ungespeichert (`config`). → {ok, error, target}"""
    stored = list(settings_store.get().get("push_targets") or [])
    if body.config is not None:
        try:
            target = PushTarget(**notifications.unmask_target(body.config, stored)).model_dump()
        except ValidationError as e:
            raise HTTPException(422, "; ".join(str(err.get("msg")) for err in e.errors())) from None
        target["enabled"] = True   # Test auch für (noch) deaktivierte Ziele
    elif body.target:
        target = next((t for t in stored if t.get("id") == body.target), None)
        if target is None:
            raise HTTPException(404, f"Push-Ziel „{body.target}“ nicht gefunden")
        target = {**target, "enabled": True}
    else:
        raise HTTPException(422, "target oder config angeben")
    msg = push.PushMessage(title=body.title, message=body.message, tags=["white_check_mark"])
    res = await run_in_threadpool(push.send, target, msg)
    return {"ok": bool(res.get("ok")), "error": res.get("error"), "target": target.get("id")}
