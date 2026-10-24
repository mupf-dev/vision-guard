"""Aktionen nach Konfigurationsänderungen."""
import logging

from sqlalchemy import select

from . import go2rtc, registry, rules
from .db import SessionLocal
from .models import Camera
from .mqtt_bus import bus

log = logging.getLogger("vg.sync")


def load_cameras() -> list[Camera]:
    with SessionLocal() as s:
        return list(s.scalars(select(Camera).order_by(Camera.id)).all())


async def cameras_changed() -> None:
    registry.reload()
    await go2rtc.sync(load_cameras())
    bus.publish_config_changed()
    bus.work.put(("reset_counts", None, None))


def rules_changed() -> None:
    rules.reload()


def settings_changed() -> None:
    bus.publish_config_changed()
