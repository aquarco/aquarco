"""Async PostgreSQL connection pool using psycopg."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import psycopg
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

from .exceptions import ConnectionPoolError, QueryError
from .logging import get_logger

log = get_logger("database")

SEARCH_PATH = "SET search_path TO aifishtank, public"


class Database:
    """Async database connection pool wrapper."""

    def __init__(self, dsn: str, max_connections: int = 5) -> None:
        self._dsn = dsn
        self._max_connections = max_connections
        self._pool: AsyncConnectionPool | None = None

    async def _configure_connection(self, conn: psycopg.AsyncConnection[Any]) -> None:
        """Pool configure callback — sets search_path once per connection lifetime."""
        await conn.execute(SEARCH_PATH)

    async def connect(self) -> None:
        """Initialize the connection pool."""
        try:
            self._pool = AsyncConnectionPool(
                conninfo=self._dsn,
                min_size=1,
                max_size=self._max_connections,
                open=False,
                kwargs={"row_factory": dict_row, "autocommit": True},
                configure=self._configure_connection,
            )
            await self._pool.open()
            # Verify connectivity
            async with self.acquire() as conn:
                await conn.execute("SELECT 1")
            log.info("database_connected", max_connections=self._max_connections)
        except Exception as e:
            raise ConnectionPoolError(f"Failed to connect to database: {e}") from e

    async def close(self) -> None:
        """Close the connection pool."""
        if self._pool:
            await self._pool.close()
            log.info("database_disconnected")

    @asynccontextmanager
    async def acquire(self) -> AsyncIterator[psycopg.AsyncConnection[Any]]:
        """Acquire a connection from the pool."""
        if not self._pool:
            raise ConnectionPoolError("Database pool not initialized")
        async with self._pool.connection() as conn:
            yield conn

    async def execute(
        self,
        query: str,
        params: tuple[Any, ...] | dict[str, Any] | None = None,
    ) -> None:
        """Execute a query without returning results."""
        try:
            async with self.acquire() as conn:
                await conn.execute(query, params)
        except psycopg.Error as e:
            raise QueryError(f"Query failed: {e}") from e

    async def fetch_one(
        self,
        query: str,
        params: tuple[Any, ...] | dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        """Execute a query and return the first row."""
        try:
            async with self.acquire() as conn:
                cur = await conn.execute(query, params)
                return await cur.fetchone()
        except psycopg.Error as e:
            raise QueryError(f"Query failed: {e}") from e

    async def fetch_all(
        self,
        query: str,
        params: tuple[Any, ...] | dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """Execute a query and return all rows."""
        try:
            async with self.acquire() as conn:
                cur = await conn.execute(query, params)
                return await cur.fetchall()
        except psycopg.Error as e:
            raise QueryError(f"Query failed: {e}") from e

    async def fetch_val(
        self,
        query: str,
        params: tuple[Any, ...] | dict[str, Any] | None = None,
    ) -> Any:
        """Execute a query and return a single scalar value."""
        try:
            async with self.acquire() as conn:
                cur = await conn.execute(query, params)
                row = await cur.fetchone()
                if row is None:
                    return None
                return next(iter(row.values()))
        except psycopg.Error as e:
            raise QueryError(f"Query failed: {e}") from e
