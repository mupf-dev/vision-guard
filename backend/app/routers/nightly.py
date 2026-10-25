"""Identitäten 07 (Paket D): REST des Nachtlaufs unter `/api/nightly` – CONTRACT ID.H.

- `GET  /api/nightly/status`
- `GET  /api/nightly/reports?limit=20&offset=0`
- `GET  /api/nightly/reports/{id}`
- `POST /api/nightly/run {mode}`               → 202; `apply` nur Session (Bearer → 403); läuft schon → 409
- `POST /api/nightly/reports/{id}/apply {idx}` → 202; nur Session; nur fertige, nicht angewandte Vorschau, sonst 409

Noch Gerüst – Umsetzung in Paket D (Endpunkte antworten 501).
"""
from fastapi import APIRouter, Depends, HTTPException, Query

from ..auth import require_user
from ..schemas import NightlyApplyIn, NightlyRunIn

router = APIRouter(prefix="/api/nightly", tags=["nightly"], dependencies=[Depends(require_user)])

_TODO = "Nachtlauf noch nicht umgesetzt"


@router.get("/status")
def nightly_status():
    raise HTTPException(501, _TODO)


@router.get("/reports")
def nightly_reports(limit: int = Query(20, ge=1, le=200), offset: int = Query(0, ge=0)):
    raise HTTPException(501, _TODO)


@router.get("/reports/{rid}")
def nightly_report(rid: int):
    raise HTTPException(501, _TODO)


@router.post("/run", status_code=202)
def nightly_run(body: NightlyRunIn):
    raise HTTPException(501, _TODO)


@router.post("/reports/{rid}/apply", status_code=202)
def nightly_apply(rid: int, body: NightlyApplyIn):
    raise HTTPException(501, _TODO)
