# douyin-harmful-sample · scripts 使用说明

把「抖音监测 Excel → 舆情有害样本 Word 批量文档」这条真实跑通的任务，整理成可直接复用的脚本包。
本目录 7 个文件配合仓库 `Flyme886/youhaiyangben` 的 `skills/harmful_sample` 技能链使用。

环境（与本任务一致）：
- Python：`~/.workbuddy/binaries/python/envs/default/bin/python`（需装 python-docx / openpyxl / Pillow / numpy / requests）
- Ego Lite CLI：`~/.local/bin/ego-browser`
- 系统 macOS + zsh，**没有 `timeout` 命令**，脚本均不使用它

> **⭐ 交付优先**：这条链路的成功定义是「**文字全量做完 + Word 交出去**」，不是「图全抓到」。
> 验证码 / 截图失败 / 页面被拦 / 超时一律**不停下来等人** —— 缺图位置留白，文字照常出 Word，
> 末尾一句话说明即可。详见 SKILL.md「交付优先原则」与 `references/troubleshooting.md` #12 / #12b。

---

## 一、这些文件分别干什么

| 文件 | 作用 |
|---|---|
| `apply_adaptations.py` | 把仓库 `excel_to_word.py` 打上 **11 类适配**（列名别名 / 编号区间筛选含「新编」ID 列 / 批次标题与编号范围行 / 元信息加编号 / 去防御性表述 / 本案关键词扩展 / **一条样本插多张图 + 插入宽度按像素宽自适应** / **截图缺失处留空白** / **`CLASSIFY_MODE` 判定方式切换** / **`meta` 对账字段** / **MINIMAL_LAYOUT_V1 极简交付版式**：每条仅「N.标题 / 链接 / 图片」三段，删批次标题、元信息、【概括】、原文、判定框）；内部拆成 28 个幂等注入块（3b/4c/5a 在极简版式下自动跳过）。**仓库侧已预置 10 类，第 11 类由本脚本对每个新 clone 自动补齐（首次注入 6 处，之后幂等跳过）**；它的实际用途是「万一被回退成上游版」或「复用到别的上游 clone」。幂等。 |
| `case_keywords.example.json` | 本案关键词扩展的**数据模板**（适配 6 读取）。复制成 `ENG/scripts/case_keywords.json` 即可按案件定制判定关键词，不用改补丁脚本。 |
| `capture_douyin.js` | 由 `ego-browser nodejs <` 执行，单条抓取抖音**视频页**截图一张，结果逐条 append 到 `results*.jsonl`。配置驱动。 |
| `capture_douyin_comments.js` | **评论截图版**：单条展开评论区、滚动加载，**只截含恶意评论的那几屏、最多 `max_shots`(=3) 张**；有评论但无恶意 → 普通评论 1–2 张；无评论 / 视频已删 → 视频页一张；清单标 `capture_comments:false` → 只截视频页。原理与 DOM 选择器见 `references/comments-mode.md`。 |
| `comment_malice_keywords.json` | 恶意评论判定词表（strong/medium/weak 三档 + `min_score`）。口径取宽：命中任意一词即算「带恶意」。 |
| `prepare_manifest.py` | **前置流程**：按链接去重（重复条默认不输出；`--keep-dups` 则保留但序号留空、不抓取）+ 批次标记（`--comment-scope all` 全抓 / `first_n` 每批只抓前 N 条）。幂等；被处理的重复条写入 `去重剔除记录.csv`。 |
| `run_capture.sh` | 分片驱动视频页抓取：每片 10–12 条、每片重建标签页、可断点续跑。 |
| `run_capture_comments.sh` | 评论截图的分片驱动：默认 `CHUNK=6`（单条重得多）、限流自动冷却，其余同上。 |
| `build_manifest.py` | 合并 `links_manifest.json` + 所有 `results*.jsonl`，输出仓库要的 `screenshots_manifest.json`；把抓取结果的 `screenshots:[…]` 摊平成 `images:[…]`（多图）。 |
| `verify_docx.py` | 生成后结构校验（MINIMAL_LAYOUT_V1 版式）：条目数、全局连续编号、每条「标题 → 链接」成对（`link_bad` 须为 0）、**无旧版式残留**（违规要素框 /【概括】/「原文：」「链接：」标签 / 元信息行 / 表格 → `legacy_leak` 须为 0）、无「举报理由草稿」；图片数 < 条目数只提示（缺图留白是可接受交付，`--strict-images` 才判失败）。 |
| `capture_config.example.json` | 抓取配置模板。**先复制成 `capture_config.json`** 再改路径（见第三节）。 |
| `README.md` | 本文件。 |

---

## 二、执行顺序（端到端）

```bash
PY=~/.workbuddy/binaries/python/envs/default/bin/python
EGO=~/.local/bin/ego-browser
ENG="<你的 youhaiyangben 仓库>/skills/harmful_sample"   # 已 apply 适配后的版本
OUTDIR="$HOME/有害样本输出/输出Word"                     # Word/截图/清单输出目录
XLSX="<原 Excel 路径>"
SHEETS="$HOME/.workbuddy/skills/douyin-harmful-sample/scripts"   # 本 skill 的 scripts 目录

# ① 幂等兜底：仓库 excel_to_word.py 已预置全部 10 类适配，这步通常输出「已包含所有适配」直接跳过
$PY "$SHEETS/apply_adaptations.py" "$ENG/scripts/excel_to_word.py"
# ② 取样 + 列映射/区间校验，并写出 links_manifest.json
$PY "$ENG/scripts/excel_to_word.py" "$XLSX" \
  --output-dir "$OUTDIR" --sheet 信息 --id-min 400 --id-max 900 --dry-run

# ②b 【前置流程·评论模式必做】清单去重 + 批次标记
#     all      = 全部条目都抓评论（先全量测限流）
#     first_n  = 每 --batch-size 条里只有前 --comments-per-batch 条抓评论（限流严重时降级）
$PY "$SHEETS/prepare_manifest.py" --in "$OUTDIR/links_manifest.json" \
  --out "$OUTDIR/links_manifest.json" --drop-dups --comment-scope all

# ③ 截图（Ego Lite；先 cp capture_config.example.json capture_config.json 并填好三个绝对路径）
#    ⚠️ ego 的 node 脚本在 app 侧执行，调用方环境变量**不会透传**；
#    配置靠 run_capture.sh 拼的 prelude（globalThis.__CAPTURE_ENV）注入，不是环境变量。
#    SPACE_ID 建议显式给：必须是 ownership=agent 的空间，属用户的要先问用户再 claimTaskSpace。
CHUNK=12 SPACE_ID=3 CONFIG="$SHEETS/capture_config.json" EGO_BROWSER="$EGO" \
  "$SHEETS/run_capture.sh"
#    评论模式把上面这行换成（CHUNK 默认 6）：
#    CHUNK=6 SPACE_ID=3 CONFIG="$SHEETS/capture_config.json" EGO_BROWSER="$EGO" \
#      "$SHEETS/run_capture_comments.sh"

# ④ 合并抓取结果 -> screenshots_manifest.json
$PY "$SHEETS/build_manifest.py" \
  --links "$OUTDIR/links_manifest.json" \
  --results-dir "$OUTDIR/../_ego" \
  --shots-dir "$OUTDIR/screenshots" \
  --output "$OUTDIR/screenshots/screenshots_manifest.json"

# ⑤ 截图质检
$PY "$ENG/scripts/quality_check_screenshots.py" \
  "$OUTDIR/screenshots/screenshots_manifest.json"

# ⑥ 生成 Word 批次（判定口径：CLASSIFY_MODE=keyword 只走关键词；未设时已加载关键词文件即自动 keyword）
$PY "$ENG/scripts/excel_to_word.py" "$XLSX" \
  --output-dir "$OUTDIR" --sheet 信息 --id-min 400 --id-max 900 \
  --title "编号400-900" --batch-size 50 \
  --screenshots-manifest "$OUTDIR/screenshots/screenshot_quality_manifest.json"

# ⑦ 结构校验（交付优先：缺图只提示、不改退出码；文字硬伤才 exit 2）
$PY "$SHEETS/verify_docx.py" "$OUTDIR"
#    素材验收想把缺图也算失败时加 --strict-images；要机器可读汇总加 --json
```

---

## 三、capture_config.json 示例（capture_douyin.js / run_capture.sh 读取）

先 `cp capture_config.example.json capture_config.json`，再把 `manifest` / `shots_dir` / `results_dir`
三个路径改成你自己的绝对路径，放本脚本同目录（默认名 `capture_config.json`），或用 `CONFIG`
环境变量指向任意路径。

> 读法区别：`run_capture.sh` 读 **`CONFIG`**，再由它把该路径以 `CAPTURE_CONFIG` 传给
> `capture_douyin.js`；若直接 `ego-browser nodejs < capture_douyin.js`，则读 `CAPTURE_CONFIG`
> （缺省回落到同目录 `capture_config.json`）。

```json
{
  "manifest":   "/abs/输出Word/links_manifest.json",
  "shots_dir":  "/abs/输出Word/screenshots",
  "results_dir":"/abs/_ego",
  "space_id":   3,
  "limit":      0,
  "tag":        "run",
  "fresh":      true,
  "recycle":    99,
  "extra_settle": 900,
  "max_attempts": 2,
  "watchdog": {
    "goto": 24000, "nav": 18000, "settle": 1300, "hide": 5000,
    "probe": 5000, "extract": 10000, "shot": 20000, "reset": 10000
  },
  "comments": {
    "enabled": true,
    "max_per_sample": 20, "max_scan": 60, "min_malice_score": 1,
    "keywords_file": "/abs/comment_malice_keywords.json",
    "dwell": 1200, "load_rounds": 8, "open_wait": 2600,
    "max_shots": 3, "plain_max_shots": 2, "plain_per_sample": 6,
    "malice_context": 2, "malice_min_cover": 6, "pad": 4
  }
}
```

`comments` 段（仅评论模式用）：

- `max_shots`（默认 3）：单条样本**最多产出几张评论截图**——业务口径「2–3 张就够」。
- `plain_max_shots` / `plain_per_sample`：一条恶意评论都没有时的降级——普通评论取前几条、最多几张。
- `malice_context` / `malice_min_cover`：窗口在首尾恶意评论外各留几条上下文 / 至少覆盖几条
  （避免「只有 1 条恶意评论」时只出一张极小的图）。
- `max_per_sample` / `max_scan`：扫描窗口（前 20 条内找恶意；都没有则往后扫到第 60 条）。
- `min_malice_score`：判定阈值，默认 1（命中任意一词即算带恶意）。

- `extra_settle`（默认 900）：正文渲染完后再多等多少毫秒才截图。
- `max_attempts`（默认 2）：单条最多尝试几次；达到上限即视为已处置（降级路径，不是失败），
  否则长期 `blocked_or_blank` 的条目会让 `remaining` 永不归零、陷入死循环。

字段也可用 **prelude 单键覆盖**：`CONFIG`/`LIMIT`/`TAG`/`FRESH`/`SPACE`
（`run_capture.sh` 自动注入 `LIMIT=CHUNK`、`FRESH=true`、`TAG=每片自增`，`SPACE` 由 `SPACE_ID` 传）。
`capture_douyin.js` 的 `envOf()` 也兼容 `process.env.CAPTURE_*`，但**仅在 app 外直跑时有意义**——
`ego-browser nodejs` 里 `process.env` 是空的（见第四节第 0 条）。
续跑靠 capture 自身的跳过逻辑；每片原始输出留档在 `<config目录>/chunklogs/chunk_<tag>.log`。

---

## 四、不要重复踩的坑（已验证结论）

0. **`ego-browser nodejs` 里 `process.env` 是空的、`cwd` 恒为 `/`**：脚本跑在 **app 侧**的嵌入式
   node，调用方环境变量**不透传**。所以 `CAPTURE_CONFIG=… ego-browser nodejs < script` **不生效**，
   `import.meta.url` 也拿不到真实脚本目录。配置必须由 prelude 注入：
   `globalThis.__CAPTURE_ENV = {CONFIG:"…", LIMIT:"12", TAG:"c0", FRESH:"true"}`。
   同理：**空间归属**必须是 `agent`，属用户的要先问用户再 `claimTaskSpace(<id>)`，不能自己抢。
1. **抖音视频页用 `domcontentloaded`，绝不用 `load`**：视频流持续加载，`load` 永远等不到，用默认 waitUntil 每条都会超时才返回。
2. **单线程，绝不并发**：曾为提速开 3 标签页并发，立刻触发风控（连续 `ERR_ABORTED` + 验证码成簇），并连累整个会话限流。全程单线程低频。
3. **渲染进程约 15 条后劣化**：同一次会话跑十几条后，单条从 2 秒涨到 60–300 秒。解法 = `goto` 改 `domcontentloaded` + **每个分片重建一次标签页**（`fresh:true`）。
4. **goto 完不能立刻截**：详情页是客户端渲染，`domcontentloaded` 后播放器/描述区还要再渲染一拍；
   还会偶发整页停在「加载中」。现在会 best-effort 等 `video/[data-e2e=…]` 节点，再轮询正文，
   卡住就 `reload()` 重试（最多 3 轮），最后才截图。代价：单条 2 秒 → 约 6–8 秒。
5. **`ok` 里出现「你要观看的视频不存在」不是失败**：原视频被作者删了，截图正是要留的证据。
6. **进度判据用 `remaining` 不是 `pending`**：`pending` 被 `limit` 截断、每片恒等于 `CHUNK`，
   拿它做「无进展就停」会第二片就误停。
7. **重试要封顶**：只跳过 `ok` 会让长期失败的条目每片重抓、无限循环；现在按 `max_attempts` 封顶。
8. **重建页面前要等 600ms 并探活**：`close()` 之后 `task.pages()` 还会短暂返回死页，
   直接取第一个会连续报 `page pNN was closed`。
9. **URL 改写**：`/share/video/{id}` → `/video/{id}`（`/share/video/` 返回「验证码中间页」）；但 `links_manifest.json` 与 Word 里保留原始 share 链接不改动。
10. **关闭登录弹窗取面积最大的 `.douyin_login_new_class`**：页面里有两个，只关面积最大的那个，再向上找全屏 `position:fixed` 祖先设 `display:none`。
11. **验证码判定只看标题 / 正文特定词**（`验证码中间页`、`请完成下列验证`、`拖动完成上方拼图`、`人机验证`、`安全验证`），**不要用 `/验证码/` 匹配全文**——登录弹窗里有「验证码登录」会误报。
12. **每条加超时看门狗，超时后必须重置页面**（`about:blank`），否则会污染后续条目。
13. **断点续跑**：结果逐条 append 到 `results*.jsonl`；重跑时已处置（`ok` 或达 `max_attempts`）的 item_id 跳过。
14. **编号是全局连续**（跨多份 Word 拼起来 1..N），`verify_docx.py` 按每份自身首个编号校验连续性并额外做全量无空洞校验——不要把校验写成「每份从 1 开始」。
15. **macOS 无 `timeout` 命令**：本包脚本均不依赖它；超时统一用 Ego 环境内的 Promise 竞赛看门狗实现。
16. **长任务必须后台跑**：前台命令约 120 秒会被 SIGTERM 杀掉。用 `nohup … &` 或后台任务，
    日志重定向到文件再 `tail`。
17. **评论截图：绝不「跨屏跳转」到目标评论**。直接滚到第 N 条时该条常还没渲染（列表按滚动
    增量渲染），或滚动量算不准使它落在容器视口外，clip 变成无效矩形 → 一条图都出不来
    （3 条测试里 2 条复现）。必须**顺序推进 + 择屏**：跟着滚，该屏含恶意评论才截图。
18. **SPA 会偶发停在首页/推荐流**：正文是「精选 推荐 AI抖音 关注…视频数据加载中」，
    长度正常、会被误判为 `ok`，整条样本报废且不会重试。现在加「详情页要素探测」
    （`video` / `data-e2e=detail-video-desc` / `feed-comment-icon` 都没有 → `detail_not_loaded`）。
19. **展开评论面板要三种手段 + 多轮**：① 官方 `[data-e2e=feed-comment-icon]` 真实点击
    ② 「评论(N)」文字节点的可点击祖先 ③ 滚一屏让入口现身；面板展开最多重试 3 轮
    （只重试 1 轮会把「点一次没展开」误判成「无评论」）。
20. **评论加载「连续 2 轮无新增」才算到底**：只判 1 轮时，面板刚展开、评论还在陆续渲染，
    会提前收手——实测同一条视频一次扫到 23 条、一次只扫到 5 条。
21. **同一链接会重复收录**：引擎已按链接去重（`meta.unique_records` / `duplicate_rows`），
    `prepare_manifest.py` 是幂等兜底 + 提供「保留重复条但序号留空」备选口径。
    重复条不抓，能省下等量的风控额度。
22. **重写 Excel 会丢公式缓存值**：URL 列若是 `=HYPERLINK(...)`，原表带缓存值、引擎能读到；
    另存/重写成新 xlsx 后缓存丢失 → URL 为空、取样为空、Word 一条不出。重写必须
    `load_workbook(..., data_only=True)` 读**值**再写。

---

## 五、apply_adaptations.py 适配明细（幂等，重复执行不重复注入）

- 列名别名 `COLUMN_ALIASES`：`标题/微博内容←标题`、`原文/评论链接←URL`、`原文作者←作者`，`序号/来源网站/摘要` 直通；`meta` 输出 `column_alias_map` 便于对账。
- `ID_COLUMN_ALIASES = ["编号","新编","新编号","id","ID"]` + `--id-min` / `--id-max` 区间筛选；
  `meta` 输出 `in_range_rows` / `out_of_range_rows`。别名里带「新编/新编号」是因为部分导出表
  把编号列命名为「新编」（重排后的新编号），旧别名只有「编号/id/ID」会找不到区间列直接报错退出。
- `--title`：批次标题追加区间标签，并在标题下加一行 `本批编号范围：X — Y　共 N 条`
  （MINIMAL_LAYOUT_V1 下整段不渲染，`--title` 仍接受用于标记与对账）。
- `Record.sheet_id`（存编号）；元信息行改为 `（编号400 · 原序号1234 · 抖音 · @作者）`；写入 `links_manifest.json`
  （MINIMAL_LAYOUT_V1 下元信息行不落版到 Word，仅进清单与日志）。
- **去防御性表述**：删除仓库为所有视频平台链接追加「视频画面及完整语音需人工核验。」的两处注入点
  （`_keyword_classify` + `classify_harm`），并注入 `DEFENSIVE_PHRASES` + `strip_defensive()` 兜底清洗
  （大模型路径若产出同类措辞也会被剔除）。判定理由保持断言式，不做免责对冲。
- **本案关键词扩展（数据驱动，可选）**：注入 `CASE_KEYWORD_FILE` 机制 —— 用一份 JSON
  合并/覆盖 `REASON_PATTERNS` 的 `words`、覆盖各类 `note`、并替换「未命中」时的兜底文案。
  关键词是**按案件定制**的数据，不该写死在通用补丁里，所以补丁只注入机制、数据独立成文件。
  不提供 JSON 时行为与仓库原版完全一致；生效类别会写进 `meta.case_keyword_applied` 便于对账。
  用法：`cp case_keywords.example.json <ENG>/scripts/case_keywords.json`（或用 `CASE_KEYWORD_FILE=<路径>`）。
- **一条样本可插多张图**：按 manifest 的 `images:[...]` 循环插入（评论截图一条多张），
  插入宽度按 PNG 像素宽自适应（窄图放大、宽图封顶 16.6cm）；仍兼容单张 `screenshot_path` 的老 manifest。
- **截图缺失处留空白**：删掉「截图待补：<原因>」「⚠️ 导航错误」这类占位文案，正文只留空白、
  不写任何说明/免责文字。**这是「交付优先」原则的实现基础** —— 抓不到图不阻塞 Word 生成。
- **判定方式可切换** `CLASSIFY_MODE=auto|keyword|llm`：显式 `keyword` 完全绕开带图大模型
  （零 API 成本，也不会盖掉本案关键词判定）；不设时若已加载关键词文件则自动 `keyword`，否则 `auto`。
- **`meta` 对账字段**：`column_alias_map` / `in_range_rows` / `out_of_range_rows` / `case_keyword_applied`。
- **极简交付版式 MINIMAL_LAYOUT_V1**：每条仅「`N.标题`（加粗 1F4E79 12pt，32 字截断加「…」）/
  链接（0B60B0 9.5pt）/ 截图（横版 ≤16.0cm、竖版按高度 20.5cm 限幅）」三段；删除批次大标题、
  「本批编号范围」行、元信息行、【概括】、原文全文与【违规要素】判定框。判定结果仍写
  `处理日志.csv` 对账。适配 3b/4c/5a 在此版式下自动跳过（守卫 `MINIMAL_LAYOUT_V1`）。

已适配的文件会先检测标记，全部命中则打印「已包含所有适配，未做任何改动」并退出；新 clone 上缺失的锚点会给出清晰报错与应手动添加的代码，不会静默出错。

> **仓库侧现状**：`youhaiyangben/skills/harmful_sample/scripts/excel_to_word.py` 已预置 10 类适配；
> 第 11 类（极简版式）由 `apply_adaptations.py` 在第 ① 步对每个新 clone 自动补齐（幂等，
> 仓库后续同步该适配后此步即输出「已包含所有适配」）。要改适配逻辑请改本脚本
> `apply_adaptations.py` 再重跑，不要直接手改仓库脚本——手改会让「注入 + 手改」混杂，换 clone 后无法复现。
>
> 上层口径：**交付优先** —— 只要 Word 产出了就算交付成功，验证码/缺图是设计内降级路径，
> 不是失败，也不要停下来等人（见 SKILL.md「交付优先原则」）。
