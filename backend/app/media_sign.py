"""Signierte Medien-URLs (`/media/<path>?exp=&sig=`), siehe CONTRACT.md Nachtrag C.

sig = base64url_ohne_padding(HMAC-SHA256(VG_SECRET_KEY, "media:" + path + ":" + exp)[:16])
`path` ist der relative Medienpfad wie in der DB (ohne führendes "/" und ohne "media/").
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import time
from typing import Any, Optional
from urllib.parse import quote

from .config import config


def normalize_path(path: str) -> str:
    """'/media/crops/x.jpg', 'media/crops/x.jpg', '/crops/x.jpg' → 'crops/x.jpg' (Query wird entfernt)."""
    p = (path or "").split("?", 1)[0].split("#", 1)[0].strip()
    p = p.lstrip("/")
    if p.startswith("media/"):
        p = p[len("media/"):]
    return p.lstrip("/")


def _sig(path: str, exp: int) -> str:
    mac = hmac.new(config.secret_key.encode("utf-8"), f"media:{path}:{int(exp)}".encode("utf-8"),
                   hashlib.sha256).digest()[:16]
    return base64.urlsafe_b64encode(mac).rstrip(b"=").decode("ascii")


def _base_url() -> str:
    try:
        from . import settings_store
        return (settings_store.get().get("public_base_url") or "").rstrip("/")
    except Exception:  # noqa: BLE001
        return ""


def sign_media_path(path: str, ttl_s: int) -> str:
    """Signierte URL für einen Medienpfad; absolut, wenn `public_base_url` gesetzt ist."""
    rel = normalize_path(path)
    exp = int(time.time()) + max(1, int(ttl_s))
    return f"{_base_url()}/media/{quote(rel, safe='/')}?exp={exp}&sig={_sig(rel, exp)}"


def verify_media_sig(path: str, exp: Any, sig: Optional[str]) -> bool:
    """True, wenn Signatur passt und noch nicht abgelaufen ist."""
    if not sig or exp is None:
        return False
    try:
        exp_i = int(exp)
    except (TypeError, ValueError):
        return False
    if exp_i < int(time.time()):
        return False
    return hmac.compare_digest(_sig(normalize_path(path), exp_i), str(sig))


def default_ttl_s() -> int:
    """TTL für signierte Links in externen Payloads und bei `signed=1` (Setting `media_link_ttl_h`)."""
    try:
        from . import settings_store
        return int(settings_store.get().get("media_link_ttl_h") or 24) * 3600
    except Exception:  # noqa: BLE001
        return 24 * 3600


def sign_url(url: Optional[str], ttl_s: Optional[int] = None) -> Optional[str]:
    """'/media/…' → signierte URL; alles andere (None, absolute fremde URLs) unverändert."""
    if not url or not isinstance(url, str) or not url.startswith("/media/"):
        return url
    return sign_media_path(url, ttl_s if ttl_s is not None else default_ttl_s())


def sign_urls(obj: Any, ttl_s: Optional[int] = None) -> Any:
    """Kopie von `obj`, in der alle Felder `*_url` mit Wert '/media/…' signiert sind (rekursiv)."""
    ttl = ttl_s if ttl_s is not None else default_ttl_s()
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            if isinstance(k, str) and k.endswith("_url") and isinstance(v, str):
                out[k] = sign_url(v, ttl)
            else:
                out[k] = sign_urls(v, ttl)
        return out
    if isinstance(obj, list):
        return [sign_urls(v, ttl) for v in obj]
    return obj
