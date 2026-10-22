"""Run a worker: python -m workers <name>. Workers are added by their tasks (T06, T10, T14, ...)."""
import importlib
import sys

if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit("usage: python -m workers <ingest|router|aggregate|curator|detective>")
    importlib.import_module(f"workers.{sys.argv[1]}").main()
