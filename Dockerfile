# ============================================================
# NetOps 网络设备监控平台 —— 多阶段构建
#
# 阶段 1：构建前端（Node 20 → 产出静态资源）
# 阶段 2：构建后端（Python 3.12 slim → 安装依赖 + 复制前端产物）
# 阶段 3：运行时镜像（精简，仅保留运行所需）
# ============================================================

# ---------- 阶段 1：前端构建 ----------
FROM node:20-alpine AS frontend-builder

WORKDIR /build

# 先拷贝依赖清单，利用 Docker 层缓存
COPY frontend/package.json frontend/package-lock.json* ./
RUN npm install --no-audit --no-fund

COPY frontend/ ./
RUN npm run build


# ---------- 阶段 2：后端构建 ----------
FROM python:3.12-slim AS backend-builder

WORKDIR /build

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential netcat-openbsd \
    && rm -rf /var/lib/apt/lists/*

COPY backend/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/ ./
# 生成 gNMI protobuf 代码（若已存在则跳过）
RUN python -m scripts.gen_proto || true


# ---------- 阶段 3：运行时 ----------
FROM python:3.12-slim AS runtime

LABEL org.opencontainers.image.title="NetOps" \
      org.opencontainers.image.description="多厂商网络设备监控平台（SNMP / NetFlow / gNMI Telemetry）" \
      org.opencontainers.image.licenses="MIT"

WORKDIR /app

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    TZ=Asia/Shanghai \
    NETOPS_BACKUP_DIR=/app/data/backups \
    NETOPS_REPORT_DIR=/app/data/reports \
    NETOPS_IMPORT_EXPORT_DIR=/app/data/imports \
    NETOPS_DATABASE_URL=sqlite:////app/data/netops.db \
    NETOPS_FRONTEND_DIR=/app/frontend/dist

# 运行时依赖：netcat 用于端口探测
RUN apt-get update \
    && apt-get install -y --no-install-recommends netcat-openbsd tzdata \
    && rm -rf /var/lib/apt/lists/*

COPY backend/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# 应用代码
COPY backend/app/ ./app/
COPY backend/scripts/ ./scripts/
COPY backend/.env.example ./.env.example

# 前端静态资源（由后端一并托管，路径与源码布局保持一致）
COPY --from=frontend-builder /build/dist ./frontend/dist

# 数据目录（作为卷挂载点）
RUN mkdir -p /app/data/backups /app/data/reports /app/data/imports

# 非 root 运行
RUN useradd -r -u 10001 -m netops \
    && chown -R netops:netops /app
USER netops

EXPOSE 8900 5514 2055 4739 9995 57400

VOLUME ["/app/data"]

HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
    CMD python -c "import urllib.request,sys;\
sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8900/api/health',timeout=4).status==200 else 1)"

CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8900"]
