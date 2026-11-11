#!/usr/bin/env bash
# NetOps 重启脚本（Linux / macOS）
#
# 用法：
#   ./restart.sh            # 停止后重新后台启动
#   ./restart.sh --rebuild  # 重启并强制重新构建前端
set -e

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "==> 停止服务"
"$ROOT/stop.sh" || true

echo ""
echo "==> 重新启动"
if [ "${1:-}" = "--rebuild" ]; then
  "$ROOT/start.sh" --rebuild
else
  "$ROOT/start.sh"
fi
