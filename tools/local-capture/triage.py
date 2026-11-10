#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""triage.py —— 把「没图的条目」分成两类，决定删档还是重抓。

分类：
  ok      有可用截图（status=ok 且有 png）
  deleted 页面明说已删，或平台接口证明不存在 / 不可见  → 整条不记录
  blocked 验证码、登录墙、超时、SPA 出错页、空白        → 限流类，应重抓
  untried 根本没有抓取记录                              → 限流类，应重抓

用法：
  triage.py --manifest m.json --results _ego [--shots screenshots] [--prune] [--json out.json]

--prune 会删掉 blocked / untried 条目的结果行与旧截图，让下一次抓取把它们当新任务重跑。
"""
import argparse
import glob
import json
import os
import re
import time
import urllib.request
from pathlib import Path

UA = {"User-Agent": "Mozilla/5.0", "Referer": "https://www.bilibili.com/"}
BLOCKED_STATUS = {"captcha", "login_wall", "timeout", "navigation_error", "err_page", "blocked_or_blank"}
DELETED_HINT = ("不存在", "已删除", "已被删除", "无法查看", "帖子可能已被删除", "内容已失效")


def load_shots(results_dirs):
    shots = {}
    for d in results_dirs:
        for f in sorted(Path(d).glob("results_*.jsonl")):
            for line in f.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                try:
                    o = json.loads(line)
                except json.JSONDecodeError:
                    continue
                k = str(o.get("shot_key"))
                if k not in shots or o.get("status") == "ok":
                    shots[k] = o
    return shots


def _get(url, timeout=12):
    try:
        return json.loads(urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=timeout).read())
    except Exception:
        return None


def bili_gone(nav_url: str):
    """B站：True=确认没了 / False=还在 / None=判断不了（风控等）"""
    if "/video/BV" in nav_url:
        bv = re.search(r"BV[0-9A-Za-z]+", nav_url).group(0)
        j = _get("https://api.bilibili.com/x/web-interface/view?bvid=%s" % bv)
        if not j:
            return None
        return j.get("code") in (-404, 62002, -403, -10403)
    m = re.search(r"(\d{15,})", nav_url)
    if not m:
        return None
    j = _get("https://api.bilibili.com/x/polymer/web-dynamic/v1/detail?id=%s" % m.group(1))
    if not j:
        return None
    if j.get("code") == 4101152:
        return True
    if j.get("code") == 0:
        return False
    return None


def api_gone(platform: str, link: str, nav_url: str):
    if platform == "bilibili":
        return bili_gone(nav_url or link)
    return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--manifest", required=True)
    ap.add_argument("--results", nargs="+", required=True)
    ap.add_argument("--shots", default="")
    ap.add_argument("--prune", action="store_true")
    ap.add_argument("--json", default="")
    ap.add_argument("--api-check", action="store_true", help="对无图条目调用平台接口判断是否真删除")
    ap.add_argument("--api-sleep", type=float, default=1.5)
    args = ap.parse_args()

    raw = json.loads(Path(args.manifest).read_text(encoding="utf-8"))
    items = raw["items"] if isinstance(raw, dict) else raw
    shots = load_shots(args.results)
    shots_dir = Path(args.shots) if args.shots else Path(args.results[0]).parent.parent / "screenshots"

    rows = []
    for it in items:
        key = str(it["shot_key"])
        s = shots.get(key) or {}
        png = shots_dir / f"{key}.png"
        usable = bool(s) and s.get("status") == "ok" and png.exists()
        if usable:
            rows.append({"item_id": it["item_id"], "shot_key": key, "author": it.get("author", ""),
                         "link": it.get("link", ""), "verdict": "ok", "why": s.get("status", "")})
            continue
        if not s:
            rows.append({"item_id": it["item_id"], "shot_key": key, "author": it.get("author", ""),
                         "link": it.get("link", ""), "verdict": "untried", "why": "no record"})
            continue
        text = (s.get("page_text") or "") + " " + (s.get("page_title") or "")
        # 只看标题 + 首 300 字：正常帖子的正文深处可能出现「不存在」「已删除」字样，
        # 全文匹配会把活着的帖子误判成已删，直接丢掉
        head = ((s.get("page_title") or "") + " " + (s.get("page_text") or ""))[:300]
        if s.get("status") == "deleted" or any(h in head for h in DELETED_HINT):
            rows.append({"item_id": it["item_id"], "shot_key": key, "author": it.get("author", ""),
                         "link": it.get("link", ""), "verdict": "deleted", "why": "page states removed"})
            continue
        why = s.get("status") or "unknown"
        if args.api_check:
            g = api_gone(it.get("platform", ""), it.get("link", ""), it.get("nav_url", ""))
            time.sleep(args.api_sleep)
            if g is True:
                rows.append({"item_id": it["item_id"], "shot_key": key, "author": it.get("author", ""),
                             "link": it.get("link", ""), "verdict": "deleted", "why": why + " + api gone"})
                continue
            if g is False:
                why += " + api alive"   # 还在但不给看：限流/需要登录，必须重抓
        rows.append({"item_id": it["item_id"], "shot_key": key, "author": it.get("author", ""),
                     "link": it.get("link", ""), "verdict": "blocked", "why": why})

    stat = {}
    for r in rows:
        stat[r["verdict"]] = stat.get(r["verdict"], 0) + 1
    print("== 无截图条目分类 ==")
    order = ["ok", "deleted", "blocked", "untried"]
    names = {"ok": "有图", "deleted": "已删/不可见（不记录）", "blocked": "被限流/加载失败（重抓）", "untried": "未抓到（重抓）"}
    for k in order:
        if stat.get(k):
            print(f"  {names[k]}: {stat[k]}")
    for r in rows:
        if r["verdict"] in ("deleted", "blocked", "untried"):
            print(f"    {r['item_id']}. [{r['verdict']}] {r['author']} {r['link']}  ({r['why']})")

    if args.json:
        Path(args.json).write_text(json.dumps({"stat": stat, "rows": rows}, ensure_ascii=False, indent=1), encoding="utf-8")

    if args.prune:
        redo = {r["shot_key"] for r in rows if r["verdict"] in ("blocked", "untried")}
        n = 0
        for d in args.results:
            for f in Path(d).glob("results_*.jsonl"):
                lines = [l for l in f.read_text(encoding="utf-8").splitlines() if l.strip()]
                keep = []
                for l in lines:
                    try:
                        if str(json.loads(l).get("shot_key")) in redo:
                            n += 1
                            continue
                    except json.JSONDecodeError:
                        pass
                    keep.append(l)
                f.write_text("\n".join(keep) + ("\n" if keep else ""), encoding="utf-8")
        for k in redo:
            p = shots_dir / f"{k}.png"
            if p.exists():
                p.unlink()
                n += 1
        print(f"--prune: 清理 {len(redo)} 条待重抓的记录/截图（{n} 项）")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
