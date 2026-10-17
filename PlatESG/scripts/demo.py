from __future__ import annotations
import json
import os
import sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT.parent))
from PlatESG.backend.bridge import DatabaseBridge

if __name__ == "__main__":
    # Separate default from the real candidate database. Explicit override is supported for tests.
    config={"PLATESG_DATA_DIR":os.getenv("PLATESG_DEMO_DATA_DIR",str(ROOT/".local/demo-data")),"PLATESG_ALLOW_DEMO_SEED":"1"}
    with_bridge=DatabaseBridge(config).start()
    try:
        print(json.dumps(with_bridge.call("seed_demo",timeout=120),ensure_ascii=False,indent=2))
    finally:
        with_bridge.close()
