#!/usr/bin/env python
"""Structurally verify generated harmful-sample Word batches.

校验项（脚本自身容易写错的地方，这里写对）：
  - 每份条目数 <= 50
  - 编号连续：注意仓库用【全局连续编号】（跨 11 份文件拼起来是 1..501），
    并非每份从 1 开始。因此逐份的连续性以该份自身的首个编号起算
    （numbers == range(numbers[0], numbers[0]+len(numbers))），并额外做
    全量无空洞校验。
  - 图片数 == 条目数（每条一条截图）
  - 【违规要素】表格数 == 条目数
  - 元信息形如：（编号400 · 原序号1234 · 抖音 · @作者）
  - 不得出现「举报理由草稿」

用法：
  python verify_docx.py [OUTDIR]
OUTDIR 默认当前目录下 "输出Word"；也可用环境变量 VERIFY_DIR。
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

from docx import Document

NUM_RE = re.compile(r"^(\d+)、")
# 元信息行：1、（编号400 · 原序号1234 · 抖音 · @作者）
META_RE = re.compile(r"^(\d+)、（编号[^·]* · 原序号[^·]* · [^·]* · @[^）]+）")
BLIP = "{http://schemas.openxmlformats.org/drawingml/2006/main}blip"


def check(path: Path) -> dict:
    doc = Document(path)
    numbers = []
    images = 0
    placeholder = 0
    boxes = 0
    draft_leak = 0
    meta_mismatch = 0
    for p in doc.paragraphs:
        text = p.text.strip()
        m = NUM_RE.match(text)
        if m:
            numbers.append(int(m.group(1)))
            if not META_RE.match(text):
                meta_mismatch += 1
        if "截图待补" in text:
            placeholder += 1
        if "举报理由草稿" in text:
            draft_leak += 1
        images += len(p._element.findall(f".//{BLIP}"))
    for t in doc.tables:
        for row in t.rows:
            for cell in row.cells:
                if "【违规要素】" in cell.text:
                    boxes += 1
    # 逐份连续：以该份首个编号起算（正确处理全局连续、非每份从 1 起的情况）
    continuous = (
        numbers == list(range(numbers[0], numbers[0] + len(numbers)))
        if numbers
        else False
    )
    return {
        "file": path.name,
        "items": len(numbers),
        "numbers_first": numbers[0] if numbers else None,
        "continuous": continuous,
        "images": images,
        "placeholders": placeholder,
        "eval_boxes": boxes,
        "meta_mismatch": meta_mismatch,
        "draft_leak": draft_leak,
    }


def main() -> int:
    outdir = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(os.environ.get("VERIFY_DIR", "输出Word"))
    files = sorted(outdir.glob("第*批.docx"))
    if not files:
        print("no docx found in", outdir)
        return 1

    ok = True
    all_numbers: list[int] = []
    for f in files:
        r = check(f)
        all_numbers.extend(range(r["numbers_first"], r["numbers_first"] + r["items"]) if r["items"] else [])
        bad = []
        if r["items"] == 0 or r["items"] > 50:
            bad.append("item_count")
        if not r["continuous"]:
            bad.append("numbering")
        if r["draft_leak"]:
            bad.append("draft_leak")
        if r["eval_boxes"] != r["items"]:
            bad.append("eval_box_mismatch")
        if r["images"] != r["items"]:
            bad.append("image_mismatch")
        if r["meta_mismatch"]:
            bad.append(f"meta_mismatch({r['meta_mismatch']})")
        ok = ok and not bad
        print(
            f"{r['file']}: items={r['items']} continuous={r['continuous']} images={r['images']} "
            f"placeholder={r['placeholders']} boxes={r['eval_boxes']} meta_mismatch={r['meta_mismatch']} "
            f"{'OK' if not bad else 'ISSUES:' + ','.join(bad)}"
        )

    # 全量连续性（无空洞、无重复）
    if all_numbers:
        uniq = sorted(set(all_numbers))
        gap_free = uniq == list(range(uniq[0], uniq[0] + len(uniq)))
        no_dup = len(uniq) == len(all_numbers)
        print(f"global: min={uniq[0]} max={uniq[-1]} count={len(all_numbers)} gap_free={gap_free} no_dup={no_dup}")
        ok = ok and gap_free and no_dup
    print("total_files", len(files))
    return 0 if ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
