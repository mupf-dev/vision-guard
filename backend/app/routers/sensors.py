"""Virtuelle Sensoren und Einkaufsliste – REST (W3/A3+C4). Vertrag: CONTRACT.md W3.F.

Zustandsautomat und Effekte stehen in `sensors.py`, die Einkaufsliste in `shopping.py`.
"""
from datetime import datetime, timezone
from typing import Any, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import i18n
from .. import sensors as vs
from .. import settings_store, shopping
from ..auth import require_user
from ..db import get_db
from ..models import Camera, SensorHistory, SensorReference, VirtualSensor

router = APIRouter(prefix="/api", tags=["sensors"], dependencies=[Depends(require_user)])

# Defaults je Art (Intervall in s; Parameter)
KIND_INTERVAL = {"brightness": 5.0, "blink": 5.0, "reference": 10.0, "inventory_count": 60.0, "fill": 60.0,
                 "vlm": 300.0}
VLM_MIN_INTERVAL = 60.0
VLM_MEASURES = ("yesno", "count", "fill")
VLM_SUPPLY_MIN_AGE = 1800.0     # C4-VLM: Vorräte per Bildmodell höchstens alle 30 min ohne sichtbare Änderung


def _vlm_item(p_in: dict, q: str, default: str = "") -> dict[str, Any]:
    """C4-VLM: englischer Artikelname (`item`) für die Fragevorlage und/oder eigene Frage (`question`)."""
    item = " ".join(str(p_in.get("item") or "").split())[:80] or default
    if not item and not q:
        raise HTTPException(422, i18n.t("errors.sensor_item_or_question"))
    out: dict[str, Any] = {}
    if item:
        out["item"] = item
    if q:
        out["question"] = q
    return out


def _vocab_item(labels: list[str]) -> str:
    """Vorgabe für die Zählfrage aus dem Inventar-Vokabular: erster Prompt des ersten Labels, Plural mit -s."""
    vocab = {e.get("label"): e for e in (settings_store.get().get("inventory_vocabulary") or [])}
    for lb in labels:
        prompts = (vocab.get(lb) or {}).get("prompts") or []
        if prompts:
            p = str(prompts[0]).strip()
            return p if p.endswith("s") else p + "s"
    return labels[0].replace("_", " ") + "s" if labels else ""


class SensorIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    camera_id: int = Field(ge=1)
    enabled: bool = True
    kind: Literal["brightness", "blink", "reference", "inventory_count", "fill", "vlm"]
    region: list[list[float]] = Field(min_length=3, max_length=64)
    params: dict[str, Any] = Field(default_factory=dict)
    state_set: Optional[Literal["binary", "level"]] = None
    supply: bool = False
    verify_vlm: bool = False
    interval_s: Optional[float] = Field(None, ge=2, le=3600)
    vlm_max_age_s: float = Field(900, ge=60, le=86400)
    change_threshold: float = Field(0.08, ge=0, le=1)
    confirm_n: int = Field(2, ge=1, le=10)

    @field_validator("name")
    @classmethod
    def _name(cls, v: str) -> str:
        v = " ".join(v.split())
        if not v:
            raise ValueError(i18n.t("errors.name_missing"))
        return v

    @field_validator("region")
    @classmethod
    def _region(cls, v: list[list[float]]) -> list[list[float]]:
        out = []
        for p in v:
            if len(p) != 2 or not all(-0.001 <= float(c) <= 1.001 for c in p):
                raise ValueError(i18n.t("errors.sensor_region_points"))
            out.append([round(min(1.0, max(0.0, float(p[0]))), 4), round(min(1.0, max(0.0, float(p[1]))), 4)])
        xs, ys = [p[0] for p in out], [p[1] for p in out]
        if max(xs) - min(xs) < 0.005 or max(ys) - min(ys) < 0.005:
            raise ValueError(i18n.t("errors.sensor_region_too_small"))
        return out


def _num(p: dict, key: str, default: float, lo: float, hi: float, label: str) -> float:
    v = p.get(key, default)
    try:
        v = float(v)
    except (TypeError, ValueError):
        raise HTTPException(422, i18n.t("errors.number_expected", label=i18n.t(label))) from None
    if not lo <= v <= hi:
        raise HTTPException(422, i18n.t("errors.value_range", label=i18n.t(label), lo=f"{lo:g}", hi=f"{hi:g}"))
    return v


def _validate(body: SensorIn, db: Session) -> dict[str, Any]:
    """SensorIn → Spaltenwerte (Defaults je Art, Prüfungen mit deutschen Meldungen)."""
    if db.get(Camera, body.camera_id) is None:
        raise HTTPException(422, i18n.t("errors.camera_not_found"))
    k = body.kind
    p_in = dict(body.params or {})
    q = str(p_in.get("question") or "").strip()
    if len(q) > 300:
        raise HTTPException(422, i18n.t("errors.question_max_300"))
    params: dict[str, Any] = {}
    if k == "brightness":
        params = {"threshold": _num(p_in, "threshold", 0.5, 0, 1, "errors.sensor_param_threshold"), "invert": bool(p_in.get("invert"))}
    elif k == "blink":
        params = {"min_std": _num(p_in, "min_std", 0.05, 0.005, 1, "errors.sensor_param_min_std"),
                  "window_s": _num(p_in, "window_s", 3, 1, 30, "errors.sensor_param_window")}
    elif k == "reference":
        params = {}
    elif k == "inventory_count":
        labels = p_in.get("labels") or []
        if not isinstance(labels, list) or not labels:
            raise HTTPException(422, i18n.t("errors.sensor_select_item"))
        known = {e.get("label") for e in (settings_store.get().get("inventory_vocabulary") or [])}
        clean: list[str] = []
        for lb in labels:
            lb = str(lb).strip()
            if lb not in known:
                raise HTTPException(422, i18n.t("errors.unknown_item", item=lb))
            if lb not in clean:
                clean.append(lb)
        params = {"labels": clean, "min_count": int(_num(p_in, "min_count", 1, 1, 100, "errors.sensor_param_min_count"))}
    elif k == "fill":
        low = _num(p_in, "low_below", 0.4, 0, 1, "errors.sensor_param_low_below")
        out = _num(p_in, "out_below", 0.15, 0, 1, "errors.sensor_param_out_below")
        if out >= low:
            raise HTTPException(422, i18n.t("errors.sensor_out_below_low"))
        params = {"low_below": low, "out_below": out}
    elif k == "vlm":
        measure = str(p_in.get("measure") or "yesno")
        if measure not in VLM_MEASURES:
            raise HTTPException(422, i18n.t("errors.sensor_measure_invalid"))
        if measure == "yesno":
            if not q:
                raise HTTPException(422, i18n.t("errors.sensor_question_missing"))
        else:                                   # C4-VLM-Modus (W4): zählen bzw. Füllstand
            params = {"measure": measure, **_vlm_item(p_in, q)}
            if measure == "count":
                params["min_count"] = int(_num(p_in, "min_count", 1, 1, vs.VLM_COUNT_MAX_MIN, "errors.sensor_param_min_count"))
    if k == "inventory_count" and body.verify_vlm:
        params.update(_vlm_item(p_in, q, default=_vocab_item(params["labels"])))
        if params["min_count"] > vs.VLM_COUNT_MAX_MIN:
            raise HTTPException(422, i18n.t("errors.sensor_verify_count_max", max=vs.VLM_COUNT_MAX_MIN))
    elif q and (k == "vlm" or k in vs.VERIFY_KINDS):
        params["question"] = q
    state_set = vs.default_state_set(k, body.state_set or ("level" if body.supply else None), params)
    if body.state_set and body.state_set != state_set:
        raise HTTPException(422, i18n.t("errors.sensor_kind_states", kind=i18n.t(f"errors.sensor_kind_{k}"),
                                        states=i18n.t("errors.sensor_states_level" if state_set == "level"
                                                      else "errors.sensor_states_binary")))
    if body.supply and state_set != "level":
        raise HTTPException(422, i18n.t("errors.sensor_supply_level_only"))
    if body.verify_vlm:
        if k not in vs.VERIFY_ALL_KINDS:
            raise HTTPException(422, i18n.t("errors.sensor_verify_kinds"))
        if k in vs.VERIFY_KINDS and not q:
            raise HTTPException(422, i18n.t("errors.sensor_verify_question"))
    interval = body.interval_s if body.interval_s is not None else KIND_INTERVAL[k]
    if k == "vlm" and interval < VLM_MIN_INTERVAL:
        raise HTTPException(422, i18n.t("errors.sensor_vlm_interval"))
    if k == "vlm" and params.get("measure") in ("count", "fill") and body.vlm_max_age_s < VLM_SUPPLY_MIN_AGE:
        raise HTTPException(422, i18n.t("errors.sensor_vlm_supply_age"))
    if k == "blink" and interval < params.get("window_s", 3):
        interval = float(params["window_s"])
    return {"name": body.name, "camera_id": body.camera_id, "enabled": body.enabled, "kind": k,
            "region": body.region, "params": params, "state_set": state_set, "supply": body.supply,
            "verify_vlm": body.verify_vlm, "interval_s": float(interval), "vlm_max_age_s": body.vlm_max_age_s,
            "change_threshold": body.change_threshold, "confirm_n": body.confirm_n}


def _sensor_or_404(db: Session, sensor_id: int) -> VirtualSensor:
    r = db.get(VirtualSensor, sensor_id)
    if r is None:
        raise HTTPException(404, i18n.t("errors.sensor_not_found"))
    return r


@router.get("/sensors")
def list_sensors(camera_id: Optional[int] = None, room: Optional[str] = None,
                 kind: Optional[Literal["brightness", "blink", "reference", "inventory_count", "fill", "vlm"]] = None,
                 supply: Optional[bool] = None,
                 signed: bool = Query(False, description="snapshot_url signieren (Middleware)")):
    """VirtualSensor[] (sortiert nach id)."""
    return vs.list_json(camera_id=camera_id, room=room, kind=kind, supply=supply)


@router.post("/sensors", status_code=201)
async def create_sensor(body: SensorIn, db: Session = Depends(get_db)):
    vals = _validate(body, db)
    now = datetime.now(timezone.utc)
    r = VirtualSensor(**vals, state="unknown", created_at=now, updated_at=now)
    db.add(r)
    db.commit()
    db.refresh(r)
    return await run_in_threadpool(vs.after_change, r.id, reset=True)


@router.get("/sensors/{sensor_id}")
def get_sensor(sensor_id: int, signed: bool = Query(False, description="snapshot_url signieren (Middleware)")):
    data = vs.get_json(sensor_id)
    if data is None:
        raise HTTPException(404, i18n.t("errors.sensor_not_found"))
    return data


@router.put("/sensors/{sensor_id}")
async def update_sensor(sensor_id: int, body: SensorIn, db: Session = Depends(get_db)):
    r = _sensor_or_404(db, sensor_id)
    vals = _validate(body, db)
    reset = (vals["kind"] != r.kind or vals["state_set"] != r.state_set or vals["camera_id"] != r.camera_id
             or (vals["params"].get("measure") or "yesno") != ((r.params or {}).get("measure") or "yesno"))
    for k, v in vals.items():
        setattr(r, k, v)
    r.updated_at = datetime.now(timezone.utc)
    db.commit()
    if reset:
        await run_in_threadpool(vs.set_state_unknown, sensor_id, "sensors.reason.config_changed")
    return await run_in_threadpool(vs.after_change, sensor_id, reset=reset)


@router.delete("/sensors/{sensor_id}", status_code=204)
async def delete_sensor(sensor_id: int):
    if not await run_in_threadpool(vs.delete_sensor, sensor_id):
        raise HTTPException(404, i18n.t("errors.sensor_not_found"))
    return Response(status_code=204)


@router.post("/sensors/{sensor_id}/evaluate")
async def evaluate_sensor(sensor_id: int):
    """Sofort bewerten (ohne Hysterese): letzte Messung bzw. neu zählen / Bildmodell fragen."""
    data = await run_in_threadpool(vs.evaluate_now, sensor_id)
    if data is None:
        raise HTTPException(404, i18n.t("errors.sensor_not_found"))
    return data


@router.get("/sensors/{sensor_id}/history")
def sensor_history(sensor_id: int, limit: int = Query(50, ge=1, le=500), db: Session = Depends(get_db),
                   signed: bool = Query(False, description="snapshot_url signieren (Middleware)")):
    _sensor_or_404(db, sensor_id)
    rows = db.scalars(select(SensorHistory).where(SensorHistory.sensor_id == sensor_id)
                      .order_by(SensorHistory.ts.desc(), SensorHistory.id.desc()).limit(limit)).all()
    return [{"ts": vs._iso(r.ts.timestamp()) if r.ts else None, "state": r.state,
             "value": None if r.value is None else round(float(r.value), 4), "engine": r.engine,
             "snapshot_url": f"/media/{r.snapshot_path}" if r.snapshot_path else None} for r in rows]


class ReferenceIn(BaseModel):
    state: Literal["on", "off", "full", "half", "empty"]


def _ref_json(r: SensorReference) -> dict[str, Any]:
    return {"id": r.id, "state": r.state, "image_url": f"/media/{r.image_path}",
            "created_at": vs._iso(r.created_at.timestamp()) if r.created_at else None}


@router.get("/sensors/{sensor_id}/references")
def sensor_references(sensor_id: int, db: Session = Depends(get_db),
                      signed: bool = Query(False, description="image_url signieren (Middleware)")):
    _sensor_or_404(db, sensor_id)
    rows = db.scalars(select(SensorReference).where(SensorReference.sensor_id == sensor_id)
                      .order_by(SensorReference.id)).all()
    return [_ref_json(r) for r in rows]


@router.post("/sensors/{sensor_id}/references", status_code=201)
async def add_reference(sensor_id: int, body: ReferenceIn, db: Session = Depends(get_db)):
    """Aktuelles Kamerabild als Referenz für einen Zustand. Gespeichert wird das ganze Bild; der Worker schneidet
    den Bereich selbst aus (Bereich darf sich später ändern). Max. 5 je Zustand."""
    r = _sensor_or_404(db, sensor_id)
    allowed = vs.REF_STATES.get(r.kind)
    if not allowed:
        raise HTTPException(422, i18n.t("errors.sensor_refs_kinds"))
    if body.state not in allowed:
        raise HTTPException(422, i18n.t("errors.sensor_ref_state", states=", ".join(allowed)))
    n = db.scalar(select(func.count()).select_from(SensorReference).where(
        SensorReference.sensor_id == sensor_id, SensorReference.state == body.state)) or 0
    if n >= vs.MAX_REFS_PER_STATE:
        raise HTTPException(422, i18n.t("errors.sensor_refs_max", max=vs.MAX_REFS_PER_STATE))
    cam = db.get(Camera, r.camera_id)
    if cam is None or not cam.enabled:
        raise HTTPException(409, i18n.t("errors.camera_disabled"))
    data = await run_in_threadpool(vs.grab_jpeg, r.camera_id)
    if not data:
        raise HTTPException(502, i18n.t("errors.camera_image_unavailable"))
    now = datetime.now(timezone.utc)
    rel = f"sensors/ref/sensor{sensor_id}_{body.state}_{int(now.timestamp() * 1000)}.jpg"
    await run_in_threadpool(vs.store, rel, data)
    ref = SensorReference(sensor_id=sensor_id, state=body.state, image_path=rel, created_at=now)
    db.add(ref)
    db.commit()
    db.refresh(ref)
    await run_in_threadpool(vs.mark_reference_changed, sensor_id)
    return _ref_json(ref)


@router.delete("/sensors/{sensor_id}/references/{ref_id}", status_code=204)
async def delete_reference(sensor_id: int, ref_id: int, db: Session = Depends(get_db)):
    ref = db.get(SensorReference, ref_id)
    if ref is None or ref.sensor_id != sensor_id:
        raise HTTPException(404, i18n.t("errors.reference_not_found"))
    path = ref.image_path
    db.delete(ref)
    db.commit()
    from ..util import safe_remove
    safe_remove(path)
    await run_in_threadpool(vs.mark_reference_changed, sensor_id)
    return Response(status_code=204)


# ------------------------------------------------------------------ Einkaufsliste (C4)

class ShoppingIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    note: Optional[str] = Field(None, max_length=300)

    @field_validator("name")
    @classmethod
    def _name(cls, v: str) -> str:
        v = " ".join(v.split())
        if not v:
            raise ValueError(i18n.t("errors.name_missing"))
        return v


class ShoppingPatch(BaseModel):
    done: Optional[bool] = None
    name: Optional[str] = Field(None, min_length=1, max_length=100)
    note: Optional[str] = Field(None, max_length=300)


@router.get("/shopping-list")
def shopping_list(done: str = Query("false", pattern="^(false|true|all)$")):
    """{items: ShoppingItem[], open_count}."""
    return shopping.list_items(done)


@router.post("/shopping-list", status_code=201)
async def add_shopping_item(body: ShoppingIn, request: Request, response: Response):
    """Offener Eintrag gleichen Namens → derselbe Eintrag (200 statt 201)."""
    p = getattr(request.state, "principal", None)
    source = "mcp" if (p is not None and p.kind == "token" and request.headers.get("x-vg-source") == "mcp") else "manual"
    item, created = await run_in_threadpool(shopping.add, body.name, body.note or "", source=source)
    if not created:
        response.status_code = 200
    return item


@router.patch("/shopping-list/{item_id}")
async def update_shopping_item(item_id: int, body: ShoppingPatch):
    item = await run_in_threadpool(shopping.patch, item_id, done=body.done, name=body.name, note=body.note)
    if item is None:
        raise HTTPException(404, i18n.t("errors.entry_not_found"))
    return item


@router.delete("/shopping-list/{item_id}", status_code=204)
async def delete_shopping_item(item_id: int):
    if not await run_in_threadpool(shopping.remove, item_id):
        raise HTTPException(404, i18n.t("errors.entry_not_found"))
    return Response(status_code=204)
