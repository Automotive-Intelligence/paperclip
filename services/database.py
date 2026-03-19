"""
services/database.py - Retry-capable database helpers for Paperclip.
"""

# AIBOS Operating Foundation
# ================================
# This system is built on servant leadership.
# Every agent exists to serve the human it works for.
# Every decision prioritizes people over profit.
# Every interaction is conducted with honesty,
# dignity, and genuine care for the other person.
# We build tools that give power back to the small
# business owner — not tools that extract from them.
# We operate with excellence because excellence
# honors the gifts we've been given.
# We do not deceive. We do not manipulate.
# We do not build features that harm the vulnerable.
# Profit is the outcome of service, not the purpose.
# ================================

import logging
import os
import time
from contextlib import contextmanager
from typing import Any, List, Optional, Tuple

from services.errors import DatabaseError

logger = logging.getLogger(__name__)

# Transient psycopg2 error codes that warrant a retry.
# Class 08 = connection exceptions, Class 57 = operator intervention,
# Class 53 = insufficient resources (e.g. too many connections).
_RETRYABLE_PGCODES = frozenset({
    "08000", "08003", "08006", "08001", "08004",  # connection failures
    "57P01", "57P02", "57P03",                     # admin shutdown / crash
    "53300",                                        # too_many_connections
})

_MAX_ATTEMPTS = 3
_BACKOFF_SECONDS = 0.5


def _normalize_url(url: str) -> str:
    if url.startswith("postgres://"):
        return "postgresql://" + url[len("postgres://"):]
    return url


def _get_url() -> str:
    return _normalize_url((os.getenv("DATABASE_URL") or "").strip())


def _is_retryable(exc: Exception) -> bool:
    pgcode = getattr(exc, "pgcode", None)
    if pgcode and str(pgcode) in _RETRYABLE_PGCODES:
        return True
    msg = str(exc).lower()
    # Catch network-level transients that don't carry a pgcode
    return any(phrase in msg for phrase in (
        "connection refused", "broken pipe", "ssl connection", "server closed", "timeout"
    ))


@contextmanager
def _connect(url: str):
    """Open a single-use psycopg2 connection with auto commit/rollback/close."""
    try:
        import psycopg2 as psycopg  # type: ignore
    except ImportError as e:
        raise DatabaseError("connect", "psycopg2 not installed") from e

    conn = psycopg.connect(url, connect_timeout=5)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def execute_query(
    sql: str,
    params: Tuple[Any, ...] = (),
    *,
    url: Optional[str] = None,
) -> None:
    """Execute a write query (INSERT / UPDATE / DELETE / DDL) with retry.

    Args:
        sql:    SQL statement to execute.
        params: Positional parameters for the statement.
        url:    Override DATABASE_URL. Defaults to env var.

    Raises:
        DatabaseError: after all retry attempts are exhausted.
    """
    db_url = url or _get_url()
    if not db_url:
        raise DatabaseError("execute_query", "DATABASE_URL is not configured.")

    last_exc: Optional[Exception] = None
    for attempt in range(1, _MAX_ATTEMPTS + 1):
        try:
            with _connect(db_url) as conn:
                with conn.cursor() as cur:
                    cur.execute(sql, params)
            return
        except Exception as exc:
            last_exc = exc
            retryable = _is_retryable(exc)
            logger.warning(
                "[DB] execute_query attempt %d/%d failed (retryable=%s): %s",
                attempt, _MAX_ATTEMPTS, retryable, exc,
            )
            if not retryable or attempt == _MAX_ATTEMPTS:
                break
            time.sleep(_BACKOFF_SECONDS * attempt)

    raise DatabaseError(
        "execute_query",
        str(last_exc),
        retryable=_is_retryable(last_exc) if last_exc else False,
    ) from last_exc


def fetch_all(
    sql: str,
    params: Tuple[Any, ...] = (),
    *,
    url: Optional[str] = None,
) -> List[Tuple[Any, ...]]:
    """Execute a read query and return all rows, with retry on transient errors.

    Args:
        sql:    SELECT statement.
        params: Positional parameters.
        url:    Override DATABASE_URL. Defaults to env var.

    Returns:
        List of row tuples (empty list if no rows).

    Raises:
        DatabaseError: after all retry attempts are exhausted.
    """
    db_url = url or _get_url()
    if not db_url:
        raise DatabaseError("fetch_all", "DATABASE_URL is not configured.")

    last_exc: Optional[Exception] = None
    for attempt in range(1, _MAX_ATTEMPTS + 1):
        try:
            with _connect(db_url) as conn:
                with conn.cursor() as cur:
                    cur.execute(sql, params)
                    return cur.fetchall()
        except Exception as exc:
            last_exc = exc
            retryable = _is_retryable(exc)
            logger.warning(
                "[DB] fetch_all attempt %d/%d failed (retryable=%s): %s",
                attempt, _MAX_ATTEMPTS, retryable, exc,
            )
            if not retryable or attempt == _MAX_ATTEMPTS:
                break
            time.sleep(_BACKOFF_SECONDS * attempt)

    raise DatabaseError(
        "fetch_all",
        str(last_exc),
        retryable=_is_retryable(last_exc) if last_exc else False,
    ) from last_exc
