# 端到端管线与参数手册（douyin-harmful-sample）

按本顺序跑。命令里的占位符：`<XLSX>` 原表、`$OUTDIR` 输出目录（如 `输出Word`）、
`$ENG` 克隆仓库的 `skills/harmful_sample`、`$SHEETS` 本 skill 的 `scripts/`。
环境：Python = `~/.workbuddy/binaries/python/envs/default/bin/python`；
Ego Lite = `~/.local/bin/ego-browser`；macOS 无 `timeout`。

---

## ⓪ 环境准备：ego lite（浏览器自动调研统一用它）

`ego-browser` 不在 PATH 就自己装，**不要因为缺浏览器就停下**：

```bash
command -v ego-browser || export PATH="$HOME/.local/bin:$PATH"; command -v ego-browser
# 缺失时：官方安装脚本（自动识别架构 + 下载 DMG + 装到 /Applications + 去 quarantine + 启动）
sh ~/.workbuddy/skills/ego-browser/scripts/install.sh
```

手动直链（脚本失败时用）：

| 架构 | 直链 |
|---|---|
| Apple Silicon (arm64) | `https://cdn.ego.app/setup/macos/arm64/egolite.dmg` |
| Intel (x64) | `https://cdn.ego.app/setup/macos/x64/egolite.dmg` |

官网：<https://lite.ego.app/>（非 macOS 走这里）。

- **唯一人工步骤**：首次 onboarding 是 GUI 操作，需用户在 app 内完成（会把 `ego-browser`
  注册到 PATH）。等用户确认后再继续。
- 冒烟验证：`ego-browser nodejs <<'EOF'` + `console.log('ego-browser ready')` + `EOF`。
- 抖音抓取前还需**用户在 ego lite 里已登录抖音**（扫码）。

> 选型理由：ego lite 是真实浏览器 + 持久化 profile，登录态可复用、风控表现远好于无头方案。
> **凡浏览器自动调研/抓取/截图，一律优先 ego lite**，不要临时改 Playwright / 无头 Chromium。

## ① 克隆仓库（私有，靠本机 git 凭据）

```bash
git clone https://github.com/Flyme886/youhaiyangben.git engine && rm -rf engine/.git
```
WebFetch 该私有仓库会 404，只能先 clone 到本地再读 `engine/skills/harmful_sample/`。

## ② 打补丁（幂等，只需一次）

```bash
$PY "$SHEETS/apply_adaptations.py" "$ENG/scripts/excel_to_word.py"
```
- 用法：`python apply_adaptations.py <fresh excel_to_word.py>`，或环境变量 `APPLY_TARGET`。
- 注入 5 类适配（列名别名 / 编号区间 / `--title` 编号范围行 / 元信息加编号 / 去防御性表述）；脚本内部拆成 15 个注入块。
- 已含全部适配则打印「已包含所有适配，未做任何改动」并退出；可重复执行。

## ③ 取样 + 列映射/区间对账（`--dry-run`）

```bash
$PY "$ENG/scripts/excel_to_word.py" "$XLSX" \
  --output-dir "$OUTDIR" --sheet 信息 --id-min 400 --id-max 900 --dry-run
```
**关键参数**
- `--sheet`：工作表名（本案 `信息`；仓库默认 `搜索结果`）。
- `--id-min` / `--id-max`：按 `编号` 列（`ID_COLUMN_ALIASES=["编号","id","ID"]`）取区间。
- `--dry-run`：只校验表头+写 `links_manifest.json`，不生成 Word。

**必须对账的 `meta` 字段**（打印在 stdout，也在 `完成标记.json`）
- `in_range_rows` / `out_of_range_rows`：区间内 / 区间外行数。本案 `501` / `7832`。
- `duplicate_rows`：按链接去重的重复行数。本案 `0`（501 条 URL 全唯一）。
- `column_alias_map`：实际列名 → 规范列名命中情况（`标题/微博内容←标题`、`原文/评论链接←URL`、`原文作者←作者`）。若缺必需列会 `SystemExit`。
- `unique_records`：去重后实际记录数，应 == `in_range_rows - duplicate_rows - 缺链接行`。

## ④ 截图（Ego Lite）

先 `cp "$SHEETS/capture_config.example.json" "$SHEETS/capture_config.json"` 并把 `manifest` /
`shots_dir` / `results_dir` 改成绝对路径（字段见末尾），再用 `CONFIG` 指向：
```bash
CHUNK=12 CONFIG="$SHEETS/capture_config.json" EGO_BROWSER="$EGO" "$SHEETS/run_capture.sh"
```
**关键参数**（环境变量覆盖）
- `CHUNK`：每片条数，默认 `12`（10–12 最佳）；`MAX_ATTEMPTS` 安全上限默认 `80`。
- `run_capture.sh` 每片注入 `CAPTURE_LIMIT=$CHUNK`、`CAPTURE_FRESH=true`、`CAPTURE_TAG=cN`，靠 `capture_douyin.js` 的 ok 跳过逻辑断点续跑。
- `capture_config.json` 必填：`manifest` / `shots_dir` / `results_dir`；`space_id` 默认 `3`。
- 被限流/卡死：`run_capture.sh` 检测到连续两片 pending 无变化会停止，冷却后重跑即可接着抓。

## ⑤ 合并抓取结果

```bash
$PY "$SHEETS/build_manifest.py" \
  --links "$OUTDIR/links_manifest.json" \
  --results-dir "$OUTDIR/../_ego" \
  --shots-dir "$OUTDIR/screenshots" \
  --output "$OUTDIR/screenshots/screenshots_manifest.json"
```
**参数**：`--links` 顺序基准；`--results-dir` 放 `results*.jsonl` 的目录；`--shots-dir` 截图目录；`--output` 输出。同 item_id 优先保留 `status==ok`，截图文件缺失降级为 `missing_file`。输出 `status_counts` 与 `ok=N/M`。

## ⑥ 截图质检（仓库脚本）

```bash
$PY "$ENG/scripts/quality_check_screenshots.py" "$OUTDIR/screenshots/screenshots_manifest.json"
```
- 输入上一步的 `screenshots_manifest.json`，分类 `usable`/`needs_review`/`unusable`，输出 `screenshot_quality_manifest.json`（下一步用）。本案 501/501 `usable`、0 `截图待补`。

## ⑦ 生成 Word（`--screenshots-manifest`）

```bash
$PY "$ENG/scripts/excel_to_word.py" "$XLSX" \
  --output-dir "$OUTDIR" --sheet 信息 --id-min 400 --id-max 900 \
  --title "编号400-900" --batch-size 50 \
  --screenshots-manifest "$OUTDIR/screenshots/screenshot_quality_manifest.json"
```
**关键参数**
- `--title`：批次标题，并自动加「本批编号范围：X — Y 共 N 条」行。
- `--batch-size`：每份条数，默认 `50`（本案 11 份 ×50，末批 1 条，全局连续 1–501）。
- `--screenshots-manifest`：**必须指向上一步质检后的 `screenshot_quality_manifest.json`**，不是 ⑤ 的 manifest。
- 未配 `DEEPSEEK_API_KEY` 时回退关键词规则（大量落到兜底文案，先与用户确认口径，见 SKILL.md）。
- 判定理由**不含任何防御性表述**：仓库原会给所有视频平台链接追加「视频画面及完整语音需人工核验。」，
  已被 `apply_adaptations.py` 的适配 5 删除注入点 + `strip_defensive()` 兜底清洗（见下节 ⑧ 校验）。

## ⑧ 结构校验

```bash
$PY "$SHEETS/verify_docx.py" "$OUTDIR"     # 或用 VERIFY_DIR 环境变量
```
逐项校验：每份 ≤50、全局连续编号、图片数==条目数、`【违规要素】`框数==条目数、元信息格式、无「举报理由草稿」、
**无防御性表述**（`defensive=N` 须为 0，否则退出码 2）。

## ⑨ 打包交付

把 `第NN批.docx` + 日志（`处理日志.csv`/`截图状态汇总.csv`/`完成标记.json`/`screenshots/`）打成 zip，并写 `交付说明.md`（数据口径、5 类适配、判定口径与需人工复核条数、复现命令）。

---

## 附：`capture_config.json` 示例

```json
{
  "manifest":    "/abs/输出Word/links_manifest.json",
  "shots_dir":   "/abs/输出Word/screenshots",
  "results_dir": "/abs/_ego",
  "space_id":    3,
  "limit":       0,
  "tag":        "run",
  "fresh":      true,
  "recycle":    99,
  "watchdog": { "goto": 24000, "nav": 18000, "settle": 1300, "hide": 5000,
                "probe": 5000, "extract": 10000, "shot": 20000, "reset": 10000 }
}
```
环境变量单键覆盖：`CAPTURE_CONFIG`/`CAPTURE_MANIFEST`/`CAPTURE_SHOTS`/`CAPTURE_RESULTS`/`CAPTURE_TAG`/`CAPTURE_LIMIT`/`CAPTURE_SPACE`/`CAPTURE_FRESH`。
