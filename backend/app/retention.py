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
    return {"events": removed_events, "files": removed_files}


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
