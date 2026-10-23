#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""通用监测 Excel -> links_manifest.json（多平台版，带命令行参数）。

用法：
  python make_manifest_generic.py <xlsx> <out.json> [--tag 前缀]

规则与上一批一致：
- item_id 与 Excel 序号 1:1，标题折叠换行、超 32 字截断加「…」
- 标题为空或「无」时，兜底用摘要前 32 字，摘要也空则「（无标题）」
- 抓图按归一化导航地址去重（同一页面只截一次）
"""
import json
import re
import sys
from pathlib import Path
from urllib.parse import urlparse

import openpyxl

TITLE_MAX = 32
PLACEHOLDER_TITLES = {"", "无", "none", "-", "None", "（无标题）", "(无标题)", "无标题"}


def clean(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "")).strip()


def fit_title(text: str) -> str:
    t = clean(text)
    return t[:TITLE_MAX] + "…" if len(t) > TITLE_MAX else t


def platform_of(url: str) -> str:
    host = urlparse(url).netloc.lower()
    if "douyin" in host:
        return "douyin"
    if "xiaohongshu" in host:
        return "xhs"
    if "weibo" in host:
        return "weibo"
    if "bilibili" in host:
        return "bilibili"
    if "tieba" in host:
        return "tieba"
    if "weixin" in host:
        return "weixin"
    if "toutiao" in host:
        return "toutiao"
    return "other"


def nav_url(url: str, platform: str) -> str:
    if platform == "douyin":
        m = re.search(r"/share/video/(\d+)", url)
        if m:
            return f"https://www.douyin.com/video/{m.group(1)}"
    if platform == "weibo":
        m = re.search(r"weibo\.com/\d+/([A-Za-z0-9]+)", url)
        if m:
            return f"https://m.weibo.cn/detail/{m.group(1)}"
    if platform == "xhs":
        m = re.search(r"/item/([0-9a-f]{24})", url)
        if m:
            return f"https://www.xiaohongshu.com/explore/{m.group(1)}"
    return url


def read_rows(ws):
    """兼容两种导出表头。

    A) 搜索监测表：序号 / 标题(微博内容) / 原文(评论)链接 / 来源网站 / 日期 / 媒体类型 / 原文作者 / 摘要 ...
    B) 热度榜表：  序号 / 标题(=平台) / 发布人 / 标题(=正文标题) / 摘要 / 原链接 / 发布时间
    """
    header = [str(c or "").strip() for c in next(ws.iter_rows(min_row=1, max_row=1, values_only=True))]
    idx = {}
    title_cols = [i for i, h in enumerate(header) if h == "标题"]

    if "原链接" in header:
        idx = {
            "no": header.index("序号") if "序号" in header else None,
            "platform": title_cols[0] if title_cols else None,
            "author": header.index("发布人") if "发布人" in header else None,
            "title": title_cols[-1] if title_cols else None,
            "summary": header.index("摘要") if "摘要" in header else None,
            "link": header.index("原链接"),
            "date": header.index("发布时间") if "发布时间" in header else None,
            "source": None,
        }
    else:
        idx = {
            "no": header.index("序号") if "序号" in header else None,
            "platform": None,
            "author": header.index("原文作者") if "原文作者" in header else None,
            "title": header.index("标题/微博内容") if "标题/微博内容" in header else 1,
            "summary": header.index("摘要") if "摘要" in header else None,
            "link": header.index("原文/评论链接") if "原文/评论链接" in header else 2,
            "date": header.index("日期") if "日期" in header else None,
            "source": header.index("来源网站") if "来源网站" in header else None,
        }

    def cell(row, key):
        i = idx.get(key)
        if i is None or i >= len(row):
            return ""
        v = row[i]
        return "" if v is None else str(v)

    out = []
    for row in ws.iter_rows(min_row=2, values_only=True):
        link = cell(row, "link").strip()
        title = cell(row, "title")
        if not (link or title):
            continue
        out.append(
            {
                "no": cell(row, "no"),
                "title": title,
                "link": link,
                "author": cell(row, "author"),
                "summary": cell(row, "summary"),
                "date": cell(row, "date"),
                "platform_name": cell(row, "platform"),
                "source": cell(row, "source"),
            }
        )
    return out


def build(xlsx: Path, out: Path, tag: str = "") -> dict:
    wb = openpyxl.load_workbook(xlsx, data_only=True)
    ws = wb.worksheets[0]
    rows = read_rows(ws)
    items = []
    seq = 0
    no_title = 0
    for row in rows:
        try:
            no = int(float(row["no"])) if row["no"] else seq + 1
        except ValueError:
            no = seq + 1
        raw_title = row["title"]
        summary = clean(row["summary"])
        link = row["link"]
        plat = platform_of(link)
        if plat == "other" and row["platform_name"]:
            name = row["platform_name"]
            for kw, key in (
                ("抖音", "douyin"),
                ("头条", "toutiao"),
                ("微博", "weibo"),
                ("小红书", "xhs"),
                ("哔哩", "bilibili"),
                ("B站", "bilibili"),
                ("微信", "weixin"),
            ):
                if kw in name:
                    plat = key
                    break
        seq += 1
        title = fit_title(raw_title)
        if clean(raw_title) in PLACEHOLDER_TITLES:
            no_title += 1
            title = fit_title(summary) if summary else "（无标题）"
        items.append(
            {
                "item_id": seq,
                "source_row": no,
                "original_no": no,
                "sheet_id": str(no),
                "source": row["source"] or row["platform_name"],
                "platform": plat,
                "platform_name": row["platform_name"],
                "title": title,
                "title_full": clean(raw_title),
                "link": link,
                "nav_url": nav_url(link, plat),
                "author": row["author"],
                "summary": summary[:200],
                "date": row["date"],
                "shot_key": f"{seq:05d}",
            }
        )
    by_link: dict[str, str] = {}
    for it in items:
        by_link.setdefault(it["nav_url"], it["shot_key"])
        it["shot_key"] = by_link[it["nav_url"]]

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(
            {
                "meta": {
                    "xlsx": xlsx.name,
                    "tag": tag,
                    "total_items": len(items),
                    "unique_pages": len(by_link),
                    "no_title_items": no_title,
                    "title_max": TITLE_MAX,
                    "layout": "MINIMAL_LAYOUT_V1",
                    "batch_size": 50,
                },
                "items": items,
            },
            ensure_ascii=False,
            indent=1,
        ),
        encoding="utf-8",
    )
    print(f"{xlsx.name}: {len(items)} 条 / {len(by_link)} 个唯一页面 / 无标题 {no_title}")
    return {"items": len(items), "pages": len(by_link)}


if __name__ == "__main__":
    x = Path(sys.argv[1])
    o = Path(sys.argv[2])
    t = sys.argv[3] if len(sys.argv) > 3 else ""
    build(x, o, t)
