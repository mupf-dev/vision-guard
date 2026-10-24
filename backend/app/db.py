import logging
import re
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


# Additive Index-Nachrüstung (wie ADDITIVE_COLUMNS): create_all legt Indizes nur für NEUE Tabellen an.
# Indizes für bestehende Tabellen hier eintragen (im Modell darf derselbe Index zusätzlich stehen –
# gleicher Name, dann bleibt es idempotent). Wird nach ADDITIVE_COLUMNS ausgeführt (Spalten existieren).
ADDITIVE_INDEXES: list[tuple[str, str, str]] = [
    # (Indexname, Tabelle, Spaltenliste bzw. Ausdruck), z. B.
    # ("ix_inventory_history_cam_ts", "inventory_history", "camera_id, ts"),
]

_IDENT = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")


def _add_columns() -> None:
    with engine.begin() as c:
        for table, column, ddl in ADDITIVE_COLUMNS:
            c.execute(text(f'ALTER TABLE {table} ADD COLUMN IF NOT EXISTS "{column}" {ddl}'))


def _add_indexes() -> None:
    for name, table, columns in ADDITIVE_INDEXES:
        if not (_IDENT.match(name) and _IDENT.match(table)):
            raise ValueError(f"ungültiger Index-/Tabellenname in ADDITIVE_INDEXES: {name!r} / {table!r}")
        # eigene Transaktion je Index: ein fehlerhafter Eintrag soll die übrigen nicht verhindern
        try:
            with engine.begin() as c:
                c.execute(text(f'CREATE INDEX IF NOT EXISTS "{name}" ON {table} ({columns})'))
        except Exception as e:  # noqa: BLE001
            log.error("Index %s auf %s konnte nicht angelegt werden: %s", name, table, e)


def init_db() -> None:
    from . import models  # noqa: F401

    wait_for_db()
    Base.metadata.create_all(engine)
    _add_columns()
    _add_indexes()
