"""pgvector-Typen für SQLAlchemy ohne Zusatzpaket (W3, K3).

    from .vector import HalfVec, to_pg
    embedding: Mapped[list[float]] = mapped_column(HalfVec(768))
    q = select(HistoryFrame).order_by(HistoryFrame.embedding.cosine_distance(vec)).limit(10)

Werte gehen als Text-Literal `'[0.1,0.2,…]'` an die Datenbank (psycopg sendet str mit unbekanntem Typ, PostgreSQL
leitet `halfvec` aus der Spalte bzw. dem Operator ab) und kommen als `list[float]` zurück. Die Extension prüft
`db.vector_available()`; ohne pgvector werden Tabellen mit diesem Typ nicht angelegt.
"""
from __future__ import annotations

from typing import Any, Iterable, Optional

from sqlalchemy import Float, cast
from sqlalchemy.types import UserDefinedType


def to_pg(vec: Iterable[float]) -> str:
    """Vektor als pgvector-Textliteral (`[1,2,3]`)."""
    return "[" + ",".join(repr(float(x)) for x in vec) + "]"


def from_pg(value: Any) -> Optional[list[float]]:
    if value is None:
        return None
    if isinstance(value, (list, tuple)):
        return [float(x) for x in value]
    s = str(value).strip().strip("[]")
    return [float(x) for x in s.split(",")] if s else []


class _VectorBase(UserDefinedType):
    cache_ok = True
    type_name = "vector"

    def __init__(self, dim: Optional[int] = None):
        self.dim = dim

    def get_col_spec(self, **kw: Any) -> str:
        return f"{self.type_name}({self.dim})" if self.dim else self.type_name

    def bind_processor(self, dialect):
        def process(value):
            if value is None or isinstance(value, str):
                return value
            return to_pg(value)
        return process

    def result_processor(self, dialect, coltype):
        return from_pg

    def bind_expression(self, bindvalue):
        # explizit casten: in Ausdrücken wie `embedding <=> :p` kann PostgreSQL den Typ sonst nicht ableiten
        return cast(bindvalue, _RawType(self.get_col_spec()))

    class comparator_factory(UserDefinedType.Comparator):
        def cosine_distance(self, other: Any):
            return self.op("<=>", return_type=Float)(_literal(other, self.type))

        def l2_distance(self, other: Any):
            return self.op("<->", return_type=Float)(_literal(other, self.type))

        def max_inner_product(self, other: Any):
            return self.op("<#>", return_type=Float)(_literal(other, self.type))


class _RawType(UserDefinedType):
    """Hilfstyp nur für den Cast-Ausdruck `CAST(:p AS halfvec(768))` (ohne eigene bind_expression → keine Rekursion)."""
    cache_ok = True

    def __init__(self, spec: str):
        self.spec = spec

    def get_col_spec(self, **kw: Any) -> str:
        return self.spec


def _literal(other: Any, typ: _VectorBase):
    from sqlalchemy import bindparam

    if hasattr(other, "__clause_element__") or hasattr(other, "compile"):
        return other
    return bindparam(None, other, type_=typ)


class Vector(_VectorBase):
    cache_ok = True        # SQLAlchemy liest cache_ok je Klasse (nicht geerbt)
    type_name = "vector"


class HalfVec(_VectorBase):
    cache_ok = True
    type_name = "halfvec"


__all__ = ["HalfVec", "Vector", "to_pg", "from_pg"]
