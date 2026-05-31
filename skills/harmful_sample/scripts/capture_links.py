#!/usr/bin/env python
"""Dispatch screenshot capture to Playwright by default, DrissionPage when configured."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlparse
import json


SCRIPT_DIR = Path(__file__).resolve().parent


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("links_manifest", type=Path)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--timeout-ms", type=int, default=30000)
    parser.add_argument("--wait-ms", type=int, default=2000)
    parser.add_argument("--retries", type=int, default=1)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--no-install-browser", action="store_true")
    parser.add_argument("--storage-state", type=Path)
    parser.add_argument("--node-bin", default=os.environ.get("NODE_BIN") or "node")
    parser.add_argument("--previous-manifest", type=Path)
    parser.add_argument("--engine", choices=["playwright", "drission"], default=os.environ.get("CAPTURE_ENGINE", "playwright"))
    parser.add_argument("--profile", choices=["desktop", "mobile"], default=os.environ.get("CAPTURE_PROFILE", "desktop"))
    parser.add_argument("--headless", choices=["true", "false"], default=os.environ.get("CAPTURE_HEADLESS", "true").lower())
    parser.add_argument("--full-page", choices=["true", "false"], default=os.environ.get("CAPTURE_FULL_PAGE", "true").lower())
    parser.add_argument("--drission-domains", default=os.environ.get("DRISSION_DOMAINS", ""))
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    engine = choose_engine(args)
    script = SCRIPT_DIR / ("capture_drission.py" if engine == "drission" else "capture_playwright.py")
    cmd = [
        sys.executable,
        str(script),
        str(args.links_manifest),
        "--output-dir",
        str(args.output_dir),
        "--limit",
        str(args.limit),
    ]
    if args.storage_state:
        cmd.extend(["--storage-state", str(args.storage_state)])
    if engine == "playwright":
        cmd.extend([
            "--timeout-ms", str(args.timeout_ms),
            "--wait-ms", str(args.wait_ms),
            "--retries", str(args.retries),
            "--node-bin", args.node_bin,
        ])
        if args.previous_manifest:
            cmd.extend(["--previous-manifest", str(args.previous_manifest)])
        cmd.extend([
            "--profile", args.profile,
            "--headless", args.headless,
            "--full-page", args.full_page,
        ])
        if args.no_install_browser:
            cmd.append("--no-install-browser")
    return subprocess.call(cmd)


def choose_engine(args: argparse.Namespace) -> str:
    if args.engine == "drission":
        return "drission"
    domains = [d.strip().lower() for d in args.drission_domains.split(",") if d.strip()]
    if not domains:
        return "playwright"
    try:
        data = json.loads(args.links_manifest.read_text(encoding="utf-8-sig"))
    except Exception:
        return "playwright"
    links = [str(item.get("link") or "") for item in data.get("items", [])]
    if links and all(any(domain in (urlparse(link).netloc.lower()) for domain in domains) for link in links):
        return "drission"
    return "playwright"


if __name__ == "__main__":
    raise SystemExit(main())
