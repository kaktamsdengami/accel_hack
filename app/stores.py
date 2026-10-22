"""Shared clients for the Aiven services, built from app.config.settings(). Lazy singletons, no import I/O.

    from app import stores
    stores.clickhouse().query(...)          # clickhouse_connect client, database "sponsorpulse"
    stores.valkey().hgetall(...)            # redis-py client (decode_responses=True); also pub/sub
    stores.postgres().fetch("SELECT ...", params)   # short-lived autocommit connections
    stores.kafka_producer().send(topic, ...)  # JSON values, key = match_id
    stores.kafka_consumer("detections", group_id="aggregate")  # NEW consumer each call (not shared)

Kafka uses SSL client certificates (paths in settings). The ClickHouse client is NOT thread safe for
concurrent queries: use one per thread (`clickhouse(fresh=True)`) if you share across threads.
"""
from __future__ import annotations

import json
import threading
from typing import Any

from app.config import settings

DB = "sponsorpulse"
_lock = threading.Lock()
_singletons: dict[str, Any] = {}


def _once(name: str, factory):
    with _lock:
        if name not in _singletons:
            _singletons[name] = factory()
        return _singletons[name]


def clickhouse(fresh: bool = False):
    """clickhouse_connect client (HTTPS, database sponsorpulse). `fresh=True` returns an unshared client."""
    import clickhouse_connect

    def make():
        s = settings()
        return clickhouse_connect.get_client(
            host=s.clickhouse_host, port=s.clickhouse_port, username=s.clickhouse_user,
            password=s.clickhouse_password, secure=True, database=DB)

    return make() if fresh else _once("clickhouse", make)


def valkey():
    """redis-py client on VALKEY_URI with str responses. Thread safe (connection pool)."""
    import redis

    return _once("valkey", lambda: redis.from_url(
        settings().valkey_uri, decode_responses=True, socket_timeout=20, socket_connect_timeout=20,
        health_check_interval=30))


class _Postgres:
    """Minimal helper: each call opens a short-lived autocommit connection (few slots on free tiers)."""

    def connect(self):
        import psycopg

        return psycopg.connect(settings().pg_uri, connect_timeout=20, autocommit=True)

    def fetch(self, sql: str, params: tuple | dict | None = None) -> list[tuple]:
        with self.connect() as conn:
            return conn.execute(sql, params).fetchall()

    def execute(self, sql: str, params: tuple | dict | None = None) -> None:
        with self.connect() as conn:
            conn.execute(sql, params)


def postgres() -> _Postgres:
    """Postgres helper: `postgres().fetch(sql, params)`, `.execute(sql, params)`, `.connect()`."""
    return _once("postgres", _Postgres)


def _kafka_ssl() -> dict:
    s = settings()
    return dict(bootstrap_servers=s.kafka_bootstrap, security_protocol="SSL", ssl_cafile=s.kafka_ca,
                ssl_certfile=s.kafka_cert, ssl_keyfile=s.kafka_key)


def kafka_producer():
    """Shared KafkaProducer: value = dict/pydantic model -> JSON UTF-8, key = str -> bytes."""
    from kafka import KafkaProducer

    def dumps(v) -> bytes:
        return (v.model_dump_json() if hasattr(v, "model_dump_json") else json.dumps(v)).encode()

    return _once("kafka_producer", lambda: KafkaProducer(
        **_kafka_ssl(), value_serializer=dumps, key_serializer=lambda k: k.encode(), linger_ms=50,
        request_timeout_ms=30000))


def kafka_consumer(*topics: str, group_id: str, auto_offset_reset: str = "earliest", **kw):
    """A new KafkaConsumer (JSON values -> dict). Not shared: each consumer owns its group membership."""
    from kafka import KafkaConsumer

    return KafkaConsumer(
        *topics, **_kafka_ssl(), group_id=group_id, auto_offset_reset=auto_offset_reset,
        enable_auto_commit=False, value_deserializer=lambda b: json.loads(b.decode()), **kw)
