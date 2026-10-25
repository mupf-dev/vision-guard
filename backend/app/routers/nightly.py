"""Identitäten 07 (Paket D): REST des Nachtlaufs unter `/api/nightly` – CONTRACT ID.H.

- `GET  /api/nightly/status`
- `GET  /api/nightly/reports?limit=20&offset=0`
- `GET  /api/nightly/reports/{id}`
- `POST /api/nightly/run {mode}`               → 202; `apply` nur Session (Bearer → 403); läuft schon → 409
- `POST /api/nightly/reports/{id}/apply {idx}` → 202; nur Session; nur fertige, nicht angewandte Vorschau, sonst 409
"""
from fastapi import APIRouter, Depends, HTTPException, Query, Request

from .. import nightly
from ..auth import require_user
from ..schemas import NightlyApplyIn, NightlyRunIn

router = APIRouter(prefix="/api/nightly", tags=["nightly"], dependencies=[Depends(require_user)])


def _session_only(request: Request) -> None:
    p = getattr(request.state, "principal", None)
    if getattr(p, "kind", "session") == "token":
        raise HTTPException(403, "Nur mit Anmeldung im Browser")


@router.get("/status")
def nightly_status():
    return nightly.status()


@router.get("/reports")
def nightly_reports(limit: int = Query(20, ge=1, le=200), offset: int = Query(0, ge=0)):
    return nightly.list_reports(limit, offset)


@router.get("/reports/{rid}")
def nightly_report(rid: int):
    r = nightly.get_report(rid)
    if r is None:
        raise HTTPException(404, "Bericht nicht gefunden")
    return r


@router.post("/run", status_code=202)
def nightly_run(body: NightlyRunIn, request: Request):
    if body.mode == "apply":
        _session_only(request)
    try:
        return nightly.start(body.mode, "manual")
    except nightly.RunningError:
        raise HTTPException(409, "Es läuft bereits ein Nachtlauf")


@router.post("/reports/{rid}/apply", status_code=202)
def nightly_apply(rid: int, body: NightlyApplyIn, request: Request):
    _session_only(request)
    try:
        return nightly.start("apply", "apply", apply_from=rid, idx=body.idx)
    except nightly.RunningError:
        raise HTTPException(409, "Es läuft bereits ein Nachtlauf")
    except nightly.ApplyError as e:
        raise HTTPException(e.status, e.detail)
