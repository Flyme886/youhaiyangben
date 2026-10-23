# 评论截图模式（capture_douyin_comments.js）

上一版只截「视频页一张图」，用来说明该条视频存在。**反馈要求**：每条样本要截到**相关恶意评论**
（口径从宽：稍微带一点点恶意的都算）。于是有了这条链路。

2026-09-22 业务口径更新（三条，务必先看）：

1. **评论区只出 2–3 张恶意评论图**（不是把前 20 条评论铺满几屏）。→ `comments.max_shots = 3`
2. 「前 20 条」指的是**每批 20 条帖子**，不是单个视频的前 20 条评论。
   → 由 `prepare_manifest.py` 给清单打 `capture_comments` 标记，抓取脚本据此决定是否开评论区。
   默认先 `--comment-scope all` 全量跑测限流；限流严重再切 `first_n`（每批 50 条取前 20 条抓评论，
   其余只截视频页）。
3. **同一链接去重后再抓取**：由 `prepare_manifest.py` 前置处理（引擎其实已按链接去重，
   脚本是幂等兜底 + 提供「保留重复条但序号留空」备选口径）。

## 一、产出形态

一条样本产出 **1–3 张**图：

| 情况 | 产出 | capture_kind |
|---|---|---|
| 有恶意评论 | **只截含恶意评论的那几屏，最多 `max_shots`(=3) 张**；每张都含至少 1 条恶意评论 | `comments` |
| 有评论但一条恶意都没有 | 退回普通评论：前 `plain_per_sample`(=6) 条，最多 `plain_max_shots`(=2) 张 | `comments` |
| 清单标记 `capture_comments: false` | 不开评论区，只截视频页 1 张 | `video_only` |
| 0 条评论（页面显示「抢首评」） | 退回截视频页 1 张 | `no_comments` |
| 视频已被删（「你要观看的视频不存在」） | 截视频页 1 张 | `video_gone` |
| 评论面板存在但截图失败 | 退回截视频页 1 张 | `video_page` |

## 二、抖音评论区 DOM 实测（2026-09-22，勿删）

**两种布局，评论面板都可能默认折叠**：

- **A 侧栏布局**：视频在左，评论区是右侧固定侧栏（实测 `x≈845, w=301, 高≈502`）。
- **B 通栏布局**：视频在上、评论在下方，滚动容器是 `.route-scroll-container`，
  评论列表 `[data-e2e=comment-list]` 宽 ≈796。

两种布局下评论条目节点都是 **`[data-e2e="comment-item"]`**，但 DOM 里同时存在一份
「不可见的预渲染副本」——`getBoundingClientRect()` 全 0。**必须按 `rect.width > 50`
过滤**，否则解析到隐藏副本，裁剪框全 0，裁不出图。

单条评论内部结构（class 是哈希名，可能变，已在脚本里写了退化路径）：

| 字段 | 选择器 | 退化方案 |
|---|---|---|
| 正文 | `.FduGc_lz` | 整条 innerText 剔除作者前缀 + 尾部「分享 回复」+ 时间地域 |
| 作者 | `.vlTNLkdG` | — |
| 时间·地域 | `.VAQA49VP` | — |
| 点赞/分享/回复 | `.Ctt1baq8` | — |

**滚动容器**：从可见条目向上找第一个 `overflowY ∈ {auto,scroll}` 且
`scrollHeight > clientHeight` 的祖先（不一定同一个元素，**别写死**）。
找不到（例如只有 1–2 条评论、容器不满屏）时**必须退回「视口即容器」**——
否则 screen 循环因 root 为空直接 break，单条样本一张图都产不出来（踩过）。

**展开评论面板的三个手段**（按可靠性排序，脚本里依次尝试）：

1. `page.click('[data-e2e="feed-comment-icon"]')` —— 官方评论图标，真实鼠标事件最可靠；
2. 点「评论(N)」文字节点的可点击祖先（`role=button` / `BUTTON`，向上最多 4 层）；
3. 都失败 → `window.scrollBy(0, 700)` 滚一屏让入口现身，主流程下一轮再试。

面板展开最多重试 **3 轮**（每轮 +1.2s 等待），只重试 1 轮时实测会把「点一次没展开」的
页面误判成「无评论」，整条只剩视频页图。

## 三、截图方式

用 `page.screenshot({ path, clip:{x,y,width,height} })`，clip 用 **CSS 像素、相对视口**。

- `clip` 的形状必须是 `{x,y,width,height}`；传 `{x,y,w,h}` 或数组会报
  `page.screenshot clip must be clip`。**width/height 为 0 也会报同一个错**，
  所以算出来要先判 `>= 40` 再截。
- 每屏只裁「该屏完全可见的评论条目」的并集框（`pad` 默认 4px），相邻屏基本无重叠。

> ⚠️ **不要「跨屏跳转」到目标评论**（2026-09-22 实测踩坑）：直接滚到第 N 条时，
> 该条往往还没渲染（列表按滚动增量渲染），或滚动量算不准导致它落在容器视口外，
> 裁切区变成无效矩形 → 整条样本一张评论图都出不来（3 条测试里 2 条复现）。
> **正确做法：顺序推进 + 择屏** —— 跟着滚动一屏一屏走，该屏含恶意评论才截图。
> 每屏最多 3 张，最多扫 `max_shots*6+12` 屏止损。

## 四、「优先恶意」的判定与窗口

词表在 `comment_malice_keywords.json`（strong=3 / medium=2 / weak=1，
`malicious = score >= min_malice_score`，默认 1 —— 命中任意一词即算「带恶意」）。

1. 扫描并给评论打分（收集前 `max_scan`(=60) 条，加载不满时靠 `load_rounds` 多滚几轮）；
2. 取恶意评论下标 `malIdx`；
3. **有恶意评论** → 窗口 = `[min(malIdx) - malice_context, max(malIdx) + 1 + malice_context]`，
   再补齐到至少 `malice_min_cover`(=6) 条（否则「只有 1 条恶意评论」时窗口只有 1 条、
   只出 1 张极小的图）；截图上限 `max_shots`(=3)，**只截含恶意评论的屏**；
4. **全域无恶意** → 窗口 = 前 `plain_per_sample`(=6) 条，上限 `plain_max_shots`(=2) 张。

> ⚠️ 评论加载轮次判断：必须**连续 2 轮没有新评论**才收手。只判 1 轮会在「面板刚展开、
> 评论还在陆续渲染」时提前退出——实测同一条视频一次扫到 23 条、一次只扫到 5 条。

> ⚠️ 词表路径解析：ego 的 node 跑在 app 侧，stdin 执行时 `import.meta.url` 是 `/`，
> **不能只靠 SCRIPT_DIR 推词表路径**，否则词表静默为空、所有评论 score 全 0（踩过）。
> 解析顺序：`comments.keywords_file` > 配置文件同目录 > 脚本同目录 > 内置兜底最小词表，
> 并在日志里打印 `malice_keywords_loaded …` 以便确认。

## 五、清单前置：去重 + 批次标记（prepare_manifest.py）

放在「引擎产出 links_manifest.json」之后、「Ego 抓取」之前：

```bash
PY=~/.workbuddy/binaries/python/envs/default/bin/python
# 先全量测限流（全部条目都抓评论）
$PY prepare_manifest.py --in "$OUTDIR/links_manifest.json" \
    --out "$OUTDIR/links_manifest.json" --drop-dups --comment-scope all
# 限流严重 → 降级：每批 50 条里只有前 20 条抓评论，其余只截视频页
$PY prepare_manifest.py --in "$OUTDIR/links_manifest.json" \
    --out "$OUTDIR/links_manifest.json" --drop-dups \
    --comment-scope first_n --batch-size 50 --comments-per-batch 20
```

- 去重口径：`--drop-dups`（默认，重复条不输出）/ `--keep-dups`（重复条保留、
  `original_no` 留空、`capture_comments=false`、出 Word 时复用首条截图）。
  被处理的重复条会写进 `去重剔除记录.csv`。
- 批次口径按清单顺序切，`--batch-size` 必须与引擎 `--batch-size` 一致（默认都是 50）。
- 抓取脚本读清单里的 `capture_comments`：`false` → 不开评论区，只截视频页（`video_only`）。
- 脚本幂等；切换 `--comment-scope` 属于预期重写。**降级不需要重抓已完成的条目**
  （断点续跑按 `item_id` 跳过 `ok` 的）。

## 六、Word 侧：一条多图

`build_manifest.py` 把抓取结果的 `screenshots:[{file,…}]` 摊平成 `images:[文件名…]`
（并保持 `screenshot_path` = 第一张，兼容老逻辑）。

`apply_adaptations.py` 的**适配 7** 让引擎按 `images` 循环插入，插入宽度按图片像素宽自适应：

```
width_cm = clamp(图片像素宽 × 0.030, 8.5, 16.6)
```

- 评论侧栏图 ≈300px → 约 **9.0cm**，评论正文字号约 **4.2mm**（A4 上清晰可读）
- 通栏评论图 ≈790px → 封顶 **16.6cm**（左右各 2cm 页边距时的可用宽度），字号约 **2.9mm**
- 视频页兜底图 ≈1190px → 也是 16.6cm

> 固定 13.5cm 的写法对 300px 宽的评论区图是不够的——那等于把窄图硬塞，
> 字号只有约 1.8mm，评论根本认不出来。所以必须按像素宽自适应。

## 七、风控与耗时

- 单条要「展开评论面板 + 滚动加载 + 择屏截图」，实测 **15–30 秒/条**；
  只截 2–3 张后比上一版（按屏铺满 5–10 张）略快。
- `run_capture_comments.sh` 的 `CHUNK` 默认 **6**（不是 12），片间 `fresh=true` 重建标签页，
  无进展时自动冷却重试（`COOLDOWN`/`MAX_COOLDOWNS`）。
- 断点续跑：`status=="ok"` 的 `item_id` 自动跳过；坏了重跑同一个文件即可接着抓。
- **先小批实测限流**：跑 20–30 条看 `consecutiveBad` 与 `ERR_CONNECTION_*` 出现频率，
  再决定是否全量 / 是否切 `first_n` 降级。

## 八、原表列名的两个坑（这份 700+ 表踩过）

1. **编号列叫「新编」**：引擎 `ID_COLUMN_ALIASES` 原来只有 `["编号","id","ID"]`，
   会因找不到区间列直接报错退出。已加适配 2c 扩展为
   `["编号","新编","新编号","id","ID"]`（`apply_adaptations.py`，幂等）。
2. **URL 列是 `=HYPERLINK(...)` 公式**：原表带缓存值，引擎用 `data_only` 能读到；
   但**如果你把表另存/重写成新 xlsx，缓存值会丢**，引擎读到的 URL 会是空
   → 取样为空、Word 一条都出不来（踩过）。要重写表必须用 `data_only=True` 读出**值**再写。
