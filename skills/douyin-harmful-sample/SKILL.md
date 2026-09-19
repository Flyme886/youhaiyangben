---
name: douyin-harmful-sample
description: 用 Ego Lite 浏览器把抖音监测 Excel 批量做成「有害样本」Word 报告（配合 harmful_sample 技能链）。当用户给出抖音监测/粉丝排序 .xlsx 并要求"做有害样本""生成有害样本 Word""按编号区间取样出报告"时使用。涵盖列名映射、编号区间筛选、去防御性表述、抖音 /share/video 路径改写、风控规避与页面劣化对策。
agent_created: true
---

# 抖音有害样本批量文档

把监测 Excel 的指定编号区间，用私有仓库 `Flyme886/youhaiyangben` 的 `skills/harmful_sample`
技能链 + Ego Lite 浏览器，产出「舆情有害样本」Word 批量文档（50 条/份，全局连续编号）。

## 何时用

用户给了抖音监测 / 粉丝排序 `.xlsx`，并要「做有害样本」「生成有害样本 Word」「按编号区间取样出报告」。
每条 = 编号元信息 + 原文 + 链接 + 页面截图 + 橙色 `【违规要素】` 判定框。

## 前置条件

- **仓库私有**：`Flyme886/youhaiyangben` 靠本机 git 凭据 clone；WebFetch 其文件会 404，只能先 clone 到本地。
- 环境：Python = `~/.workbuddy/binaries/python/envs/default/bin/python`；Ego Lite = `~/.local/bin/ego-browser`；macOS + zsh，**无 `timeout` 命令**。
- 浏览器只用 Ego Lite，**别用 Playwright 抓抖音**（会被验证码拦）。
- 先读 `references/pipeline.md`（端到端管线与参数手册）与 `references/troubleshooting.md`（踩坑）。
- 若 `ego-browser` 尚未安装，见下节**自主安装**，不要因为缺浏览器就停下来问用户。

## 浏览器环境：ego lite（任何「浏览器自动调研」都优先用它）

本技能链的截图全部依赖 ego lite。它是**真实浏览器 + 持久化 profile**，登录态可复用、风控表现
远好于无头方案。**凡是要浏览器自动调研/抓取/截图的任务，都建议用 ego lite**，不要临时换
Playwright / 无头 Chromium。

先自检装没装（顺手修 PATH）：

```bash
command -v ego-browser || export PATH="$HOME/.local/bin:$PATH"; command -v ego-browser
```

**没装就自己装，不需要人工下载** —— 本机已随 `ego-browser` 技能带官方安装脚本，会自动识别
CPU 架构、下载 DMG、装到 `/Applications`、去掉 quarantine 并启动 app：

```bash
sh ~/.workbuddy/skills/ego-browser/scripts/install.sh
```

脚本失败时用直链手动装（按架构二选一）：

| 架构 | 直链 |
|---|---|
| Apple Silicon (arm64) | `https://cdn.ego.app/setup/macos/arm64/egolite.dmg` |
| Intel (x64) | `https://cdn.ego.app/setup/macos/x64/egolite.dmg` |

官网（非 macOS，或想自己下载）：**<https://lite.ego.app/>**

> **唯一需要人工介入的一步**：首次启动的 onboarding 是 GUI 操作，要**用户在 app 里完成**
> （可导入 Chrome 数据等）。onboarding 会把 `ego-browser` 注册进 PATH（通常 `~/.local/bin`）。
> 脚本会自动打开 app，**等用户确认完成 onboarding 再继续**，别自己往下跑。

装好先冒烟验证：

```bash
ego-browser nodejs <<'EOF'
console.log('ego-browser ready')
EOF
```

打印 `ego-browser ready` 即环境就绪。抖音抓取前还要**已在 ego lite 里登录抖音**（扫码由用户完成，
见 `references/troubleshooting.md` 登录相关条目）。

## 标准链路

```bash
PY=~/.workbuddy/binaries/python/envs/default/bin/python
EGO=~/.local/bin/ego-browser
SHEETS=<本skill>/scripts            # capture_douyin.js / run_capture.sh / build_manifest.py / verify_docx.py / apply_adaptations.py
ENG=<clone>/skills/harmful_sample   # 仓库脚本：excel_to_word.py / quality_check_screenshots.py
OUTDIR=<输出Word目录>  XLSX=<原表.xlsx>

# 1) 克隆 + 打补丁（apply_adaptations.py 幂等，重复跑不改）
git clone https://github.com/Flyme886/youhaiyangben.git engine && rm -rf engine/.git
$PY "$SHEETS/apply_adaptations.py" "$ENG/scripts/excel_to_word.py"

# 2) 取样 + 列映射/区间对账（--dry-run 只出 links_manifest.json）
$PY "$ENG/scripts/excel_to_word.py" "$XLSX" \
  --output-dir "$OUTDIR" --sheet 信息 --id-min 400 --id-max 900 --dry-run

# 3) 截图：先 cp scripts/capture_config.example.json capture_config.json 并填好三个绝对路径
#    （run_capture.sh 读 CONFIG；字段见 scripts/README.md 第三节）
CHUNK=12 CONFIG="$SHEETS/capture_config.json" EGO_BROWSER="$EGO" "$SHEETS/run_capture.sh"

# 4) 合并抓取结果 -> screenshots_manifest.json
$PY "$SHEETS/build_manifest.py" --links "$OUTDIR/links_manifest.json" \
  --results-dir "$OUTDIR/../_ego" --shots-dir "$OUTDIR/screenshots" \
  --output "$OUTDIR/screenshots/screenshots_manifest.json"

# 5) 截图质检（仓库脚本）
$PY "$ENG/scripts/quality_check_screenshots.py" "$OUTDIR/screenshots/screenshots_manifest.json"

# 6) 出 Word（--screenshots-manifest 必须指向质检后 manifest）
$PY "$ENG/scripts/excel_to_word.py" "$XLSX" \
  --output-dir "$OUTDIR" --sheet 信息 --id-min 400 --id-max 900 \
  --title "编号400-900" --batch-size 50 \
  --screenshots-manifest "$OUTDIR/screenshots/screenshot_quality_manifest.json"

# 7) 结构校验
$PY "$SHEETS/verify_docx.py" "$OUTDIR"
```

## apply_adaptations.py 注入的 5 类适配

1. **列名别名**：仓库硬编码 `标题/微博内容`/`原文/评论链接`/`原文作者`；粉丝排序导出实为 `标题`/`URL`/`作者`。`COLUMN_ALIASES` 映射，命中情况写入 `meta.column_alias_map`。
2. **编号区间**：`--id-min/--id-max` 按 `编号` 列过滤；`meta` 输出 `in_range_rows`/`out_of_range_rows` 对账。
3. **批次标题**：`--title` 追加区间标签，并在标题下加一行 `本批编号范围：X — Y　共 N 条`。
4. **元信息加编号**：`Record.sheet_id` 存编号；元信息行改为 `（编号400 · 原序号1234 · 抖音 · @作者）`；`sheet_id` 同步写入 `links_manifest.json`。
5. **去防御性表述**：见下节。

> 脚本内部把上述 5 类拆成 15 个幂等注入块，全部命中即打印「已包含所有适配」并退出。

## 判定理由：不要任何防御性表述

仓库原文会给**所有视频平台链接**的判定理由追加一句「视频画面及完整语音需人工核验。」
（`_keyword_classify` 与 `classify_harm` 两处）。这是防御性/免责对冲，**成品不要**。

`apply_adaptations.py` 对此做轻量化处理：

- **删注入点**（根治）：把上述两处 `if <视频平台链接> → 追加该句` 整段删除。
- **加兜底清洗**：注入 `DEFENSIVE_PHRASES` 短语表 + `strip_defensive(text)`，在两处返回处调用。
  即使以后启用 `DEEPSEEK_API_KEY` 让大模型产出同类措辞，也会被一并剔掉。

判定理由保持**断言式**表述（如「上纲攻击高校管理和教育制度。」）；若规则未命中，渲染层会回落到
中性的「依据文案及页面文字判定为有害样本。」——同样是断言，不是对冲。

改完的验证口径：`【违规要素】`条数仍 == 条目数，且**不含**「人工核验」等对冲字样。

## 动手前必须先和用户确认的 2 件事

- **URL 改写（抓取侧）**：`/share/video/{id}` → `/video/{id}`（`/share/video/` 返回「验证码中间页」）；**Word 的「链接：」行仍输出原始 share 链接**，数据不动。
- **判定口径**：未配 `DEEPSEEK_API_KEY` 时回退关键词规则，而仓库原关键词为「央企高管/分配纠纷」案例所写，别的案子会大面积落到兜底文案「借热点事件发布负面情绪化内容。」。必须先问清：**用默认规则 / 补本案关键词 / 提供大模型 key**，三选一。

## 抖音抓取铁律

- **`page.goto` 必须 `waitUntil:"domcontentloaded"`**：抖音视频流永远等不到 `load`，用默认值每条等到超时。
- **绝不并发**：3 标签页并发会立刻触发 `net::ERR_ABORTED` + 验证码成簇，并连累整个会话 IP 限流（`验证过于频繁[5009]`，连官网首页都打不开）。必须单线程；限流是短时的，冷却数分钟可恢复。
- **每 10–12 条重建标签页**：渲染进程同会话跑约 15 条后劣化（2 秒/条 → 60–300 秒/条）。`run_capture.sh` 每片 `fresh=true` 重建，稳定回约 2 秒/条。
- **登录弹窗**：页面有两个同名 `.douyin_login_new_class`（右侧引导卡 + 大弹窗），取**面积最大**者，再向上找全屏 `position:fixed` 祖先设 `display:none`。
- **验证码判定只看标题**：别用 `/验证码/` 匹配全文——登录弹窗里有「验证码登录」字样会误报。只认「验证码中间页」或正文含「请完成下列验证」「拖动完成上方拼图」「人机验证」「安全验证」。
- 每条加超时看门狗；超时后**必须重置页面**（`about:blank`），否则污染后续所有条目。
- 复用 space 内已有页面（`task.pages()` 取第一个），别连续 `close`+`newPage`（会报 `is already page pNN`）。
- 可断点续跑：`results*.jsonl` 逐条 append，已 `ok` 的 item_id 跳过；限流冷却后重跑 `run_capture.sh` 接着抓。

## 产物与校验

`第NN批.docx`、`处理日志.csv`、`截图状态汇总.csv`、`完成标记.json`、`screenshots/`(PNG)、`screenshots/screenshots_manifest.json`。

`verify_docx.py` 校验：每份 ≤50 条、**全局连续编号**（跨文件拼起 1..N，非每份从 1）、图片数==条目数、`【违规要素】`框数==条目数、元信息形如 `1、（编号400 · 原序号1234 · 抖音 · @作者）`、不得出现「举报理由草稿」、**不得出现「需人工核验」等防御性表述**（`defensive=N` 必须为 0，否则退出码 2）。

## 交付

写 `交付说明.md`（数据口径、5 类适配、判定口径与需人工复核条数、复现命令），把 docx + 日志打成 zip。
**`blocked_or_blank`（正文<20字）与「截图待补」是设计内降级路径，不是失败**——不要当失败处理。

## 禁忌

- 不要并发抓抖音。
- 不要改 `page.goto` 的 `waitUntil`。
- 不要把 `截图待补` 算作失败。
- 不要改本 skill `scripts/` 下的脚本；仓库侧的 `excel_to_word.py` 一律**通过 `apply_adaptations.py` 打补丁**，不要手工编辑（否则换新 clone 后无法复现）。
- 不要往 Word 里写未从 Excel/页面提取出的事实内容。
