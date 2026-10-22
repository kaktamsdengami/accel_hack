"""deploy/runtime.py: env mapping, Kafka PEM materialisation, thread flags (no network)."""
import base64
import pathlib

from deploy.runtime import Runner, prepare_env

PEM = "-----BEGIN CERTIFICATE-----\nabc\n-----END CERTIFICATE-----\n"


def test_aliases_and_b64_certs(tmp_path):
    env = {"DATABASE_URL": "postgres://x", "VALKEY_URL": "rediss://y", "KAFKA_BOOTSTRAP_SERVERS": "k:1",
           "KAFKA_CA_B64": base64.b64encode(PEM.encode()).decode()}
    changed = prepare_env(env, str(tmp_path))
    assert env["PG_URI"] == "postgres://x" and env["VALKEY_URI"] == "rediss://y"
    assert env["KAFKA_BOOTSTRAP"] == "k:1"
    assert pathlib.Path(env["KAFKA_CA_PATH"]).read_text() == PEM
    assert set(changed) == {"PG_URI", "VALKEY_URI", "KAFKA_BOOTSTRAP", "KAFKA_CA_PATH"}
    assert "KAFKA_CERT_PATH" not in env  # nothing given: keep the local default (data/certs)


def test_pem_env_with_escaped_newlines_and_no_override(tmp_path):
    env = {"PG_URI": "postgres://ours", "DATABASE_URL": "postgres://theirs",
           "KAFKA_ACCESS_KEY": PEM.replace("\n", "\\n")}
    prepare_env(env, str(tmp_path))
    assert env["PG_URI"] == "postgres://ours"
    p = pathlib.Path(env["KAFKA_KEY_PATH"])
    assert p.is_absolute() and p.read_text() == PEM


def test_thread_flags(monkeypatch):
    r = Runner({"RUN_WORKER": "0", "DEMO_SYNTHETIC": "0"})
    assert r.start_threads() == []
