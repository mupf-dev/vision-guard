from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel

from .. import i18n
from ..auth import (bearer_key, check_credentials, client_ip, create_token, current_principal, current_user,
                    login_failed, login_is_local, login_retry_after, login_succeeded, require_user)
from ..config import config, weak_admin_password
from ..schemas import LoginIn, UserOut


class AuthMeOut(BaseModel):
    username: str
    auth: str = "session"            # "session" | "token"
    scopes: list[str] = ["read", "write"]
    token_name: Optional[str] = None

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.post("/login", response_model=UserOut)
def login(body: LoginIn, request: Request, response: Response):
    ip = client_ip(request)
    wait = login_retry_after(ip)
    if wait:
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS,
                            i18n.t("errors.login_too_many_attempts", wait=wait),
                            headers={"Retry-After": str(wait)})
    if not check_credentials(body.username, body.password):
        login_failed(ip)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, i18n.t("errors.login_invalid"))
    if weak_admin_password() and not login_is_local(request):
        # Standard-/Schwachpasswort: nur im eigenen Netz; von außen nur API-Keys
        login_failed(ip)
        raise HTTPException(status.HTTP_403_FORBIDDEN,
                            i18n.t("errors.login_weak_password_external"))
    login_succeeded(ip)
    secure = request.headers.get("x-forwarded-proto", request.url.scheme) == "https"
    response.set_cookie(
        config.cookie_name, create_token(body.username), max_age=config.session_days * 86400,
        httponly=True, samesite="lax", secure=secure, path="/",
    )
    return {"username": body.username}


@router.post("/logout", status_code=204)
def logout(request: Request, response: Response):
    # Nur mit Session-Cookie: ein API-Key kann sich nicht „abmelden“
    if not current_user(request) and bearer_key(request.headers):
        raise HTTPException(status.HTTP_403_FORBIDDEN, i18n.t("errors.browser_login_only"))
    response.delete_cookie(config.cookie_name, path="/")
    response.status_code = 204
    return response


@router.get("/me", response_model=AuthMeOut)
def me(request: Request, user: str = Depends(require_user)):
    p = getattr(request.state, "principal", None) or current_principal(request)
    return {"username": user, "auth": p.kind, "scopes": list(p.scopes), "token_name": p.token_name}


@router.get("/check", status_code=204)
def check(request: Request):
    if not current_user(request):
        # Nur Cookie (nginx auth_request für go2rtc) – API-Keys bleiben dort gesperrt
        return Response(status_code=403 if bearer_key(request.headers) else 401)
    return Response(status_code=204)
