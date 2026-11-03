# Start TUM Lecture Studio on Windows.
#   .\scripts\start.ps1              native app on http://127.0.0.1:8420 (CUDA if an NVIDIA GPU is present)
#   .\scripts\start.ps1 docker       docker compose (CPU image)
#   .\scripts\start.ps1 docker-gpu   docker compose (NVIDIA image, needs Docker Desktop + WSL2 GPU support)
param([string]$Mode = "native")
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

if ($Mode -eq "docker") { docker compose up -d --build studio; "Open http://localhost:8420"; exit }
if ($Mode -eq "docker-gpu") { docker compose --profile gpu up -d --build studio-gpu; "Open http://localhost:8420"; exit }

if (-not (Get-Command ffmpeg -ErrorAction SilentlyContinue)) {
  Write-Host "ffmpeg is missing. Install it with:  winget install Gyan.FFmpeg   (then open a new terminal)"; exit 1
}
if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
  Write-Host "Installing uv..."; powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
  $env:Path = "$env:USERPROFILE\.local\bin;$env:Path"
}
$extras = "whisper"
if (Get-Command nvidia-smi -ErrorAction SilentlyContinue) { $extras = "cuda" }

Set-Location "$Root\backend"
if (-not (Test-Path .venv)) { uv venv --python 3.11 .venv }
uv pip install --python .venv\Scripts\python.exe -q -e ".[$extras]"
if (-not $env:TLS_DATA_DIR) { $env:TLS_DATA_DIR = "$Root\data" }

if ($Mode -eq "companion") { & .venv\Scripts\lecture-studio-stt.exe --host 127.0.0.1 --port 8421 --models-dir "$env:TLS_DATA_DIR\models"; exit }

if (-not (Test-Path lecture_studio\static\index.html)) {
  Push-Location "$Root\frontend"; npm ci --no-audit --no-fund; npm run build; Pop-Location
  if (Test-Path lecture_studio\static) { Remove-Item -Recurse -Force lecture_studio\static }
  Copy-Item -Recurse "$Root\frontend\dist" lecture_studio\static
}
Write-Host "TUM Lecture Studio: http://127.0.0.1:8420"
& .venv\Scripts\lecture-studio.exe
