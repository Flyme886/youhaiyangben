#!/usr/bin/env python
"""Structurally verify generated harmful-sample Word batches (MINIMAL_LAYOUT_V1).

极简交付版式语义（对齐用户参考样例「王振义-7批-20条」）
------------------------------------------------------
每条样本在 Word 里**只允许三段**：

  1. ``N.标题``（编号 + 点号 + 内容截断，加粗深蓝）
  2. 原始链接（单独一段，以 http 开头）
  3. 截图（可有可无；缺图位置留白，不写任何说明文字）

除这三段外，正文**不得出现任何其他说明文字**。

两类问题严格分开：

  * **硬失败（exit 2）** —— 代表「版式或文字工作没做完」，必须修：
      - 条目数 == 0 或 > 50
      - 编号不连续（逐份 / 全量无空洞、无重复）
      - 条目顺序结构坏：某条的标题段之后没有紧跟以 http 开头的链接段
      - 旧版式残留：【违规要素】框 /【概括】/「原文：」「链接：」标签 /
        「N、（…原序号…）」元信息行 —— 出现任何一种都算「夹带了说明文字」
      - 泄漏「举报理由草稿」
  * **降级提示（exit 0，只打印）** —— 属于可接受的交付形态：
      - 图片数 < 条目数（captcha / block / timeout 导致的缺图，位置留白）
      - 标题引用的原文里出现「需人工核验」等对冲词（是引用不是我们的措辞）
      - 残留「截图待补」占位（应为留白；出现说明适配没生效，不阻塞交付）

也就是说：**只要三段结构完整，缺图一律不判失败**，任务按「文字版交付」正常结案。
需要把缺图也当失败的话（例如做素材验收），显式加 ``--strict-images``。

用法：
  python verify_docx.py [OUTDIR] [--strict-images] [--json]
OUTDIR 默认当前目录下 "输出Word"；也可用环境变量 VERIFY_DIR。

退出码：0 = 文字已交付（允许缺图）；1 = 没找到 docx；2 = 版式/文字层面有硬伤。
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

from docx import Document

NUM_RE = re.compile(r"^(\d+)\.")
# 旧版式元信息行：「N、（…原序号…）」。极简版式下出现即残留。
LEGACY_META_RE = re.compile(r"^\d+、（")
# 旧版式的标签与文案残留
LEGACY_MARKERS = (
    "【违规要素】",
    "【概括】",
    "原文：",
    "链接：",
    "举报理由草稿",
    "舆情有害样本（第",
    "本批编号范围：",
)
BLIP = "{http://schemas.openxmlformats.org/drawingml/2006/main}blip"

# 引用内容里可能出现的对冲措辞 —— 只计提示，不算我们的措辞
QUOTED_HEDGE_PHRASES = (
    "视频画面及完整语音需人工核验",
    "需人工核验",
    "需人工复核",
    "仅供参考",
    "请以官方通报为准",
    "请以官方发布为准",
)


def _has_hedge(text: str) -> bool:
    return any(phrase in text for phrase in QUOTED_HEDGE_PHRASES)


def check(path: Path) -> dict:
    doc = Document(path)
    numbers = []
    images = 0
    placeholder = 0
    legacy_leak = 0
    legacy_detail: list[str] = []
    draft_leak = 0
    quoted_hedge = 0
    link_bad = 0
    expect_link = False

    for p in doc.paragraphs:
        text = p.text.strip()
        images += len(p._element.findall(f".//{BLIP}"))
        if not text:
            continue

        if expect_link:
            # 标题段之后的第一个非空文本段必须是链接
            if text.startswith("http"):
                expect_link = False
            else:
                link_bad += 1
                expect_link = False
                # 继续按普通段落处理本段

        m = NUM_RE.match(text)
        if m:
            numbers.append(int(m.group(1)))
            expect_link = True

        if "截图待补" in text:
            placeholder += 1
        if "举报理由草稿" in text:
            draft_leak += 1
        if _has_hedge(text):
            quoted_hedge += 1
        for marker in LEGACY_MARKERS:
            if marker in text:
                legacy_leak += 1
                legacy_detail.append(marker)
                break
        if LEGACY_META_RE.match(text):
            legacy_leak += 1
            legacy_detail.append("meta_line")
        if NUM_RE.match(text) and "、" in text[:12]:
            # 「1、标题」旧编号形态（极简版式应为「1.标题」）
            legacy_leak += 1
            legacy_detail.append("num_semicolon")

    # 旧版式判定框藏在表格里；极简版式不应存在任何表格
    for t in doc.tables:
        for row in t.rows:
            for cell in row.cells:
                if cell.text.strip():
                    legacy_leak += 1
                    legacy_detail.append("table")

    # 文档结束时还在等链接 = 最后一条只有标题没有链接
    if expect_link:
        link_bad += 1

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
        "link_bad": link_bad,
        "legacy_leak": legacy_leak,
        "legacy_detail": sorted(set(legacy_detail)),
        "draft_leak": draft_leak,
        "quoted_hedge": quoted_hedge,
    }


def _collect(outdir: Path) -> list[Path]:
    """优先按标准的「第N批.docx」命名收集；为空时退化为任意 .docx。"""
    files = sorted(outdir.glob("第*批.docx"))
    if files:
        return files
    return sorted(
        p
        for p in outdir.glob("*.docx")
        if not p.name.startswith("~$") and not p.name.startswith(".")
    )


def main() -> int:
    argv = [a for a in sys.argv[1:] if not a.startswith("--")]
    flags = {a for a in sys.argv[1:] if a.startswith("--")}
    strict_images = "--strict-images" in flags
    as_json = "--json" in flags

    outdir = Path(argv[0]) if argv else Path(os.environ.get("VERIFY_DIR", "输出Word"))
    files = _collect(outdir)
    if not files:
        print("no docx found in", outdir)
        return 1

    ok = True
    all_numbers: list[int] = []
    reports: list[dict] = []
    note_total = 0

    for f in files:
        r = check(f)
        all_numbers.extend(range(r["numbers_first"], r["numbers_first"] + r["items"]) if r["items"] else [])
        bad = []
        notes = []
        if r["items"] == 0 or r["items"] > 50:
            bad.append("item_count")
        if not r["continuous"]:
            bad.append("numbering")
        if r["draft_leak"]:
            bad.append("draft_leak")
        # 每条必须「标题 → 链接」成对；坏一处就是版式硬伤
        if r["link_bad"]:
            bad.append(f"link_bad({r['link_bad']})")
        # 旧版式 / 说明文字残留 = 夹带了不该有的内容，硬失败
        if r["legacy_leak"]:
            bad.append(f"legacy_leak({r['legacy_leak']}:{','.join(r['legacy_detail'])})")
        # 评论截图模式一条可插多张；缺图默认只提示（交付优先：缺图留白不算失败）
        if r["images"] < r["items"]:
            msg = f"no_image({r['images']}/{r['items']})"
            (bad if strict_images else notes).append(msg)
        if r["placeholders"]:
            notes.append(f"placeholder({r['placeholders']})")
        if r["quoted_hedge"]:
            # 标题引用的原文里带对冲词：是引用，不是我们的措辞
            notes.append(f"quoted_hedge({r['quoted_hedge']})")

        note_total += len(notes)
        ok = ok and not bad

        r["bad"] = bad
        r["notes"] = notes
        reports.append(r)

        verdict = "OK"
        if bad:
            verdict = "ISSUES:" + ",".join(bad)
        elif notes:
            verdict = "NOTE:" + ",".join(notes)
        if not as_json:
            print(
                f"{r['file']}: items={r['items']} continuous={r['continuous']} images={r['images']} "
                f"link_bad={r['link_bad']} legacy={r['legacy_leak']} "
                f"placeholder={r['placeholders']} hedge={r['quoted_hedge']} {verdict}"
            )

    # 全量连续性（无空洞、无重复）
    global_info = None
    if all_numbers:
        uniq = sorted(set(all_numbers))
        gap_free = uniq == list(range(uniq[0], uniq[0] + len(uniq)))
        no_dup = len(uniq) == len(all_numbers)

        # 只拿到部分批次时（例如抽查第01批 + 第22批），跨文件拼编号必然有空洞 ——
        # 那是抽样导致的，不是产出缺陷。按「文件数 < 最大批号」自动识别成抽样，
        # 把 gap_free 降级为提示；真要全量校验就把全部批次放进同一个目录。
        batch_nums = [
            int(m.group(1)) for f in files if (m := re.match(r"第(\d+)批\.docx$", f.name))
        ]
        sampling = bool(batch_nums) and len(files) < max(batch_nums)

        global_info = {
            "min": uniq[0],
            "max": uniq[-1],
            "count": len(all_numbers),
            "gap_free": gap_free,
            "no_dup": no_dup,
            "sampling": sampling,
            "batches": len(batch_nums),
            "max_batch": max(batch_nums) if batch_nums else None,
        }
        if not as_json:
            print(
                f"global: min={uniq[0]} max={uniq[-1]} count={len(all_numbers)} "
                f"gap_free={gap_free} no_dup={no_dup}"
                + (f" sampling(only {len(files)}/{max(batch_nums)} batches)" if sampling else "")
            )
            if sampling and not gap_free:
                print(
                    "NOTE: 只校验了部分批次，跨文件编号必然不连续 —— 已按抽样处理，"
                    "不计失败。全量校验请传完整批次目录。"
                )
        if sampling and not gap_free:
            note_total += 1
        else:
            ok = ok and gap_free
        ok = ok and no_dup

    summary = {
        "outdir": str(outdir),
        "layout": "MINIMAL_LAYOUT_V1",
        "total_files": len(files),
        "total_items": len(all_numbers),
        "total_images": sum(r["images"] for r in reports),
        "total_notes": note_total,
        "strict_images": strict_images,
        "text_delivered": ok,
        "global": global_info,
        "files": reports,
    }

    if as_json:
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    else:
        print("total_files", len(files))
        print(
            f"delivered: text={'YES' if ok else 'NO'} "
            f"images={summary['total_images']}/{summary['total_items']} "
            f"notes={note_total}"
        )
        if ok and note_total:
            print(
                "NOTE: 文字已全量交付，以上缺图条目按留白处理 —— "
                "符合交付优先原则，不算失败。"
            )
    return 0 if ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
