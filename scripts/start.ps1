# Start TUM Lecture Studio on Windows.  Easiest: double-click start.cmd in the project folder.
#   .\scripts\start.ps1              native app on http://127.0.0.1:8420 (CUDA if an NVIDIA GPU is present)
#   .\scripts\start.ps1 docker       docker compose (CPU image)
#   .\scripts\start.ps1 docker-gpu   docker compose (NVIDIA image, needs Docker Desktop + WSL2 GPU support)
#   .\scripts\start.ps1 companion    only the STT companion on :8421
param([string]$Mode = "native")
# Native tools print progress on stderr; with "Stop", Windows PowerShell 5.1 would abort on it.
$ErrorActionPreference = "Continue"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

function Fail($msg) { Write-Host ""; Write-Host "ERROR: $msg" -ForegroundColor Red; exit 1 }
function Check($what) { if ($LASTEXITCODE -ne 0) { Fail "$what failed (exit code $LASTEXITCODE). See the output above." } }

if ($Mode -eq "docker") { docker compose up -d --build studio; Check "docker compose"; "Open http://localhost:8420"; exit }
if ($Mode -eq "docker-gpu") { docker compose --profile gpu up -d --build studio-gpu; Check "docker compose"; "Open http://localhost:8420"; exit }

$port = if ($env:TLS_PORT) { $env:TLS_PORT } else { "8420" }
try {
  $r = Invoke-WebRequest -UseBasicParsing -TimeoutSec 2 "http://127.0.0.1:$port/api/health"
  if ($r.StatusCode -eq 200) {
    Write-Host "TUM Lecture Studio is already running on http://127.0.0.1:$port - opening it."
    Start-Process "http://127.0.0.1:$port"; exit 0
  }
} catch { }

if (-not (Get-Command ffmpeg -ErrorAction SilentlyContinue)) {
  Fail "ffmpeg is missing. Install it with:  winget install Gyan.FFmpeg   (then open a new terminal)"
}
if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
  Write-Host "Installing uv (Python package manager)..."
  powershell -NoProfile -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
  $env:Path = "$env:USERPROFILE\.local\bin;$env:Path"
  if (-not (Get-Command uv -ErrorAction SilentlyContinue)) { Fail "uv could not be installed. See https://docs.astral.sh/uv/" }
}
$extras = "whisper"
if (Get-Command nvidia-smi -ErrorAction SilentlyContinue) { $extras = "cuda" }

Set-Location "$Root\backend"
if (-not (Test-Path .venv\Scripts\python.exe)) {
  Write-Host "Creating Python environment (first start only)..."
  cmd /c "uv venv --python 3.11 .venv 2>&1"; Check "Creating the Python environment"
}
Write-Host "Installing/updating dependencies ($extras)... the first time this downloads ~1-2 GB."
cmd /c "uv pip install --python .venv\Scripts\python.exe -q -e .[$extras] 2>&1"; Check "Installing dependencies"
if (-not $env:TLS_DATA_DIR) { $env:TLS_DATA_DIR = "$Root\data" }

if ($Mode -eq "companion") { & .venv\Scripts\lecture-studio-stt.exe --host 127.0.0.1 --port 8421 --models-dir "$env:TLS_DATA_DIR\models"; exit }

if (-not (Test-Path lecture_studio\static\index.html)) {
  if (-not (Get-Command npm -ErrorAction SilentlyContinue)) {
    Fail "Node.js is needed once to build the GUI. Install it with:  winget install OpenJS.NodeJS.LTS   (then open a new terminal)"
  }
  Write-Host "Building the GUI (first start only)..."
  Push-Location "$Root\frontend"
  npm ci --no-audit --no-fund; Check "npm ci"
  npm run build; Check "Building the GUI"
  Pop-Location
  if (Test-Path lecture_studio\static) { Remove-Item -Recurse -Force lecture_studio\static }
  Copy-Item -Recurse "$Root\frontend\dist" lecture_studio\static
}
if (-not $env:TLS_OPEN_BROWSER) { $env:TLS_OPEN_BROWSER = "1" }
& .venv\Scripts\lecture-studio.exe
