#!/usr/bin/env python
"""
交互式 Cookie 导出工具

依次打开微博、今日头条、抖音，手动登录后按回车，最终将 Cookie 保存为
data/storage_state.json，供采集脚本通过 --storage-state 注入使用。

用法：
    python skills/harmful_sample/scripts/export_cookies.py
    python skills/harmful_sample/scripts/export_cookies.py --output data/storage_state.json
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parents[2]

# 依次登录的平台列表，可按需增减
PLATFORMS = [
    {"name": "微博", "url": "https://weibo.com"},
    {"name": "今日头条", "url": "https://www.toutiao.com"},
    {"name": "抖音", "url": "https://www.douyin.com"},
]

JS_EXPORT = r"""
'use strict';
const { chromium } = require('playwright-extra');
const StealthPlugin = require('puppeteer-extra-plugin-stealth');
const fs = require('fs/promises');
const readline = require('readline');
chromium.use(StealthPlugin());

const outputPath = process.argv[2];
const platforms = JSON.parse(process.argv[3]);

function waitEnter(msg) {
  return new Promise(resolve => {
    const rl = readline.createInterface({ input: process.stdin, output: process.stdout });
    rl.question(msg, () => { rl.close(); resolve(); });
  });
}

(async () => {
  const browser = await chromium.launch({
    headless: false,
    args: ['--start-maximized'],
  });
  const context = await browser.newContext({
    viewport: null,
    userAgent: 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
  });

  for (const { name, url } of platforms) {
    const page = await context.newPage();
    await page.goto(url, { waitUntil: 'domcontentloaded', timeout: 30000 }).catch(() => {});
    await waitEnter(`\n[${name}] 请在浏览器中完成登录，完成后回到此窗口按回车...\n`);
    await page.close();
  }

  await context.storageState({ path: outputPath });
  await browser.close();

  const data = JSON.parse(await fs.readFile(outputPath, 'utf8'));
  console.log('SAVED:' + data.cookies.length + ':' + outputPath);
})().catch(err => {
  console.error(err.stack || String(err));
  process.exit(1);
});
"""


def build_node_env() -> dict[str, str]:
    env = os.environ.copy()
    node_paths = [PROJECT_ROOT / "node_modules"]
    existing = env.get("NODE_PATH")
    if existing:
        node_paths.append(Path(existing))
    env["NODE_PATH"] = os.pathsep.join(str(p) for p in node_paths if p.exists())
    return env


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--output",
        type=Path,
        default=PROJECT_ROOT / "data" / "storage_state.json",
        help="Cookie 保存路径（默认 data/storage_state.json）",
    )
    parser.add_argument(
        "--node-bin",
        default=os.environ.get("NODE_BIN") or shutil.which("node") or "node",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)

    print("\n=== Cookie 导出工具 ===")
    print("将依次打开以下平台，请在弹出的浏览器中手动登录：")
    for p in PLATFORMS:
        print(f"  · {p['name']}  {p['url']}")
    print(f"\n完成后 Cookie 将保存到: {args.output}\n")

    env = build_node_env()
    platforms_json = json.dumps(PLATFORMS, ensure_ascii=False)

    with tempfile.NamedTemporaryFile(
        "w", suffix=".cjs", delete=False, encoding="utf-8", dir=SCRIPT_DIR
    ) as f:
        f.write(JS_EXPORT)
        js_path = Path(f.name)

    try:
        result = subprocess.run(
            [args.node_bin, str(js_path), str(args.output.resolve()), platforms_json],
            env=env,
            # 继承终端 stdin/stdout，让用户可以直接在此窗口按回车
        )
    finally:
        try:
            js_path.unlink()
        except OSError:
            pass

    if result.returncode != 0:
        print("\n❌ 导出失败，请查看上方错误信息。")
        return 1

    if args.output.exists():
        try:
            data = json.loads(args.output.read_text(encoding="utf-8"))
            cookie_count = len(data.get("cookies", []))
            print(f"\n✅ 完成！共导出 {cookie_count} 条 Cookie")
            print(f"   保存路径: {args.output}")
            print("\n下次采集时系统会自动使用此 Cookie。")
            print("若 Cookie 过期，重新运行此脚本即可。\n")
        except Exception:
            print(f"\n✅ 文件已保存: {args.output}")
    else:
        print("\n❌ 未找到输出文件，导出可能未完成。")
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
