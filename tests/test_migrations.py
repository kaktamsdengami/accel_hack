import re

import pytest

from app.contracts import TOPICS, DecisionLog, Detection, Interval
from db import kafka_topics
from scripts import migrate


def _columns(sql: str, table: str) -> set[str]:
    m = re.search(rf"CREATE TABLE IF NOT EXISTS sponsorpulse\.{table}\s*\((.*?)\)\s*ENGINE", sql, re.S)
    assert m, table
    return {ln.split()[0] for ln in m.group(1).splitlines() if ln.strip() and not ln.strip().startswith("--")}


def _ch_sql() -> str:
    return "\n".join(f.read_text(encoding="utf-8") for f in migrate.sql_files(migrate.CLICKHOUSE_DIR))


def test_sql_files_exist_and_split():
    for d in (migrate.CLICKHOUSE_DIR, migrate.POSTGRES_DIR):
        files = migrate.sql_files(d)
        assert files
        for f in files:
            stmts = migrate.split_statements(f.read_text(encoding="utf-8"))
            assert stmts and all(s.upper().startswith(("CREATE", "ALTER")) for s in stmts)


def test_all_statements_are_idempotent():
    for d in (migrate.CLICKHOUSE_DIR, migrate.POSTGRES_DIR):
        for f in migrate.sql_files(d):
            for s in migrate.split_statements(f.read_text(encoding="utf-8")):
                assert "IF NOT EXISTS" in s.upper(), f"{f.name}: {s[:60]}"


def test_clickhouse_columns_match_contracts():
    sql = _ch_sql()
    det = _columns(sql, "detections")
    expected = set(Detection.model_fields) - {"bbox"} | {"bbox_x", "bbox_y", "bbox_w", "bbox_h"}
    assert expected <= det
    assert set(DecisionLog.model_fields) <= _columns(sql, "decisions")
    assert set(Interval.model_fields) <= _columns(sql, "intervals")
    assert {"match_id", "second", "status", "reason"} <= _columns(sql, "coverage")
    assert {"ts", "match_id", "agent", "step", "input_summary", "output_summary", "model",
            "tokens_in", "tokens_out", "cost_usd", "latency_ms"} <= _columns(sql, "agent_traces")


def test_postgres_tables_and_dimension_placeholder():
    sql = "\n".join(f.read_text(encoding="utf-8") for f in migrate.sql_files(migrate.POSTGRES_DIR))
    for t in ("brands", "stadiums", "board_maps", "contracts", "logo_memory", "labels", "evidence"):
        assert f"CREATE TABLE IF NOT EXISTS {t} " in sql
    assert "vector({{EMBEDDING_DIM}})" in sql and "vector_cosine_ops" in sql
    assert f"vector({migrate.EMBEDDING_DIM})" in migrate.render(sql)


def test_kafka_topics_match_contracts():
    assert set(kafka_topics.TOPICS) == set(TOPICS)


@pytest.mark.live
def test_migrate_twice_is_idempotent():
    assert migrate.main() == 0
    assert migrate.main() == 0
