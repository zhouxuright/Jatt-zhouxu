# =============================================================================
# Legal Intelligent Assistance System - Backend Dockerfile
# =============================================================================
# Multi-stage build for FastAPI backend with optimized dependencies.
#
# Build:
#   docker build -t legal-backend -f backend/Dockerfile backend/
#   docker run -p 8000:8000 --env-file backend/.env legal-backend
# =============================================================================

# ---------------------------------------------------------------------------
# Stage 1: Build dependencies
# ---------------------------------------------------------------------------
FROM python:3.11-slim AS builder

# ---------------------------------------------------------------------------
# pip 源（可覆盖）
# ---------------------------------------------------------------------------
# 此前两处 pip install 都硬编码清华源。实测该源在本机**不可达**
# （urllib → URLError），导致 `docker compose build backend` 必然失败于
# "Could not find a version that satisfies the requirement fastapi>=0.115.0"。
# 实测可达的源：mirrors.aliyun.com、pypi.org。
#
# 改为 ARG，默认用可达的 aliyun；如需换源无需改 Dockerfile：
#   docker compose build --build-arg PIP_INDEX_URL=https://pypi.org/simple/ backend
ARG PIP_INDEX_URL=https://mirrors.aliyun.com/pypi/simple/

# ---------------------------------------------------------------------------
# CPU-only torch
# ---------------------------------------------------------------------------
# 本部署是纯 CPU 的：实测老镜像内 torch.cuda.is_available() == False，
# docker-compose.yml 也没有任何 GPU 预留。但 PyPI 默认的 torch wheel 是 CUDA
# 构建（2.14.0+cu130），会连带拉入 15 个 nvidia-* 包、约 5GB，
# 这些依赖在本机永远无法使用 —— 镜像因此膨胀到 12.6GB，
# 且每次重建都要重新下载这 5GB（实测网速 0.3~1.2 MB/s，会卡死）。
#
# 因此先从 PyTorch 官方 CPU 索引装 torch（体积小一个数量级），
# 之后 requirements.txt 看到 torch 已满足便不会再装 CUDA 版。
# 版本与老镜像保持一致，避免引入行为变化。
ARG TORCH_CPU_INDEX=https://download.pytorch.org/whl/cpu
ARG TORCH_VERSION=2.14.0

WORKDIR /app

# Use CN mirrors: deb.debian.org is unreachable from many CN networks
RUN (sed -i 's|deb.debian.org|mirrors.aliyun.com|g' /etc/apt/sources.list.d/debian.sources || true) && \
    (sed -i 's|deb.debian.org|mirrors.aliyun.com|g' /etc/apt/sources.list || true)

# Install build dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Create the venv and install CPU-only torch FIRST, before requirements.txt.
# Keeping these in separate layers means the (large) torch layer survives any
# change to requirements.txt, and vice versa.
# --retries/--timeout: wheel 体量大，网络抖动时默认重试次数不足。
RUN python -m venv /opt/venv && \
    /opt/venv/bin/pip install --no-cache-dir --upgrade pip -i ${PIP_INDEX_URL} \
        --retries 5 --timeout 60

RUN /opt/venv/bin/pip install --no-cache-dir "torch==${TORCH_VERSION}" \
        --index-url ${TORCH_CPU_INDEX} \
        --retries 5 --timeout 60

# Copy requirements before installing them for better layer caching
COPY requirements.txt .

# torch 已在上一步装好，pip 不会再拉 CUDA 版（见文件头说明）。
RUN /opt/venv/bin/pip install --no-cache-dir -r requirements.txt -i ${PIP_INDEX_URL} \
        --retries 5 --timeout 60

# Voice input (speech-to-text): openai-whisper in a separate layer so the
# heavy requirements layer (torch + sentence-transformers/FlagEmbedding)
# stays cached. torch is already pulled by sentence-transformers.
RUN /opt/venv/bin/pip install --no-cache-dir openai-whisper -i ${PIP_INDEX_URL} \
        --retries 5 --timeout 60

# ---------------------------------------------------------------------------
# Stage 2: Runtime
# ---------------------------------------------------------------------------
FROM python:3.11-slim AS runtime

WORKDIR /app

# Install runtime dependencies (for PDF processing, etc.)
RUN (sed -i 's|deb.debian.org|mirrors.aliyun.com|g' /etc/apt/sources.list.d/debian.sources || true) && \
    (sed -i 's|deb.debian.org|mirrors.aliyun.com|g' /etc/apt/sources.list || true) && \
    apt-get update && apt-get install -y --no-install-recommends \
    libpq-dev \
    wget \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# Copy virtual environment from builder
COPY --from=builder /opt/venv /opt/venv

# Set environment variables
ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH="/app"

# Create non-root user
RUN useradd --create-home --shell /bin/bash appuser

# Create app directories
RUN mkdir -p /app/uploads /app/chroma_data /app/models/whisper && \
    chown -R appuser:appuser /app

# Copy application code
# BUILD: 2026-09-02-v3 (force rebuild for Python 3.11 f-string fix)
COPY --chown=appuser:appuser . .

# Switch to non-root user
USER appuser

# Expose port
EXPOSE 8000

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=40s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')" || exit 1

# Start the application (PORT env respected so compose/nginx/healthcheck stay in sync;
# 1 worker per replica so per-worker AI models (BGE-M3 ~1.2GB fp16) stay within memory limits)
CMD uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} --workers 1 --proxy-headers