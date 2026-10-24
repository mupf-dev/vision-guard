import os
import re
import unicodedata
from datetime import datetime, timezone
from typing import Optional

from .config import config

_UMLAUTS = str.maketrans({"ä": "ae", "ö": "oe", "ü": "ue", "Ä": "Ae", "Ö": "Oe", "Ü": "Ue", "ß": "ss"})


def slugify(name: str) -> str:
    s = (name or "").translate(_UMLAUTS)
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
    s = re.sub(r"[^a-zA-Z0-9]+", "_", s).strip("_").lower()
    return s or "camera"


def from_unix(ts: Optional[float]) -> Optional[datetime]:
    if ts is None:
        return None
    try:
        return datetime.fromtimestamp(float(ts), timezone.utc)
    except (TypeError, ValueError, OSError):
        return None


def media_path(rel: str) -> str:
    """Absoluter Pfad zu einem relativen Medienpfad; verhindert Ausbruch aus dem Medienordner."""
    rel = (rel or "").lstrip("/")
    if rel.startswith("media/"):
        rel = rel[len("media/"):]
    base = os.path.realpath(config.media_dir)
    full = os.path.realpath(os.path.join(base, rel))
    if full != base and not full.startswith(base + os.sep):
        raise ValueError("Pfad außerhalb des Medienordners")
    return full


def safe_remove(rel: Optional[str]) -> None:
    if not rel:
        return
    try:
        os.remove(media_path(rel))
    except (OSError, ValueError):
        pass


def ensure_media_dirs() -> None:
    for sub in ("snapshots", "crops", "clips", "identities"):
        os.makedirs(os.path.join(config.media_dir, sub), exist_ok=True)


def local_date_str(dt: Optional[datetime] = None) -> str:
    dt = dt or datetime.now(timezone.utc)
    return dt.astimezone(config.tz).strftime("%Y-%m-%d")
