# syntax=docker/dockerfile:1.7
# TUM Lecture Studio - one image for GUI, API, downloader and (CPU/CUDA) transcription.
#   docker build -t tum-lecture-studio .                       # CPU (amd64 + arm64)
#   docker build -t tum-lecture-studio:cuda --target cuda .    # NVIDIA CUDA 12 (amd64)

FROM node:20.18-bookworm-slim AS frontend
WORKDIR /src
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

FROM python:3.11-slim-bookworm AS base
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 \
    TLS_IN_DOCKER=1 TLS_HOST=0.0.0.0 TLS_PORT=8420 TLS_DATA_DIR=/data \
    HF_HUB_DISABLE_TELEMETRY=1
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg ca-certificates tini \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 1000 studio && mkdir -p /data && chown studio /data
WORKDIR /app
COPY backend/pyproject.toml ./
COPY backend/lecture_studio ./lecture_studio
COPY --from=frontend /src/dist ./lecture_studio/static

FROM base AS cpu
RUN pip install ".[whisper]"
USER studio
VOLUME ["/data"]
EXPOSE 8420
HEALTHCHECK --interval=30s --timeout=5s CMD python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8420/api/health')"
ENTRYPOINT ["tini", "--"]
CMD ["lecture-studio"]

FROM base AS cuda
# cuBLAS + cuDNN 9 from PyPI (as documented by faster-whisper); the driver comes from the host
# via the NVIDIA Container Toolkit.
RUN pip install ".[cuda]"
ENV LD_LIBRARY_PATH=/usr/local/lib/python3.11/site-packages/nvidia/cublas/lib:/usr/local/lib/python3.11/site-packages/nvidia/cudnn/lib
USER studio
VOLUME ["/data"]
EXPOSE 8420
HEALTHCHECK --interval=30s --timeout=5s CMD python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8420/api/health')"
ENTRYPOINT ["tini", "--"]
CMD ["lecture-studio"]

# Default target = CPU image
FROM cpu
