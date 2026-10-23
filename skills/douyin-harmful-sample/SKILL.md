---
name: douyin-harmful-sample
description: 用 Ego Lite 浏览器把抖音监测 Excel 批量做成「有害样本」Word 报告（配合 harmful_sample 技能链）。当用户给出抖音监测/粉丝排序 .xlsx 并要求"做有害样本""生成有害样本 Word""按编号区间取样出报告"时使用。涵盖列名映射、编号区间筛选、去防御性表述、抖音 /share/video 路径改写、风控规避与页面劣化对策。交付版式为极简「N.标题 / 链接 / 图片」三段（MINIMAL_LAYOUT_V1），Word 正文不出现任何其他说明文字。核心原则是**交付优先**：遇到验证码/截图抓不到时不停下来等人，文字全量做完并交付，缺图留白、末尾一句话说明。
agent_created: true
---

# 抖音有害样本批量文档

把监测 Excel 的指定编号区间，用私有仓库 `Flyme886/youhaiyangben` 的 `skills/harmful_sample`
技能链 + Ego Lite 浏览器，产出「舆情有害样本」Word 批量文档（50 条/份，全局连续编号）。

## ⭐ 交付优先原则（最高优先级，凌驾于下面所有细节）

**遇到验证码、截图抓不到、页面被拦、超时 —— 一律不要停下来等人。**

> 边界说明：**开工前**的一次性确认（URL 改写口径、判定口径三选一）、ego lite 首次 onboarding、
> task space 归属授权，属于「本来就该问人」的事，不在本条约束内。
> 本节约束的是**运行过程中**的技术性降级 —— 那些一律自己吞掉，最后一句说明就好。

正确定义的成功不是「图全抓到了」，而是「**文字部分全量做完并且 Word 已经交出去**」。
所以：

| 场景 | 错误做法（不要） | 正确做法 |
|---|---|---|
| 大面积命中验证码 | 判定失败 / 暂停并报「待人工处理」 | 把没截到的图**留白**，文字照常出完 Word，最后**一句话**说明 |
| 截图采集整体没跑起来 | 整个任务判失败 | 无图直接进「生成 Word」，按文字版交付，末尾一句说明 |
| 单条 `blocked_or_blank` / 超时 | 当失败重跑到底 / 死循环 | 按 `max_attempts` 封顶后视作已处置，留白继续 |
| 部分条目缺图 | 用「部分完成 / 需人工复核」吓用户 | 状态就是**已完成**，缺图只是「未附图 N 条」 |

写进交付物的收尾文案必须满足：

- **一句话**，不要长篇解释；放在交付说明里即可，**不要**写成待办、不要写「需人工…」。
- **不要出现分号 `;`** —— 后端把多段说明用 `"; "` 拼接、小程序按 `;` 切段翻译，
  说明句里带分号会被切碎。
- 统一口径：「`{命中数}/{总数} 条命中平台验证码，未取到截图，Word 已按文字版正常交付（图片位置留空）`」。
- 只有**「命中验证码且一个 Word 都没产出」**这种真的无物可交的情形，才允许报「待重跑」；
  这是兜底态，不是常规路径。除图片外的文字**任何情况下都不许不交**。

落地位置：`task_runner.finalize_task()` 以「有没有 docx」决定收尾状态（有 → `partial_completed`
或 `completed`；零 docx 且命中验证码 → `paused_captcha`）。改这块前先看
`references/troubleshooting.md` #12 / #20。

## 何时用

用户给了抖音监测 / 粉丝排序 `.xlsx`，并要「做有害样本」「生成有害样本 Word」「按编号区间取样出报告」。

### ⭐ 交付版式（MINIMAL_LAYOUT_V1，硬约束）

每条样本在 Word 里**严格只有三段**（对齐参考样例「王振义-7批-20条」）：

| 段 | 内容 | 样式 |
|---|---|---|
| 1 | `N.标题`（N 为条目顺序号；标题取自表格内容，换行折叠成空格，超 32 字截断加「…」） | 加粗、深蓝 `1F4E79`、12pt |
| 2 | 原始链接 | 蓝 `0B60B0`、9.5pt |
| 3 | 页面截图 | 横版最宽 16.0cm，竖版按高度 20.5cm 限幅等比缩放 |

**硬性排除项**（出现任何一种都算交付不合格）：批次大标题、「本批编号范围」行、
元信息行（编号·原序号·来源·@作者）、【概括】、原文全文、「原文：」「链接：」标签、
橙色【违规要素】判定框、以及**其他任何说明文字**。
（**截图抓不到时该位置直接留白，什么都不写** —— 见开头「交付优先原则」。）

判定结果照常计算，但只写进 `处理日志.csv` 供对账，**不落版到 Word**。
无标题条目的兜底依次为「摘要 → （无标题）」，这也是数据占位、不是说明文字。

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

### ⚠️ 两条 ego 硬约束（不遵守会白跑）

1. **环境变量不会透传进 ego 的 node**。`ego-browser nodejs` 的脚本跑在 **app 侧**，
   `process.env` 里没有调用方设置的任何变量，`cwd` 恒为 `/`。
   所以 `CAPTURE_CONFIG=… ego-browser nodejs < script` **是无效写法**。
   `run_capture.sh` 已在脚本前拼 prelude 解决：
   ```
   globalThis.__CAPTURE_ENV = {CONFIG:"…", LIMIT:"12", TAG:"c0", FRESH:"true", SPACE:"3"};
   ```
   `capture_douyin.js` 优先读 `globalThis.__CAPTURE_ENV`，其次才 `process.env`。
   **要加新配置项，加进 prelude 并把 key 写进 `capture_douyin.js` 的 `envOf()` 读取处。**

2. **空间归属**：只有 `ownership === "agent"` 的空间能操作。若报
   `The user has taken control of this task space…`，说明空间归用户所有，
   **不许自行 `claimTaskSpace`** —— 必须问用户，拿到明确授权后再
   `claimTaskSpace(<id>)` 继续。查归属：`listTaskSpaces()`。
   新建空间要传名字（`taskSpace("名字")`），传数字 id 且不存在会报 `task space not found`。

## 标准链路

```bash
PY=~/.workbuddy/binaries/python/envs/default/bin/python
EGO=~/.local/bin/ego-browser
SHEETS=<本skill>/scripts            # capture_douyin.js / run_capture.sh / build_manifest.py / verify_docx.py / apply_adaptations.py
ENG=<clone>/skills/harmful_sample   # 仓库脚本：excel_to_word.py / quality_check_screenshots.py
OUTDIR=<输出Word目录>  XLSX=<原表.xlsx>

# 1) 克隆。仓库自带 excel_to_word.py 已预置 10 类适配；本步会自动补上第 11 类（极简版式），
#    首次对新 clone 运行会打印 6 处注入，之后幂等跳过（除非仓库已同步第 11 类，则直接「已包含所有适配」）
git clone https://github.com/Flyme886/youhaiyangben.git engine && rm -rf engine/.git
$PY "$SHEETS/apply_adaptations.py" "$ENG/scripts/excel_to_word.py"   # 幂等兜底，可重复跑

# 2) 取样 + 列映射/区间对账（--dry-run 只出 links_manifest.json）
$PY "$ENG/scripts/excel_to_word.py" "$XLSX" \
  --output-dir "$OUTDIR" --sheet 信息 --id-min 400 --id-max 900 --dry-run

# 2b) 【前置流程】清单去重 + 批次标记（评论模式必做；纯视频页模式可跳过）
#     --comment-scope all      = 全部条目都抓评论（先全量测限流）
#     --comment-scope first_n  = 每 --batch-size 条里只有前 --comments-per-batch 条抓评论，
#                                其余 capture_comments=false（只截视频页）——限流严重时降级用
$PY "$SHEETS/prepare_manifest.py" --in "$OUTDIR/links_manifest.json" \
  --out "$OUTDIR/links_manifest.json" --drop-dups --comment-scope all

# 3) 截图：先 cp scripts/capture_config.example.json capture_config.json 并填好三个绝对路径
#    （run_capture.sh 读 CONFIG；字段见 scripts/README.md 第三节）
#    SPACE_ID 可选，指定已归属 agent 的 space；不设则用 config 里的 space_id
CHUNK=12 SPACE_ID=3 CONFIG="$SHEETS/capture_config.json" EGO_BROWSER="$EGO" "$SHEETS/run_capture.sh"

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

### 变体：截图要带「恶意评论」时

把第 3 步换成评论截图链路（第 2b 步的去重 + 批次标记**必做**），其余步骤完全不变：

```bash
CHUNK=6 SPACE_ID=<agent空间> CONFIG="$SHEETS/capture_config.json" EGO_BROWSER="$EGO" \
  "$SHEETS/run_capture_comments.sh"
```

差别只有几点：脚本换成 `capture_douyin_comments.js`；`CHUNK` 从 12 降到 6
（单条要展开评论面板 + 滚动加载）；**一条样本产出 1–3 张评论图**——只截「含恶意评论」
的那几屏、最多 3 张，有评论但一条恶意都没有才退回普通评论 1–2 张，无评论/视频已删退回视频页
1 张；清单里 `capture_comments: false` 的条目不开评论区、只截视频页。

**先小批（20–30 条）实测限流**，看 `ERR_CONNECTION_*` 出现频率再决定全量还是
用 `prepare_manifest.py --comment-scope first_n` 降级（降级不影响已抓好的条目）。
完整原理、DOM 选择器、恶意词表口径、多图插入宽度标定见
**`references/comments-mode.md`**（改这块之前先读它）。

## apply_adaptations.py 注入的适配

1. **列名别名**：仓库硬编码 `标题/微博内容`/`原文/评论链接`/`原文作者`；粉丝排序导出实为 `标题`/`URL`/`作者`。`COLUMN_ALIASES` 映射，命中情况写入 `meta.column_alias_map`。
2. **编号区间**：`--id-min/--id-max` 按编号列过滤；`meta` 输出 `in_range_rows`/`out_of_range_rows` 对账。
   **ID 列别名扩展**：`["编号","新编","新编号","id","ID"]`——部分导出表把编号列命名为
   「新编」（重排后的新编号），旧别名只有「编号/id/ID」会直接报错退出。
3. **批次标题**：`--title` 追加区间标签，并在标题下加一行 `本批编号范围：X — Y　共 N 条`。
4. **元信息加编号**：`Record.sheet_id` 存编号；元信息行改为 `（编号400 · 原序号1234 · 抖音 · @作者）`；`sheet_id` 同步写入 `links_manifest.json`。
5. **去防御性表述**：见下节。
6. **本案关键词扩展**（数据驱动，可选）：注入 `CASE_KEYWORD_FILE` 机制，用一个 JSON
   覆盖/追加 `REASON_PATTERNS` 并替换兜底文案，**按案件定制判定关键词而不改上游规则**。
   不提供 JSON 时行为与仓库原版完全一致。JSON 结构见 `scripts/case_keywords.example.json`。
   文件放 `ENG/scripts/case_keywords.json`（同目录即自动生效），或 `CASE_KEYWORD_FILE=<路径>` 指定。
7. **一条多图**：抓取结果带 `images:[文件名…]` 时按序插入多张，插入宽度按图片像素宽自适应
   `clamp(px × 0.030, 8.5, 16.6)cm` —— 窄的评论侧栏图不会被压成看不清的字号。
8. **截图缺失处留空白**：正文不写任何说明文字（不出现「截图待补」这类表述）。
   **这是「交付优先」的实现基础** —— 抓不到图不阻塞 Word 生成，文字照交。
9. **判定方式可切换**（`CLASSIFY_MODE=auto|keyword|llm`）：显式设 `keyword` 可完全绕开带图大模型，
   零 API 成本、不再盖掉本案关键词判定；不设时若已加载关键词文件则自动用 `keyword`，否则沿用
   `auto`（上游行为）。云端配了 `DEEPSEEK_API_KEY` 时务必显式设 `keyword`，否则几千条会逐条真调模型。
10. **对账字段**：`meta` 补齐 `column_alias_map` / `in_range_rows` / `out_of_range_rows` /
    `case_keyword_applied`，供全量跑完后逐项核对适配有没有真的生效。
11. **极简交付版式 MINIMAL_LAYOUT_V1**：每条只渲染「`N.标题` / 链接 / 截图」三段
    （样式见「交付版式」一节）。删除批次大标题、「本批编号范围」行、元信息行、
    【概括】、原文全文与【违规要素】判定框；判定结果仍写 `处理日志.csv`。
    竖版截图按高度 20.5cm 限幅（新增 `_png_pixel_height`），插图宽度上限 16.0cm。
  **适配 3b / 4c / 5a 在此版式下自动跳过**（守卫检测 `MINIMAL_LAYOUT_V1`），
  避免「先注入旧版式、再删旧版式」的锚点冲突。仓库当前预置 10 类，第 11 类
  由本脚本对每个新 clone 自动补齐。

> 脚本内部把这些拆成 **28 个幂等注入块**，全部命中即打印「已包含所有适配」并退出。
> 清单编号与 `scripts/apply_adaptations.py` 的 docstring 一致。

### 仓库脚本与补丁脚本怎么分工

- **本仓库 `youhaiyangben` 的 `skills/harmful_sample/scripts/excel_to_word.py` 已预置 10 类适配**；
  第 11 类（极简版式）由 `apply_adaptations.py` 在每次 clone 后自动补齐（幂等）。
  clone 下来即可直接用，**不必**先打补丁。
- `apply_adaptations.py` 仍然保留，用途有两个：① 万一该文件被回退成上游原始版，一条命令重新打全；
  ② 把这套流程复用到**其它**上游 clone 上。它幂等——对已适配的文件只打印「已包含所有适配」。
- **调整适配逻辑请改 `apply_adaptations.py` 再重跑**，不要直接在仓库脚本上手改。
  否则会变成「一半手改、一半注入」，换个 clone 就无法复现。

## 判定理由：不要任何防御性表述

仓库原文会给**所有视频平台链接**的判定理由追加一句「视频画面及完整语音需人工核验。」
（`_keyword_classify` 与 `classify_harm` 两处）。这是防御性/免责对冲，**成品不要**。

`apply_adaptations.py` 对此做轻量化处理：

- **删注入点**（根治）：把上述两处 `if <视频平台链接> → 追加该句` 整段删除。
- **加兜底清洗**：注入 `DEFENSIVE_PHRASES` 短语表 + `strip_defensive(text)`，在两处返回处调用。
  即使以后启用 `DEEPSEEK_API_KEY` 让大模型产出同类措辞，也会被一并剔掉。

判定理由保持**断言式**表述（如「上纲攻击高校管理和教育制度。」）；若规则未命中，渲染层会回落到
中性的「依据文案及页面文字判定为有害样本。」——同样是断言，不是对冲。

改完的验证口径：判定结果仍写入 `处理日志.csv`（条数==条目数、不含「人工核验」等对冲字样）；
**Word 正文不再渲染判定框** —— 文档内出现【违规要素】反而算旧版式残留（`legacy_leak` 硬失败）。

## 动手前必须先和用户确认的 2 件事

- **URL 改写（抓取侧）**：`/share/video/{id}` → `/video/{id}`（`/share/video/` 返回「验证码中间页」）；**Word 的「链接：」行仍输出原始 share 链接**，数据不动。
- **判定口径**：未配 `DEEPSEEK_API_KEY` 时回退关键词规则，而仓库原关键词为「央企高管/分配纠纷」案例所写，别的案子会大面积落到兜底文案「借热点事件发布负面情绪化内容。」。必须先问清：**用默认规则 / 补本案关键词 / 提供大模型 key**，三选一。

## 抖音抓取铁律

- **`page.goto` 必须 `waitUntil:"domcontentloaded"`**：抖音视频流永远等不到 `load`，用默认值每条等到超时。
- **详情页要等渲染，不能 goto 完就截**：`domcontentloaded` 后播放器/描述区还要再渲染一拍，
  只等 `settle` 会截到「作者卡已出、播放器一片空白」的半成品（实测约 1/4 条目命中）。
  现版做法：`waitForSelector('video, [data-e2e="detail-video-desc"], …')` best-effort（超时不致命）
  \+ `extra_settle`（默认 900ms）再截图。代价是单条 2 秒 → 约 7 秒，值得。
- **`ok` 里含「视频不存在」是正常数据**：原视频被删时抖音返回「你要观看的视频不存在」，
  页面文字 ≥20 字故判 `ok`，截图就是证据，**不要当失败**。
- **绝不并发**：3 标签页并发会立刻触发 `net::ERR_ABORTED` + 验证码成簇，并连累整个会话 IP 限流（`验证过于频繁[5009]`，连官网首页都打不开）。必须单线程；限流是短时的，冷却数分钟可恢复。
- **每 10–12 条重建标签页**：渲染进程同会话跑约 15 条后劣化（2 秒/条 → 60–300 秒/条）。`run_capture.sh` 每片 `fresh=true` 重建，稳定回约 2 秒/条。
- **进度判据用 `remaining`，不是 `pending`**：`pending` 被 `limit` 截断，每片恒等于 `CHUNK`，
  拿它做「无进展就停」的判据会**第二片就误停**。`capture_douyin.js` 现在同时打印
  `already_ok / remaining / pending`，`run_capture.sh` 只信 `remaining`。
- **永久失败的条目要封顶重试**：只跳过 `ok` 会让长期 `blocked_or_blank` 的条目每片重抓 →
  `remaining` 永不归零 → 死循环。现版按 `max_attempts`（默认 2）封顶，达到即视为已处置
  （降级路径，不是失败）。
- **登录弹窗**：页面有两个同名 `.douyin_login_new_class`（右侧引导卡 + 大弹窗），取**面积最大**者，再向上找全屏 `position:fixed` 祖先设 `display:none`。
- **验证码判定只看标题**：别用 `/验证码/` 匹配全文——登录弹窗里有「验证码登录」字样会误报。只认「验证码中间页」或正文含「请完成下列验证」「拖动完成上方拼图」「人机验证」「安全验证」。
- 每条加超时看门狗；超时后**必须重置页面**（`about:blank`），否则污染后续所有条目。
- 复用 space 内已有页面（`task.pages()` 取第一个），别连续 `close`+`newPage`（会报 `is already page pNN`）。
- 可断点续跑：`results*.jsonl` 逐条 append，已 `ok` 的 item_id 跳过；限流冷却后重跑 `run_capture.sh` 接着抓。

## 产物与校验

`第NN批.docx`、`处理日志.csv`、`截图状态汇总.csv`、`完成标记.json`、`screenshots/`(PNG)、`screenshots/screenshots_manifest.json`。

`verify_docx.py` 校验（**MINIMAL_LAYOUT_V1 版式 + 交付优先语义**）：
每份 ≤50 条、**全局连续编号**（跨文件拼起 1..N，非每份从 1）、
每条「标题 → 链接」成对（标题段后必须紧跟 http 开头的链接段）、
**不得残留旧版式**（【违规要素】框 /【概括】/「原文：」「链接：」标签 /
「N、（…）」元信息行 / 批次标题 / 表格 —— 出现即硬失败 `legacy_leak`）、
不得出现「举报理由草稿」。
**图片数<条目数只打印 `no_image(x/y)` 提示、不影响退出码** —— 缺图属于可接受的
文字版交付；标题引用的原文里出现对冲词只计 `quoted_hedge` 提示（是引用不是我们的措辞）。
需要把缺图也当失败时（素材验收场景）显式加 `--strict-images`；加 `--json` 出机器可读汇总。

## 交付

**Word 正文严格只有「N.标题 / 链接 / 图片」三段，不写任何其他说明** —— 口径、
适配清单、判定口径与未附图条数一律写进 `交付说明.md`，把 docx + 日志打成 zip。

**交付优先：只要 Word 产出了就算交付成功。** `blocked_or_blank`（正文<20字）、验证码、超时缺图
都是**设计内降级路径**，不是失败 —— 缺图位置留白，任务状态照样是「已完成」，末尾一句话说明即可。

## 禁忌

- 不要并发抓抖音。
- 不要改 `page.goto` 的 `waitUntil`。
- **不要因为验证码/缺图就停下等人** —— 先把文字全量做完并交付，最后一句简短说明（见开头「交付优先原则」）。
- **不要把缺图/`截图待补`/`blocked_or_blank` 算作失败**。
- 不要手改仓库侧的 `excel_to_word.py`（该文件已由 `apply_adaptations.py` 预置 11 类适配）；要调整适配逻辑请改 `apply_adaptations.py` 并重跑，保证任何 clone 都能复现。
- **不要往 Word 里写三段版式之外的任何东西**（元信息、原文全文、判定框、说明文字都不行 —— MINIMAL_LAYOUT_V1 硬约束）。
- 不要往 Word 里写未从 Excel/页面提取出的事实内容。
- 交付说明里**不要用分号**（后端按 `"; "` 拼段、小程序按 `;` 切段翻译，会被切碎）。
