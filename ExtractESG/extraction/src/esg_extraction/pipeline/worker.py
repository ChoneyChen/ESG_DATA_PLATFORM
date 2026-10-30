"""One current run executed by the unified document queue."""

import argparse, os, signal
from esg_contracts.worker import encode_event
from .runtime import execute
from .engine import PipelineCancelled


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("run_id")
    args = parser.parse_args()
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"

    def stop(signum, frame):
        raise PipelineCancelled("Worker cancellation requested")

    signal.signal(signal.SIGTERM, stop)
    result = execute(args.run_id)
    progress = result.get("progress") or {}
    print(
        encode_event(
            "result",
            payload={
                **result,
                "progress_current": progress.get("current", 0),
                "progress_total": progress.get("total", 0),
            },
        ),
        flush=True,
    )


if __name__ == "__main__":
    main()
