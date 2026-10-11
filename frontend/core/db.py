"""PostgreSQL access layer for the dashboard.

The dashboard runs on the HOST (not in Docker), so it connects to the
docker-compose Postgres through the host-mapped port (localhost:5432
by default). Every setting can be overridden with environment variables:

    DB_HOST, DB_PORT, DB_NAME, DB_USER, DB_PASSWORD,
    DB_CONNECT_TIMEOUT (seconds), DASHBOARD_CACHE_TTL (seconds)

Design notes:
- All failures are converted into `DatabaseUnavailableError` /
  `QueryError` so pages can show friendly messages instead of
  tracebacks.
- The driver is `psycopg` (v3). If only `psycopg2` is installed the
  layer falls back to it transparently.
"""

from __future__ import annotations

import os
from contextlib import contextmanager
from typing import Any

import pandas as pd
import streamlit as st

DB_CONFIG: dict[str, Any] = {
    "host": os.environ.get("DB_HOST", "localhost"),
    "port": int(os.environ.get("DB_PORT", "5432")),
    "dbname": os.environ.get("DB_NAME", "logs_db"),
    "user": os.environ.get("DB_USER", "loguser"),
    "password": os.environ.get("DB_PASSWORD", "logpass"),
    "connect_timeout": int(os.environ.get("DB_CONNECT_TIMEOUT", "5")),
}

CACHE_TTL = int(os.environ.get("DASHBOARD_CACHE_TTL", "30"))


class DatabaseUnavailableError(RuntimeError):
    """The dashboard could not reach PostgreSQL at all."""


class QueryError(RuntimeError):
    """PostgreSQL was reachable but the query failed."""


# ---------------------------------------------------------------------------
# Driver handling (psycopg v3 preferred, psycopg2 fallback)
# ---------------------------------------------------------------------------
try:  # pragma: no cover - exercised implicitly during runtime
    import psycopg  # type: ignore

    _DRIVER = "psycopg"
except ImportError:  # pragma: no cover
    try:
        import psycopg2  # type: ignore

        _DRIVER = "psycopg2"
    except ImportError:
        psycopg = None  # type: ignore
        psycopg2 = None  # type: ignore
        _DRIVER = None


def driver_name() -> str | None:
    return _DRIVER


def _connect():
    if _DRIVER is None:
        raise DatabaseUnavailableError(
            "No PostgreSQL driver installed. Run `pip install \"psycopg[binary]\"` "
            "(or `pip install psycopg2-binary`)."
        )
    try:
        if _DRIVER == "psycopg":
            return psycopg.connect(**DB_CONFIG)
        return psycopg2.connect(**DB_CONFIG)  # type: ignore[union-attr]
    except Exception as exc:  # connection refused, auth failure, timeout...
        raise DatabaseUnavailableError(
            f"Could not connect to PostgreSQL at "
            f"{DB_CONFIG['host']}:{DB_CONFIG['port']}/{DB_CONFIG['dbname']} "
            f"as {DB_CONFIG['user']}: {exc}"
        ) from exc


def run_query(sql: str, params: dict[str, Any] | None = None) -> pd.DataFrame:
    """Run a read-only query and return the result as a DataFrame.

    Timestamp columns named `timestamp` (or ending in `_at`) are
    converted to timezone-aware UTC datetimes.
    """
    conn = _connect()
    try:
        if _DRIVER == "psycopg":
            with conn.cursor() as cur:
                cur.execute(sql, params or {})
                return _to_frame(cur)
        else:  # pragma: no cover - psycopg2 fallback
            try:
                with conn.cursor() as cur:
                    cur.execute(sql, params or {})
                    return _to_frame(cur)
            finally:
                conn.rollback()
    except (DatabaseUnavailableError, QueryError):
        raise
    except Exception as exc:
        raise QueryError(f"Query failed: {exc}") from exc
    finally:
        try:
            conn.close()
        except Exception:
            pass


def _to_frame(cur) -> pd.DataFrame:
    if cur.description is None:
        return pd.DataFrame()
    columns = [col.name for col in cur.description]
    rows = cur.fetchall()
    df = pd.DataFrame(rows, columns=columns)
    for col in df.columns:
        if col == "timestamp" or col.endswith("_at"):
            try:
                df[col] = pd.to_datetime(df[col], utc=True)
            except (TypeError, ValueError):
                pass
    return df


# ---------------------------------------------------------------------------
# Convenience helpers
# ---------------------------------------------------------------------------
def check_connection() -> str:
    """Return a short human-readable connection status string."""
    if _DRIVER is None:
        return "No PostgreSQL driver installed"
    try:
        run_query("SELECT 1 AS ok")
        return "connected"
    except DatabaseUnavailableError as exc:
        return str(exc)


def connection_target() -> str:
    return (
        f"{DB_CONFIG['host']}:{DB_CONFIG['port']}/{DB_CONFIG['dbname']} "
        f"(user: {DB_CONFIG['user']})"
    )


@contextmanager
def db_error_handler():
    """Wrap a page's queries; stop the page with a friendly error on failure."""
    try:
        yield
    except DatabaseUnavailableError as exc:
        st.error(
            " could not reach PostgreSQL — showing no data.\n\n"
            f"{exc}\n\n"
            "To fix this, start the backend stack (`docker compose up -d`) and make "
            "sure the connection settings are correct (see the sidebar), or set "
            "`DB_HOST`, `DB_PORT`, `DB_NAME`, `DB_USER`, `DB_PASSWORD` environment "
            "variables."
        )
        st.stop()
    except QueryError as exc:
        st.error(f" A database query failed: {exc}")
        st.stop()
