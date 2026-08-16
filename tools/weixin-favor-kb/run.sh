#!/bin/bash
# WSL 环境启动脚本 — 注入 CUDA 库路径后运行 pipeline

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# 修改为你的 NVIDIA CUDA 库路径，例如从其他 venv 中复用
CUDA_BASE="/path/to/your/venv/lib/python3.12/site-packages/nvidia"

CUDA_LIBS=""
for dir in "$CUDA_BASE"/*/lib; do
    [ -d "$dir" ] && CUDA_LIBS="${CUDA_LIBS:+$CUDA_LIBS:}$dir"
done

export LD_LIBRARY_PATH="${CUDA_LIBS}${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"

source "${SCRIPT_DIR}/venv/bin/activate"
cd "$SCRIPT_DIR"
python pipeline.py "$@"
