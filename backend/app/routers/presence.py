"""Anwesenheit (W4/C2): wer ist wo – aus Gesicht, Tier-Identität und Wiedererkennung über Kameras.

Vertrag: CONTRACT.md „Nachträge: Welle 4 – C2“. Alles kommt aus der RAM-Galerie (`app/reid.py`), nichts aus der DB
außer Namen der Identitäten und Vorschaubildern der Events.
"""
from typing import Optional

from fastapi import APIRouter, Depends, Query, Response
from fastapi.concurrency import run_in_threadpool

from .. import reid
from ..auth import require_user

router = APIRouter(prefix="/api/presence", tags=["presence"], dependencies=[Depends(require_user)])


@router.get("")
async def list_presence(minutes: float = Query(60, ge=1, le=720, description="zuletzt gesehen innerhalb von n Minuten"),
                        label: Optional[str] = Query(None, description="kommagetrennt: person,dog,cat"),
                        signed: bool = Query(False, description="thumb_url signieren (Middleware)")):
    """[Presence] – aktive zuerst, dann zuletzt gesehen absteigend."""
    return await run_in_threadpool(reid.list_presence, minutes, label)


@router.get("/status")
def presence_status():
    """Zähler der RAM-Galerie (Einträge, Embeddings, Verknüpfungen) – ohne Embeddings."""
    return reid.gallery.status()


@router.delete("", status_code=204)
def clear_presence():
    """Galerie sofort leeren (alle Embeddings und Verknüpfungen verwerfen). Scope write."""
    reid.gallery.clear()
    return Response(status_code=204)
