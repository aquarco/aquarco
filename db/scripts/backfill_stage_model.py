#!/usr/bin/env python3
"""One-time backfill: populate stages.model from raw_output NDJSON.

Reads all stages where model IS NULL and raw_output IS NOT NULL, parses
the NDJSON to extract the model identifier, and writes it back.

Idempotent — safe to run multiple times (WHERE model IS NULL guard).

Usage:
    python backfill_stage_model.py [--database-url URL]

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


async def backfill(database_url: str) -> int:
    """Backfill model column. Returns count of updated rows."""
    updated = 0

    async with await psycopg.AsyncConnection.connect(
        database_url, row_factory=dict_row
    ) as conn:
        # Set search_path to match the supervisor
        await conn.execute("SET search_path TO aquarco, public")

        # Fetch candidates in batches
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT id, raw_output FROM stages "
                "WHERE model IS NULL AND raw_output IS NOT NULL"
            )
            rows = await cur.fetchall()

        print(f"Found {len(rows)} stages to backfill")

        for row in rows:
            raw_output = row["raw_output"]
            if not raw_output:
                continue

            summary = parse_ndjson_spending(raw_output)
            if summary.model:
                await conn.execute(
                    "UPDATE stages SET model = %s WHERE id = %s",
                    (summary.model, row["id"]),
                )
                updated += 1

        await conn.commit()

    return updated


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
    args = parser.parse_args()

    count = asyncio.run(backfill(args.database_url))
    print(f"Backfill complete: {count} stages updated")


if __name__ == "__main__":
    main()
