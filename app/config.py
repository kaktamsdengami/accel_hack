"""Settings from env (.env locally, Runtime env vars when deployed). Defaults: docs/CONTRACTS.md §9."""
import os
import pathlib
from functools import lru_cache

from dotenv import load_dotenv

ROOT = pathlib.Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")


def _f(name: str, default: float) -> float:
    return float(os.getenv(name, default))


class Settings:
    sampled_fps = _f("SAMPLED_FPS", 2)
    min_area_pct = _f("MIN_AREA_PCT", 0.1)
    min_sharpness = _f("MIN_SHARPNESS", 50)
    closeup_area_pct = _f("CLOSEUP_AREA_PCT", 3.0)
    match_budget_usd = _f("MATCH_BUDGET_USD", 3)

    pg_uri = os.getenv("PG_URI", "")
    clickhouse_host = os.getenv("CLICKHOUSE_HOST", "")
    clickhouse_port = int(os.getenv("CLICKHOUSE_PORT", "0") or 0)
    clickhouse_user = os.getenv("CLICKHOUSE_USER", "")
    clickhouse_password = os.getenv("CLICKHOUSE_PASSWORD", "")
    valkey_uri = os.getenv("VALKEY_URI", "")
    kafka_bootstrap = os.getenv("KAFKA_BOOTSTRAP", "")
    kafka_ca = str(ROOT / os.getenv("KAFKA_CA_PATH", "data/certs/kafka-ca.pem"))
    kafka_cert = str(ROOT / os.getenv("KAFKA_CERT_PATH", "data/certs/kafka-service.cert"))
    kafka_key = str(ROOT / os.getenv("KAFKA_KEY_PATH", "data/certs/kafka-service.key"))

    cors_origins = [o for o in os.getenv("CORS_ORIGINS", "*").split(",") if o]


@lru_cache
def settings() -> Settings:
    return Settings()
