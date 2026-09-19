#!/bin/zsh
# run_capture.sh —— 分片驱动 Ego Lite 抖音截图抓取（可断点续跑）
#
# 背景（踩坑结论）：渲染进程在同一次会话里跑约 15 条后会明显劣化
# （单条从 2 秒涨到 60–300 秒）。所以必须把抓取切成小片，每片重建一次标签页
# （capture_douyin.js 里 fresh:true），每片只抓 10–12 条。
# 续跑靠 capture 自身的 ok 跳过逻辑（已 ok 的 item_id 不再抓）。
#
# 用法：
#   ./run_capture.sh
#   CHUNK=12 CONFIG=/path/capture_config.json EGO_BROWSER=$HOME/.local/bin/ego-browser ./run_capture.sh
#
# 配置（capture_config.json）里至少要给：manifest / shots_dir / results_dir / space_id。
# 其余（tag/limit/fresh）由本脚本每片注入：limit=CHUNK、fresh=true、tag=每片自增。

set -u

SCRIPT_DIR="${SCRIPT_DIR:-$(cd "$(dirname "$0")" && pwd)}"
EGO_BROWSER="${EGO_BROWSER:-$HOME/.local/bin/ego-browser}"
CONFIG="${CONFIG:-$SCRIPT_DIR/capture_config.json}"
CHUNK="${CHUNK:-12}"          # 每片条数（10–12 最佳）
MAX_ATTEMPTS="${MAX_ATTEMPTS:-80}"   # 安全上限，防止持久失败死循环

if [ ! -x "$EGO_BROWSER" ] && ! command -v "$EGO_BROWSER" >/dev/null 2>&1; then
  echo "ERROR: ego-browser 不存在: $EGO_BROWSER" >&2
  exit 1
fi
if [ ! -f "$CONFIG" ]; then
  echo "ERROR: 配置不存在: $CONFIG（可用 CONFIG 环境变量指向，或在同目录放 capture_config.json）" >&2
  exit 1
fi

run_one() {
  local tag="$1"
  # 每片：只抓 CHUNK 条、强制 fresh 重建标签页、结果写入 results_<tag>.jsonl
  CAPTURE_CONFIG="$CONFIG" \
  CAPTURE_LIMIT="$CHUNK" \
  CAPTURE_TAG="$tag" \
  CAPTURE_FRESH=true \
  "$EGO_BROWSER" nodejs < "$SCRIPT_DIR/capture_douyin.js"
}

prev_pending=""
attempt=0
while true; do
  out="$(run_one "c${attempt}" 2>&1)"
  if echo "$out" | grep -q 'ALL DONE'; then
    echo "=== ALL DONE ==="
    break
  fi
  pending="$(echo "$out" | grep -oE 'pending [0-9]+' | head -1 | awk '{print $2}')"
  if [ -z "$pending" ]; then
    echo "ERROR: 抓取样片未输出 pending 计数，可能 ego-browser 启动失败。输出前 20 行：" >&2
    echo "$out" | head -20 >&2
    exit 2
  fi
  echo "--- 片 c${attempt}: pending=${pending} ---"
  # 无进展（本片 0 条成功）说明会话被限流/卡死，停止以免死循环
  if [ "$pending" = "$prev_pending" ]; then
    echo "ERROR: 连续两片 pending 无变化(${pending})，疑似持久失败/限流，停止。请检查登录态或冷却后重跑。" >&2
    exit 3
  fi
  prev_pending="$pending"
  attempt=$((attempt + 1))
  if [ "$attempt" -ge "$MAX_ATTEMPTS" ]; then
    echo "ERROR: 已达 MAX_ATTEMPTS=${MAX_ATTEMPTS}，停止。可重跑续跑剩余。" >&2
    exit 4
  fi
done

echo "抓取完成。下一步：build_manifest.py -> quality_check_screenshots.py -> excel_to_word.py"
