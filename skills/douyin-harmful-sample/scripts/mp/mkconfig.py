#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""生成 capture_multi.js 的配置。

--probe 时同时产出「每个平台只取第一条」的探测清单，用来在全量开跑前
先确认各平台到底能不能打开（30 秒拿到可达性，而不是跑完 70 页才发现）。
"""
import argparse
import json
from pathlib import Path

PLATFORM_SETTLE = {
    "douyin": 3500,
    "xhs": 3200,
    "weibo": 2200,
    "bilibili": 2200,
    "tieba": 1600,
    "weixin": 2200,
    "toutiao": 1800,
    "other": 1600,
}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--manifest", required=True)
    ap.add_argument("--shots", required=True)
    ap.add_argument("--results", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--space", default="有害样本")
    ap.add_argument("--probe", action="store_true", help="生成每平台一条的探测清单")
    ap.add_argument("--per-platform", type=int, default=1)
    ap.add_argument("--max-attempts", type=int, default=2)
    ap.add_argument("--recycle", type=int, default=10)
    args = ap.parse_args()

    manifest = Path(args.manifest)
    data = json.loads(manifest.read_text(encoding="utf-8"))
    items = data["items"]

    if args.probe:
        seen: dict[str, int] = {}
        picked = []
        for it in items:
            p = it.get("platform", "other")
            if seen.get(p, 0) >= args.per_platform:
                continue
            seen[p] = seen.get(p, 0) + 1
            picked.append(it)
        probe_path = manifest.with_name("probe_manifest.json")
        probe_path.write_text(
            json.dumps({"meta": {"probe": True, "platforms": sorted(seen)}, "items": picked}, ensure_ascii=False, indent=1),
            encoding="utf-8",
        )
        target = probe_path
        tag = "probe"
        max_attempts = 1
        print(f"探测清单：{len(picked)} 条，覆盖 {len(seen)} 个平台：{', '.join(sorted(seen))}")
    else:
        target = manifest
        tag = "full"
        max_attempts = args.max_attempts

    cfg = {
        "manifest": str(target),
        "shots_dir": str(Path(args.shots)),
        "results_dir": str(Path(args.results)),
        "space": args.space,
        "limit": 0,
        "tag": tag,
        "fresh": True,
        "recycle": args.recycle,
        "max_attempts": max_attempts,
        "extra_settle": 900,
        "platform_settle": PLATFORM_SETTLE,
    }
    Path(args.out).write_text(json.dumps(cfg, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"配置：{args.out}（tag={tag}, max_attempts={max_attempts}）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
