import logging
import time

from sqlalchemy import create_engine, text
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from .config import config

log = logging.getLogger("vg.db")

engine = create_engine(config.database_url, pool_pre_ping=True, pool_size=10, max_overflow=20)
SessionLocal = sessionmaker(engine, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


def get_db():
    with SessionLocal() as s:
        yield s


def wait_for_db(timeout: float = 120) -> None:
    deadline = time.time() + timeout
    while True:
        try:
            with engine.connect() as c:
                c.execute(text("SELECT 1"))
            return
        except Exception as e:  # noqa: BLE001
            if time.time() > deadline:
                raise
            log.info("Warte auf Datenbank ... (%s)", e.__class__.__name__)
            time.sleep(2)


def init_db() -> None:
    from . import models  # noqa: F401

    wait_for_db()
    Base.metadata.create_all(engine)
