"""API-Keys verwalten (nur mit Session-Cookie, siehe CONTRACT.md Nachtrag B)."""
from datetime import datetime, timedelta, timezone
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select

from ..auth import generate_key, invalidate_token_cache, normalize_scopes, require_session
from ..db import SessionLocal
from ..models import ApiToken
from ..schemas import iso

router = APIRouter(prefix="/api/tokens", tags=["tokens"], dependencies=[Depends(require_session)])


class TokenIn(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    scopes: list[Literal["read", "write"]] = Field(default_factory=lambda: ["read"])
    expires_days: Optional[int] = Field(None, ge=1, le=3650)

    @field_validator("name")
    @classmethod
    def _name(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("Name darf nicht leer sein")
        return v


def _aware(dt: Optional[datetime]) -> Optional[datetime]:
    if dt is not None and dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def token_json(t: ApiToken) -> dict:
    exp = _aware(t.expires_at)
    return {
        "id": t.id, "name": t.name, "prefix": t.prefix, "scopes": normalize_scopes(t.scopes),
        "created_at": iso(t.created_at), "last_used_at": iso(t.last_used_at), "expires_at": iso(t.expires_at),
        "revoked_at": iso(t.revoked_at), "revoked": t.revoked_at is not None,
        "expired": bool(exp is not None and exp <= datetime.now(timezone.utc)),
    }


@router.get("")
def list_tokens():
    with SessionLocal() as s:
        rows = s.scalars(select(ApiToken).order_by(ApiToken.created_at.desc(), ApiToken.id.desc())).all()
        return [token_json(t) for t in rows]


@router.post("", status_code=201)
def create_token(body: TokenIn):
    now = datetime.now(timezone.utc)
    with SessionLocal() as s:
        for _ in range(5):  # Präfix-Kollision praktisch ausgeschlossen, trotzdem prüfen
            key, prefix, digest = generate_key()
            if s.scalar(select(ApiToken.id).where(ApiToken.prefix == prefix)) is None:
                break
        else:
            raise HTTPException(500, "Konnte keinen eindeutigen Schlüssel erzeugen")
        t = ApiToken(name=body.name, prefix=prefix, token_hash=digest, scopes=normalize_scopes(body.scopes),
                     created_at=now,
                     expires_at=(now + timedelta(days=body.expires_days)) if body.expires_days else None)
        s.add(t)
        s.commit()
        s.refresh(t)
        out = token_json(t)
    invalidate_token_cache()
    out["token"] = key  # Klartext nur in dieser Antwort
    return out


@router.delete("/{token_id}", status_code=204)
def revoke_token(token_id: int):
    with SessionLocal() as s:
        t = s.get(ApiToken, token_id)
        if t is None:
            raise HTTPException(404, "API-Zugang nicht gefunden")
        if t.revoked_at is None:
            t.revoked_at = datetime.now(timezone.utc)
            s.commit()
    invalidate_token_cache()
    return Response(status_code=204)
