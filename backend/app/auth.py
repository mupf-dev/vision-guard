"""Auth: Single-Admin mit JWT im httpOnly-Cookie `vg_session` und API-Keys (Bearer `vgt_…`).

Siehe CONTRACT.md Nachtrag B. Der Klartext eines Keys und der Header `Authorization` werden nie geloggt.
"""
import hashlib
import hmac
import logging
import re
import secrets
import string
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import jwt
from fastapi import HTTPException, Request, WebSocket, status
from sqlalchemy import select, update

from .config import config

ALGO = "HS256"
log = logging.getLogger("vg.auth")

TOKEN_RE = re.compile(r"^vgt_([a-z0-9]{8})_([A-Za-z0-9_-]{43})$")
SCOPES = ("read", "write")
READ_METHODS = ("GET", "HEAD", "OPTIONS")
CACHE_TTL_S = 30.0
LAST_USED_INTERVAL_S = 60.0


def check_credentials(username: str, password: str) -> bool:
    u = hmac.compare_digest(username.encode(), config.admin_user.encode())
    p = hmac.compare_digest(password.encode(), config.admin_password.encode())
    return u and p


# ------------------------------------------------------------------ Anmeldung: Bremse + Herkunft

LOGIN_MAX_FAILS = 5            # Fehlversuche je IP …
LOGIN_WINDOW_S = 60.0          # … in diesem Zeitfenster, danach 429
LOGIN_GLOBAL_MAX_FAILS = 30    # alle IPs zusammen (verteiltes Durchprobieren)
_login_lock = threading.Lock()
_login_fails: dict[str, list[float]] = {}
_login_global: list[float] = []


def client_ip(request: Request) -> str:
    """Client-Adresse. nginx setzt ``X-Real-IP`` = ``$remote_addr`` (überschreibt Client-Angaben); das Backend ist
    nur über nginx bzw. intern erreichbar."""
    return (request.headers.get("x-real-ip") or (request.client.host if request.client else "") or "?").strip()


def _is_private(ip: str) -> bool:
    import ipaddress
    try:
        a = ipaddress.ip_address(ip.strip())
    except ValueError:
        return False
    return a.is_private or a.is_loopback or a.is_link_local


def login_is_local(request: Request) -> bool:
    """Anfrage kommt aus einem privaten Netz: Verbindung zu nginx privat **und** kein vorgeschalteter Proxy/Tunnel
    meldet eine öffentliche Client-Adresse (X-Forwarded-For)."""
    if not _is_private(client_ip(request)):
        return False
    xff = request.headers.get("x-forwarded-for", "")
    return all(_is_private(x) for x in xff.split(",") if x.strip())


def login_retry_after(ip: str) -> int:
    """>0: Sekunden bis zum nächsten erlaubten Versuch (zu viele Fehlversuche)."""
    now = time.time()
    with _login_lock:
        fails = [t for t in _login_fails.get(ip, []) if now - t < LOGIN_WINDOW_S]
        _login_fails[ip] = fails
        _login_global[:] = [t for t in _login_global if now - t < LOGIN_WINDOW_S]
        if len(fails) >= LOGIN_MAX_FAILS:
            return max(1, int(LOGIN_WINDOW_S - (now - fails[0])) + 1)
        if len(_login_global) >= LOGIN_GLOBAL_MAX_FAILS:
            return max(1, int(LOGIN_WINDOW_S - (now - _login_global[0])) + 1)
    return 0


def login_failed(ip: str) -> None:
    now = time.time()
    with _login_lock:
        _login_fails.setdefault(ip, []).append(now)
        _login_global.append(now)
        if len(_login_fails) > 10000:            # Speicher begrenzen
            for k in [k for k, v in _login_fails.items() if not v or now - v[-1] >= LOGIN_WINDOW_S]:
                _login_fails.pop(k, None)
    log.warning("Fehlgeschlagene Anmeldung von %s", ip)


def login_succeeded(ip: str) -> None:
    with _login_lock:
        _login_fails.pop(ip, None)


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
    """Nur Session-Cookie (für /api/auth/check u. ä.)."""
    return verify_token(request.cookies.get(config.cookie_name))


# ------------------------------------------------------------------ API-Keys


def normalize_scopes(scopes: Any) -> list[str]:
    """`write` impliziert `read`; Reihenfolge stabil, Unbekanntes fällt weg."""
    s = {str(x) for x in (scopes or [])}
    if "write" in s:
        s.add("read")
    if not s:
        s = {"read"}
    return [x for x in SCOPES if x in s]


def hash_key(key: str) -> str:
    return hmac.new(config.secret_key.encode("utf-8"), key.encode("utf-8"), hashlib.sha256).hexdigest()


def generate_key() -> tuple[str, str, str]:
    """→ (Klartext-Key, prefix 'vgt_xxxxxxxx', token_hash)."""
    alphabet = string.ascii_lowercase + string.digits
    short = "".join(secrets.choice(alphabet) for _ in range(8))
    secret = secrets.token_urlsafe(32)  # 32 Byte → 43 Zeichen base64url ohne Padding
    key = f"vgt_{short}_{secret}"
    return key, f"vgt_{short}", hash_key(key)


@dataclass
class Principal:
    kind: str                       # "session" | "token"
    username: str
    scopes: list[str] = field(default_factory=lambda: list(SCOPES))
    token_name: Optional[str] = None
    token_id: Optional[int] = None

    def has(self, scope: str) -> bool:
        return scope in self.scopes


_cache_lock = threading.Lock()
_cache: dict[str, tuple[float, Optional[dict[str, Any]]]] = {}   # prefix -> (geladen, Datensatz|None)
_last_used: dict[int, float] = {}


def invalidate_token_cache() -> None:
    with _cache_lock:
        _cache.clear()


def _load_token(prefix: str) -> Optional[dict[str, Any]]:
    now = time.monotonic()
    with _cache_lock:
        hit = _cache.get(prefix)
        if hit and now - hit[0] < CACHE_TTL_S:
            return hit[1]
    from .db import SessionLocal
    from .models import ApiToken

    rec: Optional[dict[str, Any]] = None
    try:
        with SessionLocal() as s:
            t = s.scalar(select(ApiToken).where(ApiToken.prefix == prefix))
            if t is not None:
                rec = {"id": t.id, "name": t.name, "hash": t.token_hash, "scopes": normalize_scopes(t.scopes),
                       "expires_at": t.expires_at, "revoked": t.revoked_at is not None}
    except Exception:  # noqa: BLE001
        log.exception("API-Key-Abfrage fehlgeschlagen")
        return None
    with _cache_lock:
        if len(_cache) > 1000:
            _cache.clear()
        _cache[prefix] = (now, rec)
    return rec


def _touch(token_id: int) -> None:
    now = time.monotonic()
    with _cache_lock:
        last = _last_used.get(token_id)
        if last is not None and now - last < LAST_USED_INTERVAL_S:
            return
        _last_used[token_id] = now
    from .db import SessionLocal
    from .models import ApiToken

    try:
        with SessionLocal() as s:
            s.execute(update(ApiToken).where(ApiToken.id == token_id)
                      .values(last_used_at=datetime.now(timezone.utc)))
            s.commit()
    except Exception:  # noqa: BLE001
        log.warning("last_used_at für API-Key %s nicht gespeichert", token_id)


def bearer_key(headers) -> Optional[str]:
    """Key aus `Authorization: Bearer …` oder None (kein/anderer Header)."""
    h = headers.get("authorization") or ""
    if not h[:7].lower() == "bearer ":
        return None
    return h[7:].strip() or None


def principal_from_key(key: Optional[str]) -> Optional[Principal]:
    if not key:
        return None
    m = TOKEN_RE.match(key)
    if not m:
        return None
    rec = _load_token(f"vgt_{m.group(1)}")
    if rec is None or rec["revoked"]:
        return None
    if not hmac.compare_digest(rec["hash"], hash_key(key)):
        return None
    exp = rec["expires_at"]
    if exp is not None:
        if exp.tzinfo is None:
            exp = exp.replace(tzinfo=timezone.utc)
        if exp <= datetime.now(timezone.utc):
            return None
    _touch(rec["id"])
    return Principal(kind="token", username=config.admin_user, scopes=list(rec["scopes"]),
                     token_name=rec["name"], token_id=rec["id"])


def session_principal(cookies) -> Optional[Principal]:
    user = verify_token(cookies.get(config.cookie_name))
    if not user:
        return None
    return Principal(kind="session", username=user)


def current_principal(request: Request) -> Optional[Principal]:
    """1. Cookie (volle Rechte), 2. Bearer-Key. None = nicht angemeldet."""
    p = session_principal(request.cookies)
    if p is None:
        p = principal_from_key(bearer_key(request.headers))
    if p is not None:
        request.state.principal = p
    return p


def _unauthorized() -> HTTPException:
    return HTTPException(status.HTTP_401_UNAUTHORIZED, "Nicht angemeldet",
                         headers={"WWW-Authenticate": "Bearer"})


def _authorize(request: Request, scope: str) -> Principal:
    p = current_principal(request)
    if p is None:
        raise _unauthorized()
    if not p.has(scope):
        raise HTTPException(status.HTTP_403_FORBIDDEN, f"Keine Berechtigung (Scope {scope} erforderlich)")
    return p


def require_user(request: Request) -> str:
    """Cookie oder Bearer-Key; Nicht-GET/HEAD/OPTIONS verlangt Scope `write`. Liefert den Benutzernamen."""
    scope = "read" if request.method.upper() in READ_METHODS else "write"
    return _authorize(request, scope).username


def require_read(request: Request) -> str:
    """Wie require_user, aber `read` genügt auch für POST (z. B. POST /api/media/sign)."""
    return _authorize(request, "read").username


# POST-Endpunkte, die nur Scope `read` verlangen (Wellenplan Abschnitt 2: zentrale Liste). Die Router binden dafür
# `require_read` bzw. `require_read_limited(...)` ein; die Liste dient Doku/Prüfung (MCP, Tests).
READ_POST_EXCEPTIONS: tuple[str, ...] = (
    "/api/media/sign",
    "/api/cameras/{id}/ask",        # W3 (A2): 10/min je Key
)

_rate_lock = threading.Lock()
_rate_hits: dict[tuple[str, str], list[float]] = {}


def _rate_check(bucket: str, who: str, per_min: int) -> None:
    now = time.time()
    with _rate_lock:
        hits = [t for t in _rate_hits.get((bucket, who), []) if now - t < 60.0]
        if len(hits) >= per_min:
            retry = max(1, int(60.0 - (now - hits[0])) + 1)
            _rate_hits[(bucket, who)] = hits
            raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, f"Zu viele Anfragen (max. {per_min}/min)",
                                headers={"Retry-After": str(retry)})
        hits.append(now)
        _rate_hits[(bucket, who)] = hits
        if len(_rate_hits) > 5000:   # Speicher begrenzen (alte Zähler verwerfen)
            for k in [k for k, v in _rate_hits.items() if not v or now - v[-1] > 60.0]:
                _rate_hits.pop(k, None)


def require_read_limited(bucket: str, per_min: int):
    """W3: Dependency-Fabrik – Scope `read` genügt (auch für POST), dazu höchstens `per_min` Aufrufe je Minute
    je API-Key (Sitzung: je Benutzer) im gleitenden 60-s-Fenster; sonst 429 mit `Retry-After`.

        @router.post("/{cam_id}/ask", dependencies=[Depends(require_read_limited("ask", 10))])
    """
    def dep(request: Request) -> str:
        p = _authorize(request, "read")
        who = f"token:{p.token_id}" if p.kind == "token" else f"session:{p.username}"
        _rate_check(bucket, who, int(per_min))
        return p.username
    return dep


def require_session(request: Request) -> str:
    """Nur mit Session-Cookie (Tokens dürfen z. B. keine Tokens verwalten)."""
    user = current_user(request)
    if user:
        request.state.principal = Principal(kind="session", username=user)
        return user
    if bearer_key(request.headers):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Nur mit Anmeldung im Browser")
    raise _unauthorized()


def ws_user(ws: WebSocket) -> Optional[str]:
    """WebSocket: Cookie oder Bearer-Key mit Scope `read`."""
    p = session_principal(ws.cookies)
    if p is None:
        p = principal_from_key(bearer_key(ws.headers))
    if p is None or not p.has("read"):
        return None
    return p.username


INTERNAL_LOCAL_WORKER = "local"


def require_internal(request: Request) -> str:
    """Dependency für ALLE Router unter /api/internal (D3-Vorbereitung).

    Heute ohne Wirkung: der Schutz ist allein nginx (`/api/internal/` → 403), Aufrufer ist der lokale Worker
    im Docker-Netz. Später (D3) prüft sie hier den Worker-Key (`Authorization: Bearer vgw_…`) bzw. lässt den
    lokalen Worker nur mit `VG_INTERNAL_TRUST_LOCAL=1` ohne Key zu. Liefert die worker_id des Aufrufers."""
    request.state.worker_id = INTERNAL_LOCAL_WORKER
    return INTERNAL_LOCAL_WORKER
