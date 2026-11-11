#!/usr/bin/env bash
# NetOps 启动脚本（Linux / macOS）
#
# 用法：
#   ./start.sh                # 后台启动（默认）
#   ./start.sh --foreground   # 前台运行（Ctrl+C 停止）
#   ./start.sh --rebuild      # 强制重新构建前端
#   PORT=9000 ./start.sh      # 指定端口
#
# 后台运行产物：
#   backend/data/run/netops.pid   进程 PID
#   backend/data/logs/netops.log   运行日志
set -e

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND="$ROOT/backend"
FRONTEND="$ROOT/frontend"
RUN_DIR="$BACKEND/data/run"
LOG_DIR="$BACKEND/data/logs"
PID_FILE="$RUN_DIR/netops.pid"
LOG_FILE="$LOG_DIR/netops.log"

PORT="${PORT:-8900}"
HOST="${HOST:-0.0.0.0}"
FOREGROUND=0
FORCE_BUILD=0

for arg in "$@"; do
  case "$arg" in
    --foreground|-f) FOREGROUND=1 ;;
    --rebuild)       FORCE_BUILD=1 ;;
    --help|-h)
      grep '^#' "${BASH_SOURCE[0]}" | sed -n '2,14p' | sed 's/^# \{0,1\}//'
      exit 0
      ;;
    *) echo "未知参数：$arg（用 --help 查看用法）"; exit 1 ;;
  esac
done

# ---------------------------------------------------------------- 环境准备
echo "==> 检查依赖"
command -v python >/dev/null || { echo "未找到 python，请先安装 Python 3.10+"; exit 1; }
command -v node  >/dev/null || { echo "未找到 node，请先安装 Node.js 18+";  exit 1; }

cd "$BACKEND"
if ! python -c "import fastapi" >/dev/null 2>&1; then
  echo "==> 安装后端依赖"
  python -m pip install -r requirements.txt
fi

cd "$FRONTEND"
if [ ! -d node_modules ]; then
  echo "==> 安装前端依赖"
  npm install
fi
if [ "$FORCE_BUILD" = "1" ] || [ ! -d dist ]; then
  echo "==> 构建前端"
  npm run build
fi

cd "$BACKEND"
mkdir -p data/backups data/reports data/imports "$RUN_DIR" "$LOG_DIR"

# ---------------------------------------------------------------- 端口检查
port_busy() {
  python -m scripts.service_helper port-busy "$PORT" >/dev/null 2>&1
}

# ------------------------------------------------------- 已在运行的检查
if [ -f "$PID_FILE" ]; then
  OLD_PID="$(python -m scripts.service_helper read-pid "$PID_FILE" 2>/dev/null || true)"
  if [ -n "$OLD_PID" ] && python -m scripts.service_helper alive "$OLD_PID" >/dev/null 2>&1; then
    echo "NetOps 已在运行（PID $OLD_PID），如需重启请执行 ./restart.sh"
    exit 0
  fi
  rm -f "$PID_FILE"
fi

if port_busy; then
  # 端口被占用：先判断是不是本平台的 NetOps 在跑
  RUNNING_PID="$(python -m scripts.service_helper find-pid "$PORT" 2>/dev/null | head -1 || true)"
  if [ -n "$RUNNING_PID" ]; then
    echo "NetOps 已在运行（PID $RUNNING_PID），如需重启请执行 ./restart.sh"
    exit 0
  fi
  echo "端口 $PORT 已被其他进程占用，请先释放该端口或用 PORT=<其他端口> ./start.sh"
  exit 1
fi

# ---------------------------------------------------------------- 前台模式
if [ "$FOREGROUND" = "1" ]; then
  echo "==> 前台启动 NetOps  ->  http://localhost:${PORT}"
  echo "    按 Ctrl+C 停止"
  exec python -m uvicorn app.main:app --host "$HOST" --port "$PORT"
fi

# ---------------------------------------------------------------- 后台模式
echo "==> 后台启动 NetOps"
LOG_TS="$(date +%Y%m%d_%H%M%S)"
# 日志按次切分，避免单文件无限增长
if [ -f "$LOG_FILE" ] && [ "$(wc -c < "$LOG_FILE")" -gt 5242880 ]; then
  mv -f "$LOG_FILE" "$LOG_DIR/netops_${LOG_TS}.log"
fi

# 用 Python 以平台原生方式启动脱离终端的进程：
#   Windows -> DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP | CREATE_NO_WINDOW
#   Linux   -> start_new_session（setsid 语义）
# 这样父 shell / 终端退出后服务仍继续运行。
NEW_PID="$(python -m scripts.service_helper spawn "$LOG_FILE" "$BACKEND" \
  python -m uvicorn app.main:app --host "$HOST" --port "$PORT" 2>/dev/null | head -1)"

if [ -z "$NEW_PID" ]; then
  echo "启动失败：无法创建后台进程"
  exit 1
fi
python -m scripts.service_helper write-pid "$PID_FILE" "$NEW_PID"

# ------------------------------------------------- 健康检查（等待就绪）
# 必须「进程存活」且「端口可连」才算就绪——
# 只看端口会被端口上的其他服务或连接残留误导，出现假成功。
echo -n "==> 等待服务就绪"
READY=0
for _ in $(seq 1 60); do
  if python -m scripts.service_helper healthy "$NEW_PID" "$PORT" >/dev/null 2>&1; then
    READY=1
    break
  fi
  if ! python -m scripts.service_helper alive "$NEW_PID" >/dev/null 2>&1; then
    echo ""
    echo "启动失败，进程已退出。最近日志："
    tail -n 20 "$LOG_FILE" 2>/dev/null
    rm -f "$PID_FILE"
    exit 1
  fi
  echo -n "."
  sleep 1
done

if [ "$READY" != "1" ]; then
  echo ""
  echo "服务在 60 秒内未就绪。最近日志："
  tail -n 20 "$LOG_FILE" 2>/dev/null
  echo "如需清理残留进程，请执行 ./stop.sh"
  exit 1
fi

echo ""
echo "    启动成功（PID $NEW_PID）"
echo "    访问地址 : http://localhost:${PORT}"
echo "    运行日志 : $LOG_FILE"
echo "    停止服务 : ./stop.sh"
