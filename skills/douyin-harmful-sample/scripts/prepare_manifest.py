#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""prepare_manifest.py —— 抓取【前置流程】：清单去重 + 批次标记

放在「引擎产出 links_manifest.json」之后、「Ego Lite 抓取」之前。

做三件事
--------
1) **按链接去重**（同一视频在 Excel 里被重复收录时，只抓一次，避免重复消耗风控额度）
   两种口径，二选一：
   - `--drop-dups`（默认，业务已确认）：重复条**不再输出**，只抓首次出现的条目
   - `--keep-dups`：重复条保留在清单里，但 `original_no`（序号）**留空**、
     `capture_comments=false`、`dup_of_item_id` 指向首条 —— 抓取时跳过、
     出 Word 时复用首条截图（条目数不变）
   两种口径都会把被处理的重复条写进 `--dropped-csv` 供核对。

2) **批次标记**（决定哪些条目抓评论截图，用于控风控）
   按清单顺序每 `--batch-size` 条算一批（与引擎 `--batch-size` 保持一致，默认 50），
   批内前 `--comments-per-batch` 条（默认 20）标 `capture_comments=true`，其余 false
   （false 的条目抓取时只截视频页一张，不点开评论区）。
   `--comment-scope all`（默认）时全部标 true —— 用于「先全量跑，测限流情况」；
   一旦发现限流严重，改 `--comment-scope first_n` 重跑本脚本即可降级，无需重抓已完成的条目。

3) 写出统计，便于对账。

用法
----
    PY=~/.workbuddy/binaries/python/envs/default/bin/python
    # 全量测限流（全部条目都抓评论）
    $PY prepare_manifest.py --in "$OUTDIR/links_manifest.json" \
        --out "$OUTDIR/links_manifest.json" --drop-dups --comment-scope all

    # 降级：每批 50 条里只有前 20 条抓评论
    $PY prepare_manifest.py --in "$OUTDIR/links_manifest.json" \
        --out "$OUTDIR/links_manifest.json" --drop-dups \
        --comment-scope first_n --batch-size 50 --comments-per-batch 20

注意：本脚本**幂等**（去重后的清单再跑一次结果不变）；但 `--comment-scope` 切换会
重写 `capture_comments`，属于预期行为。重复条已剔除时再次 `--drop-dups` 不会报错。
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path


def load_manifest(path: Path) -> dict:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(raw, list):
        return {"meta": {}, "items": raw}
    if "items" not in raw:
        raise SystemExit(f"[prepare_manifest] 清单缺少 items 字段：{path}")
    return raw


def dedupe(items: list, keep_dups: bool):
    """按 link 去重。返回 (保留的 items, 被处理的重复 items)。"""
    first_of: dict[str, object] = {}
    kept: list = []
    dropped: list = []
    for it in items:
        it = dict(it)
        link = str(it.get("link") or "").strip()
        if link and link in first_of:
            rec = dict(it)
            rec["dup_of_item_id"] = first_of[link]
            rec["original_no"] = ""  # 重复条序号留空
            dropped.append(rec)
            if keep_dups:
                it["dup_of_item_id"] = first_of[link]
                it["original_no"] = ""  # ← 序号留空
                it["capture_comments"] = False  # 不重复访问抖音
                kept.append(it)
            continue
        if link:
            first_of[link] = it.get("item_id")
        it["dup_of_item_id"] = ""
        kept.append(it)
    return kept, dropped


def mark_batches(items: list, batch_size: int, per_batch: int, scope: str) -> None:
    """就地标 capture_comments / batch_no / batch_idx。"""
    for i, it in enumerate(items):
        if scope == "all":
            it["batch_no"] = 0
            it["batch_idx"] = 0
            # keep-dups 模式下重复条永不抓取
            it["capture_comments"] = not it.get("dup_of_item_id")
            continue
        b, k = divmod(i, batch_size)
        it["batch_no"] = b + 1
        it["batch_idx"] = k + 1
        # 重复条（keep-dups 模式）永远不抓
        if it.get("dup_of_item_id"):
            it["capture_comments"] = False
        else:
            it["capture_comments"] = k < per_batch


def main() -> int:
    ap = argparse.ArgumentParser(description="清单前置：链接去重 + 批次标记")
    ap.add_argument("--in", dest="inp", required=True, help="输入 links_manifest.json")
    ap.add_argument("--out", dest="out", required=True, help="输出 links_manifest.json（可同路径）")
    ap.add_argument("--dropped-csv", default="", help="被处理的重复条记录（默认与 --out 同目录）")
    ap.add_argument(
        "--drop-dups",
        dest="dup_mode",
        action="store_const",
        const="drop",
        help="重复条不再输出（默认）",
    )
    ap.add_argument(
        "--keep-dups",
        dest="dup_mode",
        action="store_const",
        const="keep",
        help="重复条保留，但序号留空、不抓取（结果复用首条）",
    )
    ap.set_defaults(dup_mode="drop")
    ap.add_argument("--batch-size", type=int, default=50, help="每批条数，与引擎 --batch-size 一致")
    ap.add_argument("--comments-per-batch", type=int, default=20, help="每批前 N 条抓评论截图")
    ap.add_argument(
        "--comment-scope",
        choices=["all", "first_n"],
        default="all",
        help="all=全部条目抓评论（先测限流）；first_n=每批只抓前 N 条",
    )
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()

    inp = Path(args.inp)
    out = Path(args.out)
    if not inp.exists():
        raise SystemExit(f"[prepare_manifest] 找不到输入清单：{inp}")

    man = load_manifest(inp)
    items = list(man.get("items") or [])
    n_in = len(items)

    kept, dropped = dedupe(items, keep_dups=(args.dup_mode == "keep"))
    mark_batches(kept, args.batch_size, args.comments_per_batch, args.comment_scope)

    man = dict(man)
    man["items"] = kept
    meta = dict(man.get("meta") or {})
    meta.update(
        {
            "prepare": "prepare_manifest.py",
            "items_before_dedupe": n_in,
            "items_after_dedupe": len(kept),
            "duplicates_handled": len(dropped),
            "dup_mode": args.dup_mode,
            "comment_scope": args.comment_scope,
            "batch_size": args.batch_size,
            "comments_per_batch": args.comments_per_batch,
        }
    )
    man["meta"] = meta
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(man, ensure_ascii=False, indent=2), encoding="utf-8")

    csv_path = Path(args.dropped_csv) if args.dropped_csv else out.parent / "去重剔除记录.csv"
    with csv_path.open("w", newline="", encoding="utf-8-sig") as fh:
        w = csv.writer(fh)
        w.writerow(["item_id", "原序号", "链接", "保留条目的item_id", "处理方式"])
        for d in dropped:
            w.writerow(
                [
                    d.get("item_id", ""),
                    d.get("original_no", ""),  # keep-dups 模式下为留空
                    d.get("link", ""),
                    d.get("dup_of_item_id", ""),
                    "剔除" if args.dup_mode == "drop" else "保留但不再抓取",
                ]
            )

    n_cap = sum(1 for it in kept if it.get("capture_comments"))
    if not args.quiet:
        scope_note = (
            ""
            if args.comment_scope == "all"
            else f"（每 {args.batch_size} 条取前 {args.comments_per_batch} 条）"
        )
        print(f"[prepare_manifest] 输入 {n_in} 条 → 去重后 {len(kept)} 条（处理重复 {len(dropped)} 条）")
        print(
            f"[prepare_manifest] 口径={args.dup_mode} 评论抓取范围={args.comment_scope}{scope_note}"
        )
        print(f"[prepare_manifest] 需抓评论 {n_cap} 条，其余 {len(kept) - n_cap} 条只截视频页")
        print(f"[prepare_manifest] 写清单 {out}")
        print(f"[prepare_manifest] 写记录 {csv_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
