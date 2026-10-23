#!/bin/zsh
# run_capture_comments.sh —— 分片驱动「评论截图」抓取（可断点续跑）
#
# 与 run_capture.sh 的差别（重要）：
#   1. 执行 capture_douyin_comments.js（每条样本产出【多张】截图：评论区前 N 条）
#   2. 单条更重：要展开评论面板 + 滚动加载 + 多屏裁剪，实测 15–30 秒/条
#      → CHUNK 默认降到 6（评论抓取比纯视频页更容易触发风控）
#   3. 每片之间强制 fresh 重建标签页，续跑靠 ok 跳过逻辑
#
# 用法：
#   CHUNK=6 CONFIG=/path/capture_config.json EGO_BROWSER=$HOME/.local/bin/ego-browser ./run_capture_comments.sh
#
# 配置（capture_config.json）至少给：manifest / shots_dir / results_dir / space_id，
# 可选 comments 段（max_per_sample / max_scan / dwell / load_rounds …）。
# 其余由本脚本每片注入：limit=CHUNK、fresh=true、tag=每片自增。

set -u

SCRIPT_DIR="${SCRIPT_DIR:-$(cd "$(dirname "$0")" && pwd)}"
EGO_BROWSER="${EGO_BROWSER:-$HOME/.local/bin/ego-browser}"
CONFIG="${CONFIG:-$SCRIPT_DIR/capture_config.json}"
CHUNK="${CHUNK:-6}"           # 每片条数（评论抓取重，6 最佳）
MAX_ATTEMPTS="${MAX_ATTEMPTS:-120}"
COOLDOWN="${COOLDOWN:-300}"   # 疑似限流时冷却秒数
MAX_COOLDOWNS="${MAX_COOLDOWNS:-5}"

if [ ! -x "$EGO_BROWSER" ] && ! command -v "$EGO_BROWSER" >/dev/null 2>&1; then
  echo "ERROR: ego-browser 不存在: $EGO_BROWSER" >&2
  exit 1
fi
if [ ! -f "$CONFIG" ]; then
  echo "ERROR: 配置不存在: $CONFIG" >&2
  exit 1
fi

_json_str() {
  printf '"%s"' "$(printf '%s' "$1" | sed 's/\\/\\\\/g; s/"/\\"/g')"
}

RAWLOG_DIR="${RAWLOG_DIR:-$(dirname "$CONFIG")/chunklogs}"
mkdir -p "$RAWLOG_DIR"

run_one() {
  local tag="$1"
  local raw="$RAWLOG_DIR/chunk_${tag}.log"
  local extra=""
  [ -n "${SPACE_ID:-}" ] && extra=", SPACE:$(_json_str "$SPACE_ID")"
  # ⚠️ ego 的 node 在 app 侧执行：调用方环境变量不透传，cwd 恒为 "/"。
  #    必须拼 prelude 把配置写进 globalThis.__CAPTURE_ENV。
  {
    printf 'globalThis.__CAPTURE_ENV = {CONFIG:%s, LIMIT:%s, TAG:%s, FRESH:"true"%s};\n' \
      "$(_json_str "$CONFIG")" "$(_json_str "$CHUNK")" "$(_json_str "$tag")" "$extra"
    cat "$SCRIPT_DIR/capture_douyin_comments.js"
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
      echo "WARN: 片 c${attempt} 未输出 remaining，原样重试一次（原始输出：$RAWLOG_DIR/chunk_c${attempt}.log）"
      echo "$out" | tail -5
      continue
    fi
    echo "ERROR: 片 c${attempt} 连续两次无输出，停止。原始输出前 20 行：" >&2
    echo "$out" | head -20 >&2
    exit 2
  fi
  retried=0
  echo "--- 片 c${attempt}: remaining=${remaining} ---"
  if [ "$remaining" = "$prev_remaining" ]; then
    if [ "$cooldowns" -lt "$MAX_COOLDOWNS" ]; then
      cooldowns=$((cooldowns + 1))
      echo "WARN: 片 c${attempt} 无进展（remaining=${remaining}），疑似限流，冷却 ${COOLDOWN}s 后重试同片（${cooldowns}/${MAX_COOLDOWNS}）"
      sleep "$COOLDOWN"
      prev_remaining=""
      continue
    fi
    echo "ERROR: 连续两片 remaining 无变化(${remaining})，且已冷却 ${MAX_COOLDOWNS} 次仍无进展，停止。" >&2
    exit 3
  fi
  cooldowns=0
  prev_remaining="$remaining"
  attempt=$((attempt + 1))
  if [ "$attempt" -ge "$MAX_ATTEMPTS" ]; then
    echo "ERROR: 已达 MAX_ATTEMPTS=${MAX_ATTEMPTS}，停止。可重跑续跑剩余。" >&2
    exit 4
  fi
done

echo "抓取完成。下一步：build_manifest.py -> quality_check_screenshots.py -> excel_to_word.py"
