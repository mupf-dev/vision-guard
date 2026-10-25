"""Gegenstände (Inventar): Liste, Suche („Wo ist …?“), Labels, Verlauf (CONTRACT.md, Teil H)."""
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import inventory_diff
from .. import inventory_names as names
from .. import settings_store
from ..auth import require_user
from ..db import get_db
from ..inventory import answer_sentence, camera_info, history_json, item_json, memory
from ..models import Camera, InventoryHistory, InventoryItem

# `signed=1` wird von media.SignedUrlsMiddleware ausgewertet (signiert alle *_url-Felder der Antwort);
# der Parameter steht hier nur für die API-Doku.
router = APIRouter(prefix="/api/inventory", tags=["inventory"], dependencies=[Depends(require_user)])

_STATES = ("present", "missing", "stale")


def _csv(v: Optional[str]) -> list[str]:
    return [x.strip() for x in (v or "").split(",") if x.strip()]


def _vocab() -> tuple[list[dict], dict[str, dict]]:
    v = settings_store.get().get("inventory_vocabulary") or []
    return v, names.vocab_map(v)


def _sort_key(d: dict):
    return ((d.get("room") or "￿").lower(), (d.get("name_de") or "").lower(), d.get("camera_id") or 0,
            d.get("id") or 0)


@router.get("")
def list_inventory(
    db: Session = Depends(get_db),
    camera_id: Optional[int] = None,
    room: Optional[str] = None,
    label: Optional[str] = None,
    state: Optional[str] = None,
    visible: Optional[bool] = None,
    signed: bool = False,
    limit: int = Query(200, ge=1, le=1000),
    offset: int = Query(0, ge=0),
):
    conds = []
    if camera_id is not None:
        conds.append(InventoryItem.camera_id == camera_id)
    if room:
        cam_ids = list(db.scalars(select(Camera.id).where(func.lower(Camera.room) == room.strip().lower())).all())
        conds.append(InventoryItem.camera_id.in_(cam_ids or [-1]))
    labels = _csv(label)
    if labels:
        conds.append(InventoryItem.label.in_(labels))
    states = _csv(state)
    if states:
        bad = [x for x in states if x not in _STATES]
        if bad:
            raise HTTPException(422, f"Unbekannter Zustand: {bad[0]} (erlaubt: present, missing, stale)")
        conds.append(InventoryItem.state.in_(states))
    if visible is not None:
        conds.append(InventoryItem.state == "present" if visible else InventoryItem.state != "present")
    rows = db.scalars(select(InventoryItem).where(*conds)).all()
    _, vmap = _vocab()
    cams: dict[int, dict] = {}
    items = []
    for r in rows:
        cam = cams.setdefault(r.camera_id, camera_info(r.camera_id))
        items.append(item_json(r, vocab=vmap, cam=cam))
    items.sort(key=_sort_key)
    return {"items": items[offset:offset + limit], "total": len(items)}


def _search(db: Session, q: str, limit: int, signed: bool) -> dict:
    vocab, vmap = _vocab()
    custom = settings_store.get().get("inventory_synonyms") or {}
    db_labels = list(db.scalars(select(InventoryItem.label).distinct()).all())
    matches = names.match_labels(q, vocab, custom, extra_labels=db_labels)
    if not matches:
        return {"query": q, "answer": answer_sentence(db, [], q, None, vmap), "items": []}
    mby = {m["label"]: m for m in matches}
    rows = list(db.scalars(select(InventoryItem).where(InventoryItem.label.in_(list(mby)))).all())

    def key(r: InventoryItem):
        ls = r.last_seen.timestamp() if r.last_seen else 0
        return (0 if r.state == "present" else 1, -ls, -mby[r.label]["score"])

    rows.sort(key=key)
    # Antwort bezieht sich auf das am besten passende Label (gleich gut: das mit dem besten Fund)
    best = matches[0]["score"]
    cands = [m["label"] for m in matches if m["score"] >= best]
    top_label = next((r.label for r in rows if r.label in cands), matches[0]["label"])
    top_rows = [r for r in rows if r.label == top_label]
    answer = answer_sentence(db, top_rows, q, top_label, vmap)
    cams: dict[int, dict] = {}
    items = []
    for r in rows[:limit]:
        cam = cams.setdefault(r.camera_id, camera_info(r.camera_id))
        d = item_json(r, vocab=vmap, cam=cam)
        m = mby[r.label]
        d["match"] = {"score": m["score"], "via": m["via"]}
        items.append(d)
    return {"query": q, "answer": answer, "items": items}


@router.get("/search")
def search_inventory(q: str = Query(..., min_length=1, max_length=200),
                           limit: int = Query(10, ge=1, le=100), signed: bool = False,
                           db: Session = Depends(get_db)):
    return _search(db, q, limit, signed)


@router.get("/labels")
def inventory_labels(db: Session = Depends(get_db)):
    vocab, _ = _vocab()
    custom = settings_store.get().get("inventory_synonyms") or {}
    counts: dict[str, list[int]] = {}
    for lb, st, n in db.execute(select(InventoryItem.label, InventoryItem.state, func.count())
                                .group_by(InventoryItem.label, InventoryItem.state)).all():
        c = counts.setdefault(lb, [0, 0])
        c[1] += n
        if st == "present":
            c[0] += n
    out = []
    seen = set()
    for e in vocab:
        lb = e["label"]
        seen.add(lb)
        c = counts.get(lb, [0, 0])
        out.append({"label": lb, "name_de": e.get("name_de") or lb, "group": e.get("group") or "",
                    "prompts": list(e.get("prompts") or []), "synonyms": names.synonyms_for(lb, custom),
                    "count_present": c[0], "count_total": c[1]})
    for lb in sorted(set(counts) - seen):
        c = counts[lb]
        out.append({"label": lb, "name_de": names.display_name(lb), "group": "", "prompts": [],
                    "synonyms": names.synonyms_for(lb, custom), "count_present": c[0], "count_total": c[1]})
    return out


@router.get("/changes")
def inventory_changes(
    since: str = Query(..., description="ISO-8601, relativ (3h, 90m, 2d) oder today | morning | yesterday"),
    until: Optional[str] = Query(None, description="wie since; Default jetzt"),
    camera_id: Optional[int] = None,
    room: Optional[str] = None,
    label: Optional[str] = None,
    include_transient: bool = False,
    signed: bool = False,
    limit: int = Query(100, ge=1, le=1000),
    db: Session = Depends(get_db),
):
    """„Was hat sich verändert?“ – neu, weg, verschoben, Stand unklar (CONTRACT W1.E). Muss vor `/{item_id}` stehen."""
    try:
        t_since = inventory_diff.parse_when(since)
        t_until = inventory_diff.parse_when(until) if until else None
        return inventory_diff.compute_changes(db, t_since, t_until, camera_id=camera_id, room=room,
                                              labels=_csv(label) or None, include_transient=include_transient,
                                              limit=limit)
    except (inventory_diff.WhenError, inventory_diff.ChangesError) as e:
        raise HTTPException(422, str(e)) from None


def _get(db: Session, item_id: int) -> InventoryItem:
    it = db.get(InventoryItem, item_id)
    if it is None:
        raise HTTPException(404, "Gegenstand nicht gefunden")
    return it


@router.get("/{item_id}")
def get_item(item_id: int, signed: bool = False, db: Session = Depends(get_db)):
    return item_json(_get(db, item_id))


@router.delete("/{item_id}", status_code=204)
def delete_item(item_id: int, db: Session = Depends(get_db)):
    memory.delete_item(db, _get(db, item_id))
    return Response(status_code=204)


@router.get("/{item_id}/history")
def item_history(item_id: int, limit: int = Query(50, ge=1, le=500), signed: bool = False,
                 db: Session = Depends(get_db)):
    _get(db, item_id)
    rows = db.scalars(select(InventoryHistory).where(InventoryHistory.item_id == item_id)
                      .order_by(InventoryHistory.ts.desc(), InventoryHistory.id.desc()).limit(limit)).all()
    return [history_json(h) for h in rows]
