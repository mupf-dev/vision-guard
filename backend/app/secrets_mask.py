"""Maskiert Passwörter in Kamera-URLs für API/Oberfläche."""
from typing import Optional
from urllib.parse import urlsplit, urlunsplit

MASK = "***"


def mask_url(url: Optional[str]) -> Optional[str]:
    if not url:
        return url
    p = urlsplit(url)
    if p.password is None:
        return url
    netloc = f"{p.username}:{MASK}@{p.hostname}" + (f":{p.port}" if p.port else "")
    return urlunsplit(p._replace(netloc=netloc))


def unmask_url(new: Optional[str], candidates: list[Optional[str]]) -> Optional[str]:
    """Enthält `new` das Maskierungszeichen, wird die passende gespeicherte URL zurückgegeben."""
    if not new or f":{MASK}@" not in new:
        return new
    for old in candidates:
        if old and mask_url(old) == new:
            return old
    return new
