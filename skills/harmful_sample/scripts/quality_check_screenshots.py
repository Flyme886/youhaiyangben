#!/usr/bin/env python
"""Quality-check screenshots and prepare text evidence for report reasons."""

from __future__ import annotations

import argparse
import csv
import json
import math
from collections import Counter
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image


UNUSABLE_STATUSES = {"playwright_unavailable", "drission_unavailable", "navigation_error", "timeout", "captcha_detected"}
REVIEW_STATUSES = {"login_required", "blocked_or_blank"}


def as_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).replace("\x00", "").strip()


def image_stats(path: Path) -> dict[str, Any]:
    with Image.open(path) as image:
        rgb = image.convert("RGB")
        arr = np.asarray(rgb, dtype=np.float32)
    gray = arr.mean(axis=2)
    mean = float(gray.mean())
    std = float(gray.std())
    dark_ratio = float((gray < 12).mean())
    light_ratio = float((gray > 245).mean())
    # Downsampled color diversity is a cheap proxy for blank/error screenshots.
    small = gray[:: max(1, gray.shape[0] // 200), :: max(1, gray.shape[1] // 200)]
    hist, _ = np.histogram(small, bins=32, range=(0, 255), density=True)
    entropy = float(-sum(p * math.log2(p) for p in hist if p > 0))
    return {
        "width": int(gray.shape[1]),
        "height": int(gray.shape[0]),
        "mean_brightness": round(mean, 2),
        "std_brightness": round(std, 2),
        "dark_ratio": round(dark_ratio, 4),
        "light_ratio": round(light_ratio, 4),
        "entropy": round(entropy, 4),
    }


def classify(item: dict[str, Any], image_path: Path | None, stats: dict[str, Any] | None) -> tuple[str, str]:
    status = as_text(item.get("status"))
    if not image_path or not image_path.exists():
        return "unusable", "no screenshot file"
    if status in UNUSABLE_STATUSES:
        return "unusable", status or "capture failed"
    if not stats:
        return "unusable", "image stats unavailable"
    if stats["dark_ratio"] > 0.98 or (stats["mean_brightness"] < 8 and stats["std_brightness"] < 8):
        return "unusable", "black screenshot"
    if stats["light_ratio"] > 0.98 or (stats["mean_brightness"] > 247 and stats["std_brightness"] < 8):
        return "unusable", "white/blank screenshot"
    if stats["std_brightness"] < 3 or stats["entropy"] < 0.3:
        return "unusable", "low information screenshot"
    if status in REVIEW_STATUSES:
        return "needs_review", status
    return "usable", "ok"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("screenshots_manifest", type=Path)
    parser.add_argument("--output-dir", type=Path, help="Defaults to the manifest directory.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    manifest_path = args.screenshots_manifest.resolve()
    if not manifest_path.exists():
        raise SystemExit(f"screenshots manifest not found: {manifest_path}")
    out_dir = (args.output_dir or manifest_path.parent).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    data = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
    items = data.get("items", [])
    enriched: list[dict[str, Any]] = []
    rows: list[dict[str, Any]] = []

    for item in items:
        screenshot_path = as_text(item.get("screenshot_path") or item.get("path"))
        image_path = manifest_path.parent / screenshot_path if screenshot_path else None
        stats = None
        stats_error = ""
        if image_path and image_path.exists():
            try:
                stats = image_stats(image_path)
            except Exception as exc:
                stats_error = str(exc)
        quality, reason = classify(item, image_path, stats)
        page_text = as_text(item.get("page_text"))
        screenshot_text = page_text if quality == "usable" else ""
        updated = {
            **item,
            "quality": quality,
            "quality_reason": reason if not stats_error else f"{reason}; {stats_error}",
            "ocr_status": "not_configured",
            "screenshot_text": screenshot_text,
            "image_stats": stats or {},
        }
        enriched.append(updated)
        rows.append(
            {
                "item_id": item.get("item_id", ""),
                "source_row": item.get("source_row", ""),
                "link": item.get("link", ""),
                "capture_status": item.get("status", ""),
                "quality": updated["quality"],
                "quality_reason": updated["quality_reason"],
                "screenshot_path": screenshot_path,
                "text_chars": len(screenshot_text),
            }
        )

    quality_manifest = {"meta": {**data.get("meta", {}), "quality_checked": True}, "items": enriched}
    manifest_out = out_dir / "screenshot_quality_manifest.json"
    manifest_out.write_text(json.dumps(quality_manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    csv_out = out_dir / "screenshot_quality.csv"
    with csv_out.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "item_id",
                "source_row",
                "link",
                "capture_status",
                "quality",
                "quality_reason",
                "screenshot_path",
                "text_chars",
            ],
        )
        writer.writeheader()
        writer.writerows(rows)

    print("quality_counts", dict(Counter(row["quality"] for row in rows)))
    print(manifest_out)
    print(csv_out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
