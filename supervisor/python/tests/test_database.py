"""Tests for database module (unit tests only, no real DB)."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import psycopg
import pytest

from aquarco_supervisor.database import Database, SEARCH_PATH
from aquarco_supervisor.exceptions import ConnectionPoolError, QueryError


@pytest.mark.asyncio
async def test_configure_connection_sets_search_path() -> None:
    """_configure_connection executes SEARCH_PATH on the given connection."""
    db = Database("postgresql://test:test@localhost/test")
    mock_conn = AsyncMock()

    await db._configure_connection(mock_conn)

    mock_conn.execute.assert_awaited_once_with(SEARCH_PATH)


@pytest.mark.asyncio
async def test_connect_passes_configure_callback_to_pool() -> None:
    """AsyncConnectionPool is constructed with configure=_configure_connection."""
    from contextlib import asynccontextmanager

    db = Database("postgresql://test:test@localhost/test")

    captured: dict = {}

    mock_conn = AsyncMock()

    @asynccontextmanager
    async def _fake_connection():
        yield mock_conn

    mock_pool = AsyncMock()
    mock_pool.connection = _fake_connection

    def _capture_pool(*args, **kwargs):
        captured["configure"] = kwargs.get("configure")
        return mock_pool

    with patch(
        "aquarco_supervisor.database.AsyncConnectionPool", side_effect=_capture_pool
    ):
        await db.connect()

    assert captured.get("configure") is not None
    assert captured["configure"].__func__ is Database._configure_connection


@pytest.mark.asyncio
async def test_acquire_does_not_execute_search_path() -> None:
    """acquire() yields the connection without running SEARCH_PATH itself."""
    from contextlib import asynccontextmanager

    db = Database("postgresql://test:test@localhost/test")

    mock_conn = AsyncMock()

    @asynccontextmanager
    async def _fake_connection():
        yield mock_conn

    mock_pool = AsyncMock()
    mock_pool.connection = _fake_connection
    db._pool = mock_pool

    async with db.acquire() as conn:
        pass

    # execute must not have been called by acquire() itself
    mock_conn.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_acquire_without_connect() -> None:
    db = Database("postgresql://test:test@localhost/test")
    with pytest.raises(ConnectionPoolError, match="not initialized"):
        async with db.acquire():
            pass


@pytest.mark.asyncio
async def test_connect_invalid_dsn() -> None:
    db = Database("postgresql://invalid:invalid@nonexistent:9999/nope")
    with pytest.raises(ConnectionPoolError):
        await db.connect()


@pytest.mark.asyncio
async def test_close_when_pool_is_none() -> None:
    """Closing an uninitialized DB is a no-op."""
    db = Database("postgresql://test:test@localhost/test")
    # Should not raise
    await db.close()


@pytest.mark.asyncio
async def test_close_calls_pool_close() -> None:
    """close() delegates to the pool's close method."""
    db = Database("postgresql://test:test@localhost/test")
    mock_pool = AsyncMock()
    db._pool = mock_pool

    await db.close()

    mock_pool.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_execute_raises_query_error_on_psycopg_error() -> None:
    """execute() wraps psycopg.Error as QueryError."""
    db = Database("postgresql://test:test@localhost/test")

    mock_conn = AsyncMock()
    mock_conn.execute = AsyncMock(side_effect=psycopg.OperationalError("query failed"))

    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def _fake_acquire():
        yield mock_conn

    db.acquire = _fake_acquire  # type: ignore[method-assign]

    with pytest.raises(QueryError, match="Query failed"):
        await db.execute("SELECT 1")


@pytest.mark.asyncio
async def test_fetch_one_returns_row() -> None:
    """fetch_one() returns the first row from the cursor."""
    db = Database("postgresql://test:test@localhost/test")

    expected_row = {"id": 1, "name": "test"}
    mock_cursor = AsyncMock()
    mock_cursor.fetchone = AsyncMock(return_value=expected_row)

    mock_conn = AsyncMock()
    mock_conn.execute = AsyncMock(return_value=mock_cursor)

    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def _fake_acquire():
        yield mock_conn

    db.acquire = _fake_acquire  # type: ignore[method-assign]

    result = await db.fetch_one("SELECT * FROM test WHERE id = %(id)s", {"id": 1})
    assert result == expected_row


@pytest.mark.asyncio
async def test_fetch_one_returns_none_when_no_row() -> None:
    """fetch_one() returns None when the cursor has no rows."""
    db = Database("postgresql://test:test@localhost/test")

    mock_cursor = AsyncMock()
    mock_cursor.fetchone = AsyncMock(return_value=None)

    mock_conn = AsyncMock()
    mock_conn.execute = AsyncMock(return_value=mock_cursor)

    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def _fake_acquire():
        yield mock_conn

    db.acquire = _fake_acquire  # type: ignore[method-assign]

    result = await db.fetch_one("SELECT * FROM test WHERE id = 9999")
    assert result is None


@pytest.mark.asyncio
async def test_fetch_one_raises_query_error_on_psycopg_error() -> None:
    """fetch_one() wraps psycopg.Error as QueryError."""
    db = Database("postgresql://test:test@localhost/test")

    mock_conn = AsyncMock()
    mock_conn.execute = AsyncMock(side_effect=psycopg.ProgrammingError("syntax error"))

    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def _fake_acquire():
        yield mock_conn

    db.acquire = _fake_acquire  # type: ignore[method-assign]

    with pytest.raises(QueryError):
        await db.fetch_one("BAD SQL")


@pytest.mark.asyncio
async def test_fetch_all_returns_rows() -> None:
    """fetch_all() returns all rows from the cursor."""
    db = Database("postgresql://test:test@localhost/test")

    rows = [{"id": 1}, {"id": 2}, {"id": 3}]
    mock_cursor = AsyncMock()
    mock_cursor.fetchall = AsyncMock(return_value=rows)

    mock_conn = AsyncMock()
    mock_conn.execute = AsyncMock(return_value=mock_cursor)

    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def _fake_acquire():
        yield mock_conn

    db.acquire = _fake_acquire  # type: ignore[method-assign]

    result = await db.fetch_all("SELECT * FROM test")
    assert result == rows


@pytest.mark.asyncio
async def test_fetch_all_raises_query_error_on_psycopg_error() -> None:
    """fetch_all() wraps psycopg.Error as QueryError."""
    db = Database("postgresql://test:test@localhost/test")

    mock_conn = AsyncMock()
    mock_conn.execute = AsyncMock(side_effect=psycopg.InterfaceError("closed"))

    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def _fake_acquire():
        yield mock_conn

    db.acquire = _fake_acquire  # type: ignore[method-assign]

    with pytest.raises(QueryError):
        await db.fetch_all("SELECT * FROM test")


@pytest.mark.asyncio
async def test_fetch_val_returns_scalar() -> None:
    """fetch_val() returns the first column of the first row."""
    db = Database("postgresql://test:test@localhost/test")

    mock_cursor = AsyncMock()
    mock_cursor.fetchone = AsyncMock(return_value={"count": 42})

    mock_conn = AsyncMock()
    mock_conn.execute = AsyncMock(return_value=mock_cursor)

    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def _fake_acquire():
        yield mock_conn

    db.acquire = _fake_acquire  # type: ignore[method-assign]

    result = await db.fetch_val("SELECT COUNT(*) as count FROM test")
    assert result == 42


@pytest.mark.asyncio
async def test_fetch_val_returns_none_when_no_row() -> None:
    """fetch_val() returns None when there is no matching row."""
    db = Database("postgresql://test:test@localhost/test")

    mock_cursor = AsyncMock()
    mock_cursor.fetchone = AsyncMock(return_value=None)

    mock_conn = AsyncMock()
    mock_conn.execute = AsyncMock(return_value=mock_cursor)

    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def _fake_acquire():
        yield mock_conn

    db.acquire = _fake_acquire  # type: ignore[method-assign]

    result = await db.fetch_val("SELECT COUNT(*) FROM test WHERE 1=0")
    assert result is None


@pytest.mark.asyncio
async def test_fetch_val_raises_query_error_on_psycopg_error() -> None:
    """fetch_val() wraps psycopg.Error as QueryError."""
    db = Database("postgresql://test:test@localhost/test")

    mock_conn = AsyncMock()
    mock_conn.execute = AsyncMock(side_effect=psycopg.OperationalError("timeout"))

    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def _fake_acquire():
        yield mock_conn

    db.acquire = _fake_acquire  # type: ignore[method-assign]

    with pytest.raises(QueryError):
        await db.fetch_val("SELECT 1")


# ---------------------------------------------------------------------------
# transaction() tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_transaction_yields_connection_inside_transaction_block() -> None:
    """transaction() acquires a connection and wraps it in conn.transaction()."""
    from contextlib import asynccontextmanager

    db = Database("postgresql://test:test@localhost/test")

    mock_conn = AsyncMock()

    # conn.transaction() must be an async context manager
    mock_txn = AsyncMock()
    mock_txn.__aenter__ = AsyncMock(return_value=None)
    mock_txn.__aexit__ = AsyncMock(return_value=False)
    mock_conn.transaction = lambda: mock_txn

    @asynccontextmanager
    async def _fake_acquire():
        yield mock_conn

    db.acquire = _fake_acquire  # type: ignore[method-assign]

    # Arrange & Act
    yielded_conn = None
    async with db.transaction() as conn:
        yielded_conn = conn

    # Assert: the connection was yielded and the transaction block was entered
    assert yielded_conn is mock_conn
    mock_txn.__aenter__.assert_awaited_once()
    mock_txn.__aexit__.assert_awaited_once()


@pytest.mark.asyncio
async def test_transaction_rolls_back_on_exception() -> None:
    """transaction() propagates exceptions so conn.transaction() can roll back."""
    from contextlib import asynccontextmanager

    db = Database("postgresql://test:test@localhost/test")

    mock_conn = AsyncMock()

    # Simulate a real async context manager that re-raises exceptions
    entered = False
    exited_with_exc = False

    @asynccontextmanager
    async def _real_txn():
        nonlocal entered, exited_with_exc
        entered = True
        try:
            yield
        except Exception:
            exited_with_exc = True
            raise

    mock_conn.transaction = _real_txn

    @asynccontextmanager
    async def _fake_acquire():
        yield mock_conn

    db.acquire = _fake_acquire  # type: ignore[method-assign]

    # Act: raise an exception inside the transaction block
    with pytest.raises(RuntimeError, match="boom"):
        async with db.transaction():
            raise RuntimeError("boom")

    # Assert: the transaction block was entered and exited with the exception
    assert entered is True
    assert exited_with_exc is True


@pytest.mark.asyncio
async def test_transaction_without_connect_raises_connection_pool_error() -> None:
    """transaction() raises ConnectionPoolError when the pool is not initialized."""
    db = Database("postgresql://test:test@localhost/test")
    # _pool is None by default

    with pytest.raises(ConnectionPoolError, match="not initialized"):
        async with db.transaction():
            pass
