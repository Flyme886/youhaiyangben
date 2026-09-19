# douyin-harmful-sample · scripts 使用说明

把「抖音监测 Excel → 舆情有害样本 Word 批量文档」这条真实跑通的任务，整理成可直接复用的脚本包。
本目录 7 个文件配合仓库 `Flyme886/youhaiyangben` 的 `skills/harmful_sample` 技能链使用。

环境（与本任务一致）：
- Python：`~/.workbuddy/binaries/python/envs/default/bin/python`（需装 python-docx / openpyxl / Pillow / numpy / requests）
- Ego Lite CLI：`~/.local/bin/ego-browser`
- 系统 macOS + zsh，**没有 `timeout` 命令**，脚本均不使用它

---

## 一、7 个文件分别干什么

| 文件 | 作用 |
|---|---|
| `apply_adaptations.py` | 【一次性】把新 clone 的仓库 `excel_to_word.py` 打上本任务做的 5 类适配（列名别名 / 编号区间筛选 / 批次标题与编号范围行 / 元信息加编号 / 去防御性表述；内部拆成 15 个注入块）。幂等。 |
| `capture_douyin.js` | 由 `ego-browser nodejs <` 执行，单条抓取抖音视频页截图，结果逐条 append 到 `results*.jsonl`。配置驱动。 |
| `run_capture.sh` | 分片驱动上面的抓取：每片 10–12 条、每片重建标签页、可断点续跑。 |
| `build_manifest.py` | 合并 `links_manifest.json` + 所有 `results*.jsonl`，输出仓库要的 `screenshots_manifest.json`。 |
| `verify_docx.py` | 生成后结构校验：条目数、全局连续编号、图片数、违规要素框数、元信息格式、无「举报理由草稿」、**无防御性表述**（`defensive=N` 须为 0）。 |
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

# ① 一次性：给新 clone 的 excel_to_word.py 打适配（已打过则自动跳过）
$PY "$SHEETS/apply_adaptations.py" "$ENG/scripts/excel_to_word.py"
# ② 取样 + 列映射/区间校验，并写出 links_manifest.json
$PY "$ENG/scripts/excel_to_word.py" "$XLSX" \
  --output-dir "$OUTDIR" --sheet 信息 --id-min 400 --id-max 900 --dry-run

# ③ 截图（Ego Lite；先 cp capture_config.example.json capture_config.json 并填好三个绝对路径）
CHUNK=12 CONFIG="$SHEETS/capture_config.json" EGO_BROWSER="$EGO" \
  "$SHEETS/run_capture.sh"

# ④ 合并抓取结果 -> screenshots_manifest.json
$PY "$SHEETS/build_manifest.py" \
  --links "$OUTDIR/links_manifest.json" \
  --results-dir "$OUTDIR/../_ego" \
  --shots-dir "$OUTDIR/screenshots" \
  --output "$OUTDIR/screenshots/screenshots_manifest.json"

# ⑤ 截图质检
$PY "$ENG/scripts/quality_check_screenshots.py" \
  "$OUTDIR/screenshots/screenshots_manifest.json"

# ⑥ 生成 Word 批次（仓库默认保守关键词判定；未配 DEEPSEEK_API_KEY 自动回退）
$PY "$ENG/scripts/excel_to_word.py" "$XLSX" \
  --output-dir "$OUTDIR" --sheet 信息 --id-min 400 --id-max 900 \
  --title "编号400-900" --batch-size 50 \
  --screenshots-manifest "$OUTDIR/screenshots/screenshot_quality_manifest.json"

# ⑦ 结构校验
$PY "$SHEETS/verify_docx.py" "$OUTDIR"
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
  "watchdog": {
    "goto": 24000, "nav": 18000, "settle": 1300, "hide": 5000,
    "probe": 5000, "extract": 10000, "shot": 20000, "reset": 10000
  }
}
```

字段也可用环境变量单键覆盖：`CAPTURE_MANIFEST` / `CAPTURE_SHOTS` / `CAPTURE_RESULTS` /
`CAPTURE_TAG` / `CAPTURE_LIMIT` / `CAPTURE_SPACE` / `CAPTURE_FRESH`。
`run_capture.sh` 每片会自动注入 `limit=CHUNK`、`fresh=true`、`tag=每片自增`，续跑靠 capture 自身的 ok 跳过逻辑。

---

## 四、不要重复踩的坑（已验证结论）

1. **抖音视频页用 `domcontentloaded`，绝不用 `load`**：视频流持续加载，`load` 永远等不到，用默认 waitUntil 每条都会超时才返回。
2. **单线程，绝不并发**：曾为提速开 3 标签页并发，立刻触发风控（连续 `ERR_ABORTED` + 验证码成簇），并连累整个会话限流。全程单线程低频。
3. **渲染进程约 15 条后劣化**：同一次会话跑十几条后，单条从 2 秒涨到 60–300 秒。解法 = `goto` 改 `domcontentloaded` + **每个分片重建一次标签页**（`fresh:true`），稳定回到约 2 秒/条。
4. **URL 改写**：`/share/video/{id}` → `/video/{id}`（`/share/video/` 返回「验证码中间页」）；但 `links_manifest.json` 与 Word 里保留原始 share 链接不改动。
5. **关闭登录弹窗取面积最大的 `.douyin_login_new_class`**：页面里有两个，只关面积最大的那个，再向上找全屏 `position:fixed` 祖先设 `display:none`。
6. **验证码判定只看标题 / 正文特定词**（`验证码中间页`、`请完成下列验证`、`拖动完成上方拼图`、`人机验证`、`安全验证`），**不要用 `/验证码/` 匹配全文**——登录弹窗里有「验证码登录」会误报。
7. **每条加超时看门狗，超时后必须重置页面**（`about:blank`），否则会污染后续条目。
8. **复用 space 内已有页面**（`task.pages()` 取第一个），不要每次 `newPage()`：连续 close 再 newPage 会触发页面账本冲突（报 `is already page pNN`）。
9. **断点续跑**：结果逐条 append 到 `results*.jsonl`；重跑时 `status=="ok"` 的 item_id 全部跳过。限流/冷却后直接重跑 `run_capture.sh` 即可接着抓。
10. **编号是全局连续**（跨 11 份 Word 拼起来 1..501），`verify_docx.py` 按每份自身首个编号校验连续性并额外做全量无空洞校验——不要把校验写成「每份从 1 开始」。
11. **macOS 无 `timeout` 命令**：本包脚本均不依赖它；超时统一用 Ego 环境内的 Promise 竞赛看门狗实现。

---

## 五、apply_adaptations.py 适配明细（幂等，重复执行不重复注入）

- 列名别名 `COLUMN_ALIASES`：`标题/微博内容←标题`、`原文/评论链接←URL`、`原文作者←作者`，`序号/来源网站/摘要` 直通；`meta` 输出 `column_alias_map` 便于对账。
- `ID_COLUMN_ALIASES = ["编号","id","ID"]` + `--id-min` / `--id-max` 区间筛选；`meta` 输出 `in_range_rows` / `out_of_range_rows`。
- `--title`：批次标题追加区间标签，并在标题下加一行 `本批编号范围：X — Y　共 N 条`。
- `Record.sheet_id`（存编号）；元信息行改为 `（编号400 · 原序号1234 · 抖音 · @作者）`；写入 `links_manifest.json`。
- **去防御性表述**：删除仓库为所有视频平台链接追加「视频画面及完整语音需人工核验。」的两处注入点
  （`_keyword_classify` + `classify_harm`），并注入 `DEFENSIVE_PHRASES` + `strip_defensive()` 兜底清洗
  （大模型路径若产出同类措辞也会被剔除）。判定理由保持断言式，不做免责对冲。

已适配的文件会先检测标记，全部命中则打印「已包含所有适配，未做任何改动」并退出；新 clone 上缺失的锚点会给出清晰报错与应手动添加的代码，不会静默出错。
