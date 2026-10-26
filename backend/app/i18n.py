"""Sprachmodul des Backends (CONTRACT „Nachträge: Sprachen“).

Texte liegen je Sprache und Namensbereich in `app/locales/<code>/<namensbereich>.json`; Englisch (`en`) ist Quelle
und Rückfall. Schlüssel: `"<namensbereich>.<schlüssel>"`, Platzhalter `{name}` (str.format), Mehrzahl als Objekt
`{"one": "…", "other": "…"}` – gewählt über den Parameter `n`.

Welche Sprache gilt:
- In einer HTTP-Anfrage die Sprache aus `Accept-Language` (Middleware `LanguageMiddleware`), sonst
- die Systemsprache (Setting `language`) – für alles ohne Anfrage: Hinweise/Push, Tagesbericht, MQTT, Rückfragen,
  WebSocket-Texte, MCP.

Neue Sprache: Ordner `locales/en` kopieren, übersetzen – wird automatisch gefunden (Name aus `common.json`
→ `language_name`). Fehlende Schlüssel kommen aus Englisch.
"""
from __future__ import annotations

import contextvars
import json
import logging
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

log = logging.getLogger("vg.i18n")

LOCALES_DIR = Path(__file__).parent / "locales"
FALLBACK = "en"

_request_lang: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar("vg_request_lang", default=None)
_lock = threading.Lock()
_cache: dict[str, dict[str, Any]] = {}      # lang → {namespace: {...}}
_warned: set[str] = set()


def available() -> list[str]:
    """Sprachcodes mit eigenem Ordner unter `locales/` (Englisch zuerst)."""
    codes = sorted(p.name for p in LOCALES_DIR.iterdir() if p.is_dir() and not p.name.startswith(("_", ".")))
    return sorted(codes, key=lambda c: (c != FALLBACK, c))


def languages() -> list[dict[str, str]]:
    """[{code, name}] – Name in der eigenen Sprache (`common.language_name`)."""
    return [{"code": c, "name": str(_lookup(c, "common.language_name") or c)} for c in available()]


def _load(lang: str) -> dict[str, Any]:
    with _lock:
        hit = _cache.get(lang)
        if hit is not None:
            return hit
        data: dict[str, Any] = {}
        d = LOCALES_DIR / lang
        if d.is_dir():
            for f in sorted(d.glob("*.json")):
                try:
                    data[f.stem] = json.loads(f.read_text(encoding="utf-8"))
                except Exception as e:  # noqa: BLE001
                    log.error("Sprachdatei %s fehlerhaft: %s", f, e)
        _cache[lang] = data
        return data


def reload() -> None:
    """Sprachdateien neu lesen (Tests)."""
    with _lock:
        _cache.clear()


def _lookup(lang: str, key: str) -> Any:
    ns, _, rest = key.partition(".")
    cur: Any = _load(lang).get(ns)
    for part in rest.split(".") if rest else []:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(part)
    return cur


def normalize(code: Optional[str]) -> Optional[str]:
    """'de-DE' → 'de', nur verfügbare Sprachen, sonst None."""
    if not code:
        return None
    c = code.strip().lower().replace("_", "-").split("-")[0]
    return c if c in available() else None


def system_lang() -> str:
    """Systemsprache aus den Settings (`language`), Standard Englisch."""
    try:
        from . import settings_store
        return normalize(settings_store.get().get("language")) or FALLBACK
    except Exception:  # noqa: BLE001
        return FALLBACK


def current() -> str:
    """Sprache der laufenden HTTP-Anfrage, sonst Systemsprache."""
    return _request_lang.get() or system_lang()


def parse_accept_language(header: Optional[str]) -> Optional[str]:
    """Erste unterstützte Sprache aus `Accept-Language` (Gewichte werden respektiert)."""
    if not header:
        return None
    items = []
    for i, part in enumerate(header.split(",")):
        tag, _, params = part.strip().partition(";")
        q = 1.0
        if params.strip().startswith("q="):
            try:
                q = float(params.strip()[2:])
            except ValueError:
                q = 0.0
        items.append((-q, i, tag))
    for _q, _i, tag in sorted(items):
        n = normalize(tag)
        if n:
            return n
    return None


def t(key: str, lang: Optional[str] = None, /, **params: Any) -> str:
    """Text zu `key` in `lang` (Standard: `current()`), Rückfall Englisch, dann der Schlüssel selbst."""
    lg = normalize(lang) or current()
    val = _lookup(lg, key)
    if val is None and lg != FALLBACK:
        val = _lookup(FALLBACK, key)
    if val is None:
        if key not in _warned:
            _warned.add(key)
            log.warning("Text fehlt: %s", key)
        return key
    if isinstance(val, dict):   # Mehrzahl
        n = params.get("n", params.get("count"))
        val = val.get("one") if n == 1 and "one" in val else val.get("other", next(iter(val.values()), key))
    if not isinstance(val, str):
        return str(val)
    try:
        return val.format(**params) if params else val
    except (KeyError, IndexError, ValueError) as e:
        log.warning("Text %s: Platzhalter passt nicht (%s)", key, e)
        return val


def label(name: str, lang: Optional[str] = None) -> str:
    """Anzeigename einer Objektklasse (`common.labels.<name>`), sonst der Name selbst."""
    lg = normalize(lang) or current()
    v = _lookup(lg, f"common.labels.{name}") or _lookup(FALLBACK, f"common.labels.{name}")
    return str(v) if v else name


def fmt_num(v: float, digits: int = 0, lang: Optional[str] = None) -> str:
    """Zahl mit Dezimaltrennzeichen der Sprache (`common.decimal_sep`)."""
    s = f"{v:.{digits}f}"
    sep = t("common.decimal_sep", lang)
    return s.replace(".", sep) if sep and sep != "common.decimal_sep" else s


def fmt_time(dt: Optional[datetime], lang: Optional[str] = None, *, seconds: bool = False) -> str:
    """Uhrzeit in Ortszeit, 24 h (beide Sprachen), z. B. 14:02."""
    if dt is None:
        return "–"
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone().strftime("%H:%M:%S" if seconds else "%H:%M")


def fmt_date(dt: Optional[datetime], lang: Optional[str] = None) -> str:
    """Datum in Ortszeit nach `common.date_format` (strftime), z. B. 25.10.2026 bzw. 25/10/2026."""
    if dt is None:
        return "–"
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone().strftime(t("common.date_format", lang) or "%Y-%m-%d")


def fmt_ago(dt: Optional[datetime], lang: Optional[str] = None, now: Optional[datetime] = None) -> str:
    """„vor 5 min“ / "5 min ago" (`common.ago_*`)."""
    if dt is None:
        return t("common.never", lang)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    s = max(0, int(((now or datetime.now(timezone.utc)) - dt).total_seconds()))
    if s < 10:
        return t("common.just_now", lang)
    if s < 60:
        return t("common.ago_s", lang, n=s)
    m = round(s / 60)
    if m < 60:
        return t("common.ago_min", lang, n=m)
    h = round(m / 60)
    if h < 48:
        return t("common.ago_h", lang, n=h)
    return t("common.ago_d", lang, n=round(h / 24))


class LanguageMiddleware:
    """Setzt die Sprache einer HTTP-Anfrage aus `Accept-Language` (reines ASGI, auch für Streaming)."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope.get("type") not in ("http", "websocket"):
            return await self.app(scope, receive, send)
        header = None
        for k, v in scope.get("headers") or []:
            if k == b"accept-language":
                header = v.decode("latin-1")
                break
        token = _request_lang.set(parse_accept_language(header))
        try:
            await self.app(scope, receive, send)
        finally:
            _request_lang.reset(token)


def use_lang(lang: Optional[str]):
    """Kontextmanager für Hintergrundcode: `with i18n.use_lang("de"): …`."""
    class _Ctx:
        def __enter__(self):
            self.tok = _request_lang.set(normalize(lang))
            return self

        def __exit__(self, *exc):
            _request_lang.reset(self.tok)
            return False
    return _Ctx()
