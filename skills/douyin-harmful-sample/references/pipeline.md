# 端到端管线与参数手册（douyin-harmful-sample）

按本顺序跑。命令里的占位符：`<XLSX>` 原表、`$OUTDIR` 输出目录（如 `输出Word`）、
`$ENG` 克隆仓库的 `skills/harmful_sample`、`$SHEETS` 本 skill 的 `scripts/`。
环境：Python = `~/.workbuddy/binaries/python/envs/default/bin/python`；
Ego Lite = `~/.local/bin/ego-browser`；macOS 无 `timeout`。

> **⭐ 交付优先（贯穿全流程）**：成功 = **文字全量做完 + Word 交出去**，不是「图全抓到」。
> 验证码 / 截图失败 / 页面被拦 / 超时一律**不停下来等人**：缺图位置留白，文字照交出，
> 末尾一句话说明。详见 SKILL.md「交付优先原则」与 troubleshooting #12 / #12b / #20。

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

## ② 打补丁（幂等；本仓库已预置，通常直接跳过）

```bash
$PY "$SHEETS/apply_adaptations.py" "$ENG/scripts/excel_to_word.py"
```
- **本仓库 `youhaiyangben` 的该文件已预置全部 10 类适配**，所以这一步一般直接打印
  「已包含所有适配，未做任何改动」并退出——属正常，不是失败。
- 用法：`python apply_adaptations.py <fresh excel_to_word.py>`，或环境变量 `APPLY_TARGET`。
- 注入 10 类适配（列名别名 / 编号区间含「新编」ID 列 / `--title` 编号范围行 / 元信息加编号 /
  去防御性表述 / 本案关键词扩展 / 一条多图 / **截图缺失处留空白** / `CLASSIFY_MODE` 判定方式 /
  `meta` 对账字段）；脚本内部拆成 **23 个幂等注入块**。清单以 `apply_adaptations.py` 的
  docstring 与 SKILL.md 为准（两处编号一致）。
- 其中「截图缺失处留空白」是**交付优先原则的实现基础**：抓不到图不阻塞 Word 生成。
- 该文件若被回退成上游原始版，或要把流程复用到别的上游 clone，跑这一条即可重新打全。可重复执行。
- **要改适配逻辑就改 `apply_adaptations.py` 再重跑**，不要手改仓库脚本（否则不可复现）。

## ③ 取样 + 列映射/区间对账（`--dry-run`）

```bash
$PY "$ENG/scripts/excel_to_word.py" "$XLSX" \
  --output-dir "$OUTDIR" --sheet 信息 --id-min 400 --id-max 900 --dry-run
```
**关键参数**
- `--sheet`：工作表名（本案 `信息`；仓库默认 `搜索结果`）。
- `--id-min` / `--id-max`：按编号列取区间（`ID_COLUMN_ALIASES=["编号","新编","新编号","id","ID"]`；
  部分重排导出的编号列叫「新编」，少了这个别名会直接报错退出）。
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
CHUNK=12 SPACE_ID=3 CONFIG="$SHEETS/capture_config.json" EGO_BROWSER="$EGO" "$SHEETS/run_capture.sh"
```
**关键参数**（环境变量覆盖）
- `CHUNK`：每片条数，默认 `12`（10–12 最佳）；`MAX_ATTEMPTS` 安全上限默认 `80`。
- `SPACE_ID`：指定已归属 agent 的 space（**推荐显式给**）。不设则用 config 里的 `space_id`。
- `run_capture.sh` 每片注入 `CONFIG/LIMIT/TAG/FRESH`（可选 `SPACE`），靠 `capture_douyin.js`
  的 ok 跳过逻辑断点续跑。**注入方式是拼 prelude，不是环境变量**——见下。
- `capture_config.json` 必填：`manifest` / `shots_dir` / `results_dir`；`space_id` 默认 `3`；
  可选 `extra_settle`（默认 900，渲染后额外等多久）、`max_attempts`（默认 2，单条重试上限）。
- 被限流/卡死：`run_capture.sh` 检测到连续两片 `remaining` 没减少会停止，冷却后重跑即可接着抓。

> **踩坑（务必读 troubleshooting 17–22）**：`ego-browser nodejs` 的脚本跑在 app 侧的 node 里，
> **调用方环境变量不透传、cwd 恒为 `/`**。所以 `CAPTURE_CONFIG=… ego-browser nodejs < script`
> 是无效写法。脚本由 `run_capture.sh` 拼 prelude（`globalThis.__CAPTURE_ENV = {…}`）后再喂给 ego。
> 另外：空间归用户所有时必须**先问用户**再 `claimTaskSpace`；进度判据看 `remaining` 而非 `pending`；
> 卡在「加载中」的页会自动 `reload()` 重试。


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
逐项校验（**交付优先语义**）：
- **硬失败（退出码 2）**：每份 >50 条、编号不连续、`【违规要素】`框数 != 条目数、元信息格式错、
  出现「举报理由草稿」、**出现防御性表述**（`defensive=N` 须为 0）。
- **只提示不改退出码**：图片数 < 条目数（打印 `no_image(x/y)`）——缺图属可接受的文字版交付；
  残留「截图待补」占位（应为留白，出现说明适配 #8 没跑到）。
- 末尾打印 `delivered: text=YES/NO images=x/y notes=N`，一眼看出「文字交没交」。
- `--strict-images` 把缺图也当失败（素材验收场景）；`--json` 出机器可读汇总。

## ⑨ 打包交付

把 `第NN批.docx` + 日志（`处理日志.csv`/`截图状态汇总.csv`/`完成标记.json`/`screenshots/`）打成 zip，
并写 `交付说明.md`（数据口径、适配清单、判定口径与**未附图条数**、复现命令）。

**交付优先**：只要 Word 产出了就算交付成功。缺图是留白的，状态照样是「已完成」，
末尾**一句话**说明命中条数即可（**不要用分号**，会被后端按 `;` 切段切碎）。

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
  "extra_settle": 900,
  "max_attempts": 2,
  "watchdog": { "goto": 24000, "nav": 18000, "settle": 1300, "hide": 5000,
                "probe": 5000, "extract": 10000, "shot": 20000, "reset": 10000 }
}
```
单键覆盖由 `run_capture.sh` 的 prelude 注入（键名：`CONFIG`/`LIMIT`/`TAG`/`FRESH`/`SPACE`）；
`capture_douyin.js` 的 `envOf()` 也兼容 `process.env.CAPTURE_*`，但**仅在别处直跑时有意义**。

## 附：本案关键词扩展（可选，适配 6）

判定关键词是**按案件定制**的，不该写进通用补丁。把 `scripts/case_keywords.example.json`
复制成 `ENG/scripts/case_keywords.json`（或用 `CASE_KEYWORD_FILE=<路径>`）即可：
`words` 与仓库原词表**合并**、`note` 覆盖该类理由文案、`fallback` 替换「未命中」时的兜底文案。
生效情况会在 `--dry-run` 的 `meta.case_keyword_applied` 里列出来，方便对账。

