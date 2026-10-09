"""Engine creation only: schema is managed by Alembic."""
import os
from sqlalchemy import create_engine
from sqlalchemy.engine import make_url


def database_url(explicit: str | None = None) -> str:
    value = explicit if explicit is not None else os.environ.get("DATABASE_URL")
    if not value:
        raise RuntimeError("DATABASE_URL is required; see the training README")
    return value


def make_engine(url: str):
    parsed = make_url(url)
    if parsed.drivername == "postgresql+psycopg":
        return create_engine(
            url, pool_pre_ping=True, pool_timeout=3,
            connect_args={"connect_timeout": 3, "options": "-c statement_timeout=5000"},
        )
    if parsed.drivername == "sqlite":
        # Used by isolated tests, not the PostgreSQL operating exercise.
        return create_engine(url, connect_args={"check_same_thread": False, "timeout": 3})
    raise RuntimeError("Use postgresql+psycopg for the lab or sqlite for isolated tests")
