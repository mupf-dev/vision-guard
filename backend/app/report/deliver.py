"""B3 Tagesbericht – Versand (MQTT, Push über Notification, Webhook, Mail).

Jeder Kanal läuft für sich; Fehler landen je Kanal in `delivery` (`{ok, error, ts}`), nie Abbruch.
Kanäle, die nicht eingerichtet sind, fehlen in `delivery` – außer Mail mit Empfängern, aber ohne SMTP
(`ok:false`, „SMTP nicht konfiguriert“).
- MQTT extern `{prefix}/report` (retain): `{date, text, report_id, counts}`.
- Push: Notification `source:"report"` (erscheint unter „Hinweise“), Push-Ziele aus `notify_routes.report`.
- Webhook `report_channels.webhook_url`: POST JSON (Report ohne Fakten, signierte URLs entfallen).
- Mail per `smtplib` mit Env `VG_SMTP_HOST/PORT/USER/PASSWORD/FROM/STARTTLS` (Passwort nie in Logs/Fehlern).
"""
from __future__ import annotations

import logging
import os
import smtplib
import ssl
from email.message import EmailMessage
from email.utils import formatdate, make_msgid
from typing import Any

import httpx

from .. import settings_store, state
from ..models import utcnow
from ..schemas import iso

log = logging.getLogger("vg.report")

WEBHOOK_TIMEOUT_S = 10
SMTP_TIMEOUT_S = 20


def _ok(**extra: Any) -> dict[str, Any]:
    return {"ok": True, "error": None, "ts": iso(utcnow()), **extra}


def _err(msg: str, **extra: Any) -> dict[str, Any]:
    return {"ok": False, "error": msg[:300], "ts": iso(utcnow()), **extra}


def smtp_config() -> dict[str, Any]:
    port = os.getenv("VG_SMTP_PORT") or "587"
    try:
        port_i = int(port)
    except ValueError:
        port_i = 587
    return {"host": (os.getenv("VG_SMTP_HOST") or "").strip(), "port": port_i,
            "user": os.getenv("VG_SMTP_USER") or "", "password": os.getenv("VG_SMTP_PASSWORD") or "",
            "from": (os.getenv("VG_SMTP_FROM") or os.getenv("VG_SMTP_USER") or "").strip(),
            "starttls": (os.getenv("VG_SMTP_STARTTLS") or "1").lower() in ("1", "true", "yes", "on")}


def smtp_configured() -> bool:
    c = smtp_config()
    return bool(c["host"] and c["from"])


def _mqtt(report: dict[str, Any], date: str, counts: dict[str, int]) -> dict[str, Any]:
    from ..mqtt_bus import bus   # zyklischen Import vermeiden

    if not bus.client or not state.mqtt_connected:
        return _err("MQTT nicht verbunden")
    prefix = settings_store.get().get("mqtt_prefix") or "visionguard"
    try:
        bus.publish(f"{prefix}/report", {"date": date, "text": report["text"], "report_id": report["id"],
                                         "counts": counts}, retain=True, qos=1)
    except Exception as e:  # noqa: BLE001
        return _err(f"MQTT: {type(e).__name__}")
    return _ok(topic=f"{prefix}/report")


def _push(report: dict[str, Any], title: str) -> dict[str, Any]:
    from .. import notifications

    routes = list((settings_store.get().get("notify_routes") or {}).get("report") or [])
    n = notifications.create(source="report", level="info", title=title, message=report["text"],
                             data={"report_id": report["id"], "generator": report.get("generator")},
                             dedup_key=f"report:{report['id']}", dedup_s=60)
    if n is None:
        return _err("Hinweis nicht angelegt (Dublette)")
    targets = len(n.get("delivery") or [])
    if not routes:
        return _ok(notification_id=n["id"], targets=0, note="kein Push-Ziel für „Tagesbericht“ gewählt")
    if not targets:
        return _err("Push-Ziel(e) unbekannt oder deaktiviert", notification_id=n["id"], targets=0)
    return _ok(notification_id=n["id"], targets=targets)


def _webhook(url: str, report: dict[str, Any], date: str, counts: dict[str, int]) -> dict[str, Any]:
    body = {"type": "report", "date": date, "report_id": report["id"], "kind": report.get("kind"),
            "period_start": report.get("period_start"), "period_end": report.get("period_end"),
            "text": report["text"], "html": report.get("html"), "generator": report.get("generator"),
            "counts": counts}
    try:
        r = httpx.post(url, json=body, timeout=WEBHOOK_TIMEOUT_S, follow_redirects=True)
    except httpx.TimeoutException:
        return _err(f"keine Antwort binnen {WEBHOOK_TIMEOUT_S} s")
    except httpx.HTTPError as e:
        return _err(f"nicht erreichbar ({type(e).__name__})")
    if r.status_code >= 400:
        return _err(f"HTTP {r.status_code}")
    return _ok(status=r.status_code)


def _mail(to: list[str], report: dict[str, Any], subject: str) -> dict[str, Any]:
    c = smtp_config()
    if not (c["host"] and c["from"]):
        return _err("SMTP nicht konfiguriert")
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = c["from"]
    msg["To"] = ", ".join(to)
    msg["Date"] = formatdate(localtime=True)
    msg["Message-ID"] = make_msgid(domain="visionguard.local")
    msg.set_content(report["text"])
    if report.get("html"):
        msg.add_alternative(f"<!doctype html><html><body>{report['html']}</body></html>", subtype="html")
    try:
        if c["port"] == 465:
            srv: smtplib.SMTP = smtplib.SMTP_SSL(c["host"], c["port"], timeout=SMTP_TIMEOUT_S,
                                                 context=ssl.create_default_context())
        else:
            srv = smtplib.SMTP(c["host"], c["port"], timeout=SMTP_TIMEOUT_S)
        with srv:
            if c["port"] != 465 and c["starttls"]:
                srv.starttls(context=ssl.create_default_context())
            if c["user"] and c["password"]:
                srv.login(c["user"], c["password"])
            srv.send_message(msg)
    except smtplib.SMTPAuthenticationError:
        return _err("SMTP-Anmeldung fehlgeschlagen")
    except smtplib.SMTPNotSupportedError:
        return _err("Server unterstützt STARTTLS nicht (VG_SMTP_STARTTLS=0?)")
    except smtplib.SMTPException as e:
        return _err(f"SMTP-Fehler ({type(e).__name__})")
    except OSError as e:
        return _err(f"SMTP nicht erreichbar ({type(e).__name__})")
    return _ok(recipients=len(to))


def deliver(report: dict[str, Any], *, date: str, counts: dict[str, int], title: str) -> dict[str, Any]:
    """Alle eingerichteten Kanäle; liefert `delivery` (je Kanal `{ok, error, ts, …}`)."""
    ch = settings_store.get().get("report_channels") or {}
    out: dict[str, Any] = {}
    steps: list[tuple[str, Any]] = []
    if ch.get("mqtt", True):
        steps.append(("mqtt", lambda: _mqtt(report, date, counts)))
    steps.append(("push", lambda: _push(report, title)))
    if ch.get("webhook_url"):
        steps.append(("webhook", lambda: _webhook(ch["webhook_url"], report, date, counts)))
    to = [str(x).strip() for x in ch.get("mail_to") or [] if str(x).strip()]
    if to:
        steps.append(("mail", lambda: _mail(to, report, f"VisionGuard – {title}")))
    for name, fn in steps:
        try:
            out[name] = fn()
        except Exception as e:  # noqa: BLE001 – ein Kanal darf die anderen nie stoppen
            log.exception("Bericht %s: Kanal %s fehlgeschlagen", report.get("id"), name)
            out[name] = _err(f"interner Fehler ({type(e).__name__})")
        if not out[name].get("ok"):
            log.warning("Bericht %s: %s nicht zugestellt – %s", report.get("id"), name, out[name].get("error"))
    return out
