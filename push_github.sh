#!/usr/bin/env bash
# ============================================================
# 一键推送到 GitHub
#
# 用法：
#   ./push_github.sh                        # 交互式：询问仓库名并推送
#   ./push_github.sh my-netops              # 指定仓库名
#   ./push_github.sh my-netops private      # 指定仓库名与可见性
#
# 需要准备其中之一：
#   1) GitHub Personal Access Token（推荐，权限勾选 repo）
#      导出：export GITHUB_TOKEN=ghp_xxxxxxxxxxxx
#   2) 已配置 SSH key 并绑定到 GitHub 账号
#
# 脚本会：
#   - 校验仓库状态
#   - 通过 GitHub API 创建远程仓库
#   - 添加 remote 并推送 main 分支
#   - 创建 Release 标签（可选）
# ============================================================
set -euo pipefail

REPO_NAME="${1:-netops}"
VISIBILITY="${2:-public}"
GITHUB_API="https://api.github.com"

cd "$(dirname "${BASH_SOURCE[0]}")"

echo "=========================================="
echo " NetOps → GitHub 推送"
echo "=========================================="

# ---------- 1. 检查 git 仓库 ----------
if [ ! -d ".git" ]; then
  echo "✗ 当前目录不是 git 仓库"
  exit 1
fi

if [ -z "$(git status --porcelain)" ]; then
  echo "✓ 工作区干净，无需额外提交"
else
  echo "→ 检测到未提交的改动，正在提交"
  git add -A
  git commit -m "chore: 更新项目文件"
fi

# ---------- 2. 确认分支 ----------
CURRENT_BRANCH="$(git rev-parse --abbrev-ref HEAD)"
if [ "$CURRENT_BRANCH" = "HEAD" ]; then
  git checkout -b main
  CURRENT_BRANCH="main"
fi
echo "✓ 当前分支：$CURRENT_BRANCH"

# ---------- 3. 获取 GitHub 凭据 ----------
USE_SSH=0
if [ -n "${GITHUB_TOKEN:-}" ]; then
  echo "✓ 使用环境变量 GITHUB_TOKEN"
  AUTH_HEADER="Authorization: Bearer ${GITHUB_TOKEN}"
elif [ -f "$HOME/.ssh/id_ed25519" ] || [ -f "$HOME/.ssh/id_rsa" ]; then
  echo "✓ 检测到 SSH 密钥"
  USE_SSH=1
  AUTH_HEADER=""
else
  echo ""
  echo "✗ 未找到 GitHub 凭据。请任选一种方式后重试："
  echo ""
  echo "  方式一：使用 Personal Access Token（推荐）"
  echo "    1) 打开 https://github.com/settings/tokens"
  echo "    2) 生成 Token（勾选 repo 权限）"
  echo "    3) 执行：export GITHUB_TOKEN=ghp_你的token"
  echo ""
  echo "  方式二：配置 SSH key"
  echo "    ssh-keygen -t ed25519 -C '你的邮箱'"
  echo "    将 ~/.ssh/id_ed25519.pub 添加到 GitHub → Settings → SSH keys"
  exit 1
fi

# ---------- 4. 获取 GitHub 用户名 ----------
if [ "$USE_SSH" = "1" ]; then
  GITHUB_USER="$(ssh -T git@github.com 2>&1 | sed -n 's/^Hi \([^!]*\)!.*/\1/p')"
else
  GITHUB_USER="$(curl -s -H "$AUTH_HEADER" "$GITHUB_API/user" | python -c "import sys,json; print(json.load(sys.stdin).get('login',''))" 2>/dev/null || true)"
fi

if [ -z "$GITHUB_USER" ]; then
  echo "✗ 无法获取 GitHub 用户名，请检查凭据是否正确"
  exit 1
fi
echo "✓ GitHub 用户：$GITHUB_USER"

REMOTE_URL="https://github.com/${GITHUB_USER}/${REPO_NAME}.git"
SSH_URL="git@github.com:${GITHUB_USER}/${REPO_NAME}.git"

# ---------- 5. 创建远程仓库 ----------
if [ "$USE_SSH" = "1" ]; then
  FINAL_URL="$SSH_URL"
  CREATE_CMD=(
    curl -s -X POST "$GITHUB_API/user/repos"
    -H "$AUTH_HEADER"
    -d "{\"name\":\"${REPO_NAME}\",\"description\":\"多厂商网络设备监控平台（SNMP / NetFlow / gNMI Telemetry）\",\"private\":$( [ "$VISIBILITY" = "private" ] && echo true || echo false ),\"auto_init\":false}"
  )
else
  FINAL_URL="$REMOTE_URL"
  CREATE_CMD=(
    curl -s -X POST "$GITHUB_API/user/repos"
    -H "$AUTH_HEADER"
    -d "{\"name\":\"${REPO_NAME}\",\"description\":\"多厂商网络设备监控平台（SNMP / NetFlow / gNMI Telemetry）\",\"private\":$( [ "$VISIBILITY" = "private" ] && echo true || echo false ),\"auto_init\":false}"
  )
fi

echo "→ 检查远程仓库是否已存在"
if [ "$USE_SSH" = "1" ]; then
  EXISTS=$(ssh -T git@github.com 2>&1 >/dev/null; git ls-remote "$SSH_URL" >/dev/null 2>&1 && echo yes || echo no)
else
  EXISTS=$(curl -s -H "$AUTH_HEADER" "$GITHUB_API/repos/${GITHUB_USER}/${REPO_NAME}" | grep -q '"id"' && echo yes || echo no)
fi

if [ "$EXISTS" = "yes" ]; then
  echo "✓ 仓库已存在，直接推送"
else
  echo "→ 创建仓库 ${GITHUB_USER}/${REPO_NAME}（${VISIBILITY}）"
  RESULT=$("${CREATE_CMD[@]}" || true)
  if echo "$RESULT" | grep -q '"id"'; then
    echo "✓ 仓库创建成功"
  elif echo "$RESULT" | grep -q '"message"'; then
    echo "✗ 创建失败：$(echo "$RESULT" | python -c "import sys,json; print(json.load(sys.stdin).get('message',''))" 2>/dev/null || echo "$RESULT" | head -c 200)"
    echo "  提示：若提示 'already exists'，可改为直接推送已有仓库。"
    exit 1
  else
    echo "✗ 创建失败：$RESULT"
    exit 1
  fi
fi

# ---------- 6. 添加 remote 并推送 ----------
if git remote get-url origin >/dev/null 2>&1; then
  git remote set-url origin "$FINAL_URL"
else
  git remote add origin "$FINAL_URL"
fi
echo "✓ remote: $FINAL_URL"

echo "→ 推送分支 $CURRENT_BRANCH"
git push -u origin "$CURRENT_BRANCH"

# ---------- 7. 打标签 ----------
VERSION="v1.0.0"
if ! git tag -l "$VERSION" | grep -q .; then
  git tag -a "$VERSION" -m "NetOps $VERSION"
  git push origin "$VERSION"
  echo "✓ 已创建并推送标签 $VERSION"
fi

echo ""
echo "=========================================="
echo " ✓ 推送完成"
echo "=========================================="
echo " 仓库地址：https://github.com/${GITHUB_USER}/${REPO_NAME}"
echo ""
echo " 部署提示："
echo "   git clone https://github.com/${GITHUB_USER}/${REPO_NAME}.git"
echo "   cd ${REPO_NAME} && docker compose up -d --build"
echo "=========================================="
