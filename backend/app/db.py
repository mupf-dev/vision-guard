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
    # Welle 1 (K1)
    ("rules", "trigger_options", "jsonb NOT NULL DEFAULT '{}'::jsonb"),
    ("rules", "conditions", "jsonb NOT NULL DEFAULT '[]'::jsonb"),
    ("rules", "condition_delay_s", "double precision NOT NULL DEFAULT 0"),
    ("cameras", "health_enabled", "boolean NOT NULL DEFAULT true"),
    # Welle 2 (K2)
    ("rules", "repeat_s", "double precision NOT NULL DEFAULT 0"),
    ("rules", "repeat_max", "integer NOT NULL DEFAULT 0"),
    ("events", "acknowledged_at", "timestamptz NULL"),
    ("cameras", "pose_enabled", "boolean NOT NULL DEFAULT false"),
    ("cameras", "gesture_enabled", "boolean NOT NULL DEFAULT false"),
    ("cameras", "inventory_label_min_scores", "jsonb NOT NULL DEFAULT '{}'::jsonb"),
    ("cameras", "inventory_suppress", "jsonb NOT NULL DEFAULT '[]'::jsonb"),
    ("identities", "item_label", "varchar(40) NULL"),
    ("identity_samples", "negative", "boolean NOT NULL DEFAULT false"),
    ("identity_samples", "source", "varchar(16) NOT NULL DEFAULT 'auto'"),
    ("inventory_items", "identity_id", "integer NULL"),
    ("inventory_items", "identity_score", "double precision NULL"),
    # Welle 3 (K3)
    ("cameras", "vlm_cloud_allowed", "boolean NOT NULL DEFAULT false"),
    ("cameras", "history_enabled", "boolean NOT NULL DEFAULT false"),
    ("cameras", "history_interval_s", "double precision NOT NULL DEFAULT 30"),
    ("cameras", "history_tiling", "boolean NOT NULL DEFAULT false"),
]


# Additive Index-Nachrüstung (wie ADDITIVE_COLUMNS): create_all legt Indizes nur für NEUE Tabellen an.
# Indizes für bestehende Tabellen hier eintragen (im Modell darf derselbe Index zusätzlich stehen –
# gleicher Name, dann bleibt es idempotent). Wird nach ADDITIVE_COLUMNS ausgeführt (Spalten existieren).
ADDITIVE_INDEXES: list[tuple[str, str, str]] = [
    # (Indexname, Tabelle, Spaltenliste bzw. Ausdruck), z. B.
    # Welle 1 (B2 „Was hat sich verändert?“): Zustand je Kamera zu einem Zeitpunkt
    ("ix_inventory_history_camera_ts", "inventory_history", "camera_id, ts"),
    # Welle 2 (B4): Gegenstände je Exemplar
    ("ix_inventory_items_identity_id", "inventory_items", "identity_id"),
]

# W3 (K3): Indizes der Tabellen mit Vektorspalten (nur mit pgvector). (Name, Tabelle, Spalten/Ausdruck, Methode)
VECTOR_INDEXES: list[tuple[str, str, str, str]] = [
    ("ix_history_frames_camera_ts", "history_frames", "camera_id, ts", "btree"),
    ("ix_history_frames_ts", "history_frames", "ts", "btree"),
    ("ix_history_frames_embedding", "history_frames", "embedding halfvec_cosine_ops", "hnsw"),
]

_vector: dict = {"available": False, "version": None}


def vector_available() -> bool:
    """True, wenn die pgvector-Extension in der Datenbank aktiv ist (nach init_db)."""
    return bool(_vector["available"])


def vector_version():
    return _vector["version"]


def _vector_tables() -> list:
    return [t for t in Base.metadata.sorted_tables if t.info.get("vector")]


def _init_vector() -> None:
    """CREATE EXTENSION vector; danach Tabellen mit Vektorspalten + ihre Indizes. Fehler → B1 aus, Rest läuft."""
    try:
        with engine.begin() as c:
            c.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
            ver = c.execute(text("SELECT extversion FROM pg_extension WHERE extname = 'vector'")).scalar()
    except Exception as e:  # noqa: BLE001
        _vector.update(available=False, version=None)
        log.warning("pgvector nicht verfügbar (%s) – Zeitmaschine (B1) bleibt abgeschaltet",
                    str(e).splitlines()[0][:200])
        return
    _vector.update(available=True, version=ver)
    Base.metadata.create_all(engine, tables=_vector_tables())
    for name, table, columns, method in VECTOR_INDEXES:
        if not (_IDENT.match(name) and _IDENT.match(table) and re.match(r"^[a-z]+$", method)):
            raise ValueError(f"ungültiger Eintrag in VECTOR_INDEXES: {name!r}")
        try:
            with engine.begin() as c:
                c.execute(text(f'CREATE INDEX IF NOT EXISTS "{name}" ON {table} USING {method} ({columns})'))
        except Exception as e:  # noqa: BLE001
            log.error("Vektor-Index %s auf %s konnte nicht angelegt werden: %s", name, table, e)
    log.info("pgvector %s aktiv", ver)


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
    # Tabellen mit Vektorspalten (info.vector) nur mit pgvector – siehe _init_vector
    Base.metadata.create_all(engine, tables=[t for t in Base.metadata.sorted_tables if not t.info.get("vector")])
    _add_columns()
    _add_indexes()
    _init_vector()
