#!/usr/bin/env python
"""幂等地把一份「新 clone」的仓库 excel_to_word.py 打上本任务所需的全部适配。

适配对照源：engine/skills/harmful_sample/scripts/excel_to_word.py（已改好版本）。
本脚本不直接引用 engine 路径，而是把需要的改动以「标记+字符串替换」的形式注入，
因此可对任意一份符合上游结构的 fresh clone 复用。

适配清单（共 11 类行为，脚本内部拆成 28 个幂等注入块，按顺序执行；
编号与 SKILL.md「apply_adaptations.py 注入的适配」一节保持一致）：
  1) 列名别名映射：COLUMN_ALIASES（标题/微博内容←标题、原文/评论链接←URL、原文作者←作者、
     序号/来源网站/摘要 直通），并在 meta 输出 column_alias_map 便于对账。
  2) 编号区间筛选：ID_COLUMN_ALIASES = ["编号","新编","新编号","id","ID"] + --id-min/--id-max，
     meta 输出 in_range_rows / out_of_range_rows。
  3) 批次标题：--title 追加区间标签，并在标题下加一行
     `本批编号范围：X — Y　共 N 条`（title_extra 形参一路贯通到 build_docx_batches）。
  4) 元信息加编号：Record 增加 sheet_id 字段（存编号），元信息行改为
     `（编号400 · 原序号1234 · 抖音 · @作者）`，并写入 links_manifest.json。
  5) 去防御性表述：删掉仓库给所有视频平台链接追加「视频画面及完整语音需人工核验。」的
     两处注入点（关键词路径 + classify_harm），并注入 DEFENSIVE_PHRASES + strip_defensive
     兜底清洗（也能挡住大模型路径自带的同类对冲措辞）。判定理由保持断言、不做免责对冲。
  6) 本案关键词扩展（数据驱动，可选）：注入 CASE_KEYWORD_FILE 机制 —— 用一个 JSON 文件
     覆盖/追加 REASON_PATTERNS 并替换兜底文案，便于按案件定制判定关键词，而**不改动**上游
     通用规则。不提供 JSON 时行为与上游完全一致。JSON 结构见 case_keywords.example.json。
  7) 一条样本可插多张图：按 manifest 的 images:[...] 循环插入（评论截图一条多张），
     插入宽度按图片像素宽自适应（窄图放大、宽图封顶 16.6cm），保证评论文字可读。
     仍兼容只有单张 screenshot_path 的老 manifest。
  8) 截图缺失时正文留空白：删掉「截图待补」「⚠️ 导航错误」这类占位文案，只留空白，
     不写任何说明/免责文字（采集状态完整保留在 处理日志.csv / 截图状态汇总.csv）。
     **这一条是「交付优先」原则的实现基础**：抓不到图不等于交付失败，文字照交。
  9) 判定方式可切换 CLASSIFY_MODE=auto|keyword|llm（默认 auto 即上游行为）：
     云端配了 DEEPSEEK_API_KEY 时，上百上千条样本会逐条真调带图大模型，既慢又贵，
     还会盖掉本案定制关键词的判定。显式设 keyword 即可只走关键词、零 API 成本。
     未显式设置时，若已加载本案关键词文件则自动用 keyword，否则沿用 auto。
 10) 对账字段：meta 里补齐 column_alias_map / in_range_rows / out_of_range_rows /
     case_keyword_applied，供全量跑完之后逐项核对「适配到底生效了没有」。
 11) 极简交付版式 MINIMAL_LAYOUT_V1（对齐用户参考样例「王振义-7批-20条」）：
     每条样本只保留三段 —— ① `N.标题`（N 为条目顺序号，标题取自表格内容、
     换行折叠成空格，超过 32 字截断加「…」，加粗、深蓝 1F4E79、12pt）；
     ② 原始链接（蓝 0B60B0、9.5pt）；③ 截图（横版最宽 16.0cm，竖版按
     高度 20.5cm 限幅等比缩放）。删除批次大标题、「本批编号范围」行、
     元信息行（编号·原序号·来源·@作者）、【概括】、原文全文段与
     【违规要素】判定框。判定结果仍照常计算并写入 处理日志.csv 供对账，
     只是不再落版。Word 正文除标题/链接/图片外**不得出现任何说明文字**。

幂等性：每步先检测标记，已存在则跳过，重复执行不会重复注入。
若某步锚点在新 clone 上找不到，抛出清晰报错（含应手动添加的代码），不会静默出错。

用法：
  python apply_adaptations.py <path/to/fresh/excel_to_word.py>
  APPLY_TARGET=<path> python apply_adaptations.py
未给路径则读环境变量 APPLY_TARGET；默认 ./excel_to_word.py。
"""

from __future__ import annotations

import os
import re
import sys

# ── 已改好版本的 read_records 完整函数体（适配 1+2 的核心） ──────────────────
# 仅当目标文件里 read_records 尚未包含 range_active 时才整体替换，保证幂等。
ADAPTED_READ_RECORDS = '''def read_records(
    excel_path: Path,
    sheet_name: str | None,
    id_min: int | None = None,
    id_max: int | None = None,
) -> tuple[list[Record], list[RowLog], dict[str, Any]]:
    workbook = load_workbook(excel_path, read_only=True, data_only=True)
    worksheet = pick_sheet(workbook, sheet_name)
    rows = worksheet.iter_rows(values_only=True)
    try:
        header_row = next(rows)
    except StopIteration:
        raise SystemExit("Workbook sheet is empty.")

    headers = [as_text(v) for v in header_row]
    raw_index = {name: i for i, name in enumerate(headers) if name}

    # 通过别名把实际列名解析到脚本使用的规范列名
    index: dict[str, int] = {}
    alias_hits: dict[str, str] = {}
    for canonical, aliases in COLUMN_ALIASES.items():
        for alias in aliases:
            if alias in raw_index:
                index[canonical] = raw_index[alias]
                alias_hits[canonical] = alias
                break

    missing = [name for name in REQUIRED_COLUMNS if name not in index]
    if missing:
        raise SystemExit(
            "Missing required column(s): " + ", ".join(missing)
            + " | workbook headers: " + ", ".join(headers)
        )

    id_pos: int | None = None
    for alias in ID_COLUMN_ALIASES:
        if alias in raw_index:
            id_pos = raw_index[alias]
            break

    range_active = id_min is not None or id_max is not None
    if range_active and id_pos is None:
        raise SystemExit(
            "--id-min/--id-max requires an id column; none of "
            + ", ".join(ID_COLUMN_ALIASES) + " found in headers."
        )

    seen: dict[str, int] = {}
    records: list[Record] = []
    logs: list[RowLog] = []
    data_rows = 0
    in_range_rows = 0
    out_of_range_rows = 0

    def cell(row: tuple[Any, ...], column: str) -> str:
        if column not in index:
            return ""
        pos = index[column]
        if pos >= len(row):
            return ""
        return as_text(row[pos])

    for offset, row in enumerate(rows, start=2):
        if row is None or all(is_blank(as_text(v)) for v in row):
            continue
        data_rows += 1

        if range_active:
            raw_id = row[id_pos] if id_pos < len(row) else None
            try:
                id_value = int(str(raw_id).strip())
            except (TypeError, ValueError):
                out_of_range_rows += 1
                continue
            if (id_min is not None and id_value < id_min) or (id_max is not None and id_value > id_max):
                out_of_range_rows += 1
                continue
        in_range_rows += 1

        link = cell(row, "原文/评论链接")
        if is_blank(link):
            logs.append(RowLog(offset, "", "skipped_missing_link", "", "", "", "", "missing link"))
            continue
        if link in seen:
            logs.append(RowLog(offset, link, "duplicate", "", seen[link], "", "", "duplicate link"))
            continue

        item_id = len(records) + 1
        seen[link] = item_id
        preferred_author = cell(row, "转发/评论作者")
        fallback_author = cell(row, "原文作者")
        author = preferred_author if not is_blank(preferred_author) else fallback_author
        record = Record(
            item_id=item_id,
            source_row=offset,
            original_no=cell(row, "序号") or str(offset),
            source=cell(row, "来源网站"),
            title=cell(row, "标题/微博内容"),
            author=author,
            link=link,
            # 只要表里存在编号列就记下来 —— 不能写成 `range_active and …`：
            # 那样「不做区间筛选（不传 --id-min/--id-max）」的全量跑会把 sheet_id 全留空，
            # 元信息退化成「（原序号1 · 抖音 · @作者）」，编号整段消失（实测 1057 条全量跑即如此）。
            # 编号是否落在区间内与「这条记录带不带编号」是两回事。
            sheet_id=(str(row[id_pos]).strip() if id_pos is not None and id_pos < len(row) else ""),
            summary=cell(row, "摘要"),
            keywords=cell(row, "涉及词"),
            industry=cell(row, "行业一级"),
            category=cell(row, "行业二级"),
        )
        records.append(record)
        logs.append(RowLog(offset, link, "included", item_id, "", "", "not_captured", "not captured"))

    meta = {
        "excel_path": str(excel_path),
        "sheet": worksheet.title,
        "id_range": [id_min, id_max] if range_active else None,
        "total_data_rows": data_rows,
        "in_range_rows": in_range_rows,
        "out_of_range_rows": out_of_range_rows,
        "unique_records": len(records),
        "duplicate_rows": sum(1 for log in logs if log.status == "duplicate"),
        "skipped_missing_link": sum(1 for log in logs if log.status == "skipped_missing_link"),
        "column_alias_map": alias_hits,
    }
    return records, logs, meta
'''


# ── 适配 7 注入的「本案关键词扩展」代码块（数据驱动，可选） ────────────────────
# 关键词本身不写在这里，而是放在 CASE_KEYWORD_FILE（或脚本同目录 case_keywords.json）。
# 这样：补丁脚本保持通用，本案数据独立成文件，换 clone / 换案件都可复现。
CASE_KEYWORD_BLOCK = '''# ── 本案关键词扩展（数据驱动，可选；不改动上方通用规则） ─────────────────────
# 判定关键词是按案件定制的：用 CASE_KEYWORD_FILE 指向的 JSON 覆盖/追加 REASON_PATTERNS，
# 并可按案件替换「未命中」时的兜底文案。不提供 JSON 时行为与上游完全一致。
# JSON 结构：{"patterns": [{"label": "…", "note": "…", "words": ["…"]}],
#             "fallback": {"label": "…", "note": "…"}}
CASE_KEYWORD_FILE = os.environ.get("CASE_KEYWORD_FILE", "")
CASE_KEYWORD_APPLIED: list[str] = []
CASE_FALLBACK_LABEL = ""
CASE_FALLBACK_NOTE = ""
_CASE_KEYWORDS_LOADED = False


def _apply_case_keywords() -> None:
    """把案件关键词 JSON 合并进 REASON_PATTERNS（幂等，只会真正执行一次）。"""
    global CASE_FALLBACK_LABEL, CASE_FALLBACK_NOTE, _CASE_KEYWORDS_LOADED
    if _CASE_KEYWORDS_LOADED:
        return
    _CASE_KEYWORDS_LOADED = True
    path = CASE_KEYWORD_FILE
    if not path:
        candidate = Path(__file__).with_name("case_keywords.json")
        path = str(candidate) if candidate.exists() else ""
    if not path or not os.path.exists(path):
        return
    try:
        with open(path, encoding="utf-8") as fh:
            payload = json.load(fh)
    except Exception:
        return
    for entry in payload.get("patterns") or []:
        label = as_text(entry.get("label", ""))
        words = [as_text(w) for w in (entry.get("words") or []) if as_text(w)]
        note = as_text(entry.get("note", ""))
        if not label or not words:
            continue
        for idx, (lbl, existed, old_note) in enumerate(REASON_PATTERNS):
            if lbl == label:
                merged = list(existed) + [w for w in words if w not in existed]
                REASON_PATTERNS[idx] = (lbl, merged, note or old_note)
                break
        else:
            REASON_PATTERNS.append((label, words, note))
        CASE_KEYWORD_APPLIED.append(label)
    fallback = payload.get("fallback") or {}
    CASE_FALLBACK_LABEL = as_text(fallback.get("label", ""))
    CASE_FALLBACK_NOTE = as_text(fallback.get("note", ""))
'''


class PatchError(Exception):
    pass

def _replace_once(source: str, old: str, new: str, what: str) -> str:
    if old not in source:
        raise PatchError(f"锚点未找到（{what}），无法注入。请手动添加：\n{new}")
    if source.count(old) > 1:
        raise PatchError(f"锚点出现多次（{what}），为避免误改已中止。请手动添加：\n{new}")
    return source.replace(old, new, 1)


def apply(source: str) -> tuple[str, list[str]]:
    applied: list[str] = []

    # ── 适配 1a：COLUMN_ALIASES 别名映射常量（在 BLANK_VALUES 行之后） ──────
    # 守卫用 "COLUMN_ALIASES: dict"（定义处的特有形态）。不能只用 "COLUMN_ALIASES"：
    # 该子串包含在 "ID_COLUMN_ALIASES" 里，会被误判为「已存在」而漏注入。
    if "COLUMN_ALIASES: dict" not in source:
        anchor = 'BLANK_VALUES = {"", "无", "none", "null", "nan"}'
        ins = (
            anchor + "\n\n"
            "# 列名别名映射（扩展，不改动原判定逻辑）：兼容「粉丝排序」类导出的列名\n"
            "COLUMN_ALIASES: dict[str, list[str]] = {\n"
            '    "标题/微博内容": ["标题/微博内容", "标题", "内容", "文本"],\n'
            '    "原文/评论链接": ["原文/评论链接", "URL", "url", "Url", "链接", "原文链接", "评论链接"],\n'
            '    "原文作者": ["原文作者", "作者", "昵称", "账号"],\n'
            '    "转发/评论作者": ["转发/评论作者", "转发作者", "评论作者"],\n'
            '    "来源网站": ["来源网站", "平台"],\n'
            '    "序号": ["序号"],\n'
            '    "摘要": ["摘要", "概括"],\n'
            '    "涉及词": ["涉及词", "关键词"],\n'
            '    "行业一级": ["行业一级", "行业", "领域"],\n'
            '    "行业二级": ["行业二级", "子行业"],\n'
            "}"
        )
        source = _replace_once(source, anchor, ins, "COLUMN_ALIASES 别名映射常量")
        applied.append("COLUMN_ALIASES 别名映射常量")

    # ── 适配 2a：ID_COLUMN_ALIASES 常量（在 CONTROL_RE 行之后） ──────────────
    if "ID_COLUMN_ALIASES" not in source:
        anchor = 'CONTROL_RE = re.compile(r"[\\x00-\\x08\\x0b\\x0c\\x0e-\\x1f\\x7f]")'
        ins = (
            anchor + "\n\n"
            "# 用于 --id-min/--id-max 区间筛选的列（粉丝排序导出为 编号；重排导出为 新编）\n"
            'ID_COLUMN_ALIASES = ["编号", "新编", "新编号", "id", "ID"]'
        )
        source = _replace_once(source, anchor, ins, "ID_COLUMN_ALIASES 常量")
        applied.append("ID_COLUMN_ALIASES 常量")

    # ── 适配 2c：ID 列别名扩展（部分导出表的编号列叫「新编」） ────────────────
    # 这类表把编号列命名为「新编」（按规则重排后的新编号），引擎原别名只有
    # ["编号", "id", "ID"]，会因找不到区间列直接报错退出。幂等：已扩展过则跳过。
    _OLD_ALIAS = 'ID_COLUMN_ALIASES = ["编号", "id", "ID"]'
    _NEW_ALIAS = 'ID_COLUMN_ALIASES = ["编号", "新编", "新编号", "id", "ID"]'
    if _OLD_ALIAS in source:
        source = source.replace(_OLD_ALIAS, _NEW_ALIAS)
        applied.append("ID 列别名扩展（新编/新编号）")

    # ── 适配 6a：DEFENSIVE_PHRASES + strip_defensive（去掉判定理由里的对冲措辞） ──
    # 仓库原文会给所有视频平台链接的判定理由追加「视频画面及完整语音需人工核验。」，
    # 这是防御性/免责表述，成品不要。这里提供短语表 + 清洗函数，供 6b/6c 调用；
    # 同时兜住大模型路径可能自带的同类措辞。
    if "DEFENSIVE_PHRASES" not in source:
        anchor = 'ID_COLUMN_ALIASES = ["编号", "新编", "新编号", "id", "ID"]'
        ins = (
            anchor + "\n\n"
            "# 判定理由中的防御性/免责措辞：统一剔除，成品不出现「需人工核验」类对冲表述\n"
            "DEFENSIVE_PHRASES = (\n"
            '    "视频画面及完整语音需人工核验。",\n'
            '    "视频画面及完整语音需人工核验",\n'
            '    "需人工核验。",\n'
            '    "需人工核验",\n'
            '    "需人工复核。",\n'
            '    "需人工复核",\n'
            '    "仅供参考。",\n'
            '    "仅供参考",\n'
            '    "请以官方通报为准。",\n'
            '    "请以官方通报为准",\n'
            '    "请以官方发布为准。",\n'
            '    "请以官方发布为准",\n'
            ")\n"
            "\n"
            "\n"
            "def strip_defensive(text: str) -> str:\n"
            '    """去掉判定理由里的防御性措辞，并归并多余空白。"""\n'
            "    for phrase in DEFENSIVE_PHRASES:\n"
            '        text = text.replace(phrase, "")\n'
            '    return " ".join(text.split()).strip()'
        )
        source = _replace_once(source, anchor, ins, "DEFENSIVE_PHRASES + strip_defensive")
        applied.append("DEFENSIVE_PHRASES + strip_defensive")

    # ── 适配 1+2b：整体替换 read_records（仅当尚未包含区间筛选逻辑） ──────────
    if "range_active" not in source:
        marker = "def read_records("
        if marker not in source:
            raise PatchError("未找到 def read_records(，结构不符，已中止。")
        start = source.index(marker)
        rest = source[start:]
        nl = rest.index("\n")
        # 找到 read_records 之后的下一个顶层 def 作为边界
        m = re.search(r"\ndef \w+\(", rest[nl:])
        if m:
            end = start + nl + m.start()
            sep = "\n\n"
            tail = source[end:]  # 以 "\ndef ..." 开头
        else:
            end = len(source)
            sep = "\n"
            tail = ""
        source = source[:start] + ADAPTED_READ_RECORDS.rstrip("\n") + sep + tail
        applied.append("read_records（列名别名 + 区间筛选 + column_alias_map）")

    # ── 适配 2d：修 sheet_id 被 range_active 误绑的老 bug（就地修复，幂等） ──────
    # 老版本的 read_records 把 sheet_id 写成
    #     if range_active and id_pos is not None and id_pos < len(row)
    # 于是「不做区间筛选」的全量跑（不传 --id-min/--id-max）会把 sheet_id 全留空，
    # 元信息退化成「（原序号1 · 抖音 · @作者）」——编号整段消失。
    # 上面 1+2b 的守卫是 `"range_active" not in source`，对已打过老补丁的文件会整块跳过，
    # 所以这里必须**单独就地替换**，否则老文件永远修不好。
    _SHEET_ID_BUGGY = (
        '            sheet_id=(str(row[id_pos]).strip()'
        ' if range_active and id_pos is not None and id_pos < len(row) else ""),'
    )
    _SHEET_ID_FIXED = (
        '            sheet_id=(str(row[id_pos]).strip()'
        ' if id_pos is not None and id_pos < len(row) else ""),'
    )
    if _SHEET_ID_BUGGY in source:
        source = source.replace(_SHEET_ID_BUGGY, _SHEET_ID_FIXED)
        applied.append("修正 sheet_id 误绑 range_active（全量跑丢失编号）")

    # ── 适配 4a：Record.sheet_id 字段 ─────────────────────────────────────
    # 注意：锚点必须带上 author 行。裸的 "    link: str\n" 在 Record 与 RowLog
    # 两个 dataclass 里都出现，会被 _replace_once 判为多处匹配而中止。
    if 'sheet_id: str' not in source:
        source = _replace_once(
            source,
            "    author: str\n    link: str\n",
            "    author: str\n    link: str\n    sheet_id: str = \"\"\n",
            "Record.sheet_id 字段",
        )
        applied.append("Record.sheet_id 字段")

    # ── 适配 4b：write_links_manifest 写入 sheet_id ───────────────────────
    if '"sheet_id": r.sheet_id' not in source:
        source = _replace_once(
            source,
            '                "item_id": r.item_id,\n',
            '                "item_id": r.item_id,\n                "sheet_id": r.sheet_id,\n',
            "links_manifest 的 sheet_id",
        )
        applied.append("links_manifest 写入 sheet_id")

    # ── 适配 3：--id-min / --id-max / --title 参数 ───────────────────────
    # 守卫必须用 argparse 注册语句来判断。用 "--id-min" 判断会误判：上面注入的
    # read_records 里本来就含 "--id-min" 字样，导致整块被跳过、参数从未注册。
    if 'parser.add_argument("--id-min"' not in source:
        anchor = '    parser.add_argument("--limit", type=int, help="Limit unique records for testing.")\n'
        ins = anchor + (
            '    parser.add_argument("--id-min", type=int, help="Only keep rows whose id column (编号) >= this value.")\n'
            '    parser.add_argument("--id-max", type=int, help="Only keep rows whose id column (编号) <= this value.")\n'
            "    parser.add_argument(\"--title\", help=\"Optional label added to each batch title, e.g. '编号400-900'.\")\n"
        )
        source = _replace_once(source, anchor, ins, "--id-min/--id-max/--title 参数")
        applied.append("--id-min/--id-max/--title 参数")

    # ── 适配 3b：build_docx_batches 标题下加「本批编号范围」行 ─────────────
    # MINIMAL_LAYOUT_V1 极简版式启用后整行都不渲染，此处跳过以免锚点丢失。
    if "本批编号范围" not in source and "MINIMAL_LAYOUT_V1" not in source:
        anchor = 'set_run_font_ex(run, size=16, bold=True, font="黑体")'
        sub = (
            anchor + "\n"
            "        span_ids = [r.sheet_id for r in chunk if r.sheet_id]\n"
            "        if span_ids:\n"
            "            sub = doc.add_paragraph()\n"
            "            sub.paragraph_format.space_after = Pt(6)\n"
            '            srun = sub.add_run(f"本批编号范围：{span_ids[0]} — {span_ids[-1]}\\u3000共 {len(chunk)} 条")\n'
            '            set_run_font_ex(srun, size=9, color=(120, 120, 120))'
        )
        source = _replace_once(source, anchor, sub, "本批编号范围 行")
        applied.append("本批编号范围 行")

    # ── 适配 4c：add_record 元信息行改为（编号X · 原序号Y · 来源 · @作者） ──
    # 原写法是一整块拼接：
    #     meta = f"（原序号{record.original_no}"
    #     if record.source:
    #         meta += f" · {record.source}"
    #     if record.author:
    #         meta += f" · @{record.author}"
    #     meta += "）"
    # 必须整块替换：只换首行的话，后面几行仍会追加，导致「…） · 抖音 · @作者）」重复拼接。
    # MINIMAL_LAYOUT_V1 极简版式下整段元信息都不再渲染，此处跳过以免锚点丢失。
    if "编号{record.sheet_id}" not in source and "MINIMAL_LAYOUT_V1" not in source:
        new_meta = (
            '    meta = "（"\n'
            "    if record.sheet_id:\n"
            '        meta += f"编号{record.sheet_id} · "\n'
            '    meta += f"原序号{record.original_no}"\n'
            "    if record.source:\n"
            '        meta += f" · {record.source}"\n'
            "    if record.author:\n"
            '        author_txt = record.author.lstrip("@").strip()\n'
            "        if author_txt:\n"
            '            meta += f" · @{author_txt}"\n'
            '    meta += "）"'
        )
        start_m = re.search(r'^    meta = f?"（原序号', source, flags=re.M)
        if not start_m:
            raise PatchError(
                "未找到 add_record 的元信息起始行（形如 meta = f\"（原序号…\"），无法自动改写。\n"
                "请手动把该 meta 拼装块整体替换为：\n" + new_meta
            )
        end_marker = '    meta += "）"\n'
        end = source.find(end_marker, start_m.start())
        if end == -1:
            raise PatchError(
                "找到了元信息起始行但找不到结束行 '    meta += \"）\"'，结构不符，已中止。\n"
                "请手动把该 meta 拼装块整体替换为：\n" + new_meta
            )
        end += len(end_marker)
        source = source[: start_m.start()] + new_meta + "\n" + source[end:]
        applied.append("add_record 元信息行（编号·原序号·来源·@作者）")

    # ── 适配 4d：作者名自带 @ 时元信息出现 @@（就地修复，幂等） ──────────────
    # 元信息拼装固定会给作者加一个 @ 前缀，但源表「作者」列本身经常就以 @ 开头
    # （本案 1057 条里有 13 条，形如 `@@人生如戏@@` / `@Ming.yang`），
    # 于是成品里出现 `@@何建平` / `@@@人生如戏@@`。
    # 上面 4c 的守卫是 `if "编号{record.sheet_id}" not in source:` —— 对已经打过补丁、
    # 但当时没带上这条修正的文件会**整块跳过**，所以必须单独就地替换，否则永远修不好。
    # 只改展示：links_manifest.json / screenshots_manifest.json 里仍是原始作者名。
    _AUTHOR_BUGGY = '        meta += f" · @{record.author}"'
    _AUTHOR_FIXED = (
        '        author_txt = record.author.strip().strip("@＠").strip()\n'
        "        if author_txt:\n"
        '            meta += f" · @{author_txt}"'
    )
    if _AUTHOR_BUGGY in source:
        source = source.replace(_AUTHOR_BUGGY, _AUTHOR_FIXED)
        applied.append('修正作者名自带 @ 导致的 "@@"（元信息重复 @）')

    # ── 适配 4e：作者名**首尾**都有 @ 时，上一版只去了开头（就地修复，幂等） ──
    # 4d 最初写的是 `lstrip("@")`，只能去掉开头的 @。实测源表里有更脏的值：
    #   行710 `@@人生如戏@@`  → 4d 后变成 `@人生如戏@@`，仍带尾部 `@@`；
    #   行565 `＠_Wu_@`       → 全角 ＠ 开头，`lstrip("@")` 根本去不掉。
    # 正确做法是**把首尾的 @ 全剥掉再统一加一个前缀**（中间的 @ 属于昵称本身，必须保留，
    # 例如 `修水卢会计@燕子` / `衡山@派` 不能被破坏）。
    # 同样要单独就地替换：4d 的守卫是 `if _AUTHOR_BUGGY in source:`，
    # 对已经换成 `lstrip` 版本的文件会跳过，不补这一刀就永远留着尾部 @。
    _AUTHOR_V1 = '        author_txt = record.author.lstrip("@").strip()'
    _AUTHOR_V2 = '        author_txt = record.author.strip().strip("@＠").strip()'
    if _AUTHOR_V1 in source:
        source = source.replace(_AUTHOR_V1, _AUTHOR_V2)
        applied.append("修正作者名首尾 @ 残留（`@@人生如戏@@` → `@人生如戏`）")

    # ── 适配 5a：批次标题追加区间标签 ────────────────────────────────────
    # MINIMAL_LAYOUT_V1 极简版式下批次标题整段不渲染，此处跳过以免锚点丢失。
    if 'head = f"舆情有害样本' not in source and "MINIMAL_LAYOUT_V1" not in source:
        anchor = '        run = title.add_run(f"舆情有害样本（第{batch_index + 1:02d}批）")\n'
        ins = (
            '        head = f"舆情有害样本（第{batch_index + 1:02d}批）"\n'
            "        if title_extra:\n"
            '            head += f"　{title_extra}"\n'
            "        run = title.add_run(head)\n"
        )
        source = _replace_once(source, anchor, ins, "批次标题追加区间标签")
        applied.append("批次标题追加区间标签")

    # ── 适配 5b：build_docx_batches 增加 title_extra 形参 ────────────────
    if 'title_extra: str = ""' not in source:
        source = _replace_once(
            source,
            "    screenshot_root: Path,\n) -> list[Path]:\n",
            '    screenshot_root: Path,\n    title_extra: str = "",\n) -> list[Path]:\n',
            "build_docx_batches 的 title_extra 形参",
        )
        applied.append("build_docx_batches 的 title_extra 形参")

    # ── 适配 5c：main 把区间参数传给 read_records ────────────────────────
    if "read_records(excel_path, args.sheet, args.id_min, args.id_max)" not in source:
        source = _replace_once(
            source,
            "    records, logs, meta = read_records(excel_path, args.sheet)\n",
            "    records, logs, meta = read_records(excel_path, args.sheet, args.id_min, args.id_max)\n",
            "main 传入区间参数",
        )
        applied.append("main 传入区间参数")

    # ── 适配 5d：main 把 title_extra 传给 build_docx_batches ─────────────
    if 'title_extra=args.title or ""' not in source:
        source = _replace_once(
            source,
            "        screenshots,\n        screenshot_root.resolve(),\n    )\n",
            '        screenshots,\n        screenshot_root.resolve(),\n        title_extra=args.title or "",\n    )\n',
            "main 传入 title_extra",
        )
        applied.append("main 传入 title_extra")

    # ── 适配 7a：本案关键词扩展机制（数据驱动，可选） ─────────────────────────
    # 目的：判定关键词是「按案件定制」的，不能写死在通用补丁里。这里只注入「读取 JSON 并
    # 合并进 REASON_PATTERNS」的机制；具体关键词放在 CASE_KEYWORD_FILE 指向的 JSON 里。
    # 不提供 JSON 时，_apply_case_keywords() 直接 return，行为与上游完全一致。
    if "CASE_KEYWORD_FILE" not in source:
        anchor = (
            '        "上纲攻击高校管理和教育制度。",\n'
            "    ),\n"
            "]"
        )
        ins = anchor + "\n\n" + CASE_KEYWORD_BLOCK
        source = _replace_once(source, anchor, ins, "本案关键词扩展机制")
        applied.append("本案关键词扩展机制（数据驱动）")

    # ── 适配 7b：在 combined_evidence 之前调用一次（此时 as_text 已定义） ─────
    # 不能把调用放在 REASON_PATTERNS 后面：as_text 定义在其后，模块级调用会 NameError。
    if "\n_apply_case_keywords()\n" not in source:
        anchor = "def combined_evidence(record: Record, screenshot: dict[str, str]) -> str:"
        ins = "_apply_case_keywords()\n\n\n" + anchor
        source = _replace_once(source, anchor, ins, "本案关键词扩展调用点")
        applied.append("本案关键词扩展调用点")

    # ── 适配 7c：未命中时的兜底文案可按案件替换 ──────────────────────────────
    if "CASE_FALLBACK_LABEL or LABEL_AGITATE" not in source:
        old = (
            "    if not labels:\n"
            "        labels.append(LABEL_AGITATE)\n"
            '        notes.append("借热点事件发布负面情绪化内容。")\n'
        )
        new = (
            "    if not labels:\n"
            "        labels.append(CASE_FALLBACK_LABEL or LABEL_AGITATE)\n"
            '        notes.append(CASE_FALLBACK_NOTE or "借热点事件发布负面情绪化内容。")\n'
        )
        source = _replace_once(source, old, new, "兜底文案可替换")
        applied.append("兜底文案可按案件替换")

    # ── 适配 7d：meta 里记录实际生效的案件关键词，便于对账 ────────────────────
    if '"case_keyword_applied"' not in source:
        anchor = '        "column_alias_map": alias_hits,\n'
        ins = anchor + '        "case_keyword_applied": CASE_KEYWORD_APPLIED,\n'
        source = _replace_once(source, anchor, ins, "meta 记录 case_keyword_applied")
        applied.append("meta 记录 case_keyword_applied")

    # ── 适配 8：截图缺失时正文留空白，不写任何说明性文字 ─────────────────────
    # 仓库原会写「截图待补」「⚠️ 导航错误」「❌ 访问被拦截或页面为空」等占位文案。
    # 交付要求：截图不完整时正文只留空白，**不出现任何说明/免责表述**；
    # 采集状态仍完整保留在 处理日志.csv / 截图状态汇总.csv / 完成标记.json 里备查。
    if 'display = f"{prefix}：{reason}"' in source:
        old = (
            '    display = f"{prefix}：{reason}" if reason and reason not in {"not captured", "not_captured"} else prefix\n'
            "    run = p.add_run(display)\n"
            "    set_run_font_ex(run, size=9.5, color=color)\n"
        )
        new = (
            "    # 截图缺失/不可用时：正文只留空白，不写任何说明性文字（状态见日志 CSV）\n"
            "    return\n"
        )
        source = _replace_once(source, old, new, "截图缺失留空白")
        applied.append("截图缺失时正文留空白（不写说明文字）")

    # ── 适配 6b：删掉「视频平台 → 追加人工核验」注入点（关键词路径） ──────────
    # 守卫用「待删除的那句话是否存在」做正向判断，天然幂等，且 6b / 6c 互不影响。
    if 'notes.append("视频画面及完整语音需人工核验。")' in source:
        old = (
            "    link = record.link.lower()\n"
            '    if any(d in link for d in ["douyin", "video.weibo", "bilibili", "haokan.baidu", "weixin.qq.com", "channels.weixin"]):\n'
            '        notes.append("视频画面及完整语音需人工核验。")\n'
            '    return labels[:4], " ".join(dict.fromkeys(notes))'
        )
        new = '    return labels[:4], strip_defensive(" ".join(dict.fromkeys(notes)))'
        source = _replace_once(source, old, new, "防御性表述注入点（关键词路径）")
        applied.append("删除防御性表述注入点（关键词路径）")

    # ── 适配 6c：删掉同一注入点（classify_harm，连大模型结果也追加的那处） ────
    if 'note = (note + " 视频画面及完整语音需人工核验。").strip()' in source:
        old = (
            "    labels, note = result\n"
            "    link = record.link.lower()\n"
            '    if any(d in link for d in ["douyin", "video.weibo", "bilibili", "haokan.baidu", "weixin.qq.com", "channels.weixin"]):\n'
            '        if "视频" not in note:\n'
            '            note = (note + " 视频画面及完整语音需人工核验。").strip()\n'
            "    return labels, note"
        )
        new = "    labels, note = result\n    return labels, strip_defensive(note)"
        source = _replace_once(source, old, new, "防御性表述注入点（classify_harm）")
        applied.append("删除防御性表述注入点（classify_harm）")

    # ── 适配 9：一条样本可插多张图（评论截图：视频页 或 评论区多屏） ──────────
    # 上游每条只插一张 screenshot_path。评论抓取一条样本会产出多张
    # （00001_k1.png / _k2.png …），manifest 里对应 images:[...]。
    # 这里改成按 images 循环插入；插入宽度按图片像素宽自适应 —— 评论侧栏图
    # 只有 ~300px 宽，固定 13.5cm 会糊，需要按 0.03cm/px 放大到 ~9cm；
    # 通栏评论图 ~790px 宽则封顶 16.6cm（正文可用宽度），避免溢出页面。
    if "def image_insert_width_cm(" not in source:
        anchor = "def add_blank_screenshot_placeholder("
        helper = (
            "def _png_pixel_width(path):\n"
            '    """读 PNG 像素宽；失败返回 0。避免引入 Pillow 依赖。"""\n'
            "    try:\n"
            '        with open(str(path), "rb") as fh:\n'
            "            head = fh.read(33)\n"
            '        if head[:8] == b"\\x89PNG\\r\\n\\x1a\\n":\n'
            "            import struct as _struct\n"
            '            return int(_struct.unpack(">II", head[16:24])[0])\n'
            "    except Exception:\n"
            "        pass\n"
            "    return 0\n"
            "\n"
            "\n"
            "def image_insert_width_cm(path, px_per_cm=0.030, lo=8.5, hi=16.6, default=13.5):\n"
            '    """按图片像素宽推导插入宽度(cm)。\n'
            "\n"
            "    0.030 cm/px 的标定：评论侧栏截图约 300px 宽 → 约 9cm，评论正文字号\n"
            "    约 4.2mm（A4 上清晰可读）；通栏评论截图约 790px 宽 → 会被 hi 封顶到\n"
            "    16.6cm（左右各留 2cm 页边距时的可用宽度），字号约 2.9mm。\n"
            '    """\n'
            "    w = _png_pixel_width(path)\n"
            "    if not w:\n"
            "        return default\n"
            "    return max(lo, min(hi, w * px_per_cm))\n"
            "\n"
            "\n"
        )
        source = _replace_once(source, anchor, helper + anchor, "多图自适应宽度辅助函数")
        applied.append("新增插入宽度自适应辅助函数")

    if "for _img_i, _img_fn in enumerate(image_list):" not in source:
        old = (
            '    if image_path_text and quality in {"usable", "needs_review"}:\n'
            "        image_path = Path(image_path_text)\n"
            "        if not image_path.is_absolute():\n"
            "            image_path = screenshot_root / image_path\n"
            "        if image_path.exists():\n"
            "            try:\n"
            "                p = doc.add_paragraph()\n"
            "                p.paragraph_format.space_after = Pt(3)\n"
            "                run = p.add_run()\n"
            "                run.add_picture(str(image_path), width=Cm(13.5))\n"
            "            except Exception as exc:\n"
            '                add_blank_screenshot_placeholder(doc, f"insert_error: {exc}", status="insert_error")\n'
            '                record.screenshot_status = "insert_error"\n'
            "                record.screenshot_reason = str(exc)\n"
            "        else:\n"
            '            add_blank_screenshot_placeholder(doc, f"missing_file: {image_path}", status="missing_file")\n'
            '            record.screenshot_status = "missing_file"\n'
            "            record.screenshot_reason = str(image_path)\n"
            "    else:\n"
            "        add_blank_screenshot_placeholder(doc, quality_reason or reason, status=status)\n"
        )
        new = (
            "    # 多图：images 优先（评论截图一条多张），退化到单张 screenshot_path\n"
            "    image_list = [as_text(x) for x in (screenshot.get(\"images\") or []) if as_text(x)]\n"
            "    if not image_list and image_path_text:\n"
            "        image_list = [image_path_text]\n"
            "    inserted = 0\n"
            '    if image_list and quality in {"usable", "needs_review"}:\n'
            "        for _img_i, _img_fn in enumerate(image_list):\n"
            "            image_path = Path(_img_fn)\n"
            "            if not image_path.is_absolute():\n"
            "                image_path = screenshot_root / image_path\n"
            "            if not image_path.exists():\n"
            "                continue\n"
            "            try:\n"
            "                p = doc.add_paragraph()\n"
            "                p.paragraph_format.space_after = Pt(3)\n"
            "                run = p.add_run()\n"
            "                run.add_picture(\n"
            "                    str(image_path), width=Cm(image_insert_width_cm(image_path))\n"
            "                )\n"
            "                inserted += 1\n"
            "            except Exception as exc:\n"
            '                record.screenshot_status = "insert_error"\n'
            "                record.screenshot_reason = str(exc)\n"
            "    if not inserted:\n"
            "        add_blank_screenshot_placeholder(doc, quality_reason or reason, status=status)\n"
        )
        source = _replace_once(source, old, new, "多图插入")
        applied.append("一条样本可插入多张截图（按 images 循环）")

    # ── 适配 10：判定方式可切换（CLASSIFY_MODE = auto | keyword | llm） ──────
    # 背景：classify_harm 在上游是「先大模型、失败回退关键词」。云端 .env 一旦配置了
    # DEEPSEEK_API_KEY（config.py 用 os.environ.setdefault 写进进程环境，run_step 又整体
    # copy 给子进程），每条样本都会真调一次带图的 DeepSeek —— 上千条样本既慢又贵，而且
    # 会**盖掉**本案定制关键词（CASE_KEYWORD_FILE）的判定结果。
    # 这里只加一个开关，不改上游默认行为：
    #   keyword → 只走关键词（零 API 成本，本案关键词说了算）
    #   auto    → 上游行为：先大模型，失败回退关键词
    #   llm     → 同 auto（保留可读性，便于显式声明）
    # CLASSIFY_MODE 未显式设置时：加载到本案关键词文件就用 keyword，否则 auto。
    if "CLASSIFY_MODE" not in source:
        anchor = "_CASE_KEYWORDS_LOADED = False\n"
        ins = (
            anchor + "\n"
            "# 判定方式：auto（先大模型，失败回退关键词）| keyword（只走关键词）| llm（同 auto）\n"
            'CLASSIFY_MODE = os.environ.get("CLASSIFY_MODE", "").strip().lower()\n'
        )
        source = _replace_once(source, anchor, ins, "CLASSIFY_MODE 常量")
        applied.append("CLASSIFY_MODE 常量")

    if "def _resolved_classify_mode(" not in source:
        anchor = "def _apply_case_keywords() -> None:"
        helper = (
            "def _resolved_classify_mode() -> str:\n"
            '    """决定 classify_harm 用哪条判定路径。\n'
            "\n"
            "    CLASSIFY_MODE 显式取值优先；未设置时，若已加载本案关键词文件则用 keyword\n"
            "    （案件关键词优先，且不产生任何大模型调用），否则沿用上游 auto。\n"
            '    """\n'
            '    if CLASSIFY_MODE in {"auto", "keyword", "llm"}:\n'
            "        return CLASSIFY_MODE\n"
            '    return "keyword" if CASE_KEYWORD_APPLIED else "auto"\n'
            "\n"
            "\n"
        )
        source = _replace_once(source, anchor, helper + anchor, "_resolved_classify_mode 辅助函数")
        applied.append("_resolved_classify_mode 辅助函数")

    if 'if _resolved_classify_mode() == "keyword"' not in source:
        old = (
            "    result = _llm_classify(record.title, page_text, image_path)\n"
            "    if result is None:\n"
            "        result = _keyword_classify(record, screenshot)\n"
        )
        new = (
            '    if _resolved_classify_mode() == "keyword":\n'
            "        result = _keyword_classify(record, screenshot)\n"
            "    else:\n"
            "        result = _llm_classify(record.title, page_text, image_path)\n"
            "        if result is None:\n"
            "            result = _keyword_classify(record, screenshot)\n"
        )
        source = _replace_once(source, old, new, "classify_harm 分流")
        applied.append("classify_harm 按 CLASSIFY_MODE 分流")

    # ── 适配 11：极简交付版式 MINIMAL_LAYOUT_V1 ──────────────────────────────
    # 对齐用户参考样例「王振义-7批-20条」：每条样本只有「N.标题 / 链接 / 图片」
    # 三段，Word 正文不出现任何其他说明文字。判定结果仍计算并写日志，只是不落版。

    # 11a：竖版截图高度限幅 —— 参考样例竖版 12.1×20.5cm，横版最宽 16.0cm。
    if "def _png_pixel_height(" not in source:
        anchor = "def image_insert_width_cm("
        helper = (
            "def _png_pixel_height(path):\n"
            '    """读 PNG 像素高；失败返回 0（IHDR 头 20:24 字节）。"""\n'
            "    try:\n"
            '        with open(str(path), "rb") as fh:\n'
            "            head = fh.read(33)\n"
            '        if head[:8] == b"\\x89PNG\\r\\n\\x1a\\n":\n'
            "            import struct as _struct\n"
            '            return int(_struct.unpack(">I", head[20:24])[0])\n'
            "    except Exception:\n"
            "        pass\n"
            "    return 0\n"
            "\n"
            "\n"
        )
        source = _replace_once(source, anchor, helper + anchor, "_png_pixel_height 辅助函数")
        applied.append("新增 PNG 高度读取辅助函数")

    _IMG_TAIL_OLD = (
        "    w = _png_pixel_width(path)\n"
        "    if not w:\n"
        "        return default\n"
        "    return max(lo, min(hi, w * px_per_cm))"
    )
    _IMG_TAIL_NEW = (
        "    w = _png_pixel_width(path)\n"
        "    if not w:\n"
        "        return default\n"
        "    h = _png_pixel_height(path)\n"
        "    width = min(hi, w * px_per_cm)\n"
        "    if h and h > w:\n"
        "        # 竖版截图按高度 20.5cm 限幅，避免单图超过一页\n"
        "        width = min(width, 20.5 * w / h)\n"
        "    return max(lo, width)"
    )
    if "20.5 * w / h" not in source:
        source = _replace_once(source, _IMG_TAIL_OLD, _IMG_TAIL_NEW, "插入宽度竖版限幅")
        applied.append("插入宽度增加竖版高度限幅")

    # 11b：add_record 头部重写 —— 删空行垫、元信息、【概括】、原文、旧「链接：」行，
    # 换成「N.标题」+ 裸链接两段。
    if "MINIMAL_LAYOUT_V1" not in source:
        old = (
            '    if record.item_id > 1:\n'
            '        doc.add_paragraph("")\n'
            "\n"
            "    p = doc.add_paragraph()\n"
            "    p.paragraph_format.space_before = Pt(8)\n"
            "    p.paragraph_format.space_after = Pt(1)\n"
            '    rn = p.add_run(f"{record.item_id}、")\n'
            "    set_run_font_ex(rn, size=11.5, bold=True)\n"
            '    meta = "（"\n'
            "    if record.sheet_id:\n"
            '        meta += f"编号{record.sheet_id} · "\n'
            '    meta += f"原序号{record.original_no}"\n'
            "    if record.source:\n"
            '        meta += f" · {record.source}"\n'
            "    if record.author:\n"
            '        author_txt = record.author.strip().strip("@＠").strip()\n'
            "        if author_txt:\n"
            '            meta += f" · @{author_txt}"\n'
            '    meta += "）"\n'
            "    rm = p.add_run(meta)\n"
            "    set_run_font_ex(rm, size=9, color=(136, 136, 136))\n"
            "    if record.summary:\n"
            '        rs = p.add_run("【概括】" + first_chars(record.summary, 40))\n'
            "        set_run_font_ex(rs, size=11, bold=True, color=(31, 55, 100))\n"
            "\n"
            "    p = doc.add_paragraph()\n"
            '    label = p.add_run("原文：")\n'
            "    set_run_font_ex(label, size=9, bold=True)\n"
            "    body = p.add_run(record.title)\n"
            "    set_run_font_ex(body, size=10.5)\n"
            "\n"
            "    p = doc.add_paragraph()\n"
            '    label = p.add_run("链接：")\n'
            "    set_run_font_ex(label, size=9, bold=True)\n"
            "    link_run = p.add_run(record.link)\n"
            '    set_run_font_ex(link_run, size=8.5, color=(5, 99, 193))\n'
        )
        new = (
            "    # MINIMAL_LAYOUT_V1 极简交付版式：每条仅「N.标题 / 链接 / 图片」三段，\n"
            "    # 无元信息、无原文全文、无【概括】、无任何说明文字。\n"
            "    p = doc.add_paragraph()\n"
            "    p.paragraph_format.space_after = Pt(2)\n"
            '    title_text = " ".join(as_text(record.title).split())\n'
            "    if not title_text:\n"
            '        title_text = " ".join(as_text(record.summary).split())\n'
            "    if len(title_text) > 32:\n"
            '        title_text = title_text[:32].rstrip() + "…"\n'
            "    if not title_text:\n"
            '        title_text = "（无标题）"\n'
            '    rt = p.add_run(f"{record.item_id}.{title_text}")\n'
            "    set_run_font_ex(rt, size=12, bold=True, color=(31, 78, 121))\n"
            "\n"
            "    p = doc.add_paragraph()\n"
            "    p.paragraph_format.space_after = Pt(4)\n"
            "    rl = p.add_run(record.link)\n"
            "    set_run_font_ex(rl, size=9.5, color=(11, 96, 176))\n"
        )
        source = _replace_once(source, old, new, "add_record 极简头部")
        applied.append("add_record 改为「N.标题 / 链接」极简头部")

    # 11c：插入宽度上限对齐参考样例（横版 16.0cm），竖版限幅在 11a 已生效。
    if "image_insert_width_cm(image_path, hi=16.0)" not in source:
        old = (
            "                run.add_picture(\n"
            "                    str(image_path), width=Cm(image_insert_width_cm(image_path))\n"
            "                )\n"
        )
        new = (
            "                run.add_picture(\n"
            "                    str(image_path), width=Cm(image_insert_width_cm(image_path, hi=16.0))\n"
            "                )\n"
        )
        source = _replace_once(source, old, new, "插图宽度上限 16.0cm")
        applied.append("插图宽度上限对齐 16.0cm")

    # 11d：删【违规要素】判定框渲染（判定结果仍写 record，供 处理日志.csv 对账）。
    if "不再渲染【违规要素】框" not in source:
        old = (
            "    labels, note = pre_harm if pre_harm is not None else classify_harm(record, screenshot, screenshot_root)\n"
            "    record.harm_categories = labels\n"
            "    record.harm_note = note\n"
            "    add_evaluation_box(doc, labels, note)\n"
        )
        new = (
            "    labels, note = pre_harm if pre_harm is not None else classify_harm(record, screenshot, screenshot_root)\n"
            "    record.harm_categories = labels\n"
            "    record.harm_note = note\n"
            "    # MINIMAL_LAYOUT_V1：判定结果只进 处理日志.csv 对账，不再渲染【违规要素】框。\n"
        )
        source = _replace_once(source, old, new, "删除违规要素框渲染")
        applied.append("删除【违规要素】框渲染（判定仍写日志）")

    # 11e：删批次大标题与「本批编号范围」行。注意 3b 注入的 \u3000 可能以
    # 「字面转义文本」或「全角空格实字符」两种形态存在，两种锚点都试。
    if "不渲染批次标题" not in source:
        head_block = (
            "        title = doc.add_paragraph()\n"
            '        head = f"舆情有害样本（第{batch_index + 1:02d}批）"\n'
            "        if title_extra:\n"
            '            head += f"　{title_extra}"\n'
            "        run = title.add_run(head)\n"
            "        run.bold = True\n"
            '        set_run_font_ex(run, size=16, bold=True, font="黑体")\n'
            "        span_ids = [r.sheet_id for r in chunk if r.sheet_id]\n"
            "        if span_ids:\n"
            "            sub = doc.add_paragraph()\n"
            "            sub.paragraph_format.space_after = Pt(6)\n"
        )
        tail_a = (
            '            srun = sub.add_run(f"本批编号范围：{span_ids[0]} — {span_ids[-1]}\\u3000共 {len(chunk)} 条")\n'
            "            set_run_font_ex(srun, size=9, color=(120, 120, 120))\n"
        )
        tail_b = (
            '            srun = sub.add_run(f"本批编号范围：{span_ids[0]} — {span_ids[-1]}\u3000共 {len(chunk)} 条")\n'
            "            set_run_font_ex(srun, size=9, color=(120, 120, 120))\n"
        )
        new = (
            "        # MINIMAL_LAYOUT_V1：不渲染批次标题与「本批编号范围」行（--title 仍接受但不落版）。\n"
        )
        if head_block + tail_a in source:
            source = source.replace(head_block + tail_a, new, 1)
        elif head_block + tail_b in source:
            source = source.replace(head_block + tail_b, new, 1)
        else:
            raise PatchError(
                "未找到批次标题块（适配 11e）。请手动删除 build_docx_batches 里\n"
                "title/sub 两个段落的渲染代码，并替换为：\n" + new
            )
        applied.append("删除批次大标题与本批编号范围行")

    return source, applied


def main() -> int:
    target = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("APPLY_TARGET", "excel_to_word.py")
    if not os.path.exists(target):
        raise SystemExit(f"目标文件不存在: {target}")
    source = open(target, encoding="utf-8").read()
    # 先语法自检（注入前）
    try:
        compile(source, target, "exec")
    except SyntaxError as e:
        raise SystemExit(f"目标文件本身语法错误，已中止：{e}")

    new_source, applied = apply(source)

    if not applied:
        print(f"[幂等] {target} 已包含所有适配，未做任何改动。")
        return 0

    # 注入后语法自检
    try:
        compile(new_source, target, "exec")
    except SyntaxError as e:
        raise SystemExit(f"注入后语法错误（已中止写入）：{e}")

    backup = target + ".bak"
    if not os.path.exists(backup):
        open(backup, "w", encoding="utf-8").write(source)
        print(f"[备份] 原文件已备份到 {backup}")
    open(target, "w", encoding="utf-8").write(new_source)
    print(f"[完成] 已注入 {len(applied)} 处适配到 {target}：")
    for a in applied:
        print("  +", a)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
