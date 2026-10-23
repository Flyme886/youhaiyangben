/*
 * capture_multi.js — Ego Lite 多平台舆情页面截图（stdin 执行）
 *
 * 由 run_capture_multi.sh 拼 prelude 后 `ego-browser nodejs`
 *   globalThis.__CAPTURE_ENV = {CONFIG, LIMIT, TAG, FRESH, SPACE}
 *
 * 平台：douyin / xhs / weibo / bilibili / tieba / weixin / toutiao / other
 * 同一 shot_key（同一归一化地址）只截一次，多条样本复用同一张图。
 */
const fs = await import("node:fs/promises");
const path = await import("node:path");

const ENV = globalThis.__CAPTURE_ENV || {};
const envOf = (k, d) => (ENV[k] !== undefined ? ENV[k] : process.env["CAPTURE_" + k] ?? d);

const CONFIG = envOf("CONFIG", "");
if (!CONFIG) {
  console.error("Missing CONFIG");
  process.exit(1);
}
const cfg = JSON.parse(await fs.readFile(CONFIG, "utf8"));
cfg.limit = Number(envOf("LIMIT", cfg.limit || 0));
cfg.tag = String(envOf("TAG", cfg.tag || "run"));
cfg.fresh = String(envOf("FRESH", cfg.fresh ?? true)) === "true";
if (ENV.SPACE !== undefined && ENV.SPACE !== "") cfg.space = ENV.SPACE;

const SHOTS = cfg.shots_dir;
const RESULTS_DIR = cfg.results_dir;
const RESULTS_FILE = path.join(RESULTS_DIR, `results_${cfg.tag}.jsonl`);
await fs.mkdir(SHOTS, { recursive: true });
await fs.mkdir(RESULTS_DIR, { recursive: true });

const WD = Object.assign(
  { goto: 30000, nav: 22000, settle: 1400, hide: 5000, probe: 5000, extract: 12000, shot: 22000, reset: 10000 },
  cfg.watchdog || {}
);
const PLATFORM_SETTLE = Object.assign(
  { douyin: 3500, xhs: 3200, weibo: 2200, bilibili: 2200, tieba: 1600, weixin: 2200, toutiao: 1800, other: 1600 },
  cfg.platform_settle || {}
);

// 错误页特征表。命中即分类，不靠字数猜。
const CAPTCHA_RE =
  /验证码中间页|请完成下列验证|拖动完成上方拼图|人机验证|安全验证|滑动验证|访问过于频繁|操作过于频繁|异常访问/;
const DELETED_RE =
  /页面不见了|笔记不存在|内容不存在|该笔记不存在|已删除|已被删除|帖子可能已被删除|该内容无法查看|内容已失效|视频不存在|该微博不存在|暂无内容/;
const LOGIN_RE = /登录后查看|请先登录|扫码登录|登录可见|打开APP查看|打开小红书/;

const all = JSON.parse(await fs.readFile(cfg.manifest, "utf8")).items;
const byKey = new Map();
for (const it of all) if (!byKey.has(it.shot_key)) byKey.set(it.shot_key, it);

const settled = new Set();
const attempts = new Map();
try {
  const files = (await fs.readdir(RESULTS_DIR)).filter((f) => /^results_.*\.jsonl$/.test(f));
  for (const f of files) {
    const lines = (await fs.readFile(path.join(RESULTS_DIR, f), "utf8")).split("\n");
    for (const line of lines) {
      if (!line.trim()) continue;
      let o;
      try {
        o = JSON.parse(line);
      } catch {
        continue;
      }
      const k = String(o.shot_key);
      if (o.status === "ok") settled.add(k);
      else attempts.set(k, (attempts.get(k) || 0) + 1);
    }
  }
} catch {}

const maxAttempts = cfg.max_attempts || 2;
const remaining = [...byKey.values()].filter(
  (it) => !settled.has(String(it.shot_key)) && (attempts.get(String(it.shot_key)) || 0) < maxAttempts
);
let pending = remaining;
if (cfg.limit > 0) pending = remaining.slice(0, cfg.limit);
console.log("already_ok", settled.size, "remaining", remaining.length, "pending", pending.length);

if (pending.length === 0) {
  console.log("ALL DONE");
} else {
  const task = await taskSpace(cfg.space);
  const withTimeout = (promise, ms, label) => {
    let timer;
    const timeout = new Promise((_, rej) => {
      timer = setTimeout(() => rej(new Error("WATCHDOG:" + label)), ms);
    });
    return Promise.race([promise, timeout]).finally(() => clearTimeout(timer));
  };

  if (cfg.fresh) {
    for (const p of await task.pages()) await withTimeout(p.close(), 6000, "close").catch(() => {});
    await new Promise((r) => setTimeout(r, 600));
  }
  let page = null;
  for (const p of await task.pages()) {
    try {
      await withTimeout(p.url(), 3000, "probe-url");
      page = p;
      break;
    } catch {}
  }
  if (!page) page = await task.newPage();
  console.log("page", page.label);

  const resetPage = () =>
    withTimeout(page.goto("about:blank", { timeout: 8000, waitUntil: "commit" }), WD.reset, "reset").catch(() => {});

  // 通用遮罩清理：覆盖大半屏且含登录/扫码/打开APP字样的 fixed 层隐藏
  const hideMask = () =>
    page.evaluate(() => {
      let n = 0;
      const vw = innerWidth,
        vh = innerHeight;
      for (const el of document.querySelectorAll("div, section, aside")) {
        const cs = getComputedStyle(el);
        if (cs.position !== "fixed" || cs.display === "none") continue;
        const r = el.getBoundingClientRect();
        if (r.width < vw * 0.7 || r.height < vh * 0.6) continue;
        const t = (el.innerText || "").slice(0, 200);
        if (!/登录|扫码|打开APP|打开应用|下载客户端/.test(t)) continue;
        if (/微博正文|热门|评论/.test(t) && t.length > 120) continue;
        el.style.setProperty("display", "none", "important");
        n++;
      }
      return n;
    });

  const readBody = () =>
    page.evaluate(() => (document.body ? document.body.innerText : "").replace(/\s+/g, " ").trim());

  let n = 0;
  let consecutiveBad = 0;
  for (const it of pending) {
    if (consecutiveBad >= 15) {
      console.log("CIRCUIT_BREAKER after", consecutiveBad);
      break;
    }
    if (n > 0 && n % (cfg.recycle || 10) === 0) {
      await resetPage();
      console.log("RESET");
    }
    n += 1;

    const name = String(it.shot_key) + ".png";
    const out = {
      shot_key: it.shot_key,
      item_id: it.item_id,
      sheet_id: it.sheet_id,
      source: it.source,
      platform: it.platform,
      title: it.title,
      link: it.link,
      nav_url: it.nav_url,
      engine: "ego-browser",
      status: "ok",
      reason: "",
      screenshot_path: name,
      page_title: "",
      page_text: "",
    };
    const t0 = Date.now();
    let stuck = false;
    try {
      await withTimeout(page.goto(it.nav_url, { timeout: WD.nav, waitUntil: "domcontentloaded" }), WD.goto, "goto");
      await page.waitForTimeout(PLATFORM_SETTLE[it.platform] || WD.settle);
      if (it.platform === "douyin") {
        await withTimeout(
          page.waitForSelector('video, [data-e2e="detail-video-desc"], [data-e2e="video-desc"]', { timeout: 5000 }),
          6000,
          "waitdetail"
        ).catch(() => {});
      }
      if (it.platform === "xhs") {
        await withTimeout(
          page.waitForSelector("#detail-title, .note-content, .note-scroller", { timeout: 6000 }),
          7000,
          "waitnote"
        ).catch(() => {});
      }
      await withTimeout(hideMask(), WD.hide, "hide").catch(() => 0);

      let text = "";
      for (let round = 0; round < 2; round++) {
        const started = Date.now();
        while (Date.now() - started < 5000) {
          text = await withTimeout(readBody(), WD.extract, "extract").catch(() => "");
          if (text && text.length >= 20) break;
          await page.waitForTimeout(500);
        }
        if (text && text.length >= 20) break;
        if (round === 1) break;
        await withTimeout(page.reload({ timeout: WD.nav, waitUntil: "domcontentloaded" }), WD.goto, "reload").catch(
          () => {}
        );
        await page.waitForTimeout(WD.settle);
      }
      await page.waitForTimeout(cfg.extra_settle || 900);
      const ev = await withTimeout(
        page.evaluate(() => ({
          title: (document.title || "").trim().slice(0, 300),
          text: (document.body ? document.body.innerText : "").replace(/\s+/g, " ").trim().slice(0, 3000),
        })),
        WD.extract,
        "extract"
      );
      out.page_title = ev.title;
      out.page_text = ev.text;
      // 状态判定：先认错误页特征，再看字数。
      // 只看字数会把「安全验证」页判成成功、把「内容已删除」判成失败，
      // 而后者恰恰是有效证据，必须留下截图。
      const sig = ev.title + " " + ev.text;
      const head = ev.text.slice(0, 500);
      if (CAPTCHA_RE.test(sig)) {
        out.status = "captcha";
        out.reason = "risk challenge";
      } else if (DELETED_RE.test(sig)) {
        out.status = "deleted";
        out.reason = "page states content removed";
      } else if (LOGIN_RE.test(head) && ev.text.length < 600) {
        out.status = "login_wall";
        out.reason = "login required";
      } else if (ev.text.trim().length < 20) {
        out.status = "blocked_or_blank";
        out.reason = "blank or too short";
      }
      await withTimeout(page.screenshot({ path: path.join(SHOTS, name) }), WD.shot, "shot");
    } catch (e) {
      const msg = String((e && e.message) || e);
      stuck = msg.startsWith("WATCHDOG:");
      out.status = stuck ? "timeout" : "navigation_error";
      out.reason = (stuck ? "watchdog on " : "") + msg.slice(0, 200);
      await resetPage();
    }
    await fs.appendFile(RESULTS_FILE, JSON.stringify(out) + "\n", "utf8");
    consecutiveBad = out.status === "ok" || out.status === "deleted" ? 0 : consecutiveBad + 1;
    console.log(
      [it.shot_key, it.platform, out.status, ((Date.now() - t0) / 1000).toFixed(1) + "s", out.page_title.slice(0, 26)].join("\t")
    );
  }
}
