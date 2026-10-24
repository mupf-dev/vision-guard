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


# Additive Spalten-Nachrüstung (bis Alembic kommt): create_all legt neue Spalten in bestehenden
# Tabellen nicht an. Neue Spalten hier UND im Modell (gleicher Default) eintragen; idempotent.
ADDITIVE_COLUMNS: list[tuple[str, str, str]] = [
    # (Tabelle, Spalte, Typ inkl. Default)
    ("identities", "learning_locked", "boolean NOT NULL DEFAULT false"),
    ("identity_samples", "manual", "boolean NOT NULL DEFAULT false"),
    ("cameras", "inventory_enabled", "boolean NOT NULL DEFAULT false"),
    ("cameras", "inventory_interval_s", "double precision NOT NULL DEFAULT 3"),
    ("cameras", "inventory_labels", "jsonb NOT NULL DEFAULT '[]'::jsonb"),
    ("cameras", "inventory_min_score", "double precision NOT NULL DEFAULT 0.35"),
    ("cameras", "inventory_tiling", "boolean NOT NULL DEFAULT false"),
    ("cameras", "room", "varchar(100) NULL"),
]


def _add_columns() -> None:
    with engine.begin() as c:
        for table, column, ddl in ADDITIVE_COLUMNS:
            c.execute(text(f'ALTER TABLE {table} ADD COLUMN IF NOT EXISTS "{column}" {ddl}'))


def init_db() -> None:
    from . import models  # noqa: F401

    wait_for_db()
    Base.metadata.create_all(engine)
    _add_columns()
