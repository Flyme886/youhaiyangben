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
COOLDOWN="${COOLDOWN:-240}"   # 疑似被限流（remaining 不降）时冷却多少秒再试
MAX_COOLDOWNS="${MAX_COOLDOWNS:-4}"  # 最多冷却重试几次，仍不行才放弃

if [ ! -x "$EGO_BROWSER" ] && ! command -v "$EGO_BROWSER" >/dev/null 2>&1; then
  echo "ERROR: ego-browser 不存在: $EGO_BROWSER" >&2
  exit 1
fi
if [ ! -f "$CONFIG" ]; then
  echo "ERROR: 配置不存在: $CONFIG（可用 CONFIG 环境变量指向，或在同目录放 capture_config.json）" >&2
  exit 1
fi

# 把字符串安全地包成 JSON 字符串字面量（转义 \ 和 "）
_json_str() {
  printf '"%s"' "$(printf '%s' "$1" | sed 's/\\/\\\\/g; s/"/\\"/g')"
}

# 每片的原始输出留档，便于事后诊断「整片空输出」这类偶发失败
RAWLOG_DIR="${RAWLOG_DIR:-$(dirname "$CONFIG")/chunklogs}"
mkdir -p "$RAWLOG_DIR"

run_one() {
  local tag="$1"
  local raw="$RAWLOG_DIR/chunk_${tag}.log"
  local extra=""
  [ -n "${SPACE_ID:-}" ] && extra=", SPACE:$(_json_str "$SPACE_ID")"
  # 每片：只抓 CHUNK 条、强制 fresh 重建标签页、结果写入 results_<tag>.jsonl
  #
  # ⚠️ ego 的 node 运行时在 app 侧执行脚本：调用方设置的环境变量**不会透传**，
  #    cwd 恒为 "/"。因此 `CAPTURE_CONFIG=… ego-browser nodejs < script` 是无效写法。
  #    正确做法是在脚本前面拼一段 prelude，把配置写进 globalThis.__CAPTURE_ENV。
  {
    printf 'globalThis.__CAPTURE_ENV = {CONFIG:%s, LIMIT:%s, TAG:%s, FRESH:"true"%s};\n' \
      "$(_json_str "$CONFIG")" "$(_json_str "$CHUNK")" "$(_json_str "$tag")" "$extra"
    cat "$SCRIPT_DIR/capture_douyin.js"
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
  # 进度判据用 remaining（真正剩余条数）—— 不能用 pending：
  # pending 会被 limit 截断，每片都恒等于 CHUNK，拿它比较会误判「无进展」而提前退出。
  remaining="$(echo "$out" | grep -oE 'remaining [0-9]+' | head -1 | awk '{print $2}')"
  if [ -z "$remaining" ]; then
    # ego 偶发整片无输出（含进程被外部杀掉的情况）。先原样重试一次同片再判定失败。
    if [ "$retried" -eq 0 ]; then
      retried=1
      echo "WARN: 片 c${attempt} 未输出 remaining，疑似 ego 偶发失败，原样重试一次（原始输出：$RAWLOG_DIR/chunk_c${attempt}.log）"
      echo "$out" | tail -5
      continue
    fi
    echo "ERROR: 片 c${attempt} 连续两次无输出，停止。原始输出前 20 行：" >&2
    echo "$out" | head -20 >&2
    exit 2
  fi
  retried=0
  echo "--- 片 c${attempt}: remaining=${remaining} ---"
  # 无进展（剩余条数没减少）多为 IP 被限流（日志里是 net::ERR_CONNECTION_CLOSED/RESET）。
  # 限流是短时的：冷却后原样重试同一片即可，不要直接放弃。
  if [ "$remaining" = "$prev_remaining" ]; then
    if [ "$cooldowns" -lt "$MAX_COOLDOWNS" ]; then
      cooldowns=$((cooldowns + 1))
      echo "WARN: 片 c${attempt} 无进展（remaining=${remaining}），疑似限流，冷却 ${COOLDOWN}s 后重试同片（${cooldowns}/${MAX_COOLDOWNS}）"
      sleep "$COOLDOWN"
      prev_remaining=""
      continue
    fi
    echo "ERROR: 连续两片 remaining 无变化(${remaining})，且已冷却 ${MAX_COOLDOWNS} 次仍无进展，停止。请检查登录态或稍后重跑。" >&2
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
