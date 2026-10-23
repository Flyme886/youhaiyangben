#!/bin/zsh
# run_capture_multi.sh —— 分片驱动 Ego Lite 多平台截图抓取（可断点续跑）
set -u

SCRIPT_DIR="${SCRIPT_DIR:-$(cd "$(dirname "$0")" && pwd)}"
EGO_BROWSER="${EGO_BROWSER:-$HOME/.local/bin/ego-browser}"
CONFIG="${CONFIG:?需要 CONFIG=<capture_config.json 路径>}"
CHUNK="${CHUNK:-10}"
MAX_ATTEMPTS="${MAX_ATTEMPTS:-60}"
COOLDOWN="${COOLDOWN:-120}"
MAX_COOLDOWNS="${MAX_COOLDOWNS:-3}"

if [ ! -x "$EGO_BROWSER" ] && ! command -v "$EGO_BROWSER" >/dev/null 2>&1; then
  echo "ERROR: ego-browser 不存在: $EGO_BROWSER" >&2
  exit 1
fi
[ -f "$CONFIG" ] || { echo "ERROR: 配置不存在: $CONFIG" >&2; exit 1; }

_json_str() { printf '"%s"' "$(printf '%s' "$1" | sed 's/\\/\\\\/g; s/"/\\"/g')"; }

RAWLOG_DIR="${RAWLOG_DIR:-$(dirname "$CONFIG")/chunklogs}"
mkdir -p "$RAWLOG_DIR"

run_one() {
  local tag="$1"
  local raw="$RAWLOG_DIR/chunk_${tag}.log"
  local extra=""
  [ -n "${SPACE:-}" ] && extra=", SPACE:$(_json_str "$SPACE")"
  {
    printf 'globalThis.__CAPTURE_ENV = {CONFIG:%s, LIMIT:%s, TAG:%s, FRESH:"true"%s};\n' \
      "$(_json_str "$CONFIG")" "$(_json_str "$CHUNK")" "$(_json_str "$tag")" "$extra"
    cat "$SCRIPT_DIR/capture_multi.js"
  } | "$EGO_BROWSER" nodejs > "$raw" 2>&1
  cat "$raw"
}

prev_remaining=""
attempt=0
retried=0
cooldowns=0
while true; do
  out="$(run_one "c${attempt}" 2>&1)"
  if echo "$out" | grep -q 'ALL DONE'; then
    echo "=== ALL DONE ==="
    break
  fi
  remaining="$(echo "$out" | grep -oE 'remaining [0-9]+' | head -1 | awk '{print $2}')"
  if [ -z "$remaining" ]; then
    if [ "$retried" -eq 0 ]; then
      retried=1
      echo "WARN: 片 c${attempt} 无 remaining，原样重试一次（$RAWLOG_DIR/chunk_c${attempt}.log）"
      echo "$out" | tail -5
      continue
    fi
    echo "ERROR: 片 c${attempt} 连续两次无输出，停止。前 20 行：" >&2
    echo "$out" | head -20 >&2
    exit 2
  fi
  retried=0
  echo "--- 片 c${attempt}: remaining=${remaining} ---"
  if [ "$remaining" = "$prev_remaining" ]; then
    if [ "$cooldowns" -lt "$MAX_COOLDOWNS" ]; then
      cooldowns=$((cooldowns + 1))
      echo "WARN: 无进展（remaining=${remaining}），疑似限流，冷却 ${COOLDOWN}s 后重试（${cooldowns}/${MAX_COOLDOWNS}）"
      sleep "$COOLDOWN"
      prev_remaining=""
      continue
    fi
    echo "ERROR: 连续无进展且冷却 ${MAX_COOLDOWNS} 次仍不动，停止。" >&2
    exit 3
  fi
  cooldowns=0
  prev_remaining="$remaining"
  attempt=$((attempt + 1))
  [ "$attempt" -ge "$MAX_ATTEMPTS" ] && { echo "ERROR: 达 MAX_ATTEMPTS" >&2; exit 4; }
done

echo "抓取完成。"
