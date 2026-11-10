#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""结构自检（兼容「主图 + 命中图」两图版式）

每条固定序列：N.标题（12pt 加粗 #1F4E79）→ 链接（9.5pt #0B60B0，http 开头）→ 1~2 张居中图
"""
import re
import sys
import zipfile
from pathlib import Path

BAN = ("截图待补", "需人工核验", "部分完成", "待补", "说明：", "备注：")
MAX_W_CM, MAX_H_CM = 16.05, 20.55


def paras_of(docx: Path):
    z = zipfile.ZipFile(docx)
    xml = z.read("word/document.xml").decode("utf8")
    body = xml.split("<w:body>")[1]
    out = []
    for p in re.findall(r"<w:p[ >].*?</w:p>|<w:p/>", body, re.S):
        text = re.sub(r"<[^>]+>", "", p).strip()
        ext = [(int(a) / 360000, int(b) / 360000) for a, b in re.findall(r'<wp:extent cx="(\d+)" cy="(\d+)"', p)]
        bold = "<w:b/>" in p or "<w:b " in p
        size = re.findall(r'<w:sz w:val="(\d+)"', p)
        color = re.findall(r'<w:color w:val="([0-9A-Fa-f]{6})"', p)
        out.append({"t": text, "img": ext, "bold": bold, "size": size, "color": color})
    return out


def check(docx: Path):
    ps = [p for p in paras_of(docx) if p["t"] or p["img"]]
    errs, nums, imgs_total = [], [], 0
    i = 0
    while i < len(ps):
        p = ps[i]
        m = re.match(r"^(\d+)\.(.+)$", p["t"], re.S)
        if not m:
            errs.append(f"疑似孤立段落：{p['t'][:40] or '(空)'}")
            i += 1
            continue
        no = int(m.group(1))
        nums.append(no)
        if not p["bold"]:
            errs.append(f"条 {no} 标题未加粗")
        if not p["size"] or p["size"][0] != "24":
            errs.append(f"条 {no} 标题字号异常 {p['size']}")
        if not p["color"] or p["color"][0].upper() != "1F4E79":
            errs.append(f"条 {no} 标题颜色异常 {p['color']}")
        if not m.group(2).strip():
            errs.append(f"条 {no} 标题为空")
        i += 1
        # 链接
        if i < len(ps) and ps[i]["t"].startswith("http"):
            lk = ps[i]
            if not lk["size"] or lk["size"][0] != "19":
                errs.append(f"条 {no} 链接字号异常 {lk['size']}")
            if not lk["color"] or lk["color"][0].upper() != "0B60B0":
                errs.append(f"条 {no} 链接颜色异常")
            i += 1
        else:
            errs.append(f"条 {no} 缺少链接段")
        # 图片 1~2 张
        cnt = 0
        while i < len(ps) and ps[i]["img"] and not ps[i]["t"]:
            for w, h in ps[i]["img"]:
                if w > MAX_W_CM or h > MAX_H_CM:
                    errs.append(f"条 {no} 图片超限 {w:.1f}x{h:.1f}cm")
            cnt += 1
            imgs_total += 1
            i += 1
        if cnt == 0:
            errs.append(f"条 {no} 没有截图")
        elif cnt > 2:
            errs.append(f"条 {no} 图片数异常 {cnt}")
    if nums and nums != list(range(nums[0], nums[0] + len(nums))):
        errs.append(f"编号不连续：{nums[:5]}...{nums[-3:]}")
    if len(nums) > 50:
        errs.append(f"超过 50 条：{len(nums)}")
    body_text = "".join(p["t"] for p in ps)
    for b in BAN:
        if b in body_text:
            errs.append(f"出现禁用文案「{b}」")
    return {"file": docx.name, "count": len(nums), "imgs": imgs_total, "errs": errs}


def main():
    d = Path(sys.argv[1])
    files = sorted(d.glob("*.docx"))
    if not files:
        print("没有 docx")
        return 1
    bad = 0
    for f in files:
        r = check(f)
        tag = "OK " if not r["errs"] else "ERR"
        print(f"{tag} {r['file']}: {r['count']} 条 / {r['imgs']} 图")
        for e in r["errs"][:12]:
            print("    -", e)
        bad += len(r["errs"])
    print(("全部通过" if bad == 0 else f"共 {bad} 处问题"))
    return 0 if bad == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
