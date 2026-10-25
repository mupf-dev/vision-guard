"""Push-Versand an Handys (ntfy, Pushover) – siehe CONTRACT.md „Nachträge: Welle 1“.

Zugangsdaten (ntfy-Token, Pushover-Schlüssel) stehen nur in den Settings (`push_targets`) und werden nie geloggt
oder in Fehlermeldungen übernommen; auch die Server-URL/das Topic (bei ntfy.sh faktisch ein Passwort) nicht.
`send()` ist synchron (httpx, Timeout 10 s, 1 Wiederholung bei Netzwerkfehler/5xx) und läuft im Thread-Pool von
`notifications.py` bzw. im Threadpool des Routers (`POST /settings/push_test`).
"""
from __future__ import annotations

import base64
import logging
import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

import httpx

from .util import media_path

log = logging.getLogger("vg.push")

DEFAULT_NTFY_SERVER = "https://ntfy.sh"
DEFAULT_PUSHOVER_SERVER = "https://api.pushover.net"
TIMEOUT_S = 10.0
RETRIES = 1
MAX_IMAGE_BYTES = 2_500_000          # Pushover-Grenze (ntfy.sh ohne Konto: 15 MB) – darüber ohne Bild
IMAGE_WAIT_S = 8.0                   # Snapshot einer Regel entsteht erst Sekunden nach der Auslösung

NTFY_PRIORITY = {"low": 2, "default": 3, "high": 4}
PUSHOVER_PRIORITY = {"low": -1, "default": 0, "high": 1}


@dataclass
class PushMessage:
    title: str
    message: str
    priority: str = "default"                 # low|default|high
    click_url: Optional[str] = None           # absolut (nur wenn public_base_url gesetzt)
    image_url: Optional[str] = None           # absolute, signierte URL → ntfy „Attach“ (kein Upload)
    image_path: Optional[str] = None          # relativer Medienpfad → Upload (ntfy PUT, Pushover attachment)
    tags: list[str] = field(default_factory=list)


def _result(target: dict[str, Any], ok: bool, error: Optional[str] = None) -> dict[str, Any]:
    ts = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    return {"target": target.get("id"), "ok": ok, "status": "sent" if ok else "failed", "error": error, "ts": ts}


def _rfc2047(value: str) -> str:
    """HTTP-Header sind ASCII: Nicht-ASCII-Text als RFC-2047-Wort (versteht ntfy)."""
    value = (value or "").replace("\r", " ").replace("\n", " ")
    try:
        value.encode("ascii")
        return value
    except UnicodeEncodeError:
        return "=?UTF-8?B?" + base64.b64encode(value.encode("utf-8")).decode("ascii") + "?="


def _read_image(rel: Optional[str], wait_s: float = IMAGE_WAIT_S) -> Optional[tuple[str, bytes]]:
    """Bilddatei aus dem Medienordner (wartet kurz, falls sie gerade erst geschrieben wird)."""
    if not rel:
        return None
    try:
        full = media_path(rel)
    except ValueError:
        return None
    deadline = time.time() + max(0.0, wait_s)
    while not os.path.isfile(full):
        if time.time() >= deadline:
            return None
        time.sleep(0.5)
    try:
        if os.path.getsize(full) > MAX_IMAGE_BYTES:
            log.info("Bild für Push zu groß (%s Bytes) – wird ohne Bild gesendet", os.path.getsize(full))
            return None
        with open(full, "rb") as f:
            return os.path.basename(full), f.read()
    except OSError:
        return None


def _error_text(resp: httpx.Response) -> str:
    """Fehlertext des Dienstes (ntfy: `error`, Pushover: `errors`) ohne URL/Zugangsdaten."""
    detail = ""
    try:
        j = resp.json()
        if isinstance(j, dict):
            if isinstance(j.get("error"), str):
                detail = j["error"]
            elif isinstance(j.get("errors"), list):
                detail = "; ".join(str(x) for x in j["errors"])[:200]
    except Exception:  # noqa: BLE001
        pass
    return f"HTTP {resp.status_code}" + (f": {detail}" if detail else "")


def _ntfy(client: httpx.Client, target: dict[str, Any], msg: PushMessage) -> httpx.Response:
    server = (target.get("server") or DEFAULT_NTFY_SERVER).rstrip("/")
    topic = target.get("topic") or ""
    headers: dict[str, str] = {}
    if target.get("token"):
        headers["Authorization"] = f"Bearer {target['token']}"
    prio = NTFY_PRIORITY.get(msg.priority, 3)
    img = None if msg.image_url else _read_image(msg.image_path)
    if img:
        # Upload als Anhang: PUT {server}/{topic}, Texte in Headern
        name, data = img
        headers.update({"Title": _rfc2047(msg.title), "Message": _rfc2047(msg.message),
                        "Priority": str(prio), "Filename": name})
        if msg.click_url:
            headers["Click"] = msg.click_url
        if msg.tags:
            headers["Tags"] = ",".join(msg.tags)
        resp = client.put(f"{server}/{topic}", content=data, headers=headers)
        # Server ohne Anhänge (ntfy-Standard ohne attachment-cache-dir: 400 „attachments not allowed“) oder Bild
        # zu groß (413): Nachricht ohne Bild senden statt ganz zu scheitern
        if not (resp.status_code == 413 or (resp.status_code == 400 and "attachment" in resp.text.lower())):
            return resp
        log.info("ntfy-Ziel '%s' nimmt keine Anhänge an – sende ohne Bild", target.get("id"))
        headers = {k: v for k, v in headers.items() if k == "Authorization"}
    body: dict[str, Any] = {"topic": topic, "title": msg.title, "message": msg.message, "priority": prio}
    if msg.click_url:
        body["click"] = msg.click_url
    if msg.image_url:
        body["attach"] = msg.image_url
    if msg.tags:
        body["tags"] = msg.tags
    return client.post(f"{server}/", json=body, headers=headers)   # JSON-Publish auf die Server-Wurzel


def _pushover(client: httpx.Client, target: dict[str, Any], msg: PushMessage) -> httpx.Response:
    server = (target.get("server") or DEFAULT_PUSHOVER_SERVER).rstrip("/")
    data = {"token": target.get("app_token") or "", "user": target.get("user_key") or "",
            "title": msg.title[:250], "message": msg.message[:1024] or " ",
            "priority": str(PUSHOVER_PRIORITY.get(msg.priority, 0))}
    if msg.click_url:
        data["url"] = msg.click_url
        data["url_title"] = "In VisionGuard öffnen"
    img = _read_image(msg.image_path)
    if img:
        name, raw = img
        return client.post(f"{server}/1/messages.json", data=data, files={"attachment": (name, raw, "image/jpeg")})
    return client.post(f"{server}/1/messages.json", data=data)


SENDERS = {"ntfy": _ntfy, "pushover": _pushover}


def send(target: dict[str, Any], msg: PushMessage) -> dict[str, Any]:
    """Sendet an ein Ziel aus `push_targets`. Ergebnis `{target, ok, status, error, ts}` (wirft nie)."""
    sender = SENDERS.get(str(target.get("type")))
    if sender is None:
        return _result(target, False, f"Unbekannter Push-Typ: {target.get('type')}")
    if target.get("enabled") is False:
        return {**_result(target, False, "Ziel deaktiviert"), "status": "skipped"}
    err = "unbekannter Fehler"
    for attempt in range(RETRIES + 1):
        try:
            with httpx.Client(timeout=TIMEOUT_S, follow_redirects=False) as client:
                resp = sender(client, target, msg)
            if resp.status_code < 400:
                log.info("Push an Ziel '%s' (%s) gesendet", target.get("id"), target.get("type"))
                return _result(target, True)
            err = _error_text(resp)
            if resp.status_code < 500 and resp.status_code != 429:
                break   # Konfigurationsfehler: Wiederholen hilft nicht
        except httpx.HTTPError as e:
            err = f"Verbindung fehlgeschlagen ({type(e).__name__})"
        except Exception as e:  # noqa: BLE001
            err = f"Fehler ({type(e).__name__})"
        if attempt < RETRIES:
            time.sleep(1.0)
    log.warning("Push an Ziel '%s' (%s) fehlgeschlagen: %s", target.get("id"), target.get("type"), err)
    return _result(target, False, err)
