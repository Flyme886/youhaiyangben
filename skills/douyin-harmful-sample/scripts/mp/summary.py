#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""汇总抓取结果：按平台给一张表，再列出没取到证据的条目。

状态口径：
  ok        抓到正文
  deleted   页面明确显示已删除/不存在 —— 仍是有效证据，照常入册
  captcha / login_wall / blocked_or_blank / timeout / navigation_error / no_token  → 缺图
"""
import argparse
import json
from collections import defaultdict
from pathlib import Path

EVIDENCE = {"ok", "deleted"}
LABELS = {
    "ok": "有图",
    "deleted": "已删保留",
    "captcha": "验证码",
    "login_wall": "需登录",
    "blocked_or_blank": "空白",
    "timeout": "超时",
    "navigation_error": "打不开",
    "no_token": "缺token",
    "failed": "失败",
    "missing": "未抓",
}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--manifest", required=True)
    ap.add_argument("--results", nargs="+", required=True)
    ap.add_argument("--label", default="抓取")
    args = ap.parse_args()

    items = json.loads(Path(args.manifest).read_text(encoding="utf-8"))["items"]
    best: dict[str, dict] = {}
    for d in args.results:
        p = Path(d)
        if not p.exists():
            continue
        for f in sorted(p.glob("results_*.jsonl")):
            for line in f.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                try:
                    o = json.loads(line)
                except json.JSONDecodeError:
                    continue
                k = str(o.get("shot_key"))
                if k not in best or (o.get("status") in EVIDENCE and best[k].get("status") not in EVIDENCE):
                    best[k] = o

    by_platform: dict[str, dict[str, int]] = defaultdict(lambda: {"n": 0, "ok": 0, "deleted": 0, "miss": 0})
    missing = []
    for it in items:
        plat = it.get("platform", "other")
        st = (best.get(str(it["shot_key"])) or {}).get("status", "missing")
        row = by_platform[plat]
        row["n"] += 1
        if st == "ok":
            row["ok"] += 1
        elif st == "deleted":
            row["deleted"] += 1
        else:
            row["miss"] += 1
            missing.append((it["item_id"], plat, it.get("platform_name") or plat, it.get("author", ""), it.get("link", ""), st))

    print(f"== {args.label}结果 ==")
    print(f"{'平台':<12}{'条目':>5}{'有图':>6}{'已删保留':>9}{'缺图':>6}")
    for plat in sorted(by_platform, key=lambda p: -by_platform[p]["n"]):
        r = by_platform[plat]
        print(f"{plat:<12}{r['n']:>5}{r['ok']:>6}{r['deleted']:>9}{r['miss']:>6}")
    tot = len(items)
    ok = sum(r["ok"] for r in by_platform.values())
    dele = sum(r["deleted"] for r in by_platform.values())
    print(f"{'合计':<12}{tot:>5}{ok:>6}{dele:>9}{tot - ok - dele:>6}")

    if missing:
        print(f"\n未取到证据 {len(missing)} 条：")
        for iid, plat, pname, author, link, st in missing:
            print(f"  {iid}. [{pname}] {author}  {LABELS.get(st, st)}  {link}")
    else:
        print("\n全部条目都取到了证据。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
