$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Root

$Python = "C:\Users\89345\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"
if (-not (Test-Path $Python)) {
  $Python = "python"
}

if (-not (Test-Path ".venv")) {
  & $Python -m venv ".venv"
}

$VenvPython = Join-Path $Root ".venv\Scripts\python.exe"
if (-not (Test-Path ".env")) {
@"
APP_PASSWORD=change-this-password
HOST=0.0.0.0
PORT=8787
TASK_ROOT=$Root\data\tasks
DATABASE_PATH=$Root\data\app.db
SKILL_DIR=$Root\skills\harmful_sample
PYTHON_BIN=$VenvPython
NODE_BIN=node
STORAGE_STATE_PATH=$Root\data\storage_state.json
CAPTURE_ENGINE=playwright
CAPTURE_PROFILE=desktop
DRISSION_DOMAINS=xiaohongshu.com,weibo.com
CAPTCHA_PAUSE_THRESHOLD=1
CAPTURE_HEADLESS=true
CAPTURE_FULL_PAGE=true
CHROMIUM_EXECUTABLE_PATH=
TASK_TIMEOUT_SECONDS=7200
"@ | Set-Content -Path ".env" -Encoding UTF8
}

& $VenvPython -m pip install --upgrade pip
& $VenvPython -m pip install -r requirements.txt

if ((Test-Path "package.json") -and (Get-Command npm -ErrorAction SilentlyContinue)) {
  if (-not (Test-Path "node_modules")) {
    npm install
  }
}

$Port = "8787"
$EnvPath = Join-Path $Root ".env"
if (Test-Path $EnvPath) {
  Get-Content $EnvPath | ForEach-Object {
    if ($_ -match "^\s*PORT\s*=\s*(.+?)\s*$") {
      $script:Port = $Matches[1].Trim().Trim('"').Trim("'")
    }
  }
}

Start-Process "http://127.0.0.1:$Port"
& $VenvPython -m uvicorn app.main:app --host 0.0.0.0 --port $Port
