"""Virtuelle Sensoren und Einkaufsliste – REST (W3/A3+C4). Vertrag: CONTRACT.md W3.F.

Gerüst aus K3 (Listen liefern leere Ergebnisse, Änderungen 501), Umsetzung: A3+C4.
"""
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from ..auth import require_user
from ..db import get_db

router = APIRouter(prefix="/api", tags=["sensors"], dependencies=[Depends(require_user)])

_TODO = "Noch nicht umgesetzt (Welle 3, A3+C4)"


@router.get("/sensors")
def list_sensors(camera_id: Optional[int] = None, room: Optional[str] = None, kind: Optional[str] = None,
                 supply: Optional[bool] = None,
                 signed: bool = Query(False, description="snapshot_url signieren (Middleware)"),
                 db: Session = Depends(get_db)):
    """VirtualSensor[] (A3)."""
    return []


@router.post("/sensors", status_code=201)
def create_sensor(db: Session = Depends(get_db)):
    raise HTTPException(501, _TODO)


@router.get("/sensors/{sensor_id}")
def get_sensor(sensor_id: int, db: Session = Depends(get_db)):
    raise HTTPException(404, "Sensor nicht gefunden")


@router.get("/shopping-list")
def shopping_list(done: str = Query("false", pattern="^(false|true|all)$"), db: Session = Depends(get_db)):
    """{items: ShoppingItem[], open_count} (C4)."""
    return {"items": [], "open_count": 0}


@router.post("/shopping-list", status_code=201)
def add_shopping_item(db: Session = Depends(get_db)):
    raise HTTPException(501, _TODO)
