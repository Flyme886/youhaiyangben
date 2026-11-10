#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""prep_manifest.py —— 监测 Excel → 有序抓取清单（manifest.json）

用法：
  prep_manifest.py <xlsx> <outdir> [--sheets 搜索结果,待复核] [--offset 0] [--limit 300]
                   [--out manifest.json] [--title-max 32]

规则：
- 多张 sheet 按给定顺序串成一条有序清单（默认「搜索结果」在前、「待复核」在后）
- 按归一化页面地址去重：同一视频/帖子被多个账号转发时，取「重复次数」最高的一条做代表
- 保留「攻击词」「攻击对象」，供抓取阶段在评论区做 DOM 命中定位与红框圈注
- --offset / --limit 用来切「每天 N 条」的那一段；item_id 是全局序号，编号可跨天连续
- 表头自适应，字段名不必完全一致（见 COLS 里的候选名）

输出的 manifest.json 结构：
{
  "meta": {"source", "total_all", "segment": [起, 止], "count", "platforms"},
  "items": [{"item_id", "shot_key", "platform", "title", "link", "nav_url",
             "attack_words", "attack_objects", ...}]
}
"""
import argparse
import json
import re
from collections import Counter
from pathlib import Path

import openpyxl

PLACEHOLDER = {"", "无", "-", "none", "None", "（无标题）", "(无标题)", "无标题"}

# 表头候选名（按优先级）。不同监测系统导出的列名不一样，这里做映射。
COLS = {
    "url": ("URL", "url", "链接", "原文链接", "原文/评论链接", "原链接"),
    "title": ("标题", "标题/微博内容", "作品标题", "正文标题"),
    "summary": ("摘要", "正文内容", "正文", "内容", "作品内容"),
    "platform": ("来源网站", "来源平台", "平台"),
    "author": ("作者", "原文作者", "原贴作者", "发布人", "账号昵称"),
    "date": ("发布时间", "发文时间", "日期", "时间"),
    "no": ("序号",),
    "attack_words": ("攻击词", "恶意词", "负面词"),
    "attack_objects": ("攻击对象", "对象", "涉及对象"),
    "content_type": ("内容类型",),
    "repeat": ("重复次数", "转发数", "热度"),
    "article_type": ("文章类型",),
}


def clean(t):
    return re.sub(r"\s+", " ", (t or "")).strip()


def fit(t, n=32):
    t = clean(t)
    return t[:n] + "…" if len(t) > n else t


def platform_of(url):
    u = (url or "").lower()
    if "douyin" in u:
        return "douyin"
    if "kuaishou" in u:
        return "kuaishou"
    if "weibo" in u:
        return "weibo"
    if "tieba" in u:
        return "tieba"
    if "zjurl.cn" in u or "toutiao" in u:
        return "toutiao"
    if "bilibili" in u:
        return "bilibili"
    if "xiaohongshu" in u or "xhslink" in u:
        return "xhs"
    if "weixin" in u or "/sph/" in u:
        return "weixin"
    return "other"


def nav_key(url, plat):
    """返回（导航地址, 去重键）。导航地址用于浏览器打开，去重键用于合并同一条内容。"""
    if plat == "douyin":
        m = re.search(r"/(?:share/)?video/(\d+)", url)
        if m:
            # /share/video/ 会跳验证码中间页，一律改走 /video/{id}
            return f"https://www.douyin.com/video/{m.group(1)}", f"dy:{m.group(1)}"
    if plat == "kuaishou":
        m = re.search(r"/short-video/([0-9a-zA-Z]+)", url)
        if m:
            return url, f"ks:{m.group(1)}"
    if plat == "weibo":
        m = re.search(r"weibo\.com/\d+/([A-Za-z0-9]+)", url) or re.search(r"detail/([A-Za-z0-9]+)", url)
        if m:
            # 桌面版必撞登录墙，移动版匿名可读
            return f"https://m.weibo.cn/detail/{m.group(1)}", f"wb:{m.group(1)}"
    if plat == "tieba":
        m = re.search(r"/p/(\d+)", url)
        if m:
            return url, f"tb:{m.group(1)}"
    if plat == "toutiao":
        m = re.search(r"/w/(\d+)", url) or re.search(r"thread/(\d+)", url)
        if m:
            return url, f"tt:{m.group(1)}"
    if plat == "xhs":
        m = re.search(r"/item/([0-9a-f]{24})", url) or re.search(r"explore/([0-9a-f]{24})", url)
        if m:
            # 监测表导出的链接不含 xsec_token，直开一律 404，需回站内搜索补 token
            return f"https://www.xiaohongshu.com/explore/{m.group(1)}", f"xhs:{m.group(1)}"
    return url, "u:" + url.split("?")[0].rstrip("/")


def split_words(s):
    if not s:
        return []
    parts = re.split(r"[、,，;；/|\s]+", str(s))
    return [p.strip() for p in parts if p.strip() and p.strip() not in PLACEHOLDER]


def col_index(header):
    idx = {}
    for key, names in COLS.items():
        for n in names:
            if n in header:
                idx[key] = header.index(n)
                break
    return idx


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("xlsx")
    ap.add_argument("outdir")
    ap.add_argument("--sheets", default="搜索结果,待复核",
                    help="按顺序拼接的 sheet 名，逗号分隔；都不存在时退回第一张")
    ap.add_argument("--offset", type=int, default=0, help="从第几条开始（0 起算）")
    ap.add_argument("--limit", type=int, default=300, help="本批取多少条")
    ap.add_argument("--out", default="manifest.json")
    ap.add_argument("--title-max", type=int, default=32)
    args = ap.parse_args()

    xlsx = Path(args.xlsx)
    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)

    wb = openpyxl.load_workbook(xlsx, data_only=True)
    names = [n.strip() for n in args.sheets.split(",") if n.strip()]
    sheets = [n for n in names if n in wb.sheetnames] or [wb.sheetnames[0]]

    ordered, best, seq_of = [], {}, {}
    total_rows = 0

    for si, name in enumerate(sheets):
        ws = wb[name]
        header = [str(c or "").strip() for c in next(ws.iter_rows(min_row=1, max_row=1, values_only=True))]
        ix = col_index(header)
        if "url" not in ix:
            print(f"! sheet「{name}」没找到链接列，跳过")
            continue
        rows = list(ws.iter_rows(min_row=2, values_only=True))
        total_rows += len(rows)
        for r in rows:
            g = lambda k: (r[ix[k]] if k in ix and ix[k] < len(r) else None)
            url = str(g("url") or "").strip()
            if not url:
                continue
            plat = platform_of(url)
            nav, key = nav_key(url, plat)
            try:
                rep = int(float(g("repeat") or 0))
            except (TypeError, ValueError):
                rep = 0
            try:
                no = int(float(g("no") or 0))
            except (TypeError, ValueError):
                no = 0
            rec = {
                "sheet": name, "source_row": no, "url": url, "nav_url": nav, "key": key,
                "platform": plat, "platform_name": clean(str(g("platform") or "")),
                "title_raw": clean(str(g("title") or "")),
                "summary": clean(str(g("summary") or "")),
                "author": clean(str(g("author") or "")),
                "date": clean(str(g("date") or "")),
                "attack_objects": split_words(g("attack_objects")),
                "attack_words": split_words(g("attack_words")),
                "content_type": clean(str(g("content_type") or "")),
                "article_type": clean(str(g("article_type") or "")),
                "repeat": rep,
            }
            if key not in best:
                ordered.append(key)
                best[key] = rec
                seq_of[key] = (si, no)
            elif rep > best[key]["repeat"]:
                best[key] = rec
                seq_of[key] = (si, no)

    uniq = sorted((best[k] for k in ordered), key=lambda x: seq_of[x["key"]])
    print(f"源表 {total_rows} 行 → 去重 {len(uniq)} 条（sheet: {', '.join(sheets)}）")

    seg = uniq[args.offset: args.offset + args.limit]
    items = []
    for global_no, rec in enumerate(seg, start=args.offset + 1):
        title = rec["title_raw"]
        if title in PLACEHOLDER:
            title = rec["summary"] if rec["summary"] not in PLACEHOLDER else ""
        items.append({
            "item_id": global_no,
            "sheet_id": global_no,
            "source": rec["platform_name"],
            "platform": rec["platform"],
            "platform_name": rec["platform_name"],
            "title": fit(title, args.title_max) or "（无标题）",
            "title_full": clean(title),
            "link": rec["url"],
            "nav_url": rec["nav_url"],
            "author": rec["author"],
            "summary": rec["summary"][:200],
            "date": rec["date"],
            "shot_key": f"{global_no:04d}",
            "dup_key": rec["key"],
            "attack_words": rec["attack_words"],
            "attack_objects": rec["attack_objects"],
            "content_type": rec["content_type"],
            "article_type": rec["article_type"],
            "repeat": rec["repeat"],
            "from_sheet": rec["sheet"],
        })

    p = outdir / args.out
    p.write_text(json.dumps({
        "meta": {
            "source": xlsx.name, "sheets": sheets, "total_all": len(uniq),
            "segment": [args.offset + 1, args.offset + len(seg)], "count": len(seg),
            "platforms": dict(Counter(x["platform"] for x in seg)),
        },
        "items": items,
    }, ensure_ascii=False, indent=1), encoding="utf-8")

    print(f"{p.name}: {len(seg)} 条（全局第 {args.offset + 1}-{args.offset + len(seg)} 条）")
    print("平台:", dict(Counter(x["platform_name"] for x in seg).most_common()))
    print("无攻击词:", sum(1 for x in items if not x["attack_words"]))


if __name__ == "__main__":
    main()
