#!/usr/bin/env python
"""DrissionPage-compatible capture adapter.

This adapter is intentionally conservative: if DrissionPage is not installed,
it writes a normal screenshots manifest instead of breaking the worker.
"""

from __future__ import annotations

import argparse
import json
import re
import time
from pathlib import Path

from captcha_detector import detect_login_text, detect_text_signals, has_manual_action_signal


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("links_manifest", type=Path)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--storage-state", type=Path)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    data = json.loads(args.links_manifest.read_text(encoding="utf-8-sig"))
    items = data.get("items", [])
    if args.limit:
        items = items[: args.limit]
    args.output_dir.mkdir(parents=True, exist_ok=True)
    try:
        from DrissionPage import ChromiumOptions, ChromiumPage
    except Exception as exc:
        results = [
            {
                **item,
                "engine": "drission",
                "status": "drission_unavailable",
                "reason": f"DrissionPage unavailable: {exc}",
                "captcha_signals": [],
                "needs_manual_action": False,
                "screenshot_path": "",
                "page_title": "",
                "page_text": "",
            }
            for item in items
        ]
        out = {"meta": {"engine": "drission", "error": "DrissionPage unavailable"}, "items": results}
        (args.output_dir / "screenshots_manifest.json").write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
        print(args.output_dir / "screenshots_manifest.json")
        return 0

    options = ChromiumOptions()
    try:
        options.headless()
    except Exception:
        pass
    page = ChromiumPage(options)
    if args.storage_state and args.storage_state.exists():
        apply_playwright_storage_state(page, args.storage_state)

    results = []
    try:
        for item in items:
            results.append(capture_one(page, item, args.output_dir))
    finally:
        try:
            page.quit()
        except Exception:
            pass

    out = {"meta": {"engine": "drission", "total": len(results)}, "items": results}
    (args.output_dir / "screenshots_manifest.json").write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(args.output_dir / "screenshots_manifest.json")
    return 0


def capture_one(page: object, item: dict, output_dir: Path) -> dict:
    file_name = f"{int(item.get('item_id') or 0):05d}.png"
    try:
        page.get(item["link"])
        time.sleep(2)
        title = str(getattr(page, "title", "") or "")
        html = str(getattr(page, "html", "") or "")
        text = re.sub(r"<[^>]+>", " ", html)
        text = re.sub(r"\s+", " ", text).strip()
        signals = detect_text_signals(f"{title}\n{text}\n{item.get('link', '')}", "dom")
        if signals:
            status, reason = "captcha_detected", "captcha or risk challenge detected"
        elif detect_login_text(text):
            status, reason = "login_required", "page appears to require login"
        elif len(text) < 20:
            status, reason = "blocked_or_blank", "page body is blank or too short"
        else:
            status, reason = "ok", ""
        page.get_screenshot(path=str(output_dir), name=file_name, full_page=True)
        return {
            **item,
            "engine": "drission",
            "status": status,
            "reason": reason,
            "captcha_signals": signals,
            "needs_manual_action": has_manual_action_signal(status, signals),
            "screenshot_path": file_name,
            "page_title": title[:500],
            "page_text": text[:4000],
        }
    except Exception as exc:
        return {
            **item,
            "engine": "drission",
            "status": "navigation_error",
            "reason": str(exc)[:300],
            "captcha_signals": [],
            "needs_manual_action": False,
            "screenshot_path": "",
            "page_title": "",
            "page_text": "",
        }


def apply_playwright_storage_state(page: object, storage_state: Path) -> None:
    try:
        state = json.loads(storage_state.read_text(encoding="utf-8-sig"))
        cookies = state.get("cookies") or []
        if cookies:
            page.set.cookies(cookies)
    except Exception:
        return


if __name__ == "__main__":
    raise SystemExit(main())
