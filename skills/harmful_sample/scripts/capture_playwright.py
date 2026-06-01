#!/usr/bin/env python
"""Capture screenshots for links_manifest.json using Node Playwright."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any


SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parents[2]


def _safe_json(obj: Any) -> str:
    """Serialize to JSON, replacing any surrogate characters so UTF-8 write never fails."""
    s = json.dumps(obj, ensure_ascii=False, indent=2)
    return s.encode("utf-8", errors="replace").decode("utf-8")


JS_CAPTURE = r"""
const fs = require('fs/promises');
const path = require('path');
const { chromium } = require('playwright-extra');
const StealthPlugin = require('puppeteer-extra-plugin-stealth');
chromium.use(StealthPlugin());

const manifestPath = process.argv[2];
const outputDir = process.argv[3];
const timeoutMs = Number(process.argv[4] || 30000);
const waitMs = Number(process.argv[5] || 2000);
const retries = Number(process.argv[6] || 1);
const limit = Number(process.argv[7] || 0);
const storageState = process.argv[8] || "";
const profile = process.argv[9] || "desktop";
const headless = (process.argv[10] || "true") === "true";
const fullPage = (process.argv[11] || "true") === "true";

const captchaTerms = [
  "captcha", "recaptcha", "hcaptcha", "cloudflare", "verify", "verification",
  "risk", "challenge", "验证码", "滑块", "安全验证", "访问异常", "环境异常", "人机验证"
];
// 强信号：页面主体就是登录墙（无论内容多少都判为 login_required）
const loginStrongTerms = ["请先登录", "登录后查看", "登录后可查看", "登录查看完整", "请登录后", "login to view", "sign in to view"];
// 弱信号：导航栏常见词，仅在页面内容很少时才算登录墙
const loginWeakTerms = ["登录", "login", "sign in"];

function safeName(item) {
  return String(item.item_id).padStart(5, '0') + ".png";
}

function detectSignals(text, prefix) {
  const lower = String(text || "").toLowerCase();
  return captchaTerms.filter(term => lower.includes(String(term).toLowerCase())).map(term => `${prefix}:${term}`);
}

function detectLogin(text, bodyLen) {
  const lower = String(text || "").toLowerCase();
  // 强信号优先，直接判为登录墙
  if (loginStrongTerms.some(t => lower.includes(t.toLowerCase()))) return true;
  // 页面有实质内容（>500字符），弱信号只是导航按钮，不算登录墙
  if (bodyLen > 500) return false;
  return loginWeakTerms.some(t => lower.includes(t.toLowerCase()));
}

async function classify(page, networkSignals, error) {
  if (error) {
    const msg = String(error.message || error);
    if (msg.includes('Timeout')) return ['timeout', msg, Array.from(networkSignals), false];
    return ['navigation_error', msg.slice(0, 300), Array.from(networkSignals), false];
  }
  const title = await page.title().catch(() => "");
  const bodyText = await page.locator('body').innerText({ timeout: 3000 }).catch(() => "");
  const url = page.url();
  const globals = await page.evaluate(() => {
    const names = ['captcha_challenge_active', '__captcha', '__VERIFY__', '__RISK__'];
    return names.filter(name => Boolean(window[name])).join(' ');
  }).catch(() => "");
  const combined = `${title}\n${bodyText}\n${url}\n${globals}`;
  const domSignals = detectSignals(combined, 'dom');
  const allSignals = [...Array.from(networkSignals), ...domSignals];
  if (globals) allSignals.push(`global:${globals}`);
  const hasDomOrGlobal = domSignals.length > 0 || Boolean(globals);
  const networkOnlyAndNoContent = networkSignals.size > 0 && !hasDomOrGlobal && bodyText.trim().length < 200;
  if (hasDomOrGlobal || networkOnlyAndNoContent) return ['captcha_detected', 'captcha or risk challenge detected', allSignals, true];
  if (detectLogin(combined, bodyText.trim().length)) return ['login_required', 'page appears to require login', allSignals, true];
  if (bodyText.trim().length < 20) return ['blocked_or_blank', 'page body is blank or too short', allSignals, false];
  return ['ok', '', allSignals, false];
}

async function extractEvidence(page) {
  const title = await page.title().catch(() => "");
  const bodyText = await page.locator('body').innerText({ timeout: 3000 }).catch(() => "");
  return {
    page_title: title.trim().slice(0, 500),
    page_text: bodyText.replace(/\s+/g, ' ').trim().slice(0, 4000)
  };
}

async function captureOne(context, item) {
  const page = await context.newPage();
  const networkSignals = new Set();
  page.on('request', request => {
    const url = request.url().toLowerCase();
    for (const term of captchaTerms) {
      if (url.includes(String(term).toLowerCase())) networkSignals.add(`network:${term}`);
    }
  });
  page.setDefaultTimeout(timeoutMs);
  if (profile === "mobile") {
    await page.setViewportSize({ width: 390, height: 1100 });
  } else {
    await page.setViewportSize({ width: 1280, height: 1600 });
  }
  let lastError = null;
  for (let attempt = 0; attempt <= retries; attempt++) {
    try {
      await page.goto(item.link, { waitUntil: 'domcontentloaded', timeout: timeoutMs });
      await page.waitForTimeout(waitMs);
      const [status, reason, captchaSignals, needsManualAction] = await classify(page, networkSignals, null);
      const evidence = await extractEvidence(page);
      const fileName = safeName(item);
      const outPath = path.join(outputDir, fileName);
      // 向下滚动触发懒加载内容，再回到顶部
      await page.evaluate(() => new Promise(r => {
        window.scrollTo(0, Math.min(document.body.scrollHeight, 3000));
        setTimeout(() => { window.scrollTo(0, 0); r(); }, 300);
      })).catch(() => {});
      await page.waitForTimeout(300);
      // 计算最后一个叶节点的底部，裁掉无限滚动占位符产生的空白
      const vpWidth = profile === 'mobile' ? 390 : 1280;
      const clipH = await page.evaluate(() => {
        let max = 0;
        document.querySelectorAll('body *').forEach(el => {
          if (el.children.length === 0) {
            const r = el.getBoundingClientRect();
            if ((el.textContent.trim().length > 0 || el.tagName === 'IMG') && r.height > 3 && r.width > 3) {
              max = Math.max(max, r.bottom + window.scrollY);
            }
          }
        });
        return Math.min(Math.ceil(max) + 30, 8000);
      }).catch(() => 0);
      const screenshotOpts = clipH > 100
        ? { path: outPath, clip: { x: 0, y: 0, width: vpWidth, height: clipH } }
        : { path: outPath, fullPage };
      await page.screenshot(screenshotOpts);
      await page.close();
      return { ...item, engine: "playwright", status, reason, captcha_signals: captchaSignals, needs_manual_action: needsManualAction, screenshot_path: fileName, ...evidence };
    } catch (err) {
      lastError = err;
      if (attempt < retries) await page.waitForTimeout(1000);
    }
  }
  const evidence = await extractEvidence(page).catch(() => ({ page_title: "", page_text: "" }));
  const [status, reason, captchaSignals, needsManualAction] = await classify(page, networkSignals, lastError).catch(() => ['navigation_error', String(lastError), Array.from(networkSignals), false]);
  await page.close().catch(() => {});
  return { ...item, engine: "playwright", status, reason, captcha_signals: captchaSignals, needs_manual_action: needsManualAction, screenshot_path: "", ...evidence };
}

async function processBatch(context, items, batchSize = 6) {
  const results = [];
  for (let i = 0; i < items.length; i += batchSize) {
    const batch = items.slice(i, i + batchSize);
    const batchResults = await Promise.all(batch.map(item => captureOne(context, item)));
    results.push(...batchResults);
    batchResults.forEach(result => {
      console.log(`${result.item_id}\t${result.status}\t${result.link}`);
    });
  }
  return results;
}

(async () => {
  await fs.mkdir(outputDir, { recursive: true });
  const data = JSON.parse(await fs.readFile(manifestPath, 'utf8'));
  let items = data.items || [];
  if (limit > 0) items = items.slice(0, limit);
  const results = [];
  let browser;
  try {
    const launchOptions = { headless };
    if (process.env.CHROMIUM_EXECUTABLE_PATH) {
      launchOptions.executablePath = process.env.CHROMIUM_EXECUTABLE_PATH;
    }
    browser = await chromium.launch(launchOptions);
    const contextOptions = storageState ? { storageState } : {};
    if (profile === "mobile") {
      contextOptions.userAgent = "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1";
      contextOptions.isMobile = true;
      contextOptions.viewport = { width: 390, height: 1100 };
    }
    const context = await browser.newContext(contextOptions);
    const batchResults = await processBatch(context, items, 6);
    results.push(...batchResults);
    await context.close();
  } catch (err) {
    const reason = String(err && err.stack || err);
    results.push(...items.slice(results.length).map(item => ({
      ...item,
      engine: "playwright",
      status: 'playwright_unavailable',
      reason,
      captcha_signals: [],
      needs_manual_action: false,
      screenshot_path: ''
    })));
  } finally {
    if (browser) await browser.close().catch(() => {});
  }
  const out = { meta: { engine: "playwright", manifest: manifestPath, output_dir: outputDir, timeout_ms: timeoutMs, wait_ms: waitMs, retries, total: results.length }, items: results };
  await fs.writeFile(path.join(outputDir, 'screenshots_manifest.json'), JSON.stringify(out, null, 2), 'utf8');
})().catch(async (err) => {
  console.error(err && err.stack || err);
  process.exit(1);
});
"""


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
    parser.add_argument("--previous-manifest", type=Path)
    parser.add_argument("--node-bin", default=os.environ.get("NODE_BIN") or shutil.which("node") or "node")
    parser.add_argument("--profile", choices=["desktop", "mobile"], default=os.environ.get("CAPTURE_PROFILE", "desktop"))
    parser.add_argument("--headless", choices=["true", "false"], default=os.environ.get("CAPTURE_HEADLESS", "true").lower())
    parser.add_argument("--full-page", choices=["true", "false"], default=os.environ.get("CAPTURE_FULL_PAGE", "true").lower())
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if not args.links_manifest.exists():
        raise SystemExit(f"links manifest not found: {args.links_manifest}")
    previous = load_previous_successes(args.previous_manifest, args.output_dir)
    pending_manifest = prepare_pending_manifest(args.links_manifest, args.output_dir, previous, args.limit)
    if pending_manifest is None:
        write_merged_manifest(args.links_manifest, args.output_dir, previous, [])
        print(args.output_dir / "screenshots_manifest.json")
        return 0
    args.pending_manifest = pending_manifest
    env = build_node_env()
    preflight_error = playwright_preflight(args.node_bin, env)
    if preflight_error:
        path = unavailable_manifest(args, preflight_error)
        print(f"wrote unavailable manifest: {path}")
        return 0

    args.output_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", suffix=".cjs", delete=False, encoding="utf-8", dir=SCRIPT_DIR) as f:
        f.write(JS_CAPTURE)
        js_path = Path(f.name)
    try:
        result = capture_with_node(args, env, js_path)
        combined = (result.stdout or "") + "\n" + (result.stderr or "")
        if result.returncode == 0 and "playwright_unavailable" in combined and missing_browser_error(combined) and not args.no_install_browser:
            ok, install_output = install_chromium(args.node_bin, env)
            print("installed chromium" if ok else f"chromium install failed: {install_output}")
            if ok:
                result = capture_with_node(args, env, js_path)
        if result.stdout:
            print(result.stdout, end="")
        if result.returncode != 0:
            detail = (result.stderr or result.stdout or "").strip().splitlines()
            reason = detail[0] if detail else f"Playwright capture failed with exit code {result.returncode}"
            path = unavailable_manifest(args, reason)
            print(f"wrote fallback manifest: {path}")
            return 0
    finally:
        try:
            js_path.unlink()
        except OSError:
            pass
    print(args.output_dir / "screenshots_manifest.json")
    merge_manifest_after_capture(args.links_manifest, args.output_dir, previous)
    return 0


def unavailable_manifest(args: argparse.Namespace, reason: str) -> Path:
    data = json.loads(args.links_manifest.read_text(encoding="utf-8-sig"))
    items = data.get("items", [])
    if args.limit:
        items = items[: args.limit]
    results = [
        {**item, "engine": "playwright", "status": "playwright_unavailable", "reason": reason, "captcha_signals": [], "needs_manual_action": False, "screenshot_path": ""}
        for item in items
    ]
    args.output_dir.mkdir(parents=True, exist_ok=True)
    path = args.output_dir / "screenshots_manifest.json"
    path.write_text(_safe_json({"meta": {"engine": "playwright", "error": reason}, "items": results}), encoding="utf-8")
    return path


def playwright_preflight(node_bin: str, env: dict[str, str]) -> str:
    if not is_executable_available(node_bin):
        return f"Node runtime not found: {node_bin}"
    result = subprocess.run([node_bin, "-e", "require('playwright'); console.log('ok')"], env=env, text=True, encoding="utf-8", errors="replace", capture_output=True)
    if result.returncode != 0:
        output = (result.stderr or result.stdout or "").strip()
        return output.splitlines()[0] if output else f"Playwright require failed with exit code {result.returncode}"
    return ""


def install_chromium(node_bin: str, env: dict[str, str]) -> tuple[bool, str]:
    playwright_cli = find_playwright_cli()
    if not playwright_cli:
        return False, "Playwright CLI not found in project node_modules."
    result = subprocess.run([node_bin, str(playwright_cli), "install", "chromium"], env=env, text=True, encoding="utf-8", errors="replace", capture_output=True, timeout=600000)
    output = (result.stdout + "\n" + result.stderr).strip()
    return (result.returncode == 0, output if output else f"playwright install chromium exit={result.returncode}")


def capture_with_node(args: argparse.Namespace, env: dict[str, str], js_path: Path) -> subprocess.CompletedProcess[str]:
    cmd = [
        args.node_bin,
        str(js_path),
        str(args.pending_manifest.resolve() if hasattr(args, "pending_manifest") else args.links_manifest.resolve()),
        str(args.output_dir.resolve()),
        str(args.timeout_ms),
        str(args.wait_ms),
        str(args.retries),
        str(args.limit),
        str(args.storage_state.resolve()) if args.storage_state else "",
        args.profile,
        args.headless,
        args.full_page,
    ]
    return subprocess.run(cmd, env=env, text=True, encoding="utf-8", errors="replace", capture_output=True)


def load_previous_successes(path: Path | None, output_dir: Path) -> dict[str, dict[str, Any]]:
    if not path or not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError:
        return {}
    out: dict[str, dict[str, Any]] = {}
    for item in data.get("items", []):
        key = str(item.get("item_id") or "")
        screenshot = str(item.get("screenshot_path") or "")
        if key and item.get("status") == "ok" and screenshot and (output_dir / screenshot).exists():
            out[key] = item
    return out


def prepare_pending_manifest(links_manifest: Path, output_dir: Path, previous: dict[str, dict[str, Any]], limit: int) -> Path | None:
    data = json.loads(links_manifest.read_text(encoding="utf-8-sig"))
    items = data.get("items", [])
    if limit:
        items = items[:limit]
    pending = [item for item in items if str(item.get("item_id") or "") not in previous]
    if not pending:
        return None
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / "_pending_links_manifest.json"
    path.write_text(_safe_json({"meta": data.get("meta", {}), "items": pending}), encoding="utf-8")
    return path


def merge_manifest_after_capture(links_manifest: Path, output_dir: Path, previous: dict[str, dict[str, Any]]) -> None:
    captured_path = output_dir / "screenshots_manifest.json"
    try:
        captured = json.loads(captured_path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError:
        return
    write_merged_manifest(links_manifest, output_dir, previous, captured.get("items", []))


def write_merged_manifest(links_manifest: Path, output_dir: Path, previous: dict[str, dict[str, Any]], captured_items: list[dict[str, Any]]) -> None:
    data = json.loads(links_manifest.read_text(encoding="utf-8-sig"))
    captured = {str(item.get("item_id") or ""): item for item in captured_items}
    merged = []
    for item in data.get("items", []):
        key = str(item.get("item_id") or "")
        merged.append(captured.get(key) or previous.get(key) or item)
    out = {"meta": {"engine": "playwright", "total": len(merged), "reused": len(previous)}, "items": merged}
    (output_dir / "screenshots_manifest.json").write_text(_safe_json(out), encoding="utf-8")


def missing_browser_error(output: str) -> bool:
    return "Executable doesn't exist" in output and "playwright install" in output


def build_node_env() -> dict[str, str]:
    env = os.environ.copy()
    node_paths = []
    env_node_modules = env.get("NODE_MODULES")
    if env_node_modules:
        node_paths.append(Path(env_node_modules))
    node_paths.extend([PROJECT_ROOT / "node_modules", Path.cwd() / "node_modules"])
    existing = env.get("NODE_PATH")
    if existing:
        node_paths.append(Path(existing))
    env["NODE_PATH"] = os.pathsep.join(str(path) for path in node_paths if path.exists() or str(path) == existing)
    return env


def is_executable_available(command: str) -> bool:
    path = Path(command)
    if path.is_absolute() or path.parent != Path("."):
        return path.exists()
    return shutil.which(command) is not None


def find_playwright_cli() -> Path | None:
    candidates = [PROJECT_ROOT / "node_modules" / "playwright" / "cli.js", Path.cwd() / "node_modules" / "playwright" / "cli.js"]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None


if __name__ == "__main__":
    raise SystemExit(main())
