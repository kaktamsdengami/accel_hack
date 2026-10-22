"""Aiven Runtime entrypoint (T12): one container runs the API, the aggregation worker and, optionally,
a looping SYNTHETIC demo stream. Run: python -m deploy.runtime   (the Dockerfile CMD)

Why one process: the smallest Runtime plan (startup-10-256) has 256 MB, too little for three Python
interpreters, and the worker only waits on I/O. The API (uvicorn) owns the main thread; the worker and the
demo loop are daemon threads. If the worker crashes it is restarted after a back-off; if uvicorn exits the
container exits and Runtime restarts it.

Secrets come from Runtime environment variables (never from Git). Kafka needs PEM files, so start-up writes
them to a private temp dir from one of (first match wins, per file):
  KAFKA_CA_B64 / KAFKA_CERT_B64 / KAFKA_KEY_B64          base64 of the PEM, one line (runtime_deploy.py)
  KAFKA_CA_CERT / KAFKA_ACCESS_CERT / KAFKA_ACCESS_KEY   PEM text (names of a Runtime Kafka integration;
                                                          a literal "\\n" is turned into a newline)
and points KAFKA_CA_PATH / KAFKA_CERT_PATH / KAFKA_KEY_PATH at them. Runtime integration names are also
mapped: DATABASE_URL -> PG_URI, VALKEY_URL -> VALKEY_URI, KAFKA_BOOTSTRAP_SERVERS -> KAFKA_BOOTSTRAP.

Flags (env): PORT (8000), RUN_WORKER (1), AGG_GROUP (aggregate-runtime), DEMO_SYNTHETIC (0),
DEMO_MATCH_ID (m-synthetic), DEMO_DURATION_S (60), DEMO_SPEED (1), DEMO_PAUSE_S (10), DEMO_START_DELAY_S (15).
The demo replays the invented match of scripts/fake_stream.py: never show it as a measurement.
"""
from __future__ import annotations

import base64
import logging
import os
import pathlib
import tempfile
import threading
import time
from collections.abc import MutableMapping

log = logging.getLogger("runtime")

ALIASES = {  # our name <- Runtime integration default names
    "PG_URI": ("DATABASE_URL",),
    "VALKEY_URI": ("VALKEY_URL",),
    "KAFKA_BOOTSTRAP": ("KAFKA_BOOTSTRAP_SERVERS", "KAFKA_BOOTSTRAP_SERVER"),
}
CERTS = (  # (path var, base64 var, PEM var, file name)
    ("KAFKA_CA_PATH", "KAFKA_CA_B64", "KAFKA_CA_CERT", "kafka-ca.pem"),
    ("KAFKA_CERT_PATH", "KAFKA_CERT_B64", "KAFKA_ACCESS_CERT", "kafka-service.cert"),
    ("KAFKA_KEY_PATH", "KAFKA_KEY_B64", "KAFKA_ACCESS_KEY", "kafka-service.key"),
)


def _flag(env: MutableMapping[str, str], name: str, default: str) -> bool:
    return env.get(name, default).strip().lower() in {"1", "true", "yes", "on"}


def prepare_env(env: MutableMapping[str, str] | None = None, cert_dir: str | None = None) -> list[str]:
    """Map alias variables and write Kafka PEMs from env to files. Must run before app.config is imported.
    Returns the names of the variables it set (never their values)."""
    env = os.environ if env is None else env
    changed = []
    for ours, theirs in ALIASES.items():
        if not env.get(ours):
            src = next((n for n in theirs if env.get(n)), None)
            if src:
                env[ours] = env[src]
                changed.append(ours)
    for path_var, b64_var, pem_var, fname in CERTS:
        if env.get(b64_var):
            data = base64.b64decode(env[b64_var].strip())
        elif env.get(pem_var):
            data = env[pem_var].replace("\\n", "\n").encode()
        else:
            continue
        if cert_dir is None:
            cert_dir = tempfile.mkdtemp(prefix="kafka-certs-")
        path = pathlib.Path(cert_dir) / fname
        path.write_bytes(data)
        path.chmod(0o600)
        env[path_var] = str(path.resolve())  # absolute: app.config joins it to ROOT unchanged
        changed.append(path_var)
    return changed


class Runner:
    """Starts the worker and demo threads; `agg` is shared so the demo can reset its match between passes."""

    def __init__(self, env: MutableMapping[str, str] | None = None):
        self.env = os.environ if env is None else env
        self.stop = threading.Event()
        self.agg = None

    def worker(self) -> None:
        from workers import aggregate

        group = self.env.get("AGG_GROUP", "aggregate-runtime")
        backoff = 5.0
        while not self.stop.is_set():
            try:
                if self.agg is None:
                    self.agg = aggregate.Aggregator()
                # a dedicated group starting at "latest": the deployed worker does not share partitions with
                # a laptop running `python -m workers aggregate`, and a first start does not replay history
                aggregate.run(stop=self.stop, group=group, auto_offset_reset="latest", agg=self.agg)
            except Exception:
                log.exception("aggregate worker crashed; restarting in %.0f s", backoff)
                self.stop.wait(backoff)
                backoff = min(backoff * 2, 120.0)

    def demo(self) -> None:
        from scripts import fake_stream

        e = self.env
        match_id = e.get("DEMO_MATCH_ID", fake_stream.MATCH_ID)
        duration = float(e.get("DEMO_DURATION_S", fake_stream.DURATION_S))
        speed = float(e.get("DEMO_SPEED", "1"))
        pause = float(e.get("DEMO_PAUSE_S", "10"))
        self.stop.wait(float(e.get("DEMO_START_DELAY_S", "15")))  # let the consumer join its group first
        n = 0
        while not self.stop.is_set():
            try:
                if self.agg is not None:  # forget the previous pass: same ids would be de-duplicated
                    self.agg.states.pop(match_id, None)
                n += 1
                log.info("SYNTHETIC demo pass %d: %s, %.0f s at %sx (invented)", n, match_id, duration, speed)
                counts = fake_stream.run(match_id=match_id, duration_s=duration, speed=speed,
                                         match_duration_s=duration)
                log.info("SYNTHETIC demo pass %d done: %s", n, counts)
                self.stop.wait(pause)  # show the final numbers, let in-flight messages land
            except Exception:
                log.exception("SYNTHETIC demo pass failed; retrying in 30 s")
                self.stop.wait(30)

    def start_threads(self) -> list[threading.Thread]:
        threads = []
        if _flag(self.env, "RUN_WORKER", "1"):
            threads.append(threading.Thread(target=self.worker, name="aggregate", daemon=True))
        if _flag(self.env, "DEMO_SYNTHETIC", "0"):
            threads.append(threading.Thread(target=self.demo, name="demo", daemon=True))
        for t in threads:
            t.start()
        return threads


def main() -> None:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(threadName)s %(name)s %(levelname)s %(message)s")
    log.info("env prepared: %s", ", ".join(prepare_env()) or "nothing to map")
    import uvicorn

    from app.main import app

    runner = Runner()
    threads = runner.start_threads()
    log.info("started threads: %s", ", ".join(t.name for t in threads) or "none (API only)")
    try:
        uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", "8000")), log_level="info",
                    proxy_headers=True, forwarded_allow_ips="*")
    finally:
        runner.stop.set()
        time.sleep(1)


if __name__ == "__main__":
    main()
