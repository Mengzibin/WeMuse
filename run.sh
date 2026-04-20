#!/usr/bin/env bash
# 启动微信聊天助手。首次运行会自动建 venv 并装依赖。
set -euo pipefail

cd "$(dirname "$0")"

VENV="${VENV:-.venv}"
if [[ ! -d "$VENV" ]]; then
  echo "[setup] 创建虚拟环境 $VENV"
  python3 -m venv "$VENV"
  "$VENV/bin/pip" install --upgrade pip
  "$VENV/bin/pip" install -r requirements.txt
fi

exec "$VENV/bin/python" -m src.main
