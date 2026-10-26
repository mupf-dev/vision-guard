"""Notifications – zentrale Sammelstelle aller proaktiven Meldungen (CONTRACT.md „Nachträge: Welle 1“).

Jede Quelle (Regel-Aktion `push`, Regeln ohne Event wie `trigger=time`, Kamera-Gesundheit, später Tiere,
Rückfragen, Tagesbericht, Sensoren) ruft `create(...)` auf. Das speichert die Meldung, sendet WS
`{"type":"notification","action":"new"}`, veröffentlicht extern `{prefix}/notifications` (signierte URLs) und
stellt Push asynchron zu (Thread-Pool, blockiert nie den Track-Thread). Das Zustellergebnis landet in
`delivery` und wird per WS `action:"update"` nachgereicht.

Ziele: `targets=None` → bei `source="rule"` alle aktiven Push-Ziele, sonst `notify_routes[source]`;
`targets=[…]` → genau diese Ziel-IDs; `targets=[]` bzw. `push=False` → nur speichern/anzeigen.
"""
from __future__ import annotations

import copy
import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from sqlalchemy import func, select

from . import i18n, media_sign, push, settings_store
from .config import config
from .db import SessionLocal
from .models import Notification, utcnow
from .schemas import NOTIFY_SOURCES, PUSH_SECRET_FIELDS
from .secrets_mask import MASK
from .serializers import notification_json
from .ws import hub

log = logging.getLogger("vg.notifications")

LEVELS = ("info", "warning", "alarm")
_pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="vg-push")
_lock = threading.Lock()   # Dedup/Tageslimit atomar prüfen + anlegen


# ------------------------------------------------------------------ Settings-Maskierung


def mask_settings(data: dict[str, Any]) -> dict[str, Any]:
    """Kopie der Settings, in der Push-Zugangsdaten als "***" stehen (GET/PUT /settings, internal/settings)."""
    out = dict(data)
    targets = []
    for t in data.get("push_targets") or []:
        t = dict(t)
        for k in PUSH_SECRET_FIELDS:
            if t.get(k):
                t[k] = MASK
        targets.append(t)
    out["push_targets"] = targets
    return out


class SecretReuseError(ValueError):
    """Ein maskiertes Geheimnis ("***") soll für ein Ziel mit geänderter Art/Server wiederverwendet werden."""


def _norm_server(v: Any) -> str:
    return str(v or "").strip().rstrip("/").lower()


def unmask_target(new: dict[str, Any], stored: list[dict[str, Any]]) -> dict[str, Any]:
    """Ersetzt "***" in einem Push-Ziel durch den gespeicherten Wert des Ziels mit derselben id.

    Refinement 1: nur, wenn `type` und `server` unverändert sind – sonst könnte man ein gespeichertes Geheimnis
    (ntfy-Token, Topic, Pushover-Schlüssel) an einen fremden Server schicken lassen (`push_test`). Dann
    `SecretReuseError` (Router → 422)."""
    old = next((t for t in stored if t.get("id") == new.get("id")), None) or {}
    out = dict(new)
    masked = [k for k in PUSH_SECRET_FIELDS if out.get(k) == MASK]
    if masked and old and (old.get("type") != out.get("type")
                           or _norm_server(old.get("server")) != _norm_server(out.get("server"))):
        raise SecretReuseError(i18n.t("notify.secret_reuse"))
    for k in masked:
        out[k] = old.get(k)
    return out


def unmask_push_targets(new: list[dict[str, Any]], stored: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [unmask_target(t, stored) for t in new or []]


# ------------------------------------------------------------------ Ziele


def resolve_targets(source: str, targets: Optional[list[str]]) -> list[dict[str, Any]]:
    """Push-Ziele (vollständige Konfiguration) für eine Meldung; unbekannte IDs werden ignoriert."""
    st = settings_store.get()
    all_targets = [t for t in st.get("push_targets") or [] if t.get("enabled", True)]
    if targets is None:
        if source == "rule":
            return all_targets
        targets = list((st.get("notify_routes") or {}).get(source) or [])
    by_id = {t["id"]: t for t in all_targets}
    missing = [t for t in targets if t not in by_id]
    if missing:
        log.info("Push-Ziel(e) %s unbekannt oder deaktiviert – übersprungen", ", ".join(missing))
    return [by_id[t] for t in dict.fromkeys(targets) if t in by_id]


def _local_midnight_utc() -> datetime:
    now = datetime.now(timezone.utc).astimezone(config.tz)
    return now.replace(hour=0, minute=0, second=0, microsecond=0).astimezone(timezone.utc)


# ------------------------------------------------------------------ Anlegen


def create(*, source: str, title: str, message: str, level: str = "info",
           rule_id: Optional[int] = None, rule_name: Optional[str] = None,
           camera_id: Optional[int] = None, event_id: Optional[str] = None,
           item_ids: Optional[list[int]] = None, image_path: Optional[str] = None,
           data: Optional[dict[str, Any]] = None, targets: Optional[list[str]] = None,
           push_enabled: bool = True, priority: str = "default",
           dedup_key: Optional[str] = None, dedup_s: float = 0,
           actions: Optional[list[dict[str, Any]]] = None,
           click_path: Optional[str] = None) -> Optional[dict[str, Any]]:
    """Legt eine Notification an und stellt sie zu. Liefert Notification-JSON oder None (Dedup).

    Threadsicher und synchron schnell (DB-Insert); Push läuft im Hintergrund. `dedup_key` + `dedup_s`:
    gab es in den letzten `dedup_s` Sekunden schon eine Meldung mit diesem Schlüssel, passiert nichts.
    Tageslimit `push_max_per_rule_per_day` (nur `source="rule"` mit `rule_id`): Meldung wird gespeichert,
    Push aber übersprungen (`delivery[].status = "skipped"`).
    `actions` (W2, D2): Knöpfe für ntfy (`{action:"http", label, url, method, clear}`), werden nicht gespeichert;
    `click_path`: Ziel beim Antippen (relativ zu `public_base_url`, Default `/hinweise`)."""
    if source not in NOTIFY_SOURCES:
        raise ValueError(f"unbekannte Quelle: {source}")
    if level not in LEVELS:
        level = "info"
    st = settings_store.get()
    tgts = resolve_targets(source, targets) if push_enabled else []
    with _lock, SessionLocal() as s:
        now = utcnow()
        if dedup_key and dedup_s > 0:
            dup = s.scalar(select(Notification.id).where(
                Notification.dedup_key == dedup_key, Notification.ts >= now - timedelta(seconds=dedup_s)).limit(1))
            if dup is not None:
                log.debug("Notification %s unterdrückt (Dedup)", dedup_key)
                return None
        skip_reason = None
        limit = int(st.get("push_max_per_rule_per_day") or 0)
        if tgts and source == "rule" and rule_id is not None and limit > 0:
            today = s.scalar(select(func.count()).select_from(Notification).where(
                Notification.rule_id == rule_id, Notification.source == "rule",
                Notification.ts >= _local_midnight_utc(),
                Notification.data["pushed"].as_boolean().is_(True))) or 0
            if today >= limit:
                skip_reason = i18n.t("notify.daily_limit", limit=limit)
        delivery = [{"target": t["id"], "ok": False, "status": "skipped" if skip_reason else "pending",
                     "error": skip_reason, "ts": None} for t in tgts]
        n = Notification(ts=now, source=source, level=level, rule_id=rule_id, rule_name=rule_name,
                         title=(title or "")[:200], message=message or "", camera_id=camera_id,
                         event_id=event_id, item_ids=list(item_ids or []), image_path=image_path or None,
                         data={**(data or {}), "pushed": bool(tgts) and not skip_reason},
                         delivery=delivery, dedup_key=dedup_key)
        s.add(n)
        s.commit()
        s.refresh(n)
        out = notification_json(n)
    log.info("Notification %s (%s/%s): %s", out["id"], source, level, out["title"])
    hub.broadcast({"type": "notification", "action": "new", "notification": out})
    _publish_external(out)
    if tgts and not skip_reason:
        _pool.submit(_deliver, out["id"], tgts, _push_message(out, priority, actions, click_path))
    return out


def _push_message(n: dict[str, Any], priority: str, actions: Optional[list[dict[str, Any]]] = None,
                  click_path: Optional[str] = None) -> push.PushMessage:
    base = (settings_store.get().get("public_base_url") or "").rstrip("/")
    rel = media_sign.normalize_path(n["image_url"]) if n.get("image_url") else None
    image_url = None
    if rel and base:
        image_url = media_sign.sign_media_path(rel, media_sign.default_ttl_s())   # absolut, weil base gesetzt
    tags = {"alarm": ["rotating_light"], "warning": ["warning"]}.get(n.get("level") or "", [])
    return push.PushMessage(title=n.get("title") or "VisionGuard", message=n.get("message") or "",
                            priority=priority if priority in push.NTFY_PRIORITY else "default",
                            click_url=f"{base}{click_path or '/hinweise'}" if base else None,
                            image_url=image_url, image_path=None if image_url else rel, tags=tags,
                            actions=list(actions or []))


def _deliver(notification_id: int, targets: list[dict[str, Any]], msg: push.PushMessage) -> None:
    results = [push.send(t, msg) for t in targets]
    try:
        with SessionLocal() as s:
            n = s.get(Notification, notification_id)
            if n is None:
                return
            n.delivery = results
            s.commit()
            s.refresh(n)
            out = notification_json(n)
        hub.broadcast({"type": "notification", "action": "update", "notification": out})
    except Exception:
        log.exception("Zustellstatus für Notification %s nicht gespeichert", notification_id)


def _publish_external(n: dict[str, Any]) -> None:
    try:
        from .mqtt_bus import bus   # zyklischen Import vermeiden

        prefix = settings_store.get()["mqtt_prefix"]
        payload = media_sign.sign_urls(copy.deepcopy(n))
        bus.publish(f"{prefix}/notifications", payload, qos=1)
    except Exception as e:  # noqa: BLE001
        log.warning("MQTT {prefix}/notifications fehlgeschlagen: %s", e)


def delete(notification_id: int) -> bool:
    with SessionLocal() as s:
        n = s.get(Notification, notification_id)
        if n is None:
            return False
        s.delete(n)
        s.commit()
    hub.broadcast({"type": "notification", "action": "delete", "notification": {"id": notification_id}})
    return True
