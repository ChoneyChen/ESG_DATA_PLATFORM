import sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[3]
for module in ('extraction','contracts','shared_runtime','document'):
    sys.path.insert(0,str(ROOT/'ExtractESG'/module/'src'))
