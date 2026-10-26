"""Geschützte Auslieferung von /media/* und signierte Medien-URLs (CONTRACT.md Nachtrag C).

Zugriff: Cookie | Bearer-Key (Scope read) | Signatur `?exp=&sig=`. Übergangsschalter Env `VG_MEDIA_PUBLIC=1`.
"""
import json
import os
from datetime import datetime, timezone
from typing import Optional
from urllib.parse import parse_qs

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from .. import i18n
from ..auth import bearer_key, principal_from_key, require_read, session_principal
from ..media_sign import normalize_path, sign_media_path, sign_urls, verify_media_sig
from ..util import media_path

MEDIA_PUBLIC = os.getenv("VG_MEDIA_PUBLIC", "0").strip().lower() in ("1", "true", "yes")

router = APIRouter(tags=["media"])


def _check_access(request: Request, rel: str) -> None:
    if MEDIA_PUBLIC:
        return
    if session_principal(request.cookies) is not None:
        return
    key = bearer_key(request.headers)
    if key and principal_from_key(key) is not None:   # jeder gültige Key hat mindestens `read`
        return
    q = request.query_params
    if q.get("sig") is not None or q.get("exp") is not None:
        if verify_media_sig(rel, q.get("exp"), q.get("sig")):
            return
        raise HTTPException(403, i18n.t("errors.link_invalid"))
    raise HTTPException(401, i18n.t("errors.not_logged_in"), headers={"WWW-Authenticate": "Bearer"})


@router.api_route("/media/{path:path}", methods=["GET", "HEAD"], include_in_schema=False)
def get_media(path: str, request: Request):
    rel = normalize_path(path)
    try:
        full = media_path(rel)
    except ValueError:
        raise HTTPException(404, i18n.t("errors.not_found"))
    _check_access(request, rel)
    if not rel or not os.path.isfile(full):
        raise HTTPException(404, i18n.t("errors.not_found"))
    # Range-Anfragen (Clips im <video>) beantwortet FileResponse selbst mit 206
    return FileResponse(full, headers={"Cache-Control": "private, max-age=600"})


class MediaSignIn(BaseModel):
    paths: list[str] = Field(..., min_length=1, max_length=200)
    ttl_s: int = Field(3600, ge=60, le=604800)


@router.post("/api/media/sign", tags=["media"])
def sign_media(body: MediaSignIn, _user: str = Depends(require_read)):
    urls: dict[str, str] = {}
    for p in body.paths:
        rel = normalize_path(p)
        try:
            media_path(rel)
        except ValueError:
            raise HTTPException(422, i18n.t("errors.invalid_media_path", path=p))
        if not rel:
            raise HTTPException(422, i18n.t("errors.invalid_media_path", path=p))
        urls[p] = sign_media_path(rel, body.ttl_s)
    exp = datetime.fromtimestamp(int(datetime.now(timezone.utc).timestamp()) + body.ttl_s, timezone.utc)
    return {"urls": urls, "expires_at": exp.isoformat().replace("+00:00", "Z")}


# ------------------------------------------------------------------ ?signed=1 für alle JSON-Antworten unter /api


def _wants_signed(query_string: bytes) -> bool:
    if b"signed" not in (query_string or b""):
        return False
    try:
        vals = parse_qs(query_string.decode("latin-1")).get("signed") or []
    except Exception:  # noqa: BLE001
        return False
    return any(v.strip().lower() in ("1", "true", "yes") for v in vals)


class SignedUrlsMiddleware:
    """Bei `?signed=1` werden in JSON-Antworten unter /api/ alle Felder `*_url` mit '/media/…' signiert
    (TTL = Setting `media_link_ttl_h`). So brauchen die einzelnen Router keine eigene Logik."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if (scope.get("type") != "http" or not scope.get("path", "").startswith("/api/")
                or scope.get("path", "").startswith("/api/internal/")
                or not _wants_signed(scope.get("query_string", b""))):
            await self.app(scope, receive, send)
            return
        start: Optional[dict] = None
        chunks: list[bytes] = []
        passthrough = False

        async def _send(message):
            nonlocal start, passthrough
            if message["type"] == "http.response.start":
                headers = {k.lower(): v for k, v in message.get("headers", [])}
                ctype = headers.get(b"content-type", b"")
                if message.get("status") != 200 or not ctype.startswith(b"application/json"):
                    passthrough = True
                    await send(message)
                else:
                    start = message
                return
            if message["type"] != "http.response.body" or passthrough:
                await send(message)
                return
            chunks.append(message.get("body", b""))
            if message.get("more_body"):
                return
            body = b"".join(chunks)
            try:
                body = json.dumps(sign_urls(json.loads(body)), ensure_ascii=False, allow_nan=False,
                                  separators=(",", ":")).encode("utf-8")
            except Exception:  # noqa: BLE001 – im Zweifel unverändert ausliefern
                pass
            headers = [(k, v) for k, v in start.get("headers", []) if k.lower() != b"content-length"]
            headers.append((b"content-length", str(len(body)).encode()))
            await send({**start, "headers": headers})
            await send({"type": "http.response.body", "body": body, "more_body": False})

        await self.app(scope, receive, _send)
