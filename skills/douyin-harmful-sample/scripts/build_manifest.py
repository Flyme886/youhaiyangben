#!/usr/bin/env python
"""Merge all Ego Lite capture results into the repo-shaped screenshots_manifest.json.

读取顺序基准 links_manifest.json + 合并目录下所有 results*.jsonl：
  - 同一 item_id 优先保留 status=="ok" 的记录，其次保留最后一条（同状态取最新）
  - 截图文件不存在的条目降级为 missing_file
  - 输出 screenshots/screenshots_manifest.json（字段与仓库 quality_check_screenshots.py 兼容）

用法：
  python build_manifest.py \
    --links 输出Word/links_manifest.json \
    --results-dir _ego \
    --shots-dir 输出Word/screenshots \
    --output 输出Word/screenshots/screenshots_manifest.json

所有路径也支持环境变量覆盖：
  BUILD_LINKS / BUILD_RESULTS_DIR / BUILD_SHOTS_DIR / BUILD_OUTPUT
未给则回退到当前目录的相对默认。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter
from pathlib import Path

DEFAULT_LINKS = Path("输出Word/links_manifest.json")
DEFAULT_RESULTS_DIR = Path("_ego")
DEFAULT_SHOTS_DIR = Path("输出Word/screenshots")
DEFAULT_OUTPUT = Path("输出Word/screenshots/screenshots_manifest.json")


def resolve(arg: str | None, env: str, default: Path) -> Path:
    if arg:
        return Path(arg)
    if env and os.environ.get(env):
        return Path(os.environ[env])
    return default


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--links", help="links_manifest.json 路径")
    ap.add_argument("--results-dir", help="results*.jsonl 所在目录")
    ap.add_argument("--shots-dir", help="截图目录")
    ap.add_argument("--output", help="输出 screenshots_manifest.json 路径")
    args = ap.parse_args()

    LINKS = resolve(args.links, "BUILD_LINKS", DEFAULT_LINKS)
    EGO = resolve(args.results_dir, "BUILD_RESULTS_DIR", DEFAULT_RESULTS_DIR)
    SHOTS = resolve(args.shots_dir, "BUILD_SHOTS_DIR", DEFAULT_SHOTS_DIR)
    TARGET = resolve(args.output, "BUILD_OUTPUT", DEFAULT_OUTPUT)

    if not LINKS.exists():
        raise SystemExit(f"links_manifest not found: {LINKS}")
    links = json.loads(LINKS.read_text(encoding="utf-8-sig"))["items"]

    # 同一个 item_id 可能在多轮里被重复抓；优先保留 status=ok，其次保留最后一条
    best: dict[str, dict] = {}
    if EGO.is_dir():
        for f in sorted(EGO.glob("results*.jsonl")):
            for line in f.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                key = str(row.get("item_id"))
                prev = best.get(key)
                if prev is None:
                    best[key] = row
                elif prev.get("status") != "ok" and row.get("status") == "ok":
                    best[key] = row
                elif prev.get("status") == row.get("status"):
                    best[key] = row  # 同状态取最新

    items = []
    for base in links:
        key = str(base["item_id"])
        row = best.get(key)
        if row is None:
            row = {
                **base,
                "engine": "ego-browser",
                "status": "not_captured",
                "reason": "not captured",
                "screenshot_path": "",
                "page_title": "",
                "page_text": "",
                "captcha_signals": [],
                "needs_manual_action": False,
            }
        path = str(row.get("screenshot_path") or "")
        if path and not (SHOTS / path).exists():
            row["status"] = "missing_file"
            row["reason"] = "screenshot file missing"
            row["screenshot_path"] = ""
        items.append(row)

    TARGET.parent.mkdir(parents=True, exist_ok=True)
    TARGET.write_text(
        json.dumps(
            {"meta": {"engine": "ego-browser", "total": len(items)}, "items": items},
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print("wrote", TARGET)
    print("status_counts", dict(Counter(str(i.get("status")) for i in items)))
    ok = sum(1 for i in items if i.get("status") == "ok")
    print(f"ok={ok}/{len(items)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
