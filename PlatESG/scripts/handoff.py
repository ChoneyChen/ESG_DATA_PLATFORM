"""Deliver a finalized full/partial Result Bundle with explicit report identity."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT.parent))
from PlatESG.backend.main import ImportRequest


def deliver(bundle: Path, metadata: Path, inbox: Path):
    payload = ImportRequest.model_validate({"bundle_path": str(bundle.resolve()),
        "report_metadata": json.loads(metadata.read_text(encoding="utf-8"))}).model_dump(mode="json")
    data = json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2).encode("utf-8")
    inbox.mkdir(parents=True, exist_ok=True)
    target = inbox / (hashlib.sha256(data).hexdigest() + ".json")
    temporary = target.with_suffix(".tmp")
    temporary.write_bytes(data)
    temporary.replace(target)
    return target


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="交付候选包；主体及报告版本必须明确，交付不批准任何事实")
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--metadata", type=Path, required=True)
    parser.add_argument("--inbox", type=Path, default=Path(os.getenv("PLATESG_INBOX", ROOT / ".local/inbox")))
    args = parser.parse_args()
    print(deliver(args.bundle, args.metadata, args.inbox))
