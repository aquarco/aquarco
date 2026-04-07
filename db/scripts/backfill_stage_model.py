#!/usr/bin/env python3
"""One-time backfill: populate stages.model from raw_output NDJSON.

Reads all stages where model IS NULL and raw_output IS NOT NULL, parses
the NDJSON to extract the model identifier, and writes it back.

Idempotent — safe to run multiple times (WHERE model IS NULL guard).

Usage:
    python backfill_stage_model.py [--database-url URL] [--batch-size N]

Requires the aquarco_supervisor package to be installed (for the NDJSON parser).
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys

import psycopg
from psycopg.rows import dict_row


# Add the supervisor package to the path so we can import spending.py
sys.path.insert(
    0,
    os.path.join(os.path.dirname(__file__), "..", "..", "supervisor", "python", "src"),
)

from aquarco_supervisor.spending import parse_ndjson_spending  # noqa: E402

BATCH_SIZE = 500


async def backfill(database_url: str, batch_size: int = BATCH_SIZE) -> int:
    """Backfill model column. Returns count of updated rows."""
    updated = 0

    async with await psycopg.AsyncConnection.connect(
        database_url, row_factory=dict_row
    ) as conn:
        # Set search_path to match the supervisor
        await conn.execute("SET search_path TO aquarco, public")

        # Use a named server-side cursor to avoid loading all rows into memory
        async with conn.cursor(name="backfill_model_cursor") as cur:
            await cur.execute(
                "SELECT id, raw_output FROM stages "
                "WHERE model IS NULL AND raw_output IS NOT NULL"
            )

            batch_updates: list[tuple[str, int]] = []

            async for row in cur:
                raw_output = row["raw_output"]
                if not raw_output:
                    continue

                try:
                    summary = parse_ndjson_spending(raw_output)
                except Exception:
                    print(f"  Warning: failed to parse raw_output for stage {row['id']}")
                    continue

                if summary.model:
                    batch_updates.append((summary.model, row["id"]))

                # Flush batch when it reaches the batch size
                if len(batch_updates) >= batch_size:
                    updated += await _flush_batch(conn, batch_updates)
                    batch_updates.clear()

            # Flush remaining
            if batch_updates:
                updated += await _flush_batch(conn, batch_updates)

        await conn.commit()

    return updated


async def _flush_batch(
    conn: psycopg.AsyncConnection,  # type: ignore[type-arg]
    updates: list[tuple[str, int]],
) -> int:
    """Execute a batch of UPDATE statements and return count."""
    async with conn.cursor() as cur:
        await cur.executemany(
            "UPDATE stages SET model = %s WHERE id = %s",
            updates,
        )
    print(f"  Flushed batch of {len(updates)} updates")
    return len(updates)


def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill stages.model from raw_output")
    parser.add_argument(
        "--database-url",
        default=os.environ.get(
            "DATABASE_URL",
            "postgresql://aquarco:aquarco@localhost:5432/aquarco",
        ),
        help="PostgreSQL connection URL",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=BATCH_SIZE,
        help=f"Number of rows per batch commit (default: {BATCH_SIZE})",
    )
    args = parser.parse_args()

    count = asyncio.run(backfill(args.database_url, args.batch_size))
    print(f"Backfill complete: {count} stages updated")


if __name__ == "__main__":
    main()
