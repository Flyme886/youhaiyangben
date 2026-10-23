#!/bin/zsh
# run.sh —— 一份监测 Excel 跑到 Word，一条命令。
#
#   zsh run.sh <xlsx> <outdir> [--prefix 名字] [--space 空间名] [--batch 50] [--no-probe]
#
# 顺序：清单 → 探测（每平台 1 条）→ 全量抓取 → 出 Word → 结构自检
# 探测这一步是为了在铺开跑之前就知道哪个平台打不开，而不是跑完 70 页才发现。
set -u

XLSX="${1:?用法: run.sh <xlsx> <outdir> [--prefix 名] [--space 名] [--batch 50] [--no-probe]}"
OUTDIR="${2:?缺少输出目录}"
shift 2

PREFIX="第"
SPACE="有害样本"
BATCH=50
PROBE=1
while [ $# -gt 0 ]; do
  case "$1" in
    --prefix) PREFIX="$2"; shift 2 ;;
    --space) SPACE="$2"; shift 2 ;;
    --batch) BATCH="$2"; shift 2 ;;
    --no-probe) PROBE=0; shift ;;
    *) echo "未知参数: $1" >&2; exit 1 ;;
  esac
done

PY="${PY:-$HOME/.workbuddy/binaries/python/envs/default/bin/python}"
MP="${MP:-$(cd "$(dirname "$0")" && pwd)}"
WORK="$OUTDIR/_work"
SHOTS="$OUTDIR/screenshots"
mkdir -p "$WORK" "$SHOTS" || exit 1

echo "=== 1/5 清单 ==="
"$PY" "$MP/make_manifest_generic.py" "$XLSX" "$WORK/manifest.json" || exit 1

if [ "$PROBE" -eq 1 ]; then
  echo "=== 2/5 探测（每平台 1 条）==="
  "$PY" "$MP/mkconfig.py" --manifest "$WORK/manifest.json" --shots "$SHOTS" \
    --results "$WORK/_ego" --space "$SPACE" --out "$WORK/probe_cfg.json" --probe || exit 1
  CONFIG="$WORK/probe_cfg.json" CHUNK=20 MAX_COOLDOWNS=1 COOLDOWN=60 \
    zsh "$MP/run_capture_multi.sh" > "$WORK/probe.log" 2>&1
  tail -3 "$WORK/probe.log"
  "$PY" "$MP/summary.py" --manifest "$WORK/manifest.json" --results "$WORK/_ego" --label "探测"
else
  echo "=== 2/5 跳过探测 ==="
fi

echo "=== 3/5 全量抓取 ==="
"$PY" "$MP/mkconfig.py" --manifest "$WORK/manifest.json" --shots "$SHOTS" \
  --results "$WORK/_ego" --space "$SPACE" --out "$WORK/cfg.json" || exit 1
CONFIG="$WORK/cfg.json" CHUNK=10 MAX_COOLDOWNS=2 COOLDOWN=120 \
  zsh "$MP/run_capture_multi.sh" > "$WORK/capture.log" 2>&1
tail -3 "$WORK/capture.log"
"$PY" "$MP/summary.py" --manifest "$WORK/manifest.json" --results "$WORK/_ego" --label "抓取"

echo "=== 4/5 生成 Word ==="
"$PY" "$MP/build_docx.py" --manifest "$WORK/manifest.json" --shots "$SHOTS" \
  --results "$WORK/_ego" --outdir "$OUTDIR" --prefix "$PREFIX" --batch "$BATCH" || exit 1

echo "=== 5/5 结构自检 ==="
"$PY" "$MP/verify_docx.py" "$OUTDIR"
