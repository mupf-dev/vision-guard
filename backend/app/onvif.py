"""Schlanker ONVIF-Client für PTZ (SOAP 1.2, WS-Security UsernameToken, Fallback HTTP-Digest).

Nur die Aufrufe, die VisionGuard braucht: Geräte-Info, Medienprofile, PTZ-Knoten, ContinuousMove/Stop,
Presets (Get/Set/Remove/Goto) und GetStatus. Synchron (httpx.Client) – wird aus den PTZ-Threads benutzt.
Zugangsdaten werden nie geloggt.
"""
from __future__ import annotations

import base64
import hashlib
import logging
import os
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from typing import Any, Optional
from urllib.parse import urlsplit, urlunsplit
from xml.sax.saxutils import escape

import httpx
from . import i18n

log = logging.getLogger("vg.onvif")

DEFAULT_PORTS = (80, 8000, 2020, 8899, 8080)
NS_DEVICE = "http://www.onvif.org/ver10/device/wsdl"
NS_MEDIA = "http://www.onvif.org/ver10/media/wsdl"
NS_PTZ = "http://www.onvif.org/ver20/ptz/wsdl"
NS_SCHEMA = "http://www.onvif.org/ver10/schema"
WSSE = "http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-secext-1.0.xsd"
WSU = "http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-utility-1.0.xsd"
PW_DIGEST = "http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-username-token-profile-1.0#PasswordDigest"
B64 = "http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-soap-message-security-1.0#Base64Binary"
PANTILT_VELOCITY = "http://www.onvif.org/ver10/tptz/PanTiltSpaces/VelocityGenericSpace"
ZOOM_VELOCITY = "http://www.onvif.org/ver10/tptz/ZoomSpaces/VelocityGenericSpace"


class OnvifError(Exception):
    """Fehler mit deutscher, für Nutzer geeigneter Meldung."""


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _find(el: ET.Element, *names: str) -> Optional[ET.Element]:
    """Erstes Nachfahren-Element mit diesem lokalen Namen (Pfad aus mehreren Namen = verschachtelt)."""
    cur: Optional[ET.Element] = el
    for n in names:
        if cur is None:
            return None
        cur = next((x for x in cur.iter() if x is not cur and _local(x.tag) == n), None)
    return cur


def _findall(el: ET.Element, name: str) -> list[ET.Element]:
    return [x for x in el.iter() if _local(x.tag) == name]


def _text(el: ET.Element, *names: str) -> Optional[str]:
    x = _find(el, *names)
    return x.text.strip() if x is not None and x.text else None


class OnvifPtz:
    """Verbindung zu einer Kamera. `connect()` ermittelt Dienste, Profil und Fähigkeiten."""

    def __init__(self, host: str, port: Optional[int], user: str, password: str,
                 profile_token: Optional[str] = None, timeout: float = 5.0):
        self.host = host
        self.port = port
        self.user = user or ""
        self.password = password or ""
        self.want_profile = profile_token
        self.timeout = timeout
        self.http = httpx.Client(timeout=timeout)
        self.time_offset = 0.0                    # Kamerauhr − eigene Uhr (s), für WS-Security „Created“
        self.digest = False                       # HTTP-Digest statt WS-Security (manche Geräte)
        self.device_url = ""
        self.media_url = ""
        self.ptz_url = ""
        self.profile = ""
        self.device: dict[str, Any] | None = None
        self.caps: dict[str, Any] = {"continuous": False, "zoom": False, "presets": False, "presets_max": 0,
                                     "status": False}

    def close(self) -> None:
        try:
            self.http.close()
        except Exception:  # noqa: BLE001
            pass

    # ------------------------------------------------------------------ SOAP
    def _security(self) -> str:
        if not self.user or self.digest:
            return ""
        nonce = os.urandom(16)
        created = datetime.fromtimestamp(datetime.now(timezone.utc).timestamp() + self.time_offset,
                                         timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        digest = base64.b64encode(hashlib.sha1(nonce + created.encode() + self.password.encode()).digest()).decode()
        return (f'<s:Header><Security s:mustUnderstand="1" xmlns="{WSSE}"><UsernameToken>'
                f"<Username>{escape(self.user)}</Username>"
                f'<Password Type="{PW_DIGEST}">{digest}</Password>'
                f'<Nonce EncodingType="{B64}">{base64.b64encode(nonce).decode()}</Nonce>'
                f'<Created xmlns="{WSU}">{created}</Created></UsernameToken></Security></s:Header>')

    def _call(self, url: str, body: str, auth: bool = True) -> ET.Element:
        env = ('<?xml version="1.0" encoding="UTF-8"?>'
               f'<s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope" xmlns:tt="{NS_SCHEMA}">'
               f'{self._security() if auth else ""}<s:Body>{body}</s:Body></s:Envelope>')
        kw: dict[str, Any] = {}
        if auth and self.digest and self.user:
            kw["auth"] = httpx.DigestAuth(self.user, self.password)
        try:
            r = self.http.post(url, content=env.encode(), headers={"Content-Type": "application/soap+xml; charset=utf-8"},
                               **kw)
        except httpx.TimeoutException as e:
            raise OnvifError(i18n.t("errors.onvif_timeout")) from e
        except httpx.HTTPError as e:
            raise OnvifError(i18n.t("errors.onvif_unreachable", error=type(e).__name__)) from e
        if r.status_code == 401 and auth and self.user and not self.digest:
            # einige Geräte verlangen HTTP-Digest statt WS-Security
            self.digest = True
            return self._call(url, body, auth)
        try:
            root = ET.fromstring(r.content)
        except ET.ParseError as e:
            raise OnvifError(i18n.t("errors.onvif_invalid_response", status=r.status_code)) from e
        fault = _find(root, "Fault")
        if fault is not None or r.status_code >= 400:
            reason = " ".join(t.strip() for t in (fault.itertext() if fault is not None else []) if t.strip())
            low = reason.lower()
            if r.status_code == 401 or "notauthorized" in low or "not authorized" in low or "sender not auth" in low:
                raise OnvifError(i18n.t("errors.onvif_auth_failed"))
            raise OnvifError(i18n.t("errors.onvif_camera_error", reason=reason[:200] or f"HTTP {r.status_code}"))
        return root

    def _rebase(self, xaddr: Optional[str]) -> str:
        """Dienst-Adresse auf den Host/Port umschreiben, über den wir die Kamera erreichen (NAT, falsche IPs)."""
        if not xaddr:
            return ""
        u = urlsplit(xaddr)
        return urlunsplit(("http", f"{self.host}:{self.port}", u.path or "/", u.query, ""))

    # ------------------------------------------------------------------ Verbindung
    def _try_port(self, port: int) -> bool:
        self.port = port
        self.device_url = f"http://{self.host}:{port}/onvif/device_service"
        try:
            root = self._call(self.device_url, f'<GetSystemDateAndTime xmlns="{NS_DEVICE}"/>', auth=False)
        except OnvifError:
            return False
        utc = _find(root, "UTCDateTime")
        if utc is not None:
            try:
                d = {k: int(_text(utc, k) or 0) for k in ("Year", "Month", "Day", "Hour", "Minute", "Second")}
                cam = datetime(d["Year"], d["Month"], d["Day"], d["Hour"], d["Minute"], d["Second"], tzinfo=timezone.utc)
                self.time_offset = cam.timestamp() - datetime.now(timezone.utc).timestamp()
            except (ValueError, TypeError):
                self.time_offset = 0.0
        return True

    def connect(self) -> None:
        ports = [self.port] if self.port else list(DEFAULT_PORTS)
        if not any(self._try_port(p) for p in ports):
            self.port = ports[0] if len(ports) == 1 else None
            raise OnvifError(i18n.t("errors.onvif_no_service_port", port=ports[0]) if len(ports) == 1 else
                             i18n.t("errors.onvif_no_service_ports", ports=", ".join(map(str, ports))))
        root = self._call(self.device_url, f'<GetDeviceInformation xmlns="{NS_DEVICE}"/>')
        self.device = {"manufacturer": _text(root, "Manufacturer"), "model": _text(root, "Model"),
                       "firmware": _text(root, "FirmwareVersion")}
        root = self._call(self.device_url, f'<GetCapabilities xmlns="{NS_DEVICE}"><Category>All</Category></GetCapabilities>')
        self.media_url = self._rebase(_text(root, "Media", "XAddr"))
        self.ptz_url = self._rebase(_text(root, "PTZ", "XAddr"))
        if not self.ptz_url:
            raise OnvifError(i18n.t("errors.onvif_no_ptz"))
        if not self.media_url:
            raise OnvifError(i18n.t("errors.onvif_no_media_service"))
        root = self._call(self.media_url, f'<GetProfiles xmlns="{NS_MEDIA}"/>')
        profiles = [p for p in _findall(root, "Profiles") if p.get("token")]
        with_ptz = [p for p in profiles if _find(p, "PTZConfiguration") is not None]
        pick = None
        if self.want_profile:
            pick = next((p for p in profiles if p.get("token") == self.want_profile), None)
            if pick is None:
                raise OnvifError(i18n.t("errors.onvif_profile_missing", profile=self.want_profile))
        pick = pick or (with_ptz[0] if with_ptz else (profiles[0] if profiles else None))
        if pick is None:
            raise OnvifError(i18n.t("errors.onvif_no_media_profile"))
        self.profile = pick.get("token") or ""
        self.caps = {"continuous": True, "zoom": False, "presets": True, "presets_max": 0, "status": True}
        try:
            root = self._call(self.ptz_url, f'<GetNodes xmlns="{NS_PTZ}"/>')
            spaces = {_local(x.tag) for sp in _findall(root, "SupportedPTZSpaces") for x in sp}
            self.caps["continuous"] = "ContinuousPanTiltVelocitySpace" in spaces or not spaces
            self.caps["zoom"] = "ContinuousZoomVelocitySpace" in spaces
            mx = _text(root, "MaximumNumberOfPresets")
            self.caps["presets_max"] = int(mx) if mx and mx.isdigit() else 0
            self.caps["presets"] = self.caps["presets_max"] != 0 or not mx
        except OnvifError as e:
            log.info("ONVIF GetNodes nicht unterstützt (%s) – nehme Standard-Fähigkeiten an", e)
        try:
            self.status()
        except OnvifError:
            self.caps["status"] = False

    # ------------------------------------------------------------------ PTZ
    def continuous_move(self, pan: float, tilt: float, zoom: float = 0.0, timeout_s: float = 1.0) -> None:
        """Bewegung mit Geschwindigkeit (-1..1). `timeout_s`: die Kamera stoppt selbst (ONVIF `Timeout`) – auch
        wenn ein späteres `Stop` verloren geht (WLAN-Kameras)."""
        parts = []
        if pan or tilt:
            parts.append(f'<tt:PanTilt x="{pan + 0.0:.3f}" y="{tilt + 0.0:.3f}" space="{PANTILT_VELOCITY}"/>')
        if zoom and self.caps.get("zoom"):
            parts.append(f'<tt:Zoom x="{zoom:.3f}" space="{ZOOM_VELOCITY}"/>')
        if not parts:
            return
        tmo = f"<Timeout>PT{max(0.1, min(10.0, timeout_s)):.1f}S</Timeout>" if timeout_s else ""
        self._call(self.ptz_url, f'<ContinuousMove xmlns="{NS_PTZ}"><ProfileToken>{escape(self.profile)}</ProfileToken>'
                                 f'<Velocity>{"".join(parts)}</Velocity>{tmo}</ContinuousMove>')

    def stop(self) -> None:
        self._call(self.ptz_url, f'<Stop xmlns="{NS_PTZ}"><ProfileToken>{escape(self.profile)}</ProfileToken>'
                                 '<PanTilt>true</PanTilt><Zoom>true</Zoom></Stop>')

    def presets(self) -> list[dict[str, str]]:
        root = self._call(self.ptz_url, f'<GetPresets xmlns="{NS_PTZ}"><ProfileToken>{escape(self.profile)}</ProfileToken></GetPresets>')
        out = []
        for p in _findall(root, "Preset"):
            tok = p.get("token")
            if tok is None:
                continue
            out.append({"token": tok, "name": _text(p, "Name") or tok})
        return out

    def goto_preset(self, token: str) -> None:
        self._call(self.ptz_url, f'<GotoPreset xmlns="{NS_PTZ}"><ProfileToken>{escape(self.profile)}</ProfileToken>'
                                 f"<PresetToken>{escape(token)}</PresetToken></GotoPreset>")

    def set_preset(self, name: str, token: Optional[str] = None) -> str:
        tok = f"<PresetToken>{escape(token)}</PresetToken>" if token else ""
        root = self._call(self.ptz_url, f'<SetPreset xmlns="{NS_PTZ}"><ProfileToken>{escape(self.profile)}</ProfileToken>'
                                        f"<PresetName>{escape(name)}</PresetName>{tok}</SetPreset>")
        return _text(root, "PresetToken") or token or ""

    def remove_preset(self, token: str) -> None:
        self._call(self.ptz_url, f'<RemovePreset xmlns="{NS_PTZ}"><ProfileToken>{escape(self.profile)}</ProfileToken>'
                                 f"<PresetToken>{escape(token)}</PresetToken></RemovePreset>")

    def status(self) -> Optional[bool]:
        """True = bewegt sich, False = steht, None = unbekannt (Gerät meldet keinen MoveStatus)."""
        root = self._call(self.ptz_url, f'<GetStatus xmlns="{NS_PTZ}"><ProfileToken>{escape(self.profile)}</ProfileToken></GetStatus>')
        ms = _find(root, "MoveStatus")
        if ms is None:
            return None
        vals = [(x.text or "").strip().upper() for x in ms]
        if not vals and ms.text:
            vals = [ms.text.strip().upper()]
        if not vals:
            return None
        return any(v == "MOVING" for v in vals)
