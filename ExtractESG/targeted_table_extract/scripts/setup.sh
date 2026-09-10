#!/bin/zsh
set -euo pipefail

ROOT="${0:A:h:h}"
MODEL_RUNTIME="${ESG_MODEL_RUNTIME:-$HOME/Desktop/model/.runtime}"
PYTHON="${ESG_MODEL_PYTHON:-$MODEL_RUNTIME/venv/bin/python}"
UV="${ESG_MODEL_UV:-$MODEL_RUNTIME/bin/uv}"

if [[ ! -x "$PYTHON" || ! -x "$UV" ]]; then
  print -u2 "模型运行环境不存在：$MODEL_RUNTIME"
  print -u2 "请先运行桌面的 deploy_esg_models.command。"
  exit 1
fi

"$UV" pip install --python "$PYTHON" \
  -e "$ROOT/../standard_packages" \
  -e "$ROOT/backend[models,dev]"

"$PYTHON" - <<'PY'
from pathlib import Path
import importlib

checks = {
    "NuExtract3": Path.home() / "Desktop/model/NuExtract3-mlx-4bits/model.safetensors",
    "Qwen3 Embedding": Path.home() / "Desktop/model/Qwen3-Embedding-0.6B-8bit/model.safetensors",
}
for name, path in checks.items():
    print(f"{name}: {'OK' if path.is_file() else 'MISSING'} ({path})")
for module in ("esg_targeted", "esg_standard_packages", "mlx_vlm", "mlx_embeddings"):
    importlib.import_module(module)
    print(f"Python module: OK ({module})")
PY

print "安装检查完成。"
