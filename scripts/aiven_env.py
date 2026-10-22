"""Fill .env with Aiven connection details and download Kafka certs. Prints no secrets.

Usage: python scripts/aiven_env.py            (summary only)
       python scripts/aiven_env.py --write    (update .env and data/certs/)
"""
import json
import pathlib
import sys
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parents[1]
ENV = ROOT / ".env"
CERTS = ROOT / "data" / "certs"
API = "https://api.aiven.io/v1"


def read_env():
    env = {}
    for line in ENV.read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            k, v = line.split("=", 1)
            env[k.strip()] = v.split("#", 1)[0].strip() if not k.startswith("AIVEN_TOKEN") else v.strip()
    return env


def set_env(updates):
    lines = ENV.read_text(encoding="utf-8").splitlines()
    seen = set()
    for i, line in enumerate(lines):
        k = line.split("=", 1)[0].strip()
        if "=" in line and k in updates:
            lines[i] = f"{k}={updates[k]}"
            seen.add(k)
    lines += [f"{k}={v}" for k, v in updates.items() if k not in seen]
    ENV.write_text("\n".join(lines) + "\n", encoding="utf-8")


def get(path, token):
    req = urllib.request.Request(API + path, headers={"Authorization": f"aivenv1 {token}"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def main():
    write = "--write" in sys.argv
    env = read_env()
    token, project = env["AIVEN_TOKEN"], env["AIVEN_PROJECT"]
    services = get(f"/project/{project}/service", token)["services"]
    updates = {}
    for s in services:
        name, typ, state, plan = s["service_name"], s["service_type"], s["state"], s["plan"]
        print(f"{typ:12} {name:22} {state:12} {plan}")
        if state != "RUNNING":
            continue
        # The list endpoint returns redacted passwords; the detail call with include_secrets does not.
        s = get(f"/project/{project}/service/{name}?include_secrets=true", token)["service"]
        users = {u["username"]: u for u in s.get("users", [])}
        admin = users.get("avnadmin", {})
        if typ == "pg" and plan != "free-1-1gb":
            updates["PG_URI"] = s["service_uri"]
        elif typ == "valkey":
            updates["VALKEY_URI"] = s["service_uri"]
        elif typ == "clickhouse":
            comp = next(c for c in s["components"] if c["component"] == "clickhouse_https")
            updates |= {"CLICKHOUSE_HOST": comp["host"], "CLICKHOUSE_PORT": str(comp["port"]),
                        "CLICKHOUSE_USER": "avnadmin", "CLICKHOUSE_PASSWORD": admin.get("password", "")}
        elif typ == "kafka":
            kc = [c for c in s["components"] if c["component"] == "kafka"]
            for c in kc:
                print(f"  kafka component: auth={c.get('kafka_authentication_method')} port={c['port']} route={c.get('route')}")
            comp = next(c for c in kc if c.get("route") == "dynamic")
            updates["KAFKA_BOOTSTRAP"] = f"{comp['host']}:{comp['port']}"
            if write and admin.get("access_cert"):
                CERTS.mkdir(parents=True, exist_ok=True)
                ca = get(f"/project/{project}/kms/ca", token)["certificate"]
                (CERTS / "kafka-ca.pem").write_text(ca, encoding="utf-8")
                (CERTS / "kafka-service.cert").write_text(admin["access_cert"], encoding="utf-8")
                (CERTS / "kafka-service.key").write_text(admin["access_key"], encoding="utf-8")
                print("  kafka certs written to data/certs/")
    print("keys found:", ", ".join(sorted(updates)) or "none")
    if write and updates:
        set_env(updates)
        print(".env updated")


if __name__ == "__main__":
    main()
