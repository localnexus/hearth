"""sidecar_runner.py — run the Hindsight server as a sidecar process.

Executed with the SIDECAR venv's python (the one holding ``hindsight-all``),
never the engine venv: hindsight's server closure needs protobuf>=7 while
pipecat pins protobuf<7, so the two can never share an interpreter. The
engine's adapter (backend_hindsight.py, mode="sidecar") spawns this script,
reads the ``HINDSIGHT_URL=…`` line from stdout, talks to the server with the
lightweight ``hindsight-client`` SDK, and SIGTERMs the process at close.

Protocol (parent-facing, deliberately tiny):
  stdout line 1..n : free logging from hindsight's own startup
  one line         : HINDSIGHT_URL=http://127.0.0.1:<port>
  then             : blocks until SIGTERM/SIGINT → server.stop() → exit 0
"""

from __future__ import annotations

import argparse
import os
import signal
import sys
import threading

# Egress kill switch #2 (survey §5b): litellm otherwise fetches its model-cost
# map from raw.githubusercontent.com at import. The parent usually exports this
# already; setdefault keeps the runner safe when launched by hand.
os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--db-url", default="pg0")
    parser.add_argument("--llm-provider", default="ollama")
    parser.add_argument("--llm-model", required=True)
    parser.add_argument("--llm-api-key", default="")
    parser.add_argument("--log-level", default="warning")
    args = parser.parse_args()

    from hindsight import HindsightServer  # sidecar venv only

    server = HindsightServer(
        db_url=args.db_url,
        llm_provider=args.llm_provider,
        llm_api_key=args.llm_api_key,
        llm_model=args.llm_model,
        log_level=args.log_level,
    )
    server.start()
    print(f"HINDSIGHT_URL={server.url}", flush=True)

    stop = threading.Event()

    def _terminate(_sig, _frame):  # noqa: ANN001
        stop.set()

    signal.signal(signal.SIGTERM, _terminate)
    signal.signal(signal.SIGINT, _terminate)
    stop.wait()
    server.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
