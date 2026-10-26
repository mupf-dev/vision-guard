"""Fernseher (DLNA): öffentlicher, signierter Live-Strom für den Fernseher (CONTRACT.md „Nachträge: Fernseher“).

go2rtc liefert MPEG-TS/HLS ohne SPS/PPS im Strom (Reolink sendet sie nur im SDP) – das lehnt Samsung ab, ebenso
fragmentiertes MP4 als Live-Strom. Deshalb packt ffmpeg den RTSP-Strom um: Bild unverändert (`-c:v copy`), Ton nach
AAC 48 kHz Stereo, Ausgabe MPEG-TS mit Parametersätzen vor jedem Schlüsselbild.
"""
import asyncio
import logging
import time

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from .. import i18n
from .. import go2rtc, tv
from ..auth import require_user
from ..config import config
from ..db import get_db
from ..models import Camera

log = logging.getLogger("vg.tv")

# Ohne require_user: der Fernseher kann sich nicht anmelden, Zugriff nur mit gültiger Signatur.
public_router = APIRouter(prefix="/api/tv", tags=["tv"])
# Verwaltung (TV.C): GET Scope read, sonst write
router = APIRouter(prefix="/api/tv", tags=["tv"], dependencies=[Depends(require_user)])


class TvProbeIn(BaseModel):
    description_url: str = Field(min_length=1, max_length=300)


class TvShowIn(BaseModel):
    camera_id: int
    duration_s: float = Field(60, ge=10, le=3600)


@router.get("")
def tv_list():
    return [tv.status_of(t) for t in tv.targets()]


@router.post("/probe")
async def tv_probe(body: TvProbeIn):
    return await run_in_threadpool(tv.probe, body.description_url)


@router.post("/{target_id}/show")
async def tv_show(target_id: str, body: TvShowIn, db: Session = Depends(get_db)):
    if db.get(Camera, body.camera_id) is None:
        raise HTTPException(404, i18n.t("errors.camera_not_found"))
    if tv.find_target(target_id) is None:
        raise HTTPException(404, i18n.t("errors.tv_not_found"))
    try:
        return await run_in_threadpool(tv.show, target_id, body.camera_id, body.duration_s)
    except tv.TvError as e:
        if "tv_stream_base_url" in str(e):
            raise HTTPException(409, str(e)) from e
        raise HTTPException(502, str(e)) from e


@router.post("/{target_id}/stop")
async def tv_stop(target_id: str):
    if tv.find_target(target_id) is None:
        raise HTTPException(404, i18n.t("errors.tv_not_found"))
    try:
        return await run_in_threadpool(tv.stop_target, target_id)
    except tv.TvError as e:
        raise HTTPException(502, str(e)) from e

MAX_STREAMS = 4
# Samsung (und viele DLNA-Geräte) spielen nur mit bekannter Länge; ein Live-Strom gibt sich als sehr lange Datei aus
FAKE_LENGTH = str(50 * 1024 ** 3)
_active = 0
HEADERS = {"Cache-Control": "no-store", "transferMode.dlna.org": "Streaming",
           "contentFeatures.dlna.org": tv.CONTENT_FEATURES}


def _check(cam_id: int, exp: int, t: str, sig: str) -> None:
    if not tv.verify(cam_id, exp, t, sig):
        raise HTTPException(403, i18n.t("errors.link_invalid"))


@public_router.head("/stream/{cam_id}.ts", include_in_schema=False)
async def stream_head(request: Request, cam_id: int, exp: int = Query(...), t: str = Query(...), sig: str = Query(...)):
    """Fernseher prüfen den Link vorab per HEAD (Samsung: sonst UPnP 716)."""
    _check(cam_id, exp, t, sig)
    log.debug("TV HEAD %s", {k: v for k, v in request.headers.items() if k.lower() not in ("host",)})
    return Response(status_code=200, media_type="video/mp2t", headers={**HEADERS, "Content-Length": FAKE_LENGTH})


@public_router.get("/stream/{cam_id}.ts", include_in_schema=False)
async def stream(request: Request, cam_id: int, exp: int = Query(...), t: str = Query(...), sig: str = Query(...)):
    global _active
    _check(cam_id, exp, t, sig)
    log.debug("TV GET %s", {k: v for k, v in request.headers.items() if k.lower() not in ("host",)})
    if _active >= MAX_STREAMS:
        raise HTTPException(429, i18n.t("errors.tv_too_many_streams"))
    src = f"{config.go2rtc_rtsp}/{go2rtc.stream_name(cam_id)}"
    proc = await asyncio.create_subprocess_exec(
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-rtsp_transport", "tcp", "-i", src,
        "-map", "0:v:0", "-map", "0:a:0?", "-c:v", "copy", "-c:a", "aac", "-ar", "48000", "-ac", "2",
        "-f", "mpegts", "pipe:1",
        stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
    first = await proc.stdout.read(65536)
    if not first:
        await proc.wait()
        raise HTTPException(502, i18n.t("errors.camera_image_unavailable"))
    _active += 1

    async def body():
        global _active
        t0, n = time.time(), len(first)
        try:
            yield first
            while time.time() < exp:   # Link abgelaufen → Strom endet
                chunk = await proc.stdout.read(65536)
                if not chunk:
                    break
                n += len(chunk)
                yield chunk
        finally:
            _active -= 1
            log.info("TV-Strom Kamera %s beendet nach %.0f s (%.1f MB)", cam_id, time.time() - t0, n / 1e6)
            if proc.returncode is None:
                proc.kill()
            await proc.wait()

    return StreamingResponse(body(), media_type="video/mp2t", headers={**HEADERS, "Content-Length": FAKE_LENGTH})
