#!/usr/bin/env bash
# NetOps 一键启动：构建前端 + 启动后端（前后端单端口）
set -e

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PORT="${PORT:-8900}"
HOST="${HOST:-0.0.0.0}"

echo "==> 检查依赖"
command -v python >/dev/null || { echo "未找到 python"; exit 1; }
command -v node >/dev/null || { echo "未找到 node"; exit 1; }

cd "$ROOT/backend"
if ! python -c "import fastapi" >/dev/null 2>&1; then
  echo "==> 安装后端依赖"
  python -m pip install -r requirements.txt
fi

cd "$ROOT/frontend"
if [ ! -d node_modules ]; then
  echo "==> 安装前端依赖"
  npm install
fi
if [ ! -d dist ] || [ "${FORCE_BUILD:-0}" = "1" ]; then
  echo "==> 构建前端"
  npm run build
fi

cd "$ROOT/backend"
mkdir -p data/backups data/reports data/imports

echo "==> 启动 NetOps  →  http://localhost:${PORT}"
exec python -m uvicorn app.main:app --host "$HOST" --port "$PORT"
