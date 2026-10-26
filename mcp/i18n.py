"""Sprachmodul des MCP-Dienstes (CONTRACT „Nachträge: Sprachen“, I18N.D).

Texte je Sprache in `locales/<code>.json` (verschachtelt, Schlüssel `"<bereich>.<schlüssel>"`); Englisch (`en`) ist
Quelle und Rückfall. Platzhalter `{name}` (str.format), Mehrzahl als Objekt `{"one": "…", "other": "…"}` – gewählt
über den Parameter `n`.

Sprache der Tool-Antworten = Systemsprache des Backends (`GET /api/settings` → `language`, 60 s gecacht, gesetzt von
`tools.common.ensure_language`). Ohne Angabe Englisch. Neue Sprache: `locales/en.json` kopieren und übersetzen –
wird automatisch gefunden, fehlende Schlüssel kommen aus Englisch.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional
from zoneinfo import ZoneInfo

log = logging.getLogger("vg.mcp.i18n")

LOCALES_DIR = Path(__file__).parent / "locales"
FALLBACK = "en"

try:
    LOCAL_TZ = ZoneInfo(os.environ.get("TZ") or "Europe/Berlin")
except Exception:  # noqa: BLE001
    LOCAL_TZ = ZoneInfo("Europe/Berlin")

_cache: dict[str, dict[str, Any]] = {}
_warned: set[str] = set()
_lang = FALLBACK


def available() -> list[str]:
    """Sprachcodes mit eigener Datei unter `locales/` (Englisch zuerst)."""
    codes = sorted(p.stem for p in LOCALES_DIR.glob("*.json") if not p.stem.startswith(("_", ".")))
    return sorted(codes, key=lambda c: (c != FALLBACK, c))


def _load(lang: str) -> dict[str, Any]:
    hit = _cache.get(lang)
    if hit is not None:
        return hit
    data: dict[str, Any] = {}
    f = LOCALES_DIR / f"{lang}.json"
    if f.is_file():
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except Exception as e:  # noqa: BLE001
            log.error("Sprachdatei %s fehlerhaft: %s", f, e)
    _cache[lang] = data
    return data


def _lookup(lang: str, key: str) -> Any:
    cur: Any = _load(lang)
    for part in key.split("."):
        if not isinstance(cur, dict):
            return None
        cur = cur.get(part)
    return cur


def normalize(code: Optional[str]) -> Optional[str]:
    """'de-DE' → 'de', nur verfügbare Sprachen, sonst None."""
    if not code:
        return None
    c = str(code).strip().lower().replace("_", "-").split("-")[0]
    return c if c in available() else None


def current() -> str:
    """Aktuelle Antwortsprache (Systemsprache des Backends)."""
    return _lang


def set_lang(code: Optional[str]) -> str:
    global _lang
    _lang = normalize(code) or FALLBACK
    return _lang


def t(key: str, lang: Optional[str] = None, /, **params: Any) -> str:
    """Text zu `key` in `lang` (Standard: `current()`), Rückfall Englisch, dann der Schlüssel selbst."""
    lg = normalize(lang) or _lang
    val = _lookup(lg, key)
    if val is None and lg != FALLBACK:
        val = _lookup(FALLBACK, key)
    if val is None:
        if key not in _warned:
            _warned.add(key)
            log.warning("Text fehlt: %s", key)
        return key
    if isinstance(val, dict):   # Mehrzahl
        n = params.get("n")
        val = val.get("one") if n == 1 and "one" in val else val.get("other", next(iter(val.values()), key))
    if isinstance(val, list):
        return ", ".join(map(str, val))
    if not isinstance(val, str):
        return str(val)
    try:
        return val.format(**params) if params else val
    except (KeyError, IndexError, ValueError) as e:
        log.warning("Text %s: Platzhalter passt nicht (%s)", key, e)
        return val


def t_list(key: str, lang: Optional[str] = None) -> list[str]:
    """Liste (z. B. Wochentage) zu `key`, Rückfall Englisch."""
    lg = normalize(lang) or _lang
    val = _lookup(lg, key)
    if not isinstance(val, list):
        val = _lookup(FALLBACK, key)
    return [str(v) for v in val] if isinstance(val, list) else []


def mapped(prefix: str, value: Any, default: Any = None) -> str:
    """Anzeigetext eines Code-Werts (`<prefix>.<value>`), sonst `default` bzw. der Wert selbst."""
    v = str(value or "")
    if v:
        lg = _lang
        hit = _lookup(lg, f"{prefix}.{v}")
        if hit is None and lg != FALLBACK:
            hit = _lookup(FALLBACK, f"{prefix}.{v}")
        if isinstance(hit, str):
            return hit
    return v if default is None else str(default)


# ------------------------------------------------------------------ Formatierung


def parse_ts(value: Any) -> Optional[datetime]:
    if not value:
        return None
    if isinstance(value, datetime):
        dt = value
    else:
        try:
            dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def fmt_num(v: float, digits: int = 0) -> str:
    """Zahl mit dem Dezimaltrennzeichen der Sprache (`common.decimal_sep`)."""
    s = f"{float(v):.{digits}f}"
    sep = t("common.decimal_sep")
    return s.replace(".", sep) if sep and sep != "common.decimal_sep" else s


def fmt_time(value: Any) -> str:
    """„14:02“ für heute, sonst „23.10. 14:02“ bzw. „23/10 14:02“ (Ortszeit, 24 h)."""
    dt = parse_ts(value)
    if dt is None:
        return "?"
    local = dt.astimezone(LOCAL_TZ)
    if local.date() == datetime.now(LOCAL_TZ).date():
        return local.strftime("%H:%M")
    return local.strftime(t("common.day_month_format") + " %H:%M")


def fmt_datetime(value: Any, *, seconds: bool = False) -> str:
    """Datum und Uhrzeit (Ortszeit), z. B. „25.10.2026 14:02:05“ bzw. „25/10/2026 14:02:05“."""
    dt = parse_ts(value)
    if dt is None:
        return "?"
    return dt.astimezone(LOCAL_TZ).strftime(t("common.date_format") + (" %H:%M:%S" if seconds else " %H:%M"))


def fmt_ago(value: Any) -> str:
    """„vor 5 min“ / "5 min ago"."""
    dt = parse_ts(value)
    if dt is None:
        return t("common.unknown_time")
    s = max(0.0, (datetime.now(timezone.utc) - dt).total_seconds())
    if s < 90:
        return t("common.just_now")
    if s < 3600:
        return t("common.ago_min", n=round(s / 60))
    if s < 48 * 3600:
        return t("common.ago_h", n=round(s / 3600))
    return t("common.ago_d", n=round(s / 86400))
