"""Aufräumen: alte Events (DB) und alte Mediendateien."""
import logging
import os
import time
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, select

from . import settings_store
from .config import config
from .db import SessionLocal
from .models import Event
from .util import safe_remove

log = logging.getLogger("vg.retention")

MEDIA_SUBDIRS = ("snapshots", "crops", "clips")


def run_once() -> dict:
    st = settings_store.get()
    ev_days = int(st["retention_days_events"])
    media_days = int(st["retention_days_media"])
    now = datetime.now(timezone.utc)
    removed_events = 0
    with SessionLocal() as s:
        cutoff = now - timedelta(days=ev_days)
        rows = s.execute(select(Event.id, Event.snapshot_path, Event.clip_path, Event.thumb_path)
                         .where(Event.start_ts < cutoff, Event.active.is_(False))).all()
        for _, snap, clip, thumb in rows:
            for p in (snap, clip, thumb):
                safe_remove(p)
        if rows:
            s.execute(delete(Event).where(Event.id.in_([r[0] for r in rows])))
            s.commit()
        removed_events = len(rows)
        # Unbenannte, ungesperrte Identitäten ohne Events (z. B. nach Event-Aufräumen) entfernen
        try:
            from .identity import cleanup_orphans  # zyklischen Import vermeiden
            gone = cleanup_orphans(s, ev_days)
            if gone:
                log.info("Aufräumen: %d unbenannte Identitäten ohne Events entfernt", len(gone))
        except Exception as e:  # noqa: BLE001
            s.rollback()
            log.warning("Aufräumen der Identitäten fehlgeschlagen: %s", e)

    removed_files = 0
    limit = time.time() - media_days * 86400
    for sub in MEDIA_SUBDIRS:
        root = os.path.join(config.media_dir, sub)
        if not os.path.isdir(root):
            continue
        for dirpath, dirnames, filenames in os.walk(root, topdown=False):
            for fn in filenames:
                fp = os.path.join(dirpath, fn)
                try:
                    if os.path.getmtime(fp) < limit:
                        os.remove(fp)
                        removed_files += 1
                except OSError:
                    pass
            if dirpath != root:
                try:
                    os.rmdir(dirpath)  # nur wenn leer
                except OSError:
                    pass
    if removed_files:
        _clear_missing_media(now - timedelta(days=media_days - 1))
    if removed_events or removed_files:
        log.info("Aufräumen: %d Events, %d Dateien entfernt", removed_events, removed_files)
    try:
        inv = _inventory_retention(now, int(st.get("retention_days_inventory") or 30))
    except Exception as e:  # noqa: BLE001
        log.warning("Aufräumen der Gegenstände fehlgeschlagen: %s", e)
        inv = {}
    try:
        w1 = _w1_retention(now, int(st.get("retention_days_notifications") or 30), ev_days)
    except Exception as e:  # noqa: BLE001
        log.warning("Aufräumen der Hinweise/Gesundheits-Zeitleiste fehlgeschlagen: %s", e)
        w1 = {}
    return {"events": removed_events, "files": removed_files, "inventory": inv, **w1}


def _w1_retention(now: datetime, notif_days: int, health_days: int) -> dict:
    """W1: Notifications älter als `retention_days_notifications`; abgeschlossene Einträge der
    Kamera-Gesundheit (`camera_health_log`) älter als `retention_days_events`. Bilder der Hinweise sind
    Snapshots/Crops und folgen deren Aufbewahrung."""
    from .models import CameraHealthLog, Notification

    with SessionLocal() as s:
        n = s.execute(delete(Notification).where(Notification.ts < now - timedelta(days=notif_days))).rowcount or 0
        h = s.execute(delete(CameraHealthLog).where(CameraHealthLog.end_ts.is_not(None),
                                                    CameraHealthLog.end_ts < now - timedelta(days=health_days))
                      ).rowcount or 0
        s.commit()
    if n or h:
        log.info("Aufräumen: %d Hinweise, %d Gesundheits-Einträge entfernt", n, h)
    return {"notifications": n, "health_log": h}


def _inventory_retention(now: datetime, days: int) -> dict:
    """Gegenstände: Historie und nicht sichtbare Gegenstände älter als `retention_days_inventory` löschen,
    Dateien unter inventory/ ebenso – außer solchen, auf die ein Gegenstand oder die Historie noch zeigt."""
    from .models import InventoryHistory, InventoryItem

    from .inventory import memory

    cutoff = now - timedelta(days=days)
    # unter memory.lock: der MQTT-Thread (InventoryMemory._handle) ändert sonst evtl. gerade dieselben Zeilen
    with memory.lock, SessionLocal() as s:
        h = s.execute(delete(InventoryHistory).where(InventoryHistory.ts < cutoff)).rowcount or 0
        old_items = list(s.scalars(select(InventoryItem.id).where(InventoryItem.state != "present",
                                                                  InventoryItem.last_seen < cutoff)).all())
        if old_items:
            s.execute(delete(InventoryHistory).where(InventoryHistory.item_id.in_(old_items)))
            s.execute(delete(InventoryItem).where(InventoryItem.id.in_(old_items)))
        s.commit()
    with SessionLocal() as s:
        keep: set[str] = set()
        for crop, snap in s.execute(select(InventoryItem.crop_path, InventoryItem.snapshot_path)).all():
            keep.update(p for p in (crop, snap) if p)
        keep.update(p for p in s.scalars(select(InventoryHistory.crop_path).distinct()).all() if p)
        # W2 (D2): Bilder offener Rückfragen und der Beispielsammlung bleiben erhalten
        from .models import InventoryExample, ReviewRequest
        for crop, snap in s.execute(select(ReviewRequest.crop_path, ReviewRequest.snapshot_path)
                                    .where(ReviewRequest.status == "open")).all():
            keep.update(p for p in (crop, snap) if p)
        keep.update(p for p in s.scalars(select(InventoryExample.crop_path).distinct()).all() if p)
    keep_abs = {os.path.realpath(os.path.join(config.media_dir, p.lstrip("/"))) for p in keep}
    root = os.path.join(config.media_dir, "inventory")
    limit = time.time() - days * 86400
    files = 0
    if os.path.isdir(root):
        for dirpath, _dirnames, filenames in os.walk(root, topdown=False):
            for fn in filenames:
                fp = os.path.join(dirpath, fn)
                try:
                    if os.path.getmtime(fp) < limit and os.path.realpath(fp) not in keep_abs:
                        os.remove(fp)
                        files += 1
                except OSError:
                    pass
            if dirpath != root:
                try:
                    os.rmdir(dirpath)  # nur wenn leer
                except OSError:
                    pass
    if h or old_items or files:
        log.info("Aufräumen Gegenstände: %d Verlaufseinträge, %d Gegenstände, %d Dateien entfernt",
                 h, len(old_items), files)
    if old_items:
        with memory.lock:
            for i in old_items:
                memory.last_history.pop(i, None)
                memory.last_ws.pop(i, None)
    return {"history": h, "items": len(old_items), "files": files}


def _clear_missing_media(before: datetime) -> None:
    """Verweise auf gelöschte Dateien in Events auf null setzen (Thumbnail bleibt als Verweis erhalten)."""
    with SessionLocal() as s:
        changed = 0
        q = select(Event).where(Event.start_ts < before,
                                (Event.snapshot_path.is_not(None)) | (Event.clip_path.is_not(None)))
        for ev in s.scalars(q):
            for attr in ("snapshot_path", "clip_path"):
                p = getattr(ev, attr)
                if p and not os.path.exists(os.path.join(config.media_dir, p)):
                    setattr(ev, attr, None)
                    changed += 1
        if changed:
            s.commit()
