"""Livebild auf Fernseher (DLNA/UPnP MediaRenderer), siehe CONTRACT.md „Nachträge: Fernseher“.

Der Fernseher bekommt per `SetAVTransportURI` + `Play` einen signierten, kurzlebigen Link auf den Kamerastrom
(`/api/tv/stream/<id>.ts?exp=&t=&sig=`, MPEG-TS, von ffmpeg umgepackt – siehe `routers/tv.py`) und spielt ihn im Vollbild ab. Nach `duration_s` folgt
`Stop` – aber nur, wenn der Fernseher noch unseren Strom zeigt (sonst hat jemand umgeschaltet).
SOAP direkt über httpx, kein Zusatzpaket.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import re
import threading
import time
import xml.etree.ElementTree as ET
from typing import Any, Optional
from urllib.parse import urljoin, urlparse
from xml.sax.saxutils import escape

import httpx

from .config import config

log = logging.getLogger("vg.tv")

AVT = "urn:schemas-upnp-org:service:AVTransport:1"
STREAM_TTL_MAX_S = 3600
_ctl_cache: dict[str, tuple[float, str]] = {}   # description_url → (ts, controlURL)
_lock = threading.Lock()
_sessions: dict[str, dict[str, Any]] = {}       # target_id → {token, until, camera_id}
_last_show: dict[str, float] = {}               # target_id → Unix-s (Sperrzeit)


class TvError(Exception):
    """Fehler mit deutscher Meldung (ohne Zugangsdaten)."""


# ------------------------------------------------------------------ Signierter Stream-Link

def _sig(camera_id: int, exp: int, token: str) -> str:
    mac = hmac.new(config.secret_key.encode("utf-8"), f"tv:{int(camera_id)}:{int(exp)}:{token}".encode("utf-8"),
                   hashlib.sha256).digest()[:16]
    return base64.urlsafe_b64encode(mac).rstrip(b"=").decode("ascii")


CONTENT_FEATURES = "DLNA.ORG_OP=00;DLNA.ORG_CI=0;DLNA.ORG_FLAGS=01700000000000000000000000000000"


def stream_url(base_url: str, camera_id: int, ttl_s: int) -> tuple[str, str]:
    """Signierter Link für den Fernseher → (url, token). `token` erkennt später „zeigt noch unseren Strom“."""
    exp = int(time.time()) + max(30, min(int(ttl_s), STREAM_TTL_MAX_S))
    token = base64.urlsafe_b64encode(hashlib.sha256(f"{camera_id}:{exp}:{time.time_ns()}".encode()).digest()[:6]) \
        .rstrip(b"=").decode("ascii")
    url = f"{base_url.rstrip('/')}/api/tv/stream/{int(camera_id)}.ts?exp={exp}&t={token}&sig={_sig(camera_id, exp, token)}"
    return url, token


def verify(camera_id: int, exp: Any, token: Optional[str], sig: Optional[str]) -> bool:
    try:
        exp_i = int(exp)
    except (TypeError, ValueError):
        return False
    if not sig or not token or exp_i < int(time.time()) or exp_i > int(time.time()) + STREAM_TTL_MAX_S + 60:
        return False
    return hmac.compare_digest(_sig(camera_id, exp_i, token), str(sig))


# ------------------------------------------------------------------ DLNA (UPnP AVTransport)

def _host(url: str) -> str:
    return urlparse(url).hostname or "?"


def _control_url(description_url: str) -> str:
    now = time.time()
    hit = _ctl_cache.get(description_url)
    if hit and now - hit[0] < 600:
        return hit[1]
    try:
        r = httpx.get(description_url, timeout=5)
        r.raise_for_status()
        root = ET.fromstring(r.content)
    except Exception as e:  # noqa: BLE001
        raise TvError(f"Fernseher {_host(description_url)} nicht erreichbar (eingeschaltet?)") from e
    ns = {"d": "urn:schemas-upnp-org:device-1-0"}
    for svc in root.iter("{urn:schemas-upnp-org:device-1-0}service"):
        if (svc.findtext("d:serviceType", default="", namespaces=ns) or "").startswith(
                "urn:schemas-upnp-org:service:AVTransport"):
            ctl = urljoin(description_url, svc.findtext("d:controlURL", default="", namespaces=ns))
            _ctl_cache[description_url] = (now, ctl)
            return ctl
    raise TvError("Gerät ist kein DLNA-Abspieler (kein AVTransport-Dienst)")


def _soap(description_url: str, action: str, args: str, timeout: float = 8.0) -> str:
    ctl = _control_url(description_url)
    body = ('<?xml version="1.0" encoding="utf-8"?>'
            '<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/" '
            's:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/"><s:Body>'
            f'<u:{action} xmlns:u="{AVT}"><InstanceID>0</InstanceID>{args}</u:{action}>'
            '</s:Body></s:Envelope>')
    try:
        r = httpx.post(ctl, content=body.encode("utf-8"), timeout=timeout, headers={
            "Content-Type": 'text/xml; charset="utf-8"', "SOAPAction": f'"{AVT}#{action}"'})
    except Exception as e:  # noqa: BLE001
        _ctl_cache.pop(description_url, None)
        raise TvError(f"Fernseher {_host(description_url)} nicht erreichbar (eingeschaltet?)") from e
    if r.status_code != 200:
        m = re.search(r"<errorDescription>(.*?)</errorDescription>", r.text or "", re.S)
        code = re.search(r"<errorCode>(\d+)</errorCode>", r.text or "")
        raise TvError(f"Fernseher lehnt {action} ab (HTTP {r.status_code}"
                      f"{', UPnP ' + code.group(1) if code else ''}{': ' + m.group(1).strip() if m else ''})")
    return r.text


def _didl(url: str, title: str) -> str:
    didl = ('<DIDL-Lite xmlns="urn:schemas-upnp-org:metadata-1-0/DIDL-Lite/" xmlns:dc="http://purl.org/dc/elements/1.1/" '
            'xmlns:upnp="urn:schemas-upnp-org:metadata-1-0/upnp/" xmlns:dlna="urn:schemas-dlna-org:metadata-1-0/">'
            f'<item id="0" parentID="-1" restricted="1"><dc:title>{escape(title)}</dc:title>'
            '<upnp:class>object.item.videoItem</upnp:class>'
            f'<res protocolInfo="http-get:*:video/mp2t:{CONTENT_FEATURES}">'
            f'{escape(url)}</res></item></DIDL-Lite>')
    return escape(didl)


def play(description_url: str, url: str, title: str) -> None:
    try:   # manche Geräte lehnen SetAVTransportURI während der Wiedergabe ab
        _soap(description_url, "Stop", "", timeout=4)
    except TvError:
        pass
    _soap(description_url, "SetAVTransportURI",
          f"<CurrentURI>{escape(url)}</CurrentURI><CurrentURIMetaData>{_didl(url, title)}</CurrentURIMetaData>")
    _soap(description_url, "Play", "<Speed>1</Speed>")


def stop(description_url: str) -> None:
    _soap(description_url, "Stop", "", timeout=5)


def current_uri(description_url: str) -> Optional[str]:
    try:
        txt = _soap(description_url, "GetMediaInfo", "", timeout=4)
    except TvError:
        return None
    m = re.search(r"<CurrentURI>(.*?)</CurrentURI>", txt, re.S)
    if not m:
        return None
    return m.group(1).replace("&amp;", "&")


def transport_state(description_url: str) -> Optional[str]:
    try:
        txt = _soap(description_url, "GetTransportInfo", "", timeout=4)
    except TvError:
        return None
    m = re.search(r"<CurrentTransportState>(.*?)</CurrentTransportState>", txt)
    return m.group(1) if m else None


# ------------------------------------------------------------------ Fernseher aus den Settings, Anzeigen/Stoppen

def normalize_description_url(v: str) -> str:
    """'10.10.100.245' → 'http://10.10.100.245:9197/dmr' (Samsung-Standard); sonst http(s)-URL unverändert."""
    v = (v or "").strip()
    if re.fullmatch(r"[A-Za-z0-9.-]+(:\d{1,5})?", v):
        host, _, port = v.partition(":")
        return f"http://{host}:{port or 9197}/dmr"
    if not re.match(r"^https?://[^\s/]+", v):
        raise ValueError("Adresse des Fernsehers: IP-Adresse oder http://…-URL der Gerätebeschreibung")
    return v


def probe(description_url: str) -> dict[str, Any]:
    """Gerätebeschreibung lesen und AVTransport prüfen (`POST /api/tv/probe`)."""
    try:
        url = normalize_description_url(description_url)
    except ValueError as e:
        return {"ok": False, "description_url": description_url, "name": None, "model": None, "error": str(e)}
    out: dict[str, Any] = {"ok": False, "description_url": url, "name": None, "model": None, "error": None}
    try:
        r = httpx.get(url, timeout=5)
        r.raise_for_status()
        root = ET.fromstring(r.content)
        ns = "{urn:schemas-upnp-org:device-1-0}"
        dev = root.find(f"{ns}device")
        if dev is not None:
            out["name"] = dev.findtext(f"{ns}friendlyName")
            out["model"] = dev.findtext(f"{ns}modelName")
        _ctl_cache.pop(url, None)
        _control_url(url)
        out["ok"] = True
    except TvError as e:
        out["error"] = str(e)
    except Exception:  # noqa: BLE001
        out["error"] = f"Fernseher {_host(url)} nicht erreichbar oder keine UPnP-Gerätebeschreibung (eingeschaltet?)"
    return out


def _settings() -> dict[str, Any]:
    from . import settings_store
    return settings_store.get()


def targets() -> list[dict[str, Any]]:
    return list(_settings().get("tv_targets") or [])


def find_target(target_id: str) -> Optional[dict[str, Any]]:
    return next((t for t in targets() if t.get("id") == target_id), None)


def _iso(ts: Optional[float]) -> Optional[str]:
    from datetime import datetime, timezone
    return datetime.fromtimestamp(ts, timezone.utc).isoformat().replace("+00:00", "Z") if ts else None


def _camera_name(camera_id: int) -> Optional[str]:
    try:
        from .db import SessionLocal
        from .models import Camera
        with SessionLocal() as s:
            c = s.get(Camera, camera_id)
            return c.name if c else None
    except Exception:  # noqa: BLE001
        return None


def status_of(t: dict[str, Any]) -> dict[str, Any]:
    with _lock:
        sess = dict(_sessions.get(t["id"]) or {})
    showing = None
    if sess.get("until") and sess["until"] > time.time():
        showing = {"camera_id": sess["camera_id"], "camera_name": sess.get("camera_name"),
                   "since": _iso(sess.get("since")), "until": _iso(sess["until"]), "source": sess.get("source")}
    return {"id": t["id"], "name": t.get("name") or t["id"], "enabled": bool(t.get("enabled", True)),
            "showing": showing, "last_error": _errors.get(t["id"])}


_errors: dict[str, Optional[str]] = {}


def show(target_id: str, camera_id: int, duration_s: float, *, source: str = "manual") -> dict[str, Any]:
    """Livebild von `camera_id` für `duration_s` auf dem Fernseher zeigen (blockiert bis `Play`, ≈ 1 s).
    Ersetzt eine laufende Anzeige. Danach stoppt ein Hintergrund-Thread – nur wenn noch unser Strom läuft."""
    t = find_target(target_id)
    if t is None:
        raise KeyError(target_id)
    base = (_settings().get("tv_stream_base_url") or "").rstrip("/")
    if not base:
        raise TvError("Adresse für Fernseher (tv_stream_base_url) ist nicht eingestellt")
    duration_s = max(10.0, min(float(duration_s), 3600.0))
    url, token = stream_url(base, camera_id, int(duration_s) + 30)
    name = _camera_name(camera_id) or f"Kamera {camera_id}"
    try:
        play(t["description_url"], url, f"VisionGuard – {name}")
    except TvError as e:
        _errors[target_id] = str(e)
        raise
    _errors[target_id] = None
    now = time.time()
    with _lock:
        _sessions[target_id] = {"token": token, "camera_id": camera_id, "camera_name": name, "since": now,
                                "until": now + duration_s, "source": source}
    threading.Thread(target=_stop_later, args=(target_id, token, duration_s), daemon=True,
                     name=f"tv-stop-{target_id}").start()
    log.info("Fernseher %s: zeige Kamera %s für %.0f s (%s)", target_id, camera_id, duration_s, source)
    return status_of(t)


def _ours(description_url: str, token: str) -> bool:
    uri = current_uri(description_url)
    return bool(uri) and f"t={token}" in uri


def _stop_later(target_id: str, token: str, duration_s: float) -> None:
    time.sleep(duration_s)
    with _lock:
        sess = _sessions.get(target_id)
        if not sess or sess.get("token") != token:
            return   # inzwischen ersetzt oder gestoppt
        _sessions.pop(target_id, None)
    t = find_target(target_id)
    if t is None:
        return
    try:
        if _ours(t["description_url"], token):
            stop(t["description_url"])
            log.info("Fernseher %s: Anzeige beendet", target_id)
        else:
            log.info("Fernseher %s: zeigt inzwischen etwas anderes – nicht gestoppt", target_id)
    except TvError as e:
        log.info("Fernseher %s: Stoppen fehlgeschlagen: %s", target_id, e)


def stop_target(target_id: str) -> dict[str, Any]:
    t = find_target(target_id)
    if t is None:
        raise KeyError(target_id)
    with _lock:
        sess = _sessions.pop(target_id, None)
    if sess and _ours(t["description_url"], sess["token"]):
        stop(t["description_url"])
    return status_of(t)


def run_action(action: dict[str, Any], camera_id: Optional[int], rule_name: str) -> None:
    """Regel-Aktion `tv` (CONTRACT TV.D) – im Hintergrund, blockiert die Regel nicht."""
    cam = action.get("camera_id") or camera_id
    if not cam:
        log.info("Regel '%s': Fernseher-Aktion ohne Kamera – übersprungen", rule_name)
        return
    want = action.get("target")
    ts = [t for t in targets() if t.get("enabled", True) and (want is None or t.get("id") == want)]
    if not ts:
        log.info("Regel '%s': kein aktiver Fernseher %s", rule_name, want or "")
        return
    dur = float(action.get("duration_s") or 60)

    def work() -> None:
        for t in ts:
            try:
                show(t["id"], int(cam), dur, source="rule")
            except (TvError, KeyError) as e:
                log.warning("Regel '%s': Fernseher %s: %s", rule_name, t.get("id"), e)

    threading.Thread(target=work, daemon=True, name="tv-action").start()
