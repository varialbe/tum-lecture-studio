#!/usr/bin/env bash
# Start TUM Lecture Studio natively (macOS / Linux).
#   scripts/start.sh            -> native app on http://127.0.0.1:8420
#   scripts/start.sh docker     -> docker compose (CPU image)
#   scripts/start.sh docker-gpu -> docker compose (NVIDIA image)
#   scripts/start.sh companion  -> only the native STT companion (macOS + Docker setup)
set -euo pipefail
cd "$(dirname "$0")/.."
ROOT="$PWD"
mode="${1:-native}"

need() { command -v "$1" >/dev/null 2>&1 || { echo "Missing: $1 ($2)"; exit 1; }; }

case "$mode" in
  docker)     need docker "https://docs.docker.com/get-docker/"; docker compose up -d --build studio; echo "Open http://localhost:8420"; exit ;;
  docker-gpu) need docker "https://docs.docker.com/get-docker/"; docker compose --profile gpu up -d --build studio-gpu; echo "Open http://localhost:8420"; exit ;;
esac

need ffmpeg "macOS: brew install ffmpeg | Debian/Ubuntu: sudo apt install ffmpeg"
if ! command -v uv >/dev/null 2>&1; then
  echo "Installing uv (Python package manager)..."; curl -LsSf https://astral.sh/uv/install.sh | sh; export PATH="$HOME/.local/bin:$PATH"
fi

extras="whisper"
if [[ "$(uname -s)" == "Darwin" && "$(uname -m)" == "arm64" ]]; then extras="whisper,mlx"
elif command -v nvidia-smi >/dev/null 2>&1; then extras="cuda"; fi

cd backend
[[ -d .venv ]] || uv venv --python 3.11 .venv
uv pip install --python .venv/bin/python -q -e ".[${extras}]"
if [[ "$extras" == "cuda" ]]; then
  SP="$(.venv/bin/python -c 'import site;print(site.getsitepackages()[0])')"
  export LD_LIBRARY_PATH="$SP/nvidia/cublas/lib:$SP/nvidia/cudnn/lib:${LD_LIBRARY_PATH:-}"
fi
export TLS_DATA_DIR="${TLS_DATA_DIR:-$ROOT/data}"

if [[ "$mode" == "companion" ]]; then
  exec .venv/bin/lecture-studio-stt --host 127.0.0.1 --port 8421 --models-dir "$TLS_DATA_DIR/models"
fi

if [[ ! -f lecture_studio/static/index.html ]]; then
  need npm "https://nodejs.org (20+)"
  (cd "$ROOT/frontend" && npm ci --no-audit --no-fund && npm run build)
  rm -rf lecture_studio/static && cp -r "$ROOT/frontend/dist" lecture_studio/static
fi
echo "TUM Lecture Studio: http://127.0.0.1:8420"
exec .venv/bin/lecture-studio
