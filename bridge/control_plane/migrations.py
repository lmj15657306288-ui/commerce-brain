"""Migration CLI for the PostgreSQL persistence adapter."""

from __future__ import annotations

import argparse
import os

from core.postgres_persistence import PostgresPersistenceAdapter


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Commerce Brain PostgreSQL migrations")
    parser.add_argument("command", choices=("current", "upgrade"))
    parser.add_argument("--database-url", default=os.environ.get("DATABASE_URL", ""))
    args = parser.parse_args(argv)
    if not args.database_url:
        parser.error("--database-url or DATABASE_URL is required")
    adapter = PostgresPersistenceAdapter(args.database_url)
    try:
        print(f"schema_version={adapter.schema_version}")
    finally:
        adapter.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
