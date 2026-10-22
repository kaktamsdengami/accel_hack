"""Connect to every Aiven service and print OK/FAIL. Enables pgvector. Prints no secrets."""
import os
import pathlib
import time

from dotenv import load_dotenv

ROOT = pathlib.Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")


def check(name, fn):
    t = time.perf_counter()
    try:
        info = fn()
        print(f"OK   {name:11} {(time.perf_counter() - t) * 1000:6.0f} ms  {info}")
        return True
    except Exception as e:  # report and continue with the other services
        print(f"FAIL {name:11} {type(e).__name__}: {str(e)[:200]}")
        return False


def postgres():
    import psycopg
    with psycopg.connect(os.environ["PG_URI"], connect_timeout=15) as conn:
        conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
        ver = conn.execute("SELECT extversion FROM pg_extension WHERE extname='vector'").fetchone()[0]
        return f"pgvector {ver}"


def clickhouse():
    import clickhouse_connect
    c = clickhouse_connect.get_client(
        host=os.environ["CLICKHOUSE_HOST"], port=int(os.environ["CLICKHOUSE_PORT"]),
        username=os.environ["CLICKHOUSE_USER"], password=os.environ["CLICKHOUSE_PASSWORD"], secure=True)
    return f"version {c.server_version}"


def valkey():
    import redis
    r = redis.from_url(os.environ["VALKEY_URI"], socket_timeout=15)
    r.set("smoke", "1", ex=60)
    return f"ping={r.ping()}"


def kafka():
    from kafka import KafkaAdminClient
    p = lambda k: str(ROOT / os.environ[k])
    admin = KafkaAdminClient(
        bootstrap_servers=os.environ["KAFKA_BOOTSTRAP"], security_protocol="SSL",
        ssl_cafile=p("KAFKA_CA_PATH"), ssl_certfile=p("KAFKA_CERT_PATH"), ssl_keyfile=p("KAFKA_KEY_PATH"),
        request_timeout_ms=20000)
    topics = admin.list_topics()
    admin.close()
    return f"{len(topics)} topics"


if __name__ == "__main__":
    results = [check(n, f) for n, f in
               [("postgres", postgres), ("clickhouse", clickhouse), ("valkey", valkey), ("kafka", kafka)]]
    raise SystemExit(0 if all(results) else 1)
