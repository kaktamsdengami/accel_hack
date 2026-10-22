"""Create / update / inspect the Aiven Runtime application through the Aiven API (docs/DEPLOY.md).

Uses AIVEN_TOKEN + AIVEN_PROJECT from .env and copies the service credentials from .env and data/certs into
Runtime env vars of kind "secret" (Kafka PEMs as base64, see deploy/runtime.py). Prints no secret.
No service integration is created, so the existing Aiven services are not modified.

    python scripts/runtime_deploy.py                  dry run: plan, price, env var names (no API write)
    python scripts/runtime_deploy.py --create         create the app (BILLED per hour while powered on)
    python scripts/runtime_deploy.py --update-env     replace its env vars from .env (redeploys same commit)
    python scripts/runtime_deploy.py --branch main    switch the deployed branch
    python scripts/runtime_deploy.py --status         state + public URL

Options: --name sponsorpulse  --plan startup-10-256  --cloud google-europe-west4  --demo 1|0
"""
from __future__ import annotations

import argparse
import base64
import json
import pathlib
import sys
import urllib.error
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.aiven_env import API, CERTS, read_env  # noqa: E402

REPO = "https://github.com/kaktamsdengami/accel_hack-"
PORT = 8000
SECRETS = ["PG_URI", "CLICKHOUSE_HOST", "CLICKHOUSE_PORT", "CLICKHOUSE_USER", "CLICKHOUSE_PASSWORD",
           "VALKEY_URI",
           "KAFKA_BOOTSTRAP"]
CERT_VARS = {"KAFKA_CA_B64": "kafka-ca.pem", "KAFKA_CERT_B64": "kafka-service.cert",
             "KAFKA_KEY_B64": "kafka-service.key"}


def call(method: str, path: str, token: str, body: dict | None = None) -> dict:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(API + path, data=data, method=method, headers={
        "Authorization": f"aivenv1 {token}", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        sys.exit(f"{method} {path} -> HTTP {e.code}: {e.read().decode()[:800]}")


def env_vars(env: dict, demo: bool) -> list[dict]:
    out = []
    for k in SECRETS:
        if not env.get(k):
            sys.exit(f"{k} missing in .env (run python scripts/aiven_env.py --write)")
        out.append({"key": k, "value": env[k], "kind": "secret"})
    for k, fname in CERT_VARS.items():
        p = CERTS / fname
        if not p.exists():
            sys.exit(f"{p} missing (run python scripts/aiven_env.py --write)")
        out.append({"key": k, "value": base64.b64encode(p.read_bytes()).decode(), "kind": "secret"})
    out += [{"key": "DEMO_SYNTHETIC", "value": "1" if demo else "0", "kind": "variable"},
            {"key": "CORS_ORIGINS", "value": env.get("CORS_ORIGINS") or "*", "kind": "variable"},
            {"key": "AGG_GROUP", "value": "aggregate-runtime", "kind": "variable"}]
    return out


def source(branch: str) -> dict:
    return {"repository_url": REPO, "branch": branch, "build_path": ".", "containerfile_path": "Dockerfile"}


def app_config(branch: str, env: dict, demo: bool) -> dict:
    return {"application": {"source": source(branch),
                            "ports": [{"name": "http", "port": PORT, "protocol": "HTTP"}],
                            "restart_policy": {"condition": "any"},
                            "environment_variables": env_vars(env, demo)}}


def price(token: str, project: str, plan: str, cloud: str) -> str:
    plans = call("GET", f"/project/{project}/service-types/application/plans", token).get("service_plans", [])
    for p in plans:
        if p["service_plan"] == plan:
            usd = p.get("regions", {}).get(cloud, {}).get("price_usd")
            return f"${usd}/h (~${float(usd) * 24:.2f}/day)" if usd else f"plan not offered in {cloud}"
    return "plan not found"


def public_urls(svc: dict) -> list[str]:
    """Best effort: the API shape of the Runtime endpoint is not documented, so look for https hosts."""
    found = set()

    def walk(x):
        if isinstance(x, dict):
            for v in x.values():
                walk(v)
        elif isinstance(x, list):
            for v in x:
                walk(v)
        elif isinstance(x, str) and (x.startswith("https://") or x.endswith(".aiven.app")):
            if REPO not in x and "@" not in x:
                found.add(x)
    walk({k: svc.get(k) for k in ("service_uri", "service_uri_params", "components", "connection_info",
                                     "user_config", "metadata")})
    return sorted(found)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--name", default="sponsorpulse")
    ap.add_argument("--plan", default="startup-10-256")
    ap.add_argument("--cloud", default="google-europe-west4")
    ap.add_argument("--branch", help="branch to deploy (with --create) or to switch to")
    ap.add_argument("--demo", default="1", help="DEMO_SYNTHETIC value (1 loops the invented match)")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--create", action="store_true")
    g.add_argument("--update-env", action="store_true")
    g.add_argument("--status", action="store_true")
    a = ap.parse_args()
    env = read_env()
    token, project = env["AIVEN_TOKEN"], env["AIVEN_PROJECT"]
    demo = a.demo == "1"
    path = f"/project/{project}/service/{a.name}"

    if a.status:
        svc = call("GET", path, token)["service"]
        print("state:", svc["state"], "| plan:", svc["plan"], "| cloud:", svc["cloud_name"])
        print("branch:", svc.get("user_config", {}).get("application", {}).get("source", {}).get("branch"))
        print("public URL candidates:", ", ".join(public_urls(svc)) or "none yet")
        return
    if a.update_env or (a.branch and not a.create):
        # PUT the whole application config: secret values read back from the API are redacted
        cur = call("GET", path, token)["service"]["user_config"]["application"]["source"]["branch"]
        branch = a.branch or cur
        call("PUT", path, token, {"user_config": app_config(branch, env, demo)})
        print(f"{a.name}: config replaced from .env, branch {branch}; Runtime redeploys")
        return

    body = {"service_name": a.name, "service_type": "application", "plan": a.plan, "cloud": a.cloud,
            "user_config": app_config(a.branch or "main", env, demo)}
    print(f"app {a.name}: plan {a.plan} in {a.cloud}, price {price(token, project, a.plan, a.cloud)}")
    print(f"source {REPO} @ {a.branch or 'main'}, Dockerfile, port {PORT} (HTTP, public HTTPS on 443)")
    print("env:", ", ".join(f"{v['key']}({v['kind']})" for v in body["user_config"]["application"]
                            ["environment_variables"]))
    if not a.create:
        print("dry run: add --create to create it (billed while powered on)")
        return
    svc = call("POST", f"/project/{project}/service", token, body)["service"]
    print("created:", svc["service_name"], svc["state"])


if __name__ == "__main__":
    main()
