#!/usr/bin/env bash
set -e

pause_on_error() {
  local exit_code=$?
  if [ "$exit_code" -ne 0 ]; then
    echo
    echo "启动失败，错误码: $exit_code"
    echo "请把上面的错误信息截图或复制出来。"
    read -r -p "按回车退出..."
  fi
}
trap pause_on_error EXIT

ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

echo "================================"
echo "有害样本任务台 - Mac 后台启动"
echo "================================"

echo
echo "[1/5] 检查 Python..."
CODEX_PYTHON="$HOME/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3.12"
if [ -x "$CODEX_PYTHON" ]; then
  PYTHON="$CODEX_PYTHON"
elif command -v python3.12 >/dev/null 2>&1; then
  PYTHON="python3.12"
elif command -v python3.11 >/dev/null 2>&1; then
  PYTHON="python3.11"
elif command -v python3.10 >/dev/null 2>&1; then
  PYTHON="python3.10"
elif command -v python3 >/dev/null 2>&1; then
  PYTHON="python3"
elif command -v python >/dev/null 2>&1; then
  PYTHON="python"
else
  echo "错误: 未找到 Python。请先安装 Python 3.10 或更高版本。"
  read -r -p "按回车退出..."
  exit 1
fi
"$PYTHON" --version
"$PYTHON" - <<'PY'
import sys
if sys.version_info < (3, 10):
    raise SystemExit("错误: 当前 Python 版本低于 3.10，无法安装项目依赖。请安装 Python 3.10 或更高版本。")
PY

echo
echo "[2/5] 准备虚拟环境..."
if [ -d ".venv" ]; then
  if ! .venv/bin/python - <<'PY' >/dev/null 2>&1
import sys
raise SystemExit(0 if sys.version_info >= (3, 10) else 1)
PY
  then
    echo "发现旧虚拟环境 Python 版本过低，正在重建 .venv..."
    rm -rf .venv
  fi
fi
if [ ! -d ".venv" ]; then
  "$PYTHON" -m venv .venv
fi
VENV_PYTHON="$ROOT/.venv/bin/python"

echo
echo "[3/5] 准备本地配置..."
if [ ! -f ".env" ]; then
  cat > .env <<EOF
APP_PASSWORD=change-this-password
HOST=127.0.0.1
PORT=8787
TASK_ROOT=$ROOT/data/tasks
DATABASE_PATH=$ROOT/data/app.db
SKILL_DIR=$ROOT/skills/harmful_sample
PYTHON_BIN=$VENV_PYTHON
NODE_BIN=node
STORAGE_STATE_PATH=$ROOT/data/storage_state.json
CAPTURE_ENGINE=playwright
CAPTURE_PROFILE=desktop
DRISSION_DOMAINS=xiaohongshu.com,weibo.com
CAPTCHA_PAUSE_THRESHOLD=1
CAPTURE_HEADLESS=true
CAPTURE_FULL_PAGE=true
CHROMIUM_EXECUTABLE_PATH=
TASK_TIMEOUT_SECONDS=7200
SESSION_SECRET=harmful-sample-web-local-secret
EOF
  echo "已创建 .env，默认后台密码: change-this-password"
else
  echo ".env 已存在，沿用当前配置"
fi

echo
echo "[4/5] 安装 Python 依赖..."
"$VENV_PYTHON" -m pip install --upgrade pip
"$VENV_PYTHON" -m pip install -r requirements.txt

echo
echo "[5/5] 准备浏览器依赖..."
if [ -f "package.json" ] && command -v npm >/dev/null 2>&1; then
  export npm_config_cache="$ROOT/.npm-cache"
  mkdir -p "$npm_config_cache"
  if [ ! -d "node_modules" ]; then
    if ! npm install; then
      echo "npm install 失败，暂时跳过浏览器依赖安装。后台仍会继续启动。"
    fi
  fi
  if [ -d "node_modules" ]; then
    if ! npx playwright install chromium; then
      echo "Playwright 浏览器安装失败，暂时跳过。后台仍会继续启动，截图功能之后可能需要再处理。"
    fi
  fi
else
  echo "未找到 npm，跳过 Node/Playwright 浏览器安装。截图功能可能需要手动安装。"
fi

PORT="8787"
if [ -f ".env" ]; then
  ENV_PORT="$(grep -E '^[[:space:]]*PORT[[:space:]]*=' .env | tail -1 | cut -d= -f2- | tr -d '\"'"'"'[:space:]')"
  if [ -n "$ENV_PORT" ]; then
    PORT="$ENV_PORT"
  fi
fi

URL="http://127.0.0.1:$PORT"
LOG_DIR="$ROOT/data"
LOG_FILE="$LOG_DIR/server.log"
PID_FILE="$LOG_DIR/server.pid"
mkdir -p "$LOG_DIR"
APP_PASSWORD_HINT="change-this-password"
if [ -f ".env" ]; then
  ENV_APP_PASSWORD="$(grep -E '^[[:space:]]*APP_PASSWORD[[:space:]]*=' .env | tail -1 | cut -d= -f2- | sed "s/^[[:space:]]*//; s/[[:space:]]*$//; s/^['\\\"]//; s/['\\\"]$//")"
  if [ -n "$ENV_APP_PASSWORD" ]; then
    APP_PASSWORD_HINT="$ENV_APP_PASSWORD"
  fi
fi

echo
echo "检查旧后台进程..."
OLD_LISTENERS="$(lsof -tiTCP:"$PORT" -sTCP:LISTEN 2>/dev/null || true)"
if [ -n "$OLD_LISTENERS" ]; then
  echo "发现旧后台进程，正在重启以读取最新配置..."
  echo "$OLD_LISTENERS" | xargs kill >/dev/null 2>&1 || true
  sleep 1
fi
rm -f "$PID_FILE"

echo
echo "================================"
echo "后台即将启动: $URL"
echo "当前密码: $APP_PASSWORD_HINT"
echo "保持这个终端窗口打开，关闭窗口会停止后台。"
echo "日志文件: $LOG_FILE"
echo "================================"
echo

"$VENV_PYTHON" -m uvicorn app.main:app --host 127.0.0.1 --port "$PORT" > "$LOG_FILE" 2>&1 &
SERVER_PID=$!
echo "$SERVER_PID" > "$PID_FILE"

stop_server() {
  if kill -0 "$SERVER_PID" >/dev/null 2>&1; then
    kill "$SERVER_PID" >/dev/null 2>&1 || true
  fi
}
trap stop_server INT TERM

echo "等待后台服务启动..."
for _ in $(seq 1 30); do
  if curl -fsS "$URL/login" >/dev/null 2>&1; then
    echo "后台已启动，正在打开浏览器..."
    open "$URL" >/dev/null 2>&1 || true
    echo
    echo "后台正在运行，进程号: $SERVER_PID"
    echo "请保持这个终端窗口打开。"
    echo "如需停止后台，在这个窗口按 Ctrl+C。"
    echo
    tail -f "$LOG_FILE" &
    TAIL_PID=$!
    wait "$SERVER_PID"
    SERVER_EXIT=$?
    kill "$TAIL_PID" >/dev/null 2>&1 || true
    exit "$SERVER_EXIT"
  fi
  if ! kill -0 "$SERVER_PID" >/dev/null 2>&1; then
    echo "后台服务启动失败，请查看日志: $LOG_FILE"
    exit 1
  fi
  sleep 1
done

echo "后台启动超时，请查看日志: $LOG_FILE"
kill "$SERVER_PID" >/dev/null 2>&1 || true
exit 1
