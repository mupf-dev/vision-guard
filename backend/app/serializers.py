"""ORM → JSON gemäß CONTRACT.md."""
from typing import Any, Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from . import state
from .models import Camera, Event, Identity, IdentitySample, Notification, Rule
from .schemas import iso, media_url
from .secrets_mask import mask_url


def camera_json(c: Camera) -> dict[str, Any]:
    return {
        "id": c.id,
        "name": c.name,
        "url": mask_url(c.url),
        "detect_url": mask_url(c.detect_url),
        "enabled": c.enabled,
        "detect_fps": c.detect_fps,
        "labels": list(c.labels or []),
        "min_score": c.min_score,
        "face_recognition": c.face_recognition,
        "plate_recognition": c.plate_recognition,
        "pet_recognition": c.pet_recognition,
        "zones": list(c.zones or []),
        "room": c.room,
        "inventory_enabled": bool(c.inventory_enabled),
        "inventory_interval_s": c.inventory_interval_s if c.inventory_interval_s is not None else 3,
        "inventory_labels": list(c.inventory_labels or []),
        "inventory_min_score": c.inventory_min_score if c.inventory_min_score is not None else 0.35,
        "inventory_tiling": True if c.inventory_tiling is None else bool(c.inventory_tiling),
        "health_enabled": True if c.health_enabled is None else bool(c.health_enabled),
        "status": state.status_for(c.id, c.enabled),
    }


def rule_json(r: Rule) -> dict[str, Any]:
    ident = r.identity or {}
    return {
        "id": r.id,
        "name": r.name,
        "enabled": r.enabled,
        "camera_ids": list(r.camera_ids or []),
        "zone_ids": list(r.zone_ids or []),
        "labels": list(r.labels or []),
        "min_score": r.min_score,
        "identity": {"mode": ident.get("mode", "any"), "identity_ids": list(ident.get("identity_ids") or [])},
        "trigger": r.trigger,
        "trigger_options": dict(r.trigger_options or {}),
        "min_dwell_s": r.min_dwell_s,
        "schedule": r.schedule,
        "cooldown_s": r.cooldown_s,
        "conditions": list(r.conditions or []),
        "condition_delay_s": float(r.condition_delay_s or 0),
        "actions": list(r.actions or []),
    }


def identity_summary(i: Optional[Identity]) -> Optional[dict[str, Any]]:
    if i is None:
        return None
    return {"id": i.id, "kind": i.kind, "display_name": i.display_name, "name": i.name}


def identity_json(s: Session, i: Identity) -> dict[str, Any]:
    sample_count = s.scalar(select(func.count()).select_from(IdentitySample).where(IdentitySample.identity_id == i.id)) or 0
    event_count = s.scalar(select(func.count()).select_from(Event).where(Event.identity_id == i.id)) or 0
    cover = s.scalar(
        select(IdentitySample.image_path)
        .where(IdentitySample.identity_id == i.id, IdentitySample.image_path.is_not(None))
        .order_by(IdentitySample.quality.desc(), IdentitySample.id.desc())
        .limit(1)
    )
    return {
        "id": i.id,
        "kind": i.kind,
        "name": i.name,
        "display_name": i.display_name,
        "plate_text": i.plate_text,
        "notes": i.notes or "",
        "sample_count": sample_count,
        "cover_url": media_url(cover),
        "first_seen": iso(i.first_seen),
        "last_seen": iso(i.last_seen),
        "event_count": event_count,
        "learning_locked": bool(i.learning_locked),
    }


def identities_json(s: Session, items: list[Identity]) -> list[dict[str, Any]]:
    """Batch-Variante für Listen (vermeidet N+1-Abfragen)."""
    if not items:
        return []
    ids = [i.id for i in items]
    sc = dict(s.execute(
        select(IdentitySample.identity_id, func.count()).where(IdentitySample.identity_id.in_(ids))
        .group_by(IdentitySample.identity_id)).all())
    ec = dict(s.execute(
        select(Event.identity_id, func.count()).where(Event.identity_id.in_(ids)).group_by(Event.identity_id)).all())
    covers: dict[int, str] = {}
    rows = s.execute(
        select(IdentitySample.identity_id, IdentitySample.image_path)
        .where(IdentitySample.identity_id.in_(ids), IdentitySample.image_path.is_not(None))
        .order_by(IdentitySample.identity_id, IdentitySample.quality.desc(), IdentitySample.id.desc())
    ).all()
    for iid, path in rows:
        covers.setdefault(iid, path)
    out = []
    for i in items:
        out.append({
            "id": i.id, "kind": i.kind, "name": i.name, "display_name": i.display_name,
            "plate_text": i.plate_text, "notes": i.notes or "", "sample_count": sc.get(i.id, 0),
            "cover_url": media_url(covers.get(i.id)), "first_seen": iso(i.first_seen),
            "last_seen": iso(i.last_seen), "event_count": ec.get(i.id, 0),
            "learning_locked": bool(i.learning_locked),
        })
    return out


def event_json(e: Event) -> dict[str, Any]:
    return {
        "id": e.id,
        "camera_id": e.camera_id,
        "camera_name": e.camera_name,
        "track_id": e.track_id,
        "label": e.label,
        "score": round(float(e.score or 0), 4),
        "start_ts": iso(e.start_ts),
        "end_ts": iso(e.end_ts),
        "zones": list(e.zones or []),
        "identity": identity_summary(e.identity),
        "attributes": dict(e.attributes or {}),
        "thumb_url": media_url(e.thumb_path),
        "snapshot_url": media_url(e.snapshot_path),
        "clip_url": media_url(e.clip_path),
        "rules": list(e.rules or []),
        "active": bool(e.active),
    }


def notification_json(n: Notification) -> dict[str, Any]:
    return {
        "id": n.id,
        "ts": iso(n.ts),
        "source": n.source,
        "level": n.level,
        "rule_id": n.rule_id,
        "rule": n.rule_name,
        "title": n.title,
        "message": n.message,
        "camera_id": n.camera_id,
        "event_id": n.event_id,
        "item_ids": list(n.item_ids or []),
        "image_url": media_url(n.image_path),
        "data": dict(n.data or {}),
        "delivery": list(n.delivery or []),
    }
