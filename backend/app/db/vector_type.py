"""pgvector SQLAlchemy type with an import-safe development fallback."""

from __future__ import annotations

try:
    from pgvector.sqlalchemy import Vector as Vector
except ImportError:  # pragma: no cover - only supports local SQLite tooling
    from sqlalchemy.types import UserDefinedType

    class Vector(UserDefinedType):
        cache_ok = True

        def __init__(self, dimensions: int | None = None):
            self.dimensions = dimensions

        def get_col_spec(self, **kw) -> str:
            del kw
            return f"VECTOR({self.dimensions})" if self.dimensions else "VECTOR"
