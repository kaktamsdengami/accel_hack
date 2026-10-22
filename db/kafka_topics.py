"""Kafka topics (docs/CONTRACTS.md section 8). Aiven free plan: one partition each, default replication.

create_topics() tries kafka-python's admin client; if that fails (some free plans restrict it) it falls
back to the Aiven REST API using AIVEN_TOKEN / AIVEN_PROJECT. Idempotent. Prints no secrets.
"""
from __future__ import annotations

import json
import os
import pathlib
import urllib.error
import urllib.request

from dotenv import load_dotenv

ROOT = pathlib.Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")

TOPICS = ["frames", "detections", "decisions"]
PARTITIONS = 1
API = "https://api.aiven.io/v1"


def _path(key: str, default: str) -> str:
    return str(ROOT / os.getenv(key, default))


def _via_admin() -> dict[str, str]:
    from kafka import KafkaAdminClient
    from kafka.admin import NewTopic
    from kafka.errors import TopicAlreadyExistsError

    admin = KafkaAdminClient(
        bootstrap_servers=os.environ["KAFKA_BOOTSTRAP"], security_protocol="SSL",
        ssl_cafile=_path("KAFKA_CA_PATH", "data/certs/kafka-ca.pem"),
        ssl_certfile=_path("KAFKA_CERT_PATH", "data/certs/kafka-service.cert"),
        ssl_keyfile=_path("KAFKA_KEY_PATH", "data/certs/kafka-service.key"),
        request_timeout_ms=20000)
    try:
        existing = set(admin.list_topics())
        out = {}
        for name in TOPICS:
            if name in existing:
                out[name] = "exists"
                continue
            try:
                admin.create_topics([NewTopic(name, num_partitions=PARTITIONS, replication_factor=-1)])
                out[name] = "created"
            except TopicAlreadyExistsError:
                out[name] = "exists"
        return out
    finally:
        admin.close()


def _api(method: str, path: str, body: dict | None = None) -> dict:
    req = urllib.request.Request(
        API + path, method=method, data=json.dumps(body).encode() if body is not None else None,
        headers={"Authorization": f"aivenv1 {os.environ['AIVEN_TOKEN']}", "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def _via_rest() -> dict[str, str]:
    project = os.environ["AIVEN_PROJECT"]
    services = _api("GET", f"/project/{project}/service")["services"]
    svc = next(s["service_name"] for s in services if s["service_type"] == "kafka")
    base = f"/project/{project}/service/{svc}/topic"
    existing = {t["topic_name"] for t in _api("GET", base)["topics"]}
    out = {}
    for name in TOPICS:
        if name in existing:
            out[name] = "exists"
            continue
        try:
            _api("POST", base, {"topic_name": name, "partitions": PARTITIONS, "replication": 2})
            out[name] = "created"
        except urllib.error.HTTPError as e:
            if e.code != 409:
                raise
            out[name] = "exists"
    return out


def create_topics() -> dict[str, str]:
    """Return {topic: 'created' | 'exists'}."""
    try:
        return _via_admin()
    except Exception as e:  # fall back to REST on any admin failure
        print(f"  kafka admin failed ({type(e).__name__}), using Aiven REST API")
        return _via_rest()


if __name__ == "__main__":
    print(create_topics())
