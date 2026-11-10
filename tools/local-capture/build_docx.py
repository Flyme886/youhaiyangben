#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""MINIMAL_LAYOUT_V1 Word 生成器（参数化，可多平台复用）

每条样本严格三段：N.标题 / 原始链接 / 页面截图
- 标题 12pt 加粗 #1F4E79，段前 2pt
- 链接 9.5pt #0B60B0，段前 4pt
- 截图段居中，横图固定 16.0cm 宽，竖图按高 20.5cm 限幅
- 没有可用截图的条目整条不写（编号自动前移），不写任何说明文字
- 文件名：日期-平台-第几批.docx，如「10月1日-b站-第一批.docx」
"""
import argparse
import datetime
import json
import re
from pathlib import Path

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.image.image import Image as DocxImage
from docx.oxml.ns import qn
from docx.shared import Cm, Pt, RGBColor

COLOR_TITLE = RGBColor(0x1F, 0x4E, 0x79)
COLOR_LINK = RGBColor(0x0B, 0x60, 0xB0)
MAX_W, MAX_H = 16.0, 20.5
TITLE_MAX = 32
DELETED_HINT = ("不存在", "已删除", "已被删除", "无法查看", "帖子可能已被删除")

# 文件名里的平台写法（示例：10月1日-b站-第一批）
PLATFORM_FILE = {
    "bilibili": "b站",
    "weibo": "微博",
    "xhs": "小红书",
    "douyin": "抖音",
    "tieba": "贴吧",
    "weixin": "微信",
    "toutiao": "头条",
    "other": "其他",
}
# 对话里回话用的写法
PLATFORM_SHOW = {
    "bilibili": "B站",
    "weibo": "微博",
    "xhs": "小红书",
    "douyin": "抖音",
    "tieba": "贴吧",
    "weixin": "微信",
    "toutiao": "头条",
}
_CN = "零一二三四五六七八九十"


def cn_num(n: int) -> str:
    if n <= 10:
        return _CN[n]
    if n < 20:
        return "十" + _CN[n - 10]
    if n < 100:
        t, o = divmod(n, 10)
        return _CN[t] + "十" + (_CN[o] if o else "")
    return str(n)

# 表格没给标题时，用抓取时从页面读到的标题兜底（页面读到的内容允许入档）
SITE_WORDS = ("哔哩哔哩", "bilibili", "BILIBILI", "BiliBili", "B站", "小红书", "抖音", "微博")
BAD_TITLE_RE = re.compile(r"验证码|安全验证|人机验证|访问异常|访问频繁|出错|页面不存在|登录|404|undefined|Not Found")


def clean_page_title(raw: str) -> str:
    t = re.sub(r"\s+", " ", raw or "").strip()
    for w in SITE_WORDS:
        t = t.replace(w, " ")
    t = re.sub(r"\s{2,}", " ", t)
    t = t.strip(" _-|｜—–·,")
    t = re.sub(r"[_\-|｜—–]\s*$", "", t).strip()
    if not t or BAD_TITLE_RE.search(t):
        return ""
    return t[:TITLE_MAX] + "…" if len(t) > TITLE_MAX else t


def display_title(item: dict, shot: dict | None) -> str:
    t = (item.get("title") or "").strip()
    if t and t != "（无标题）":
        return t
    return clean_page_title((shot or {}).get("page_title") or "") or "（无标题）"


def usable(shot: dict | None) -> bool:
    """只认 ok。页面明说「已删除 / 不存在 / 不可见」的一律剔除，不进文档。"""
    if not shot:
        return False
    # 删除判定在抓取阶段已完成（status != ok 即已删/限流），此处只认 ok
    return shot.get("status") == "ok"


def load_shots(dirs: list[Path]) -> dict:
    out = {}
    for d in dirs:
        if not d.exists():
            continue
        for f in sorted(d.glob("results_*.jsonl")):
            for line in f.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                try:
                    o = json.loads(line)
                except json.JSONDecodeError:
                    continue
                k = str(o.get("shot_key"))
                if k not in out or o.get("status") == "ok":
                    out[k] = o
    return out


def new_doc() -> Document:
    doc = Document()
    style = doc.styles["Normal"]
    style.font.name = "微软雅黑"
    style.font.size = Pt(10.5)
    rpr = style.element.get_or_add_rPr()
    fonts = rpr.get_or_add_rFonts()
    for attr in ("w:eastAsia", "w:ascii", "w:hAnsi"):
        fonts.set(qn(attr), "微软雅黑")
    sec = doc.sections[0]
    sec.page_width, sec.page_height = Cm(21.59), Cm(27.94)
    sec.left_margin = sec.right_margin = Cm(3.175)
    sec.top_margin = sec.bottom_margin = Cm(2.54)
    return doc


def add_pic(p, img: Path) -> bool:
    """按横图 16cm / 竖图 20.5cm 限幅插入一张图"""
    try:
        info = DocxImage.from_file(str(img))
        w_px, h_px = info.px_width, info.px_height
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        p.paragraph_format.space_before = Pt(4)
        p.paragraph_format.space_after = Pt(0)
        if h_px > w_px:
            p.add_run().add_picture(str(img), width=Cm(min(MAX_W, MAX_H * w_px / h_px)), height=Cm(MAX_H))
        else:
            p.add_run().add_picture(str(img), width=Cm(MAX_W))
        return True
    except Exception as e:
        print(f"  ! 插图失败 {img.name}: {e}")
        return False


def add_item(doc, n: int, item: dict, shot: dict | None, shots_dir: Path) -> bool:
    p = doc.add_paragraph()
    p.paragraph_format.space_before = Pt(2)
    r = p.add_run(f"{n}.{display_title(item, shot)}")
    r.bold = True
    r.font.size = Pt(12)
    r.font.color.rgb = COLOR_TITLE

    p2 = doc.add_paragraph()
    p2.paragraph_format.space_before = Pt(4)
    r2 = p2.add_run(item["link"])
    r2.font.size = Pt(9.5)
    r2.font.color.rgb = COLOR_LINK

    if not (shot and usable(shot)):
        return False
    key = item["shot_key"]
    main_img = shots_dir / f"{key}.png"
    if not main_img.exists():
        return False
    ok = add_pic(doc.add_paragraph(), main_img)
    hit_img = shots_dir / f"{key}_hit.png"
    if hit_img.exists():
        add_pic(doc.add_paragraph(), hit_img)
    return ok


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--manifest", required=True)
    ap.add_argument("--shots", required=True)
    ap.add_argument("--results", nargs="+", default=[])
    ap.add_argument("--outdir", required=True)
    ap.add_argument("--prefix", default="", help="完整文件名前缀，给了就跳过「日期-平台-第」自动命名")
    ap.add_argument("--name-date", default="", help="文件名里的日期，默认今天，如 10月24日")
    ap.add_argument("--platform-label", default="", help="文件名里的平台写法，默认按清单自动取，如 b站")
    ap.add_argument("--batch", type=int, default=50)
    ap.add_argument("--start-num", type=int, default=1, help="首条编号，补抓同一平台后续批次时接着上一批（如 17）")
    ap.add_argument("--batch-index", type=int, default=1, help="批次序号起点，补抓时填 2 即出「第二批」")
    ap.add_argument("--keep-blank", action="store_true", help="旧行为：无图条目留空占位（默认整条不记录）")
    ap.add_argument("--triage", default="", help="triage.py --json 的分类结果，用于区分已删/限流")
    args = ap.parse_args()
    if not args.name_date:
        d = datetime.date.today()
        args.name_date = f"{d.month}月{d.day}日"

    raw = json.loads(Path(args.manifest).read_text(encoding="utf-8"))
    items = raw["items"] if isinstance(raw, dict) else raw
    shots_dir = Path(args.shots)
    shots = load_shots([Path(p) for p in args.results])
    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)

    verdict = {}
    if args.triage and Path(args.triage).exists():
        for r in json.loads(Path(args.triage).read_text(encoding="utf-8")).get("rows", []):
            verdict[str(r["shot_key"])] = r

    usable_items, dropped = [], []
    for item in items:
        shot = shots.get(str(item["shot_key"]))
        img = shots_dir / f"{item['shot_key']}.png"
        if args.keep_blank:
            usable_items.append(item)
            if not (shot and usable(shot) and img.exists()):
                dropped.append(item)
        elif shot and usable(shot) and img.exists():
            usable_items.append(item)
        else:
            dropped.append(item)

    if not usable_items:
        print("无可入册条目：没有任何一条拿到页面截图，本次不产出 docx。")
        for item in dropped:
            v = verdict.get(str(item["shot_key"]), {})
            print(f"  {item['item_id']}. [{v.get('verdict', 'unknown')}] {item.get('author', '')} {item['link']}")
        return 0

    nb = (len(usable_items) + args.batch - 1) // args.batch

    if args.prefix:
        name_head = args.prefix
    else:
        label = args.platform_label or PLATFORM_FILE.get(platform_key_of(usable_items), "其他")
        name_head = f"{args.name_date}-{label}-第"

    for b in range(nb):
        chunk = usable_items[b * args.batch : (b + 1) * args.batch]
        doc = new_doc()
        got = 0
        for idx, item in enumerate(chunk, start=b * args.batch + args.start_num):
            if add_item(doc, idx, item, shots.get(str(item["shot_key"])), shots_dir):
                got += 1
        if args.prefix:
            name = f"{name_head}{b + args.batch_index:02d}批.docx"
        else:
            name = f"{name_head}{cn_num(b + args.batch_index)}批.docx"
        doc.save(outdir / name)
        n_img = sum(
            1
            for it in chunk
            for nm in (f"{it['shot_key']}.png", f"{it['shot_key']}_hit.png")
            if (shots_dir / nm).exists()
        )
        print(f"{name}: {len(chunk)} 条，{n_img} 张图（含命中图）")

    imgs = sum(
        1
        for it in usable_items
        for name in (f"{it['shot_key']}.png", f"{it['shot_key']}_hit.png")
        if (shots_dir / name).exists()
    )
    print(f"合计入册 {len(usable_items)} 条 / 图 {imgs} 张")
    show = PLATFORM_SHOW.get(platform_key_of(usable_items), "该平台")
    if dropped:
        gone = [i for i in dropped if verdict.get(str(i["shot_key"]), {}).get("verdict") == "deleted"]
        stuck = [i for i in dropped if i not in gone]
        print(f"未入册 {len(dropped)} 条：已删除/不可见 {len(gone)} 条，未取到（限流等）{len(stuck)} 条")
        for i in gone:
            print(f"  {show}「{i.get('author', '') or '无署名'}」的帖子已经删除，未列入")
        for i in stuck:
            print(f"  {show}「{i.get('author', '') or '无署名'}」没有拿到截图")
    return 0


def platform_key_of(items) -> str:
    cnt = {}
    for it in items:
        p = it.get("platform") or "other"
        cnt[p] = cnt.get(p, 0) + 1
    return max(cnt, key=cnt.get) if cnt else "other"


if __name__ == "__main__":
    raise SystemExit(main())
