#!/usr/bin/env python
"""幂等地把一份「新 clone」的仓库 excel_to_word.py 打上本任务做的 4 类适配。

适配对照源：engine/skills/harmful_sample/scripts/excel_to_word.py（已改好版本）。
本脚本不直接引用 engine 路径，而是把需要的改动以「标记+字符串替换」的形式注入，
因此可对任意一份符合上游结构的 fresh clone 复用。

4 类适配（脚本内部拆成下列若干幂等注入块，按顺序执行）：
  1) COLUMN_ALIASES 列名别名映射（标题/微博内容←标题、原文/评论链接←URL、原文作者←作者、
     序号/来源网站/摘要 直通），并在 meta 输出 column_alias_map 便于对账。
  2) ID_COLUMN_ALIASES = ["编号","id","ID"] + --id-min/--id-max 区间筛选，
     meta 输出 in_range_rows / out_of_range_rows。
  3) --title 参数：批次标题追加区间标签，并在标题下加一行
     `本批编号范围：X — Y　共 N 条`。
  4) Record 增加 sheet_id 字段（存编号），元信息行改为
     `（编号400 · 原序号1234 · 抖音 · @作者）`，并写入 links_manifest.json。

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
            sheet_id=(str(row[id_pos]).strip() if range_active and id_pos is not None and id_pos < len(row) else ""),
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
            "# 用于 --id-min/--id-max 区间筛选的列（粉丝排序导出为 编号）\n"
            'ID_COLUMN_ALIASES = ["编号", "id", "ID"]'
        )
        source = _replace_once(source, anchor, ins, "ID_COLUMN_ALIASES 常量")
        applied.append("ID_COLUMN_ALIASES 常量")

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
    if "本批编号范围" not in source:
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
    if "编号{record.sheet_id}" not in source:
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

    # ── 适配 5a：批次标题追加区间标签 ────────────────────────────────────
    if 'head = f"舆情有害样本' not in source:
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
