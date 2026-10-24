from fastapi import APIRouter, Depends, HTTPException, Request, Response, status

from ..auth import check_credentials, create_token, current_user, require_user
from ..config import config
from ..schemas import LoginIn, UserOut

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.post("/login", response_model=UserOut)
def login(body: LoginIn, request: Request, response: Response):
    if not check_credentials(body.username, body.password):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Benutzername oder Passwort falsch")
    secure = request.headers.get("x-forwarded-proto", request.url.scheme) == "https"
    response.set_cookie(
        config.cookie_name, create_token(body.username), max_age=config.session_days * 86400,
        httponly=True, samesite="lax", secure=secure, path="/",
    )
    return {"username": body.username}


@router.post("/logout", status_code=204)
def logout(response: Response):
    response.delete_cookie(config.cookie_name, path="/")
    response.status_code = 204
    return response


@router.get("/me", response_model=UserOut)
def me(user: str = Depends(require_user)):
    return {"username": user}


@router.get("/check", status_code=204)
def check(request: Request):
    if not current_user(request):
        return Response(status_code=401)
    return Response(status_code=204)
