#!/bin/zsh
# run.sh —— 一条命令：Excel → 清单 → 截图 → Word → 结构自检
#
#   zsh run.sh <xlsx> <outdir> [--day 2 --rows 400] [--offset 0] [--limit 400] [--date 11月10日]
#               [--start-num 197] [--batch-index 5]
#               [--space 有害样本] [--batch 50] [--sheets 搜索结果,待复核] [--no-probe]
#
# 例（每天扫 400 行）：
#   # 第 1 天：前 400 条
#   zsh run.sh 监测.xlsx ./day1 --day 1 --rows 400 --date 11月10日
#   # 第 2 天：第 401-800 条，编号接着昨天最后一条
#   zsh run.sh 监测.xlsx ./day2 --day 2 --rows 400 --start-num 197 --batch-index 5 --date 11月11日
#
# 产出：<outdir>/<日期>-<平台>-第N批.docx，每份 ≤50 条
# 依赖：ego lite（~/.local/bin/ego-browser）、python 的 openpyxl / python-docx
set -u

if [ $# -lt 2 ]; then
  sed -n '2,16p' "$0" | sed 's/^# \{0,1\}//'
  exit 1
fi

XLSX="$1"; OUTDIR="$2"; shift 2
OFFSET=0; LIMIT=400; DAY=0; ROWS=400; START_NUM=1; BATCH_INDEX=1
NAME_DATE=""; SPACE="有害样本"; BATCH=50; SHEETS="搜索结果,待复核"; PROBE=0
while [ $# -gt 0 ]; do
  case "$1" in
    --offset) OFFSET="$2"; shift 2 ;;
    --limit) LIMIT="$2"; shift 2 ;;
    --day) DAY="$2"; shift 2 ;;
    --rows) ROWS="$2"; shift 2 ;;
    --start-num) START_NUM="$2"; shift 2 ;;
    --batch-index) BATCH_INDEX="$2"; shift 2 ;;
    --date) NAME_DATE="$2"; shift 2 ;;
    --space) SPACE="$2"; shift 2 ;;
    --batch) BATCH="$2"; shift 2 ;;
    --sheets) SHEETS="$2"; shift 2 ;;
    --probe) PROBE=1; shift ;;
    *) echo "未知参数: $1" >&2; exit 1 ;;
  esac
done

# 日更切段：--day 2 --rows 400 → 跳过前 400、跑 401-800
if [ "$DAY" -gt 0 ]; then
  OFFSET=$(( (DAY - 1) * ROWS ))
  LIMIT="$ROWS"
fi

PY="${PY:-$HOME/.workbuddy/binaries/python/envs/default/bin/python}"
command -v "$PY" >/dev/null 2>&1 || PY=python3
MP="$(cd "$(dirname "$0")" && pwd)"
EGO_BROWSER="${EGO_BROWSER:-$HOME/.local/bin/ego-browser}"

# 绝对路径：ego-browser 的 nodejs 进程 cwd 不同，相对路径会 ENOENT
XLSX="$(cd "$(dirname "$XLSX")" && pwd)/$(basename "$XLSX")"
mkdir -p "$OUTDIR" && OUTDIR="$(cd "$OUTDIR" && pwd)"
WORK="$OUTDIR/_work"; SHOTS="$OUTDIR/screenshots"; RESULTS="$WORK/_ego"
mkdir -p "$WORK" "$SHOTS" "$RESULTS"

echo "=== 1/5 清单（第 $((OFFSET+1))-$((OFFSET+LIMIT)) 条，编号 $START_NUM 起）==="
"$PY" "$MP/prep_manifest.py" "$XLSX" "$WORK" --sheets "$SHEETS" \
  --offset "$OFFSET" --limit "$LIMIT" --out manifest.json || exit 1

echo "=== 2/5 抓取 ==="
"$PY" - "$WORK/manifest.json" "$SHOTS" "$RESULTS" "$WORK/cfg.json" "$SPACE" <<'PYEOF'
import json, sys
man, shots, results, out, space = sys.argv[1:6]
cfg = {
    "manifest": man, "shots_dir": shots, "results_dir": results, "space": space,
    "limit": 0, "tag": "full", "fresh": True, "recycle": 12, "max_attempts": 2,
    "extra_settle": 900, "zoom": "1.9", "max_scrolls": 14,
    "platform_settle": {"douyin": 3500, "xhs": 3200, "weibo": 2200, "bilibili": 2200,
                        "tieba": 1600, "weixin": 2200, "toutiao": 1800, "other": 1600},
}
open(out, "w").write(json.dumps(cfg, ensure_ascii=False, indent=1))
print("cfg:", out)
PYEOF

# Ego Lite 的控制权一旦被用户接管，脚本会被硬停（listTabs: Control of this task space is with the user）
# 这里包一层：失败就 takeOverTaskSpace 恢复再续跑，最多 6 轮。断点续跑靠 results jsonl，已完成的不会重抓。
cat > "$WORK/takeover.js" <<'JSEOF'
try { await takeOverTaskSpace(process.env.CAPTURE_SPACE || "有害样本"); console.log("TAKEOVER_OK"); }
catch (e) { console.log("TAKEOVER_FAIL " + String(e.message).slice(0, 160)); }
JSEOF

for round in 1 2 3 4 5 6; do
  echo "--- 抓取轮次 $round ---"
  CONFIG="$WORK/cfg.json" CHUNK=15 SCRIPT_DIR="$MP" SPACE="$SPACE" \
    zsh "$MP/run_capture_multi.sh"
  code=$?
  [ "$code" -eq 0 ] && break
  echo "本轮中断（exit=$code），尝试恢复浏览器控制权后继续"
  CAPTURE_SPACE="$SPACE" "$EGO_BROWSER" nodejs < "$WORK/takeover.js" || true
  sleep 6
done

echo "=== 3/5 复核分流（区分「已删」和「没抓到」）==="
"$PY" "$MP/triage.py" --manifest "$WORK/manifest.json" --results "$RESULTS" \
  --shots "$SHOTS" --json "$WORK/triage.json" 2>/dev/null \
  || echo "（triage 缺失，跳过）"

TRIAGE="$WORK/triage.json"
echo "=== 4/5 出稿 ==="
# 编号按「实际入册条数」连续，不按行号：已删除/排除的条目会被抽掉，编号自然前移。
# 跨天接续用 --start-num（昨天最后一条 +1）、--batch-index（昨天末批 +1）。
"$PY" "$MP/build_docx.py" --manifest "$WORK/manifest.json" --shots "$SHOTS" \
  --results "$RESULTS" --outdir "$OUTDIR" --batch "$BATCH" \
  --start-num "$START_NUM" --batch-index "$BATCH_INDEX" \
  ${NAME_DATE:+--name-date $NAME_DATE} \
  ${TRIAGE:+--triage $TRIAGE} || exit 1

echo "=== 5/5 结构自检 ==="
"$PY" "$MP/verify_docx.py" "$OUTDIR" || exit 2
echo "完成：$OUTDIR"
