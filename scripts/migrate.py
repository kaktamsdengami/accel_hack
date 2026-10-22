"""Apply every migration: Postgres, ClickHouse, Kafka topics, Valkey schema marker. Idempotent.

Usage: python -m scripts.migrate     (or python scripts/migrate.py)
Prints a short summary and no secrets.
"""
from __future__ import annotations

import os
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")

# The one place that fixes the embedding dimension (SigLIP 768; CLIP would be 512).
# Override with EMBEDDING_DIM in .env before the FIRST migration. Applied to db/postgres/*.sql.
EMBEDDING_DIM = int(os.getenv("EMBEDDING_DIM", "768"))
SCHEMA_VERSION = "1"
CLICKHOUSE_DIR = ROOT / "db" / "clickhouse"
POSTGRES_DIR = ROOT / "db" / "postgres"


def split_statements(sql: str) -> list[str]:
    """Split a SQL file on ';' after dropping '--' comment lines. Files must not contain ';' in strings."""
    lines = [ln for ln in sql.splitlines() if not ln.strip().startswith("--")]
    return [s.strip() for s in "\n".join(lines).split(";") if s.strip()]


def sql_files(directory: pathlib.Path) -> list[pathlib.Path]:
    return sorted(directory.glob("*.sql"))


def render(sql: str) -> str:
    return sql.replace("{{EMBEDDING_DIM}}", str(EMBEDDING_DIM))


def migrate_postgres() -> str:
    import psycopg

    n = 0
    with psycopg.connect(os.environ["PG_URI"], connect_timeout=20, autocommit=True) as conn:
        for f in sql_files(POSTGRES_DIR):
            for stmt in split_statements(render(f.read_text(encoding="utf-8"))):
                conn.execute(stmt)
                n += 1
        tables = conn.execute(
            "SELECT count(*) FROM information_schema.tables WHERE table_schema='public'").fetchone()[0]
    return f"{n} statements, {tables} tables in public"


def migrate_clickhouse() -> str:
    import clickhouse_connect

    client = clickhouse_connect.get_client(
        host=os.environ["CLICKHOUSE_HOST"], port=int(os.environ["CLICKHOUSE_PORT"]),
        username=os.environ["CLICKHOUSE_USER"], password=os.environ["CLICKHOUSE_PASSWORD"], secure=True)
    n = 0
    for f in sql_files(CLICKHOUSE_DIR):
        for stmt in split_statements(f.read_text(encoding="utf-8")):
            client.command(stmt)
            n += 1
    tables = client.query("SELECT count() FROM system.tables WHERE database='sponsorpulse'").result_rows[0][0]
    return f"{n} statements, {tables} tables/views in sponsorpulse"


def migrate_kafka() -> str:
    from db.kafka_topics import create_topics

    return ", ".join(f"{k}={v}" for k, v in create_topics().items())


def migrate_valkey() -> str:
    import redis

    r = redis.from_url(os.environ["VALKEY_URI"], socket_timeout=20)
    r.set("sp:meta:schema_version", SCHEMA_VERSION)
    return f"sp:meta:schema_version={SCHEMA_VERSION}"


STEPS = [("postgres", migrate_postgres), ("clickhouse", migrate_clickhouse),
         ("kafka", migrate_kafka), ("valkey", migrate_valkey)]


def main() -> int:
    failed = 0
    for name, fn in STEPS:
        try:
            print(f"OK   {name:11} {fn()}")
        except Exception as e:  # report and continue with the other services
            failed += 1
            print(f"FAIL {name:11} {type(e).__name__}: {str(e)[:200]}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
