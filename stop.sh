#!/usr/bin/env bash
# NetOps 停止脚本（Linux / macOS / Git Bash）
#
# 用法：
#   ./stop.sh            # 优雅停止（超时后强制结束）
#   ./stop.sh --force    # 立即强制结束
#
# 停止顺序：终止信号 -> 等待最多 10 秒 -> 强制结束 -> 清理端口残留
set -e

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND="$ROOT/backend"
PID_FILE="$BACKEND/data/run/netops.pid"
PORT="${PORT:-8900}"

FORCE=0
for arg in "$@"; do
  case "$arg" in
    --force|-f) FORCE=1 ;;
    --help|-h)
      grep '^#' "${BASH_SOURCE[0]}" | sed -n '2,9p' | sed 's/^# \{0,1\}//'
      exit 0
      ;;
    *) echo "未知参数：$arg（用 --help 查看用法）"; exit 1 ;;
  esac
done

cd "$BACKEND"

# ------------------------------------------------------ 读取 PID（可回退按端口反查）
PID=""
if [ -f "$PID_FILE" ]; then
  PID="$(python -m scripts.service_helper read-pid "$PID_FILE" 2>/dev/null || true)"
fi
if [ -z "$PID" ]; then
  PID="$(python -m scripts.service_helper find-pid "$PORT" 2>/dev/null | head -1 || true)"
fi

if [ -z "$PID" ]; then
  rm -f "$PID_FILE"
  echo "服务未在运行（无 PID，且端口 $PORT 上没有 NetOps 进程）"
  exit 0
fi

if ! python -m scripts.service_helper alive "$PID" >/dev/null 2>&1; then
  echo "进程 $PID 不存在（可能已退出），清理 PID 文件"
  rm -f "$PID_FILE"
  echo "服务未在运行"
  exit 0
fi

echo "==> 停止 NetOps（PID $PID）"

if [ "$FORCE" = "1" ]; then
  echo "    强制结束..."
  python -m scripts.service_helper kill "$PID" --force >/dev/null 2>&1 || true
  sleep 1
  rm -f "$PID_FILE"
  echo "已强制结束"
  exit 0
fi

# 第一步：优雅停止
echo "    发送终止信号..."
python -m scripts.service_helper kill "$PID" >/dev/null 2>&1 || true

WAITED=0
while [ "$WAITED" -lt 10 ]; do
  if ! python -m scripts.service_helper alive "$PID" >/dev/null 2>&1; then
    rm -f "$PID_FILE"
    echo "    已停止（用时 ${WAITED}s）"
    exit 0
  fi
  sleep 1
  WAITED=$((WAITED + 1))
done

# 第二步：超时强制结束
echo "    等待超时（10s），强制结束..."
python -m scripts.service_helper kill "$PID" --force >/dev/null 2>&1 || true
sleep 1

# 清理端口上可能残留的 NetOps 进程
python -m scripts.service_helper kill-netops-on-port "$PORT" >/dev/null 2>&1 || true

rm -f "$PID_FILE"
echo "已强制停止"
