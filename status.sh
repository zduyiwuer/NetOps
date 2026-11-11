#!/usr/bin/env bash
# NetOps 服务状态查看（Linux / macOS）
#
# 用法：
#   ./status.sh
set -e

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND="$ROOT/backend"
PID_FILE="$BACKEND/data/run/netops.pid"
LOG_FILE="$BACKEND/data/logs/netops.log"
PORT="${PORT:-8900}"

cd "$BACKEND"

echo "=== NetOps 服务状态 ==="

PID=""
if [ -f "$PID_FILE" ]; then
  PID="$(python -m scripts.service_helper read-pid "$PID_FILE" 2>/dev/null || true)"
fi
if [ -z "$PID" ]; then
  PID="$(python -m scripts.service_helper find-pid "$PORT" 2>/dev/null | head -1 || true)"
fi

if [ -z "$PID" ]; then
  echo "状态    : 未运行"
  if python -m scripts.service_helper reachable "$PORT" >/dev/null 2>&1; then
    echo "提示    : 端口 $PORT 有服务在监听，但不是 NetOps 进程，请人工确认"
  fi
  exit 1
fi

if ! python -m scripts.service_helper alive "$PID" >/dev/null 2>&1; then
  echo "状态    : 未运行（PID 文件已失效，进程不存在）"
  exit 1
fi

echo "状态    : 运行中"
echo "PID     : $PID"

# 健康：必须「进程存活」且「端口可连」才算正常，
# 只看端口会在进程已死但端口被其他程序占用时误报「健康」
if python -m scripts.service_helper healthy "$PID" "$PORT" >/dev/null 2>&1; then
  echo "健康    : 正常（进程存活，端口 $PORT 可连接）"
else
  echo "健康    : 异常（进程存活但端口 $PORT 无法连接，服务可能仍在启动或已卡死）"
fi

echo "日志    : $LOG_FILE"
if [ -f "$LOG_FILE" ]; then
  echo "日志大小: $(du -h "$LOG_FILE" 2>/dev/null | cut -f1)"
fi
