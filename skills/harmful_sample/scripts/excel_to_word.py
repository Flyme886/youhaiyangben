#!/usr/bin/env python
"""Convert monitoring Excel rows into deduplicated Word report batches."""

from __future__ import annotations

import argparse
import base64
import csv
import json
import math
import os
import re
import shutil
import sys
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

import requests as _http

from docx import Document
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt, RGBColor
from openpyxl import load_workbook


SCRIPT_DIR = Path(__file__).resolve().parent
SKILL_DIR = SCRIPT_DIR.parent
DEFAULT_TEMPLATE = SKILL_DIR / "assets" / "template.docx"

REQUIRED_COLUMNS = ["标题/微博内容", "原文/评论链接", "原文作者"]
OPTIONAL_COLUMNS = ["转发/评论作者", "来源网站", "序号"]
BLANK_VALUES = {"", "无", "none", "null", "nan"}

# 列名别名映射（扩展，不改动原判定逻辑）：兼容「粉丝排序」类导出的列名
COLUMN_ALIASES: dict[str, list[str]] = {
    "标题/微博内容": ["标题/微博内容", "标题", "内容", "文本"],
    "原文/评论链接": ["原文/评论链接", "URL", "url", "Url", "链接", "原文链接", "评论链接"],
    "原文作者": ["原文作者", "作者", "昵称", "账号"],
    "转发/评论作者": ["转发/评论作者", "转发作者", "评论作者"],
    "来源网站": ["来源网站", "平台"],
    "序号": ["序号"],
    "摘要": ["摘要", "概括"],
    "涉及词": ["涉及词", "关键词"],
    "行业一级": ["行业一级", "行业", "领域"],
    "行业二级": ["行业二级", "子行业"],
}
CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")

# 用于 --id-min/--id-max 区间筛选的列（粉丝排序导出为 编号）
ID_COLUMN_ALIASES = ["编号", "新编", "新编号", "id", "ID"]

# 判定理由中的防御性/免责措辞：统一剔除，成品不出现「需人工核验」类对冲表述
DEFENSIVE_PHRASES = (
    "视频画面及完整语音需人工核验。",
    "视频画面及完整语音需人工核验",
    "需人工核验。",
    "需人工核验",
    "需人工复核。",
    "需人工复核",
    "仅供参考。",
    "仅供参考",
    "请以官方通报为准。",
    "请以官方通报为准",
    "请以官方发布为准。",
    "请以官方发布为准",
)


def strip_defensive(text: str) -> str:
    """去掉判定理由里的防御性措辞，并归并多余空白。"""
    for phrase in DEFENSIVE_PHRASES:
        text = text.replace(phrase, "")
    return " ".join(text.split()).strip()

LABEL_FALSE_INFO = "编造虚假信息"
LABEL_DISTORT = "歪曲事实"
LABEL_AGITATE = "煽动对立·借机引流"
LABEL_ATTACK_SYSTEM = "攻击高校与制度（言语激烈、上纲上线）"
HARM_LABELS = [LABEL_FALSE_INFO, LABEL_DISTORT, LABEL_AGITATE, LABEL_ATTACK_SYSTEM]
LABEL_NUMBERS = {
    LABEL_FALSE_INFO: "①",
    LABEL_DISTORT: "②",
    LABEL_AGITATE: "③",
    LABEL_ATTACK_SYSTEM: "④",
}
CATEGORY_MAP = {
    1: LABEL_FALSE_INFO,
    2: LABEL_DISTORT,
    3: LABEL_AGITATE,
    4: LABEL_ATTACK_SYSTEM,
}

# ── DeepSeek 大模型配置 ───────────────────────────────────────────────────────
DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY", "")
DEEPSEEK_BASE_URL = os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
DEEPSEEK_MODEL = os.environ.get("DEEPSEEK_MODEL", "deepseek-v4-flash")
LLM_MAX_WORKERS = 5   # 并发 LLM 调用数
LLM_TIMEOUT = 30      # 单次调用超时秒数
LLM_MAX_TOKENS = 200  # 非推理模式下 JSON 输出够用

REASON_PATTERNS: list[tuple[str, list[str], str]] = [
    (
        LABEL_FALSE_INFO,
        ["央企高管", "其父", "父亲", "家庭背景", "家族", "靠关系", "保送", "内定", "上面有人", "特权背景"],
        "将未经证实的背景传闻作事实传播。",
    ),
    (
        LABEL_DISTORT,
        ["私吞", "侵占", "贪污", "刑事", "拘留", "判刑", "伪造证件", "犯罪", "5000元", "奖金"],
        "将分配纠纷歪曲为侵占犯罪。",
    ),
    (
        LABEL_AGITATE,
        ["特权阶层", "寒门", "普通人", "老百姓", "草民", "权贵", "阶层", "公平没了", "扒", "遮羞布", "流量"],
        "借个案渲染群体对立并引流。",
    ),
    (
        LABEL_ATTACK_SYSTEM,
        ["高校", "学校", "校方", "交大", "教育公平", "体制", "制度", "校长", "开除", "彻查", "腐败", "黑幕"],
        "上纲攻击高校管理和教育制度。",
    ),
]

# ── 本案关键词扩展（数据驱动，可选；不改动上方通用规则） ─────────────────────
# 判定关键词是按案件定制的：用 CASE_KEYWORD_FILE 指向的 JSON 覆盖/追加 REASON_PATTERNS，
# 并可按案件替换「未命中」时的兜底文案。不提供 JSON 时行为与上游完全一致。
# JSON 结构：{"patterns": [{"label": "…", "note": "…", "words": ["…"]}],
#             "fallback": {"label": "…", "note": "…"}}
CASE_KEYWORD_FILE = os.environ.get("CASE_KEYWORD_FILE", "")
CASE_KEYWORD_APPLIED: list[str] = []
CASE_FALLBACK_LABEL = ""
CASE_FALLBACK_NOTE = ""
_CASE_KEYWORDS_LOADED = False

# 判定方式：auto（先大模型，失败回退关键词）| keyword（只走关键词）| llm（同 auto）
CLASSIFY_MODE = os.environ.get("CLASSIFY_MODE", "").strip().lower()


def _resolved_classify_mode() -> str:
    """决定 classify_harm 用哪条判定路径。

    CLASSIFY_MODE 显式取值优先；未设置时，若已加载本案关键词文件则用 keyword
    （案件关键词优先，且不产生任何大模型调用），否则沿用上游 auto。
    """
    if CLASSIFY_MODE in {"auto", "keyword", "llm"}:
        return CLASSIFY_MODE
    return "keyword" if CASE_KEYWORD_APPLIED else "auto"


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



@dataclass
class Record:
    item_id: int
    source_row: int
    original_no: str
    source: str
    title: str
    author: str
    link: str
    sheet_id: str = ""
    summary: str = ""
    keywords: str = ""
    industry: str = ""
    category: str = ""
    reason: str = ""
    output_file: str = ""
    screenshot_status: str = "not_captured"
    screenshot_path: str = ""
    screenshot_reason: str = "not captured"
    harm_categories: list[str] | None = None
    harm_note: str = ""


@dataclass
class RowLog:
    source_row: int
    link: str
    status: str
    item_id: int | str
    duplicate_of: int | str
    output_file: str
    screenshot_status: str
    screenshot_reason: str
    harm_categories: str = ""
    harm_note: str = ""


def as_text(value: Any) -> str:
    if value is None:
        return ""
    text = str(value).replace("\r\n", "\n").replace("\r", "\n")
    text = CONTROL_RE.sub("", text)
    text = re.sub(r"[ \t\f\v]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def is_blank(value: str) -> bool:
    return as_text(value).lower() in BLANK_VALUES


def pick_sheet(workbook: Any, requested: str | None) -> Any:
    if requested:
        if requested not in workbook.sheetnames:
            raise SystemExit(f"Sheet not found: {requested}")
        return workbook[requested]
    if "搜索结果" in workbook.sheetnames:
        return workbook["搜索结果"]
    return workbook[workbook.sheetnames[0]]


def read_records(
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
        "case_keyword_applied": CASE_KEYWORD_APPLIED,
    }
    return records, logs, meta


def load_screenshots(path: Path | None) -> dict[str, dict[str, str]]:
    if not path:
        return {}
    if not path.exists():
        raise SystemExit(f"Screenshots manifest not found: {path}")
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    items = data.get("items", data if isinstance(data, list) else [])
    out: dict[str, dict[str, str]] = {}
    for item in items:
        key = str(item.get("item_id") or item.get("id") or "")
        if key:
            out[key] = item
    return out


def write_links_manifest(records: list[Record], output_dir: Path, meta: dict[str, Any]) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    manifest = {
        "meta": meta,
        "items": [
            {
                "item_id": r.item_id,
                "sheet_id": r.sheet_id,
                "source_row": r.source_row,
                "original_no": r.original_no,
                "source": r.source,
                "title": r.title,
                "author": r.author,
                "link": r.link,
                "summary": r.summary,
                "keywords": r.keywords,
                "industry": r.industry,
                "category": r.category,
            }
            for r in records
        ],
    }
    path = output_dir / "links_manifest.json"
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def clear_document_body(doc: Document) -> None:
    body = doc.element.body
    sect_pr = None
    for child in list(body):
        if child.tag == qn("w:sectPr"):
            sect_pr = child
        body.remove(child)
    if sect_pr is not None:
        body.append(sect_pr)


def set_cell_borders_none(cell: Any) -> None:
    tc_pr = cell._tc.get_or_add_tcPr()
    borders = tc_pr.first_child_found_in("w:tcBorders")
    if borders is None:
        borders = OxmlElement("w:tcBorders")
        tc_pr.append(borders)
    for edge in ("top", "left", "bottom", "right", "insideH", "insideV"):
        tag = "w:" + edge
        element = borders.find(qn(tag))
        if element is None:
            element = OxmlElement(tag)
            borders.append(element)
        element.set(qn("w:val"), "nil")


def shade_cell(cell: Any, fill: str) -> None:
    tc_pr = cell._tc.get_or_add_tcPr()
    shd = OxmlElement("w:shd")
    shd.set(qn("w:val"), "clear")
    shd.set(qn("w:color"), "auto")
    shd.set(qn("w:fill"), fill)
    tc_pr.append(shd)


def set_cell_borders(cell: Any, color: str = "E0B070", size: str = "4") -> None:
    tc_pr = cell._tc.get_or_add_tcPr()
    borders = tc_pr.first_child_found_in("w:tcBorders")
    if borders is None:
        borders = OxmlElement("w:tcBorders")
        tc_pr.append(borders)
    for edge in ("top", "left", "bottom", "right"):
        element = borders.find(qn("w:" + edge))
        if element is None:
            element = OxmlElement("w:" + edge)
            borders.append(element)
        element.set(qn("w:val"), "single")
        element.set(qn("w:sz"), size)
        element.set(qn("w:color"), color)


def apply_doc_style(doc: Document) -> None:
    section = doc.sections[0]
    section.top_margin = Cm(1.6)
    section.bottom_margin = Cm(1.6)
    section.left_margin = Cm(2.0)
    section.right_margin = Cm(2.0)
    normal = doc.styles["Normal"]
    normal.font.name = "宋体"
    normal._element.rPr.rFonts.set(qn("w:eastAsia"), "宋体")
    normal.font.size = Pt(10.5)
    normal.paragraph_format.space_before = Pt(0)
    normal.paragraph_format.space_after = Pt(0)
    normal.paragraph_format.line_spacing = 1.05


def add_text(paragraph: Any, text: str, bold_prefix: str | None = None) -> None:
    paragraph.paragraph_format.space_before = Pt(0)
    paragraph.paragraph_format.space_after = Pt(0)
    if bold_prefix:
        run = paragraph.add_run(bold_prefix)
        run.bold = True
        set_run_font(run)
        rest = text
        if rest:
            run = paragraph.add_run(rest)
            set_run_font(run)
    else:
        run = paragraph.add_run(text)
        set_run_font(run)


def set_run_font(run: Any) -> None:
    run.font.name = "宋体"
    run._element.rPr.rFonts.set(qn("w:eastAsia"), "宋体")
    run.font.size = Pt(10.5)


def set_run_font_ex(
    run: Any,
    size: float = 10.5,
    bold: bool = False,
    color: tuple[int, int, int] | None = None,
    font: str = "宋体",
) -> None:
    run.font.name = font
    run._element.rPr.rFonts.set(qn("w:eastAsia"), font)
    run.font.size = Pt(size)
    run.bold = bold
    if color:
        run.font.color.rgb = RGBColor(*color)


def compact_join(parts: list[str], sep: str = "；") -> str:
    seen: set[str] = set()
    out: list[str] = []
    for part in parts:
        text = as_text(part)
        if not text or is_blank(text) or text in seen:
            continue
        seen.add(text)
        out.append(text)
    return sep.join(out)


def first_chars(text: str, limit: int) -> str:
    text = as_text(text)
    return text if len(text) <= limit else text[:limit].rstrip() + "..."


_apply_case_keywords()


def combined_evidence(record: Record, screenshot: dict[str, str]) -> str:
    return compact_join(
        [
            record.title,
            record.summary,
            record.keywords,
            record.industry,
            record.category,
            screenshot.get("screenshot_text") or "",
            screenshot.get("page_text") or "",
        ],
        "\n",
    )


def _keyword_classify(record: Record, screenshot: dict[str, str]) -> tuple[list[str], str]:
    """关键词匹配兜底分类（原始逻辑）。"""
    text = combined_evidence(record, screenshot)
    labels: list[str] = []
    notes: list[str] = []
    for label, words, note in REASON_PATTERNS:
        if any(word in text for word in words):
            labels.append(label)
            notes.append(note)
    if not labels:
        labels.append(CASE_FALLBACK_LABEL or LABEL_AGITATE)
        notes.append(CASE_FALLBACK_NOTE or "借热点事件发布负面情绪化内容。")
    return labels[:4], strip_defensive(" ".join(dict.fromkeys(notes)))


def _image_b64(path: Path) -> Optional[str]:
    if not path.exists():
        return None
    if path.stat().st_size > 3 * 1024 * 1024:
        return None
    with open(path, "rb") as f:
        return base64.b64encode(f.read()).decode()


def _llm_classify(title: str, page_text: str, image_path: Optional[Path]) -> Optional[tuple[list[str], str]]:
    """调用 DeepSeek 大模型进行违规判定，失败返回 None。"""
    if not DEEPSEEK_API_KEY:
        return None

    system = (
        "你是网络有害内容审核专家。分析帖子内容与截图，判断违规类别并写出简短理由。\n\n"
        "可选类别（数字标识）：\n"
        "1 编造虚假信息——传播未经核实的身份、背景、事件声明\n"
        "2 歪曲事实——扭曲、夸大或断章取义真实事件\n"
        "3 煽动对立·借机引流——渲染阶层/群体对立，蹭热点引流\n"
        "4 攻击高校与制度（言语激烈、上纲上线）——攻击学校管理或教育制度\n\n"
        "输出纯 JSON，不加 ```，格式：\n"
        "{\"categories\":[2,4],\"note\":\"将分配纠纷歪曲为侵占犯罪。上纲攻击高校管理和教育制度。\"}\n"
        "note 不超过 60 字，不重复类别名称本身。"
    )

    user_parts: list[dict] = []
    if image_path:
        b64 = _image_b64(image_path)
        if b64:
            user_parts.append({
                "type": "image_url",
                "image_url": {"url": f"data:image/png;base64,{b64}"},
            })

    text = ""
    if title:
        text += f"内容：{title[:400]}\n"
    if page_text:
        text += f"页面文字：{page_text[:400]}"
    user_parts.append({"type": "text", "text": text.strip() or "（无文字内容）"})

    try:
        resp = _http.post(
            f"{DEEPSEEK_BASE_URL}/chat/completions",
            headers={"Authorization": f"Bearer {DEEPSEEK_API_KEY}", "Content-Type": "application/json"},
            json={
                "model": DEEPSEEK_MODEL,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user_parts},
                ],
                "max_tokens": LLM_MAX_TOKENS,
                "thinking": {"type": "disabled"},
            },
            timeout=LLM_TIMEOUT,
        )
        resp.raise_for_status()
        raw = resp.json()["choices"][0]["message"]["content"].strip()
        raw = re.sub(r"^```[a-z]*\n?", "", raw).rstrip("`").strip()
        data = json.loads(raw)
        cats = [CATEGORY_MAP[c] for c in data.get("categories", []) if c in CATEGORY_MAP]
        note = as_text(data.get("note", ""))
        return (cats or [LABEL_AGITATE])[:4], note
    except Exception:
        return None


def classify_harm(
    record: Record,
    screenshot: dict[str, str],
    screenshot_root: Optional[Path] = None,
) -> tuple[list[str], str]:
    """优先调大模型，失败回退关键词匹配。"""
    image_path: Optional[Path] = None
    if screenshot_root:
        raw = str(screenshot.get("screenshot_path") or screenshot.get("path") or "")
        if raw:
            p = Path(raw)
            if not p.is_absolute():
                p = screenshot_root / p
            if p.exists():
                image_path = p

    page_text = screenshot.get("page_text") or screenshot.get("screenshot_text") or ""
    if _resolved_classify_mode() == "keyword":
        result = _keyword_classify(record, screenshot)
    else:
        result = _llm_classify(record.title, page_text, image_path)
        if result is None:
            result = _keyword_classify(record, screenshot)

    labels, note = result
    return labels, strip_defensive(note)


def _png_pixel_width(path):
    """读 PNG 像素宽；失败返回 0。避免引入 Pillow 依赖。"""
    try:
        with open(str(path), "rb") as fh:
            head = fh.read(33)
        if head[:8] == b"\x89PNG\r\n\x1a\n":
            import struct as _struct
            return int(_struct.unpack(">II", head[16:24])[0])
    except Exception:
        pass
    return 0


def _png_pixel_height(path):
    """读 PNG 像素高；失败返回 0（IHDR 头 20:24 字节）。"""
    try:
        with open(str(path), "rb") as fh:
            head = fh.read(33)
        if head[:8] == b"\x89PNG\r\n\x1a\n":
            import struct as _struct
            return int(_struct.unpack(">I", head[20:24])[0])
    except Exception:
        pass
    return 0


def image_insert_width_cm(path, px_per_cm=0.030, lo=8.5, hi=16.6, default=13.5):
    """按图片像素宽推导插入宽度(cm)。

    0.030 cm/px 的标定：评论侧栏截图约 300px 宽 → 约 9cm，评论正文字号
    约 4.2mm（A4 上清晰可读）；通栏评论截图约 790px 宽 → 会被 hi 封顶到
    16.6cm（左右各留 2cm 页边距时的可用宽度），字号约 2.9mm。
    """
    w = _png_pixel_width(path)
    if not w:
        return default
    h = _png_pixel_height(path)
    width = min(hi, w * px_per_cm)
    if h and h > w:
        # 竖版截图按高度 20.5cm 限幅，避免单图超过一页
        width = min(width, 20.5 * w / h)
    return max(lo, width)


def add_blank_screenshot_placeholder(doc: Document, reason: str, status: str = "not_captured") -> None:
    STATUS_STYLE: dict[str, tuple[tuple[int, int, int], str]] = {
        "login_required":   ((204, 102, 0),  "⚠️ 需要登录才能查看"),
        "blocked_or_blank": ((192, 0,   0),  "❌ 访问被拦截或页面为空"),
        "captcha_detected": ((192, 0,   0),  "❌ 触发验证码，无法采集"),
        "timeout":          ((153, 51,  0),  "⏱ 加载超时"),
        "navigation_error": ((153, 51,  0),  "⚠️ 导航错误"),
    }
    color, prefix = STATUS_STYLE.get(status, ((128, 128, 128), "截图待补"))
    p = doc.add_paragraph()
    p.paragraph_format.space_after = Pt(2)
    # 截图缺失/不可用时：正文只留空白，不写任何说明性文字（状态见日志 CSV）
    return


def add_evaluation_box(doc: Document, labels: list[str], note: str) -> None:
    table = doc.add_table(rows=1, cols=1)
    table.alignment = WD_TABLE_ALIGNMENT.CENTER
    cell = table.cell(0, 0)
    shade_cell(cell, "FFF6E6")
    set_cell_borders(cell, "E0B070")
    p = cell.paragraphs[0]
    p.paragraph_format.space_after = Pt(0)
    r1 = p.add_run("【违规要素】")
    set_run_font_ex(r1, size=10, bold=True, color=(192, 80, 0))
    cat_text = " / ".join((LABEL_NUMBERS.get(label, "") + label) for label in labels)
    r2 = p.add_run(cat_text)
    set_run_font_ex(r2, size=10, bold=True, color=(192, 80, 0))
    r3 = p.add_run("　—　" + (note or "依据文案及页面文字判定为有害样本。"))
    set_run_font_ex(r3, size=9.5)


def add_record(
    doc: Document,
    record: Record,
    screenshot: dict[str, str],
    screenshot_root: Path,
    pre_harm: Optional[tuple[list[str], str]] = None,
) -> None:
    # MINIMAL_LAYOUT_V1 极简交付版式：每条仅「N.标题 / 链接 / 图片」三段，
    # 无元信息、无原文全文、无【概括】、无任何说明文字。
    p = doc.add_paragraph()
    p.paragraph_format.space_after = Pt(2)
    title_text = " ".join(as_text(record.title).split())
    if not title_text:
        title_text = " ".join(as_text(record.summary).split())
    if len(title_text) > 32:
        title_text = title_text[:32].rstrip() + "…"
    if not title_text:
        title_text = "（无标题）"
    rt = p.add_run(f"{record.item_id}.{title_text}")
    set_run_font_ex(rt, size=12, bold=True, color=(31, 78, 121))

    p = doc.add_paragraph()
    p.paragraph_format.space_after = Pt(4)
    rl = p.add_run(record.link)
    set_run_font_ex(rl, size=9.5, color=(11, 96, 176))

    status = as_text(screenshot.get("status") or "not_captured")
    quality = as_text(screenshot.get("quality") or ("usable" if screenshot.get("screenshot_path") else "unusable"))
    quality_reason = as_text(screenshot.get("quality_reason") or "")
    reason = as_text(screenshot.get("reason") or "not captured")
    image_path_text = str(screenshot.get("screenshot_path") or screenshot.get("path") or "")
    record.screenshot_status = status
    record.screenshot_reason = reason
    record.screenshot_path = image_path_text

    # 多图：images 优先（评论截图一条多张），退化到单张 screenshot_path
    image_list = [as_text(x) for x in (screenshot.get("images") or []) if as_text(x)]
    if not image_list and image_path_text:
        image_list = [image_path_text]
    inserted = 0
    if image_list and quality in {"usable", "needs_review"}:
        for _img_i, _img_fn in enumerate(image_list):
            image_path = Path(_img_fn)
            if not image_path.is_absolute():
                image_path = screenshot_root / image_path
            if not image_path.exists():
                continue
            try:
                p = doc.add_paragraph()
                p.paragraph_format.space_after = Pt(3)
                run = p.add_run()
                run.add_picture(
                    str(image_path), width=Cm(image_insert_width_cm(image_path, hi=16.0))
                )
                inserted += 1
            except Exception as exc:
                record.screenshot_status = "insert_error"
                record.screenshot_reason = str(exc)
    if not inserted:
        add_blank_screenshot_placeholder(doc, quality_reason or reason, status=status)

    labels, note = pre_harm if pre_harm is not None else classify_harm(record, screenshot, screenshot_root)
    record.harm_categories = labels
    record.harm_note = note
    # MINIMAL_LAYOUT_V1：判定结果只进 处理日志.csv 对账，不再渲染【违规要素】框。


def build_docx_batches(
    records: list[Record],
    batch_size: int,
    output_dir: Path,
    template: Path,
    screenshots: dict[str, dict[str, str]],
    screenshot_root: Path,
    title_extra: str = "",
) -> list[Path]:
    if not template.exists():
        raise SystemExit(f"Template not found: {template}")

    # 并发预分类（LLM 或关键词）
    print(f"[LLM] 开始并发违规判定（{len(records)} 条，并发={LLM_MAX_WORKERS}）…", flush=True)
    harm_cache: dict[int, tuple[list[str], str]] = {}

    def _classify_one(record: Record) -> tuple[int, tuple[list[str], str]]:
        sc = screenshots.get(str(record.item_id), {})
        return record.item_id, classify_harm(record, sc, screenshot_root)

    with ThreadPoolExecutor(max_workers=LLM_MAX_WORKERS) as pool:
        futs = {pool.submit(_classify_one, r): r for r in records}
        done = 0
        for fut in as_completed(futs):
            try:
                item_id, result = fut.result()
                harm_cache[item_id] = result
            except Exception:
                pass
            done += 1
            if done % 20 == 0 or done == len(records):
                print(f"[LLM] {done}/{len(records)} 完成", flush=True)

    output_paths: list[Path] = []
    total_batches = max(1, math.ceil(len(records) / batch_size))
    for batch_index in range(total_batches):
        chunk = records[batch_index * batch_size : (batch_index + 1) * batch_size]
        if not chunk:
            continue
        doc = Document(template)
        clear_document_body(doc)
        apply_doc_style(doc)
        # MINIMAL_LAYOUT_V1：不渲染批次标题与「本批编号范围」行（--title 仍接受但不落版）。
        for record in chunk:
            filename = f"第{batch_index + 1:02d}批.docx"
            record.output_file = filename
            add_record(
                doc,
                record,
                screenshots.get(str(record.item_id), {}),
                screenshot_root,
                pre_harm=harm_cache.get(record.item_id),
            )
        path = output_dir / f"第{batch_index + 1:02d}批.docx"
        doc.save(path)
        output_paths.append(path)
    return output_paths


def update_logs(logs: list[RowLog], records: list[Record]) -> None:
    by_id = {record.item_id: record for record in records}
    for log in logs:
        if log.status != "included" or not isinstance(log.item_id, int):
            continue
        record = by_id[log.item_id]
        log.output_file = record.output_file
        log.screenshot_status = record.screenshot_status
        log.screenshot_reason = record.screenshot_reason
        log.harm_categories = " / ".join(record.harm_categories or [])
        log.harm_note = record.harm_note


def write_log(logs: list[RowLog], output_dir: Path) -> Path:
    path = output_dir / "处理日志.csv"
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "source_row",
                "link",
                "status",
                "item_id",
                "duplicate_of",
                "output_file",
                "screenshot_status",
                "screenshot_reason",
                "harm_categories",
                "harm_note",
            ],
        )
        writer.writeheader()
        for log in logs:
            writer.writerow(log.__dict__)
    return path


def write_status_summary(logs: list[RowLog], output_dir: Path) -> Path:
    counts = Counter(log.screenshot_status or "not_captured" for log in logs if log.status == "included")
    path = output_dir / "截图状态汇总.csv"
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=["screenshot_status", "count"])
        writer.writeheader()
        for status, count in sorted(counts.items()):
            writer.writerow({"screenshot_status": status, "count": count})
    return path


def write_run_notes(logs: list[RowLog], output_dir: Path) -> tuple[Path, Path]:
    included = [log for log in logs if log.status == "included"]
    status_counts = Counter(log.screenshot_status or "not_captured" for log in included)
    issues: list[str] = []
    if status_counts.get("playwright_unavailable"):
        issues.append("- Playwright runtime unavailable; screenshots were not captured. Check Node module resolution and browser installation.")
    if status_counts.get("navigation_error"):
        issues.append(f"- {status_counts['navigation_error']} record(s) had navigation errors and therefore use screenshot failure placeholders.")
    login_or_blocked = status_counts.get("login_required", 0) + status_counts.get("blocked_or_blank", 0)
    if login_or_blocked:
        issues.append(f"- {login_or_blocked} screenshot(s) show login-required, blocked, or blank pages. The visible page state was still captured.")
    if not issues:
        issues.append("- No blocking issues detected during this run.")

    issue_path = output_dir / "问题记录.md"
    issue_path.write_text("# 问题记录\n\n" + "\n".join(issues) + "\n", encoding="utf-8")

    improvements = [
        "- If many pages are `login_required`, provide a Playwright `storage_state` file from a logged-in browser and rerun `capture_links.py`.",
        "- If many pages are `blocked_or_blank`, increase `--wait-ms` or use a logged-in/real-browser capture path for those domains.",
        "- Keep `截图状态汇总.csv` and `处理日志.csv` with the Word files so later reruns can target only failed or low-quality screenshots.",
    ]
    opt_path = output_dir / "优化方向.md"
    opt_path.write_text("# 优化方向\n\n" + "\n".join(improvements) + "\n", encoding="utf-8")
    return issue_path, opt_path


def write_done_marker(
    output_dir: Path,
    meta: dict[str, Any],
    records: list[Record],
    output_paths: list[Path],
    logs: list[RowLog],
) -> Path:
    included = [log for log in logs if log.status == "included"]
    marker = {
        "completed_at": datetime.now().isoformat(timespec="seconds"),
        "meta": meta,
        "docx_files": [path.name for path in output_paths],
        "docx_count": len(output_paths),
        "record_count": len(records),
        "included_log_rows": len(included),
        "screenshot_status_counts": dict(Counter(log.screenshot_status or "not_captured" for log in included)),
    }
    path = output_dir / "完成标记.json"
    path.write_text(json.dumps(marker, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def copy_self_if_requested(output_dir: Path, script_path: Path | None) -> None:
    if not script_path:
        return
    target = output_dir / script_path.name
    if script_path.resolve() != target.resolve():
        shutil.copy2(script_path, target)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("excel", type=Path, help="Input .xlsx workbook")
    parser.add_argument("--output-dir", type=Path, default=Path("输出Word"))
    parser.add_argument("--sheet", help="Worksheet name. Defaults to 搜索结果 when present.")
    parser.add_argument("--batch-size", type=int, default=50)
    parser.add_argument("--limit", type=int, help="Limit unique records for testing.")
    parser.add_argument("--id-min", type=int, help="Only keep rows whose id column (编号) >= this value.")
    parser.add_argument("--id-max", type=int, help="Only keep rows whose id column (编号) <= this value.")
    parser.add_argument("--title", help="Optional label added to each batch title, e.g. '编号400-900'.")
    parser.add_argument("--dry-run", action="store_true", help="Validate/read Excel and write links_manifest.json only.")
    parser.add_argument("--screenshots-manifest", type=Path, help="screenshots_manifest.json from capture_links.py")
    parser.add_argument("--template", type=Path, default=DEFAULT_TEMPLATE)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    excel_path = args.excel.resolve()
    if not excel_path.exists():
        raise SystemExit(f"Excel file not found: {excel_path}")
    if excel_path.name.startswith("~$"):
        raise SystemExit("Refusing to process Office lock file.")
    if args.batch_size <= 0:
        raise SystemExit("--batch-size must be greater than zero.")

    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    records, logs, meta = read_records(excel_path, args.sheet, args.id_min, args.id_max)
    if args.limit:
        records = records[: args.limit]
        keep_ids = {record.item_id for record in records}
        keep_rows = {record.source_row for record in records}
        logs = [
            log
            for log in logs
            if (log.status == "included" and log.item_id in keep_ids)
            or (log.status != "included" and log.source_row in keep_rows)
        ]
        meta["limited_unique_records"] = len(records)
    links_manifest = write_links_manifest(records, output_dir, meta)

    print(json.dumps(meta, ensure_ascii=False, indent=2))
    print(f"links_manifest: {links_manifest}")
    print(f"expected_batches: {math.ceil(len(records) / args.batch_size) if records else 0}")

    if args.dry_run:
        return 0

    screenshots = load_screenshots(args.screenshots_manifest)
    screenshot_root = args.screenshots_manifest.parent if args.screenshots_manifest else output_dir / "screenshots"
    output_paths = build_docx_batches(
        records,
        args.batch_size,
        output_dir,
        args.template.resolve(),
        screenshots,
        screenshot_root.resolve(),
        title_extra=args.title or "",
    )
    update_logs(logs, records)
    log_path = write_log(logs, output_dir)
    summary_path = write_status_summary(logs, output_dir)
    issue_path, opt_path = write_run_notes(logs, output_dir)
    done_path = write_done_marker(output_dir, meta, records, output_paths, logs)
    print(f"docx_files: {len(output_paths)}")
    for path in output_paths:
        print(path)
    print(f"log: {log_path}")
    print(f"screenshot_summary: {summary_path}")
    print(f"issues: {issue_path}")
    print(f"improvements: {opt_path}")
    print(f"done: {done_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
