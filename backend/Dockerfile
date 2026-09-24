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

WORKDIR /app

# Use CN mirrors: deb.debian.org is unreachable from many CN networks
RUN (sed -i 's|deb.debian.org|mirrors.aliyun.com|g' /etc/apt/sources.list.d/debian.sources || true) && \
    (sed -i 's|deb.debian.org|mirrors.aliyun.com|g' /etc/apt/sources.list || true)

# Install build dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements first for better layer caching
COPY requirements.txt .

# Install Python dependencies into a virtual environment (CN pip mirror)
RUN python -m venv /opt/venv && \
    /opt/venv/bin/pip install --no-cache-dir --upgrade pip -i https://pypi.tuna.tsinghua.edu.cn/simple && \
    /opt/venv/bin/pip install --no-cache-dir -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple

# Voice input (speech-to-text): openai-whisper in a separate layer so the
# heavy requirements layer (torch + sentence-transformers/FlagEmbedding)
# stays cached. torch is already pulled by sentence-transformers.
RUN /opt/venv/bin/pip install --no-cache-dir openai-whisper -i https://pypi.tuna.tsinghua.edu.cn/simple

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