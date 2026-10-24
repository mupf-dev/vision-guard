"""Single-Admin-Auth mit JWT im httpOnly-Cookie `vg_session`."""
import hmac
from datetime import datetime, timedelta, timezone
from typing import Optional

import jwt
from fastapi import HTTPException, Request, WebSocket, status

from .config import config

ALGO = "HS256"


def check_credentials(username: str, password: str) -> bool:
    u = hmac.compare_digest(username.encode(), config.admin_user.encode())
    p = hmac.compare_digest(password.encode(), config.admin_password.encode())
    return u and p


def create_token(username: str) -> str:
    now = datetime.now(timezone.utc)
    payload = {"sub": username, "iat": now, "exp": now + timedelta(days=config.session_days)}
    return jwt.encode(payload, config.secret_key, algorithm=ALGO)


def verify_token(token: Optional[str]) -> Optional[str]:
    if not token:
        return None
    try:
        data = jwt.decode(token, config.secret_key, algorithms=[ALGO])
    except jwt.PyJWTError:
        return None
    sub = data.get("sub")
    if sub != config.admin_user:
        return None
    return sub


def current_user(request: Request) -> Optional[str]:
    return verify_token(request.cookies.get(config.cookie_name))


def require_user(request: Request) -> str:
    user = current_user(request)
    if not user:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Nicht angemeldet")
    return user


def ws_user(ws: WebSocket) -> Optional[str]:
    return verify_token(ws.cookies.get(config.cookie_name))
