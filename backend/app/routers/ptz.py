"""PTZ-Steuerung je Kamera (CONTRACT „Nachträge: PTZ“, PTZ.C). GET Scope read, alle anderen write."""
import logging

from fastapi import APIRouter, Depends, HTTPException, Response
from fastapi.concurrency import run_in_threadpool
from sqlalchemy.orm import Session

from .. import ptz
from ..auth import require_user
from ..db import get_db
from ..models import Camera
from ..onvif import OnvifError
from ..sync import cameras_changed
from ..schemas import PtzGotoIn, PtzMoveIn, PtzPauseIn, PtzPresetIn, PtzProbeIn

log = logging.getLogger("vg.ptz")
router = APIRouter(prefix="/api/cameras/{cam_id}/ptz", tags=["ptz"], dependencies=[Depends(require_user)])


def _camera(db: Session, cam_id: int) -> Camera:
    c = db.get(Camera, cam_id)
    if c is None:
        raise HTTPException(404, "Kamera nicht gefunden")
    return c


def _controller(db: Session, cam_id: int) -> ptz.Controller:
    c = _camera(db, cam_id)
    ctl = ptz.manager.get(cam_id)
    if not c.ptz_enabled or ctl is None:
        raise HTTPException(409, "PTZ ist für diese Kamera nicht eingeschaltet")
    return ctl


async def _do(fn, *a):
    try:
        return await run_in_threadpool(fn, *a)
    except ptz.PtzUnavailable as e:
        raise HTTPException(502, str(e)) from e
    except OnvifError as e:
        raise HTTPException(502, str(e)) from e
    except ValueError as e:
        raise HTTPException(422, str(e)) from e


@router.get("")
async def ptz_info(cam_id: int, db: Session = Depends(get_db)):
    c = _camera(db, cam_id)
    ctl = ptz.manager.get(cam_id) if c.ptz_enabled else None
    if ctl is None:
        return {"camera_id": cam_id, "enabled": bool(c.ptz_enabled), "connected": False, "error": None,
                "device": None, "capabilities": None, "presets": [], "state": None}
    presets = await run_in_threadpool(ctl.presets)
    cl = ctl.client if ctl.connected else None
    return {"camera_id": cam_id, "enabled": True, "connected": ctl.connected, "error": ctl.error,
            "device": cl.device if cl else None, "capabilities": cl.caps if cl else None,
            "presets": presets, "state": ctl.state()}


@router.post("/probe")
async def ptz_probe(cam_id: int, body: PtzProbeIn | None = None, db: Session = Depends(get_db)):
    c = _camera(db, cam_id)
    cfg = ptz.merged_config(c.ptz)
    b = body or PtzProbeIn()
    if b.onvif_port is not None:
        cfg["onvif_port"] = b.onvif_port
    if b.onvif_user is not None:
        cfg["onvif_user"] = b.onvif_user.strip() or None
    if b.onvif_password is not None and b.onvif_password != "***":
        cfg["onvif_password"] = b.onvif_password or None
    return await run_in_threadpool(ptz.probe, c.url, cfg)


@router.post("/move")
async def ptz_move(cam_id: int, body: PtzMoveIn, db: Session = Depends(get_db)):
    ctl = _controller(db, cam_id)
    await _do(ctl.move, body.pan, body.tilt, body.zoom, body.duration_ms)
    return ctl.state()


@router.post("/stop")
async def ptz_stop(cam_id: int, db: Session = Depends(get_db)):
    ctl = _controller(db, cam_id)
    await _do(ctl.stop)
    return ctl.state()


@router.post("/goto")
async def ptz_goto(cam_id: int, body: PtzGotoIn, db: Session = Depends(get_db)):
    ctl = _controller(db, cam_id)
    await _do(ctl.goto, body.preset, body.hold_s)
    return ctl.state()


@router.post("/presets", status_code=201)
async def ptz_save_preset(cam_id: int, body: PtzPresetIn, db: Session = Depends(get_db)):
    ctl = _controller(db, cam_id)
    return await _do(ctl.save_preset, body.name.strip())


@router.post("/home")
async def ptz_set_home(cam_id: int, body: PtzPresetIn | None = None, db: Session = Depends(get_db)):
    """Aktuelle Ausrichtung als Grundposition speichern (PTZ.C) → {token, name, state}."""
    ctl = _controller(db, cam_id)
    res = await _do(ctl.set_home_here, (body.name.strip() if body else "") or "Grundposition")
    c = _camera(db, cam_id)
    c.ptz = {**dict(c.ptz or {}), "home_preset": res["token"]}
    db.commit()
    await cameras_changed()
    return {**res, "state": ctl.state()}


@router.delete("/presets/{token}", status_code=204)
async def ptz_delete_preset(cam_id: int, token: str, db: Session = Depends(get_db)):
    ctl = _controller(db, cam_id)
    cfg = ptz.merged_config(_camera(db, cam_id).ptz)
    if token == cfg.get("home_preset"):
        raise HTTPException(409, "Das ist die Grundposition – erst eine andere Grundposition wählen")
    if any(str(s.get("preset")) == token for s in cfg["patrol"].get("steps") or []):
        raise HTTPException(409, "Position wird in der Patrouille benutzt – erst dort entfernen")
    await _do(ctl.remove_preset, token)
    return Response(status_code=204)


@router.post("/resume")
async def ptz_resume(cam_id: int, db: Session = Depends(get_db)):
    ctl = _controller(db, cam_id)
    await _do(ctl.resume)
    return ctl.state()


@router.post("/pause")
async def ptz_pause(cam_id: int, body: PtzPauseIn, db: Session = Depends(get_db)):
    ctl = _controller(db, cam_id)
    await _do(ctl.pause, body.minutes)
    return ctl.state()
