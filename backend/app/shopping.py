"""Einkaufsliste (C4). Vertrag: CONTRACT.md „Nachträge: Welle 3“, W3.F.

- Vorrats-Sensoren (`supply`) rufen bei einem bestätigten Zustand `on_supply_state(sensor, state)`. Mit
  `shopping_list_auto`: `low`/`out` → offener Eintrag (Quelle `auto`, Name = Sensorname, Notiz „knapp“/„leer“);
  `ok` → offene automatische Einträge dieses Sensors werden erledigt. Manuelle Einträge gleichen Namens werden
  nicht verdoppelt (der vorhandene offene Eintrag bekommt die Sensor-Zuordnung).
- REST in `routers/sensors.py` (`/api/shopping-list*`) nutzt `add`, `patch`, `remove`, `list_items`.
- WS `{"type":"shopping_list","action":"new|update|delete","item":ShoppingItem}`,
  MQTT extern `{prefix}/shopping_list` → `{count, items:[name]}` (retain).
"""
from __future__ import annotations

import logging
import threading
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import func, select

from .db import SessionLocal
from .models import ShoppingItem

log = logging.getLogger("vg.shopping")

_lock = threading.Lock()      # „offener Eintrag gleichen Namens → derselbe“ ohne Doppelanlage bei Gleichzeitigkeit


def _iso(dt: Optional[datetime]) -> Optional[str]:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def to_json(r: ShoppingItem) -> dict[str, Any]:
    return {"id": r.id, "name": r.name, "note": r.note or "", "source": r.source or "manual",
            "sensor_id": r.sensor_id, "created_at": _iso(r.created_at), "done_at": _iso(r.done_at),
            "done": r.done_at is not None}


def _norm(name: str) -> str:
    return " ".join((name or "").split()).casefold()


def _find_open(s, name: str) -> Optional[ShoppingItem]:
    key = _norm(name)
    for r in s.scalars(select(ShoppingItem).where(ShoppingItem.done_at.is_(None),
                                                  func.lower(ShoppingItem.name) == key.lower())).all():
        if _norm(r.name) == key:
            return r
    for r in s.scalars(select(ShoppingItem).where(ShoppingItem.done_at.is_(None))).all():
        if _norm(r.name) == key:
            return r
    return None


def list_items(done: str = "false") -> dict[str, Any]:
    with SessionLocal() as s:
        q = select(ShoppingItem)
        if done == "false":
            q = q.where(ShoppingItem.done_at.is_(None)).order_by(ShoppingItem.created_at, ShoppingItem.id)
        elif done == "true":
            q = q.where(ShoppingItem.done_at.is_not(None)).order_by(ShoppingItem.done_at.desc())
        else:
            q = q.order_by(ShoppingItem.done_at.is_not(None), ShoppingItem.created_at.desc())
        items = [to_json(r) for r in s.scalars(q.limit(500)).all()]
        open_count = s.scalar(select(func.count()).select_from(ShoppingItem)
                              .where(ShoppingItem.done_at.is_(None))) or 0
    return {"items": items, "open_count": int(open_count)}


def add(name: str, note: str = "", *, source: str = "manual",
        sensor_id: Optional[int] = None) -> tuple[dict[str, Any], bool]:
    """Neuen offenen Eintrag anlegen. Gibt es schon einen offenen gleichen Namens: diesen (Notiz ergänzt).
    → (ShoppingItem, neu angelegt)."""
    name = " ".join((name or "").split())[:100]
    note = (note or "").strip()[:300]
    with _lock, SessionLocal() as s:
        r = _find_open(s, name)
        created = r is None
        if r is None:
            r = ShoppingItem(name=name, note=note, source=source, sensor_id=sensor_id)
            s.add(r)
        else:
            changed = False
            if note and note not in (r.note or ""):
                r.note = note if not r.note or source == "auto" else f"{r.note}; {note}"
                changed = True
            if sensor_id and not r.sensor_id:
                r.sensor_id = sensor_id
                changed = True
            if not changed:
                return to_json(r), False
        s.commit()
        s.refresh(r)
        data = to_json(r)
    _emit("new" if created else "update", data)
    return data, created


def patch(item_id: int, *, done: Optional[bool] = None, name: Optional[str] = None,
          note: Optional[str] = None) -> Optional[dict[str, Any]]:
    with SessionLocal() as s:
        r = s.get(ShoppingItem, item_id)
        if r is None:
            return None
        if name is not None:
            r.name = " ".join(name.split())[:100] or r.name
        if note is not None:
            r.note = note.strip()[:300]
        if done is not None:
            if done and r.done_at is None:
                r.done_at = datetime.now(timezone.utc)
            elif not done:
                r.done_at = None
        s.commit()
        s.refresh(r)
        data = to_json(r)
    _emit("update", data)
    return data


def remove(item_id: int) -> bool:
    with SessionLocal() as s:
        r = s.get(ShoppingItem, item_id)
        if r is None:
            return False
        data = to_json(r)
        s.delete(r)
        s.commit()
    _emit("delete", data)
    return True


def on_supply_state(sensor: dict[str, Any], state: str) -> None:
    """Vorrats-Sensor hat einen bestätigten Zustand (ok|low|out)."""
    from . import i18n, settings_store
    if not settings_store.get().get("shopping_list_auto", True):
        return
    lg = i18n.system_lang()         # Einträge werden gespeichert: Systemsprache
    sid = int(sensor.get("id") or 0)
    name = str(sensor.get("name") or i18n.t("sensors.sensor_fallback", lg, id=sid))
    if state in ("low", "out"):
        note = i18n.t(f"sensors.state.{state}", lg)
        with SessionLocal() as s:
            r = s.scalars(select(ShoppingItem).where(ShoppingItem.sensor_id == sid,
                                                     ShoppingItem.done_at.is_(None))).first()
            if r is not None:
                if r.source == "auto" and r.note != note:
                    r.note = note
                    s.commit()
                    s.refresh(r)
                    _emit("update", to_json(r))
                return
        add(name, note, source="auto", sensor_id=sid)
        log.info("Einkaufsliste: %s (%s) eingetragen", name, note)
    elif state == "ok":
        now = datetime.now(timezone.utc)
        changed = []
        with SessionLocal() as s:
            for r in s.scalars(select(ShoppingItem).where(ShoppingItem.sensor_id == sid,
                                                          ShoppingItem.source == "auto",
                                                          ShoppingItem.done_at.is_(None))).all():
                r.done_at = now
                changed.append(r)
            if changed:
                s.commit()
                for r in changed:
                    s.refresh(r)
                data = [to_json(r) for r in changed]
            else:
                data = []
        for d in data:
            _emit("update", d)
        if data:
            log.info("Einkaufsliste: %s wieder vorrätig – erledigt", name)


def on_sensor_deleted(sensor_id: int) -> None:
    """Sensor gelöscht: Einträge bleiben, verlieren aber die Zuordnung."""
    with SessionLocal() as s:
        rows = s.scalars(select(ShoppingItem).where(ShoppingItem.sensor_id == sensor_id)).all()
        for r in rows:
            r.sensor_id = None
        if rows:
            s.commit()


def _emit(action: str, item: dict[str, Any]) -> None:
    try:
        from .ws import hub
        hub.broadcast({"type": "shopping_list", "action": action, "item": item})
    except Exception:  # noqa: BLE001
        pass
    publish_external()


def publish_external() -> None:
    """`{prefix}/shopping_list` → {count, items:[name]} (retain)."""
    try:
        from . import settings_store
        from .mqtt_bus import bus
        with SessionLocal() as s:
            names = list(s.scalars(select(ShoppingItem.name).where(ShoppingItem.done_at.is_(None))
                                   .order_by(ShoppingItem.created_at, ShoppingItem.id)).all())
        bus.publish(f"{settings_store.get()['mqtt_prefix']}/shopping_list", {"count": len(names), "items": names},
                    retain=True, qos=1)
    except Exception as e:  # noqa: BLE001
        log.debug("MQTT extern shopping_list: %s", e)
