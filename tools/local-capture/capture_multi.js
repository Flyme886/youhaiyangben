/*
 * capture_multi.js —— Ego Lite 页面截图（由 run_capture_multi.sh 驱动）
 *
 * 通过 globalThis.__CAPTURE_ENV 注入 {CONFIG, LIMIT, TAG, FRESH, SPACE}，
 * 配置项：manifest / shots_dir / results_dir / space / recycle / max_attempts / zoom / max_scrolls
 *
 * 每条产出两张：
 *   {shot_key}.png      主图，首屏整屏（内容主体 + 作者 + 侧栏）
 *   {shot_key}_hit.png  命中图，评论命中处放大 + 红框圈注
 *
 * 命中定位优先级：标题片段 → 攻击词 → 攻击对象词。
 * 红框用元素自身 box-shadow/outline 渲染（随页面 zoom 一起缩放，不做坐标换算）。
 * 没命中就不产出第二张图——不截与标题无关的评论区首屏，避免误导。
 */
const fs = await import("node:fs/promises");
const path = await import("node:path");

const ENV = globalThis.__CAPTURE_ENV || {};
const envOf = (k, d) => (ENV[k] !== undefined ? ENV[k] : process.env["CAPTURE_" + k] ?? d);

const CONFIG = envOf("CONFIG", "");
if (!CONFIG) { console.error("Missing CONFIG"); process.exit(1); }
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

const WD = Object.assign({ goto: 32000, nav: 26000, settle: 3600, shot: 25000, extract: 12000, reset: 8000 }, cfg.watchdog || {});
const ZOOM = cfg.zoom || "1.9";
const MAX_SCROLLS = cfg.max_scrolls || 14;

const CAPTCHA_RE = /验证码中间页|请完成下列验证|拖动完成上方拼图|人机验证|安全验证|滑动验证|访问过于频繁|操作过于频繁|异常访问/;
const DELETED_RE = /你要观看的视频不存在|视频不存在|作品已删除|该内容无法查看|内容已失效|页面不见了|帖子已被删除|该微博不存在|暂无内容|笔记不存在/;
const LOGIN_RE = /登录后查看|请先登录|扫码登录|登录可见/;
const ERROR_PAGE_RE = /出错啦|出错了|页面加载失败|系统繁忙|请求崩溃|网络错误/;

const all = JSON.parse(await fs.readFile(cfg.manifest, "utf8")).items;
const done = new Set();
const attempts = new Map();
try {
  const files = (await fs.readdir(RESULTS_DIR)).filter((f) => /^results_.*\.jsonl$/.test(f));
  for (const f of files) {
    for (const line of (await fs.readFile(path.join(RESULTS_DIR, f), "utf8")).split("\n")) {
      if (!line.trim()) continue;
      let o; try { o = JSON.parse(line); } catch { continue; }
      const k = String(o.item_id);
      if (o.status === "ok" || o.status === "deleted") done.add(k);
      else attempts.set(k, (attempts.get(k) || 0) + 1);
    }
  }
} catch {}

const maxAttempts = cfg.max_attempts || 2;
const remaining = all.filter((it) => !done.has(String(it.item_id)) && (attempts.get(String(it.item_id)) || 0) < maxAttempts);
let pending = remaining;
if (cfg.limit > 0) pending = remaining.slice(0, cfg.limit);
console.log("already_ok", done.size, "remaining", remaining.length, "pending", pending.length);

if (pending.length === 0) {
  console.log("ALL DONE");
} else {
  const task = await taskSpace(cfg.space);
  const withTimeout = (p, ms, label) => {
    let t;
    const to = new Promise((_, rej) => { t = setTimeout(() => rej(new Error("WATCHDOG:" + label)), ms); });
    return Promise.race([p, to]).finally(() => clearTimeout(t));
  };

  if (cfg.fresh) {
    for (const p of await task.pages()) await withTimeout(p.close(), 6000, "close").catch(() => {});
    await new Promise((r) => setTimeout(r, 600));
  }
  let page = null;
  for (const p of await task.pages()) {
    try { await withTimeout(p.url(), 3000, "probe-url"); page = p; break; } catch {}
  }
  if (!page) page = await task.newPage();
  console.log("page", page.label);

  const resetPage = () => withTimeout(page.goto("about:blank", { timeout: 8000, waitUntil: "commit" }), WD.reset, "reset").catch(() => {});

  const windowId = async () => {
    const w = await withTimeout(task.cdp("Browser.getWindowForTarget", { targetId: page.targetId }), 4000, "win").catch(() => null);
    return w && w.windowId ? w.windowId : null;
  };
  const ensureWindow = async () => {
    const id = await windowId();
    if (!id) return false;
    try {
      const b = await withTimeout(task.cdp("Browser.getWindowBounds", { windowId: id }), 4000, "bounds");
      if (b && b.bounds && b.bounds.windowState === "minimized") {
        await withTimeout(task.cdp("Browser.setWindowBounds", { windowId: id, bounds: { windowState: "normal" } }), 6000, "restore");
        await page.waitForTimeout(700);
        return true;
      }
    } catch {}
    return false;
  };
  const shot = async (file, opts = {}) => {
    try {
      await withTimeout(page.screenshot({ path: file, ...opts }), WD.shot, "shot");
    } catch (e) {
      const msg = String((e && e.message) || e);
      if (!/minimized|captureScreenshot|WATCHDOG:shot/.test(msg)) throw e;
      const restored = await ensureWindow();
      await page.waitForTimeout(restored ? 500 : 1200);
      await withTimeout(page.screenshot({ path: file, ...opts }), WD.shot, "shot-retry");
    }
  };

  // 遮罩清理
  const hideMask = () => page.evaluate(() => {
    let n = 0;
    const vw = innerWidth, vh = innerHeight;
    for (const el of document.querySelectorAll("div,section,aside")) {
      const cs = getComputedStyle(el);
      if (cs.position !== "fixed" || cs.display === "none") continue;
      const r = el.getBoundingClientRect();
      if (r.width < vw * 0.6 || r.height < vh * 0.5) continue;
      const t = (el.innerText || "").slice(0, 200);
      if (!/登录|扫码|打开APP|打开应用|下载客户端|快速访问/.test(t)) continue;
      el.style.setProperty("display", "none", "important");
      n++;
    }
    return n;
  }).catch(() => 0);

  const readState = () => page.evaluate(() => {
    const t = document.body ? document.body.innerText : "";
    return {
      title: (document.title || "").trim().slice(0, 200),
      text: t.replace(/\s+/g, " ").trim(),
      len: t.length,
      loading: t.includes("视频数据加载中"),
      hasVideo: !!document.querySelector("video"),
      comments: document.querySelectorAll('[data-e2e="comment-item"]').length,
      vw: innerWidth, vh: innerHeight,
    };
  });

  // 评论区可见条数
  const commentVisible = () => page.evaluate(() =>
    [...document.querySelectorAll('[data-e2e="comment-item"]')].filter((e) => e.getBoundingClientRect().height > 0).length
  ).catch(() => 0);

  // 顶部导航在 zoom 后会压进视口，截图前隐藏
  const hideNav = () => page.evaluate(() => {
    let n = 0;
    for (const s of ['header', '[data-e2e="navbar"]', '[class*="header"]', '[class*="Header"]']) {
      for (const el of document.querySelectorAll(s)) {
        const r = el.getBoundingClientRect();
        if (r.top < 80 && r.height < 120 && r.width > 400) { el.style.setProperty("visibility", "hidden", "important"); n++; }
      }
    }
    return n;
  }).catch(() => 0);

  // 点开评论区：tab → 评论图标 → 竖排互动栏里的评论按钮
  const clickCandidate = (kind) => page.evaluate((k) => {
    const hit = (el) => { el.click(); return true; };
    if (k === "tab") {
      for (const el of document.querySelectorAll("div,span,button,a")) {
        const t = (el.innerText || "").trim();
        if (/^评论\s*[（(]?\d*[）)]?$/.test(t) && el.children.length <= 2) return hit(el);
      }
      return false;
    }
    if (k === "icon") {
      for (const s of ['[data-e2e="feed-comment-icon"]', '[data-e2e="comment-icon"]', '[data-e2e*="comment"]']) {
        const el = document.querySelector(s);
        if (el && el.getBoundingClientRect().height > 0) return hit(el);
      }
      return false;
    }
    // 竖排互动栏：评论数按钮（innerText 为纯数字，且在右侧）
    const btns = [...document.querySelectorAll("div,button,span")].filter((el) => {
      const t = (el.innerText || "").trim();
      if (!/^\d{1,6}$/.test(t)) return false;
      const r = el.getBoundingClientRect();
      return r.height > 0 && r.height < 80 && r.left > innerWidth * 0.6;
    });
    if (btns.length) return hit(btns[0]);
    return false;
  }, kind).catch(() => false);

  const openComments = async () => {
    if ((await commentVisible()) > 0) return "already";
    for (const k of ["tab", "icon", "count"]) {
      const ok = await clickCandidate(k);
      if (!ok) continue;
      await page.waitForTimeout(1900);
      if ((await commentVisible()) > 0) return k;
    }
    return null;
  };

  // 找命中：返回元素 rect；带 outline 标记
  const markHit = (needles) => page.evaluate((list) => {
    const items = [...document.querySelectorAll('[data-e2e="comment-item"]')];
    const vis = items.filter((e) => e.getBoundingClientRect().height > 0);
    for (const w of list) {
      if (!w || w.length < 2) continue;
      for (const el of vis) {
        const t = el.innerText || "";
        if (!t.includes(w)) continue;
        el.scrollIntoView({ block: "center" });
        el.style.setProperty("box-shadow", "0 0 0 4px #FF0000 inset", "important");
        el.style.setProperty("outline", "4px solid #FF0000", "important");
        el.setAttribute("data-zyj-hit", "1");
        return { word: w, text: t.replace(/\s+/g, " ").slice(0, 120) };
      }
    }
    return null;
  }, needles);

  const clearMark = () => page.evaluate(() => {
    for (const el of document.querySelectorAll('[data-zyj-hit]')) {
      el.style.removeProperty("outline");
      el.style.removeProperty("box-shadow");
      el.removeAttribute("data-zyj-hit");
    }
  }).catch(() => {});

  const scrollComments = () => page.evaluate(() => {
    const items = [...document.querySelectorAll('[data-e2e="comment-item"]')];
    let box = null;
    const last = items[items.length - 1];
    let el = last && last.parentElement;
    while (el && el !== document.body) {
      const cs = getComputedStyle(el);
      if (/auto|scroll/.test(cs.overflowY) && el.scrollHeight > el.clientHeight + 40) { box = el; break; }
      el = el.parentElement;
    }
    if (!box) window.scrollBy(0, 700);
    else box.scrollBy(0, Math.max(400, box.clientHeight * 0.8));
    return !!box;
  }).catch(() => false);

  // 命中图：zoom 放大后按视口裁剪
  const shootHit = async (key) => {
    await page.evaluate(() => {
      const el = document.querySelector('[data-zyj-hit]');
      if (el) el.scrollIntoView({ block: "center" });
    }).catch(() => {});
    await page.waitForTimeout(650);
    const box = await page.evaluate(() => {
      const el = document.querySelector('[data-zyj-hit]');
      if (!el) return null;
      const r = el.getBoundingClientRect();
      return { x: Math.round(r.left), y: Math.round(r.top), w: Math.round(r.width), h: Math.round(r.height), vw: innerWidth, vh: innerHeight };
    });
    if (!box) return null;
    const padX = 22, padY = 30;
    const w = Math.min(box.w + padX * 2, box.vw);
    const h = Math.min(Math.max(box.h + padY * 2, 280), box.vh);
    const x = Math.max(0, Math.min(box.x - padX, box.vw - w));
    const y = Math.max(0, Math.min(box.y - padY, box.vh - h));
    if (w < 40 || h < 40) return null;
    await shot(path.join(SHOTS, `${key}_hit.png`), { clip: { x, y, width: w, height: h } });
    return { w, h, hit_h: box.h };
  };

  let n = 0, bad = 0;
  await ensureWindow();
  for (const it of pending) {
    if (bad >= 15) { console.log("CIRCUIT_BREAKER", bad); break; }
    if (n > 0 && n % (cfg.recycle || 12) === 0) { await resetPage(); console.log("RESET"); }
    n += 1;

    const key = String(it.shot_key);
    const out = {
      shot_key: key, item_id: it.item_id, sheet_id: it.sheet_id, source: it.source,
      platform: it.platform, title: it.title, link: it.link, nav_url: it.nav_url,
      engine: "ego-browser", status: "ok", reason: "", screenshot_path: `${key}.png`,
      page_title: "", page_text: "", hit: false, hit_word: "", hit_text: "", hit_file: "", has_hit_img: false,
    };
    const t0 = Date.now();
    try {
      await withTimeout(page.goto(it.nav_url, { timeout: WD.nav, waitUntil: "domcontentloaded" }), WD.goto, "goto");
      await page.waitForTimeout(WD.settle);
      let st = await withTimeout(readState(), WD.extract, "extract").catch(() => ({ text: "", len: 0, loading: true }));
      // 卡在加载中就重载一次
      if (st.loading || st.len < 300) {
        await withTimeout(page.reload({ timeout: WD.nav, waitUntil: "domcontentloaded" }), WD.goto, "reload").catch(() => {});
        await page.waitForTimeout(4600);
        st = await withTimeout(readState(), WD.extract, "extract2").catch(() => st);
      }
      await withTimeout(hideMask(), 5000, "hide").catch(() => 0);
      st = await withTimeout(readState(), WD.extract, "extract3").catch(() => st);

      out.page_title = st.title;
      out.page_text = (st.text || "").slice(0, 3000);
      const sig = (st.title || "") + " " + (st.text || "");
      if (CAPTCHA_RE.test(sig)) { out.status = "captcha"; out.reason = "risk challenge"; }
      else if (DELETED_RE.test(sig)) { out.status = "deleted"; out.reason = "content removed"; }
      else if (LOGIN_RE.test((st.text || "").slice(0, 500)) && st.len < 600) { out.status = "login_wall"; out.reason = "login required"; }
      else if (ERROR_PAGE_RE.test(sig)) { out.status = "err_page"; out.reason = "spa error"; }
      else if (st.loading) { out.status = "loading_stuck"; out.reason = "video loading forever"; }
      else if (st.len < 200) { out.status = "blocked_or_blank"; out.reason = "blank"; }
      if (out.status === "captcha" || out.status === "err_page" || out.status === "loading_stuck") {
        // 截图留档便于复核，但不入册
        await shot(path.join(SHOTS, `${key}.png`)).catch(() => {});
        throw Object.assign(new Error("RETRY:" + out.status), { retry: true });
      }

      // 1) 主图：首屏
      await shot(path.join(SHOTS, `${key}.png`));

      if (out.status === "ok") {
        // 2) 评论 tab + 命中定位
        if (it.platform === "douyin") {
          const tab = await withTimeout(openComments(), 8000, "tab").catch(() => null);
          out.comment_tab = tab || "";
          if (tab) {
            await page.waitForTimeout(2400);
            // 关键词：标题片段优先，然后攻击词、攻击对象
            const titleRaw = (it.title_full || it.title || "").replace(/[（(]无标题[）)]/g, "");
            const frag = [];
            const clean = titleRaw.replace(/[^\u4e00-\u9fa5A-Za-z0-9]/g, "");
            if (clean.length >= 6) {
              frag.push(clean.slice(0, 14));
              for (let i = 6; i + 10 <= Math.min(clean.length, 60); i += 8) frag.push(clean.slice(i, i + 10));
            }
            for (const w of (it.attack_words || [])) if (w.length >= 2) frag.push(w);
            for (const w of (it.attack_objects || [])) if (w.length >= 3) frag.push(w);
            const uniq = [...new Set(frag)].slice(0, 8);

            let hit = await markHit(uniq).catch(() => null);
            for (let s = 0; s < MAX_SCROLLS && !hit; s++) {
              await scrollComments();
              await page.waitForTimeout(600);
              hit = await markHit(uniq).catch(() => null);
            }
            if (hit) {
              out.hit = true;
              out.hit_word = hit.word;
              out.hit_text = hit.text;
              // 放大后重定位再框（保证截得清）
              await page.evaluate((z) => { document.documentElement.style.zoom = z; }, ZOOM);
              await page.waitForTimeout(1100);
              await hideNav();
              const hit2 = await markHit([hit.word]).catch(() => null);
              await page.evaluate(() => {
                const el = document.querySelector('[data-zyj-hit]');
                if (el) el.scrollIntoView({ block: "center" });
              }).catch(() => {});
              await page.waitForTimeout(800);
              const meta = await shootHit(key).catch(() => null);
              if (meta) {
                out.has_hit_img = true;
                out.hit_file = `${key}_hit.png`;
                out.hit_size = `${meta.w}x${meta.h}`;
              }
              if (!hit2) out.reason = "zoom re-locate failed";
              await page.evaluate(() => { document.documentElement.style.zoom = ""; }).catch(() => {});
              await clearMark();
            } else {
              // 没命中：截评论首屏（无框），仍入册
              out.reason = "comment hit not found";
              await page.evaluate((z) => { document.documentElement.style.zoom = z; }, ZOOM);
              await page.waitForTimeout(900);
              await page.evaluate(() => {
                const items = [...document.querySelectorAll('[data-e2e="comment-item"]')].filter((e) => e.getBoundingClientRect().height > 0);
                if (items[0]) items[0].scrollIntoView({ block: "start" });
              }).catch(() => {});
              await page.waitForTimeout(600);
              const m = await page.evaluate(() => {
                const items = [...document.querySelectorAll('[data-e2e="comment-item"]')].filter((e) => e.getBoundingClientRect().height > 0);
                if (!items.length) return null;
                const r = items[0].getBoundingClientRect();
                return { x: Math.round(r.left), y: Math.round(r.top), vw: innerWidth, vh: innerHeight };
              });
              if (m) {
                await shot(path.join(SHOTS, `${key}_hit.png`), {
                  clip: { x: Math.max(0, m.x - 26), y: Math.max(0, m.y - 40), width: Math.min(m.vw - Math.max(0, m.x - 26), 760), height: Math.min(m.vh - Math.max(0, m.y - 40), 640) },
                }).then(() => { out.has_hit_img = true; out.hit_file = `${key}_hit.png`; }).catch(() => {});
              }
              await page.evaluate(() => { document.documentElement.style.zoom = ""; }).catch(() => {});
            }
          }
        } else {
          // 非抖音：只标正文命中
          const t = (it.title_full || "").slice(0, 14);
          if (t.length >= 6) {
            const r = await page.evaluate((w) => {
              for (const el of document.querySelectorAll("p,div,span")) {
                const tx = el.innerText || "";
                if (tx.length > 600 || tx.length < 4 || !tx.includes(w)) continue;
                el.scrollIntoView({ block: "center" });
                el.style.setProperty("outline", "4px solid #FF0000", "important");
                el.style.setProperty("outline-offset", "2px", "important");
                el.setAttribute("data-zyj-hit", "1");
                return true;
              }
              return false;
            }, t).catch(() => false);
            if (r) {
              out.hit = true; out.hit_word = t;
              await page.evaluate((z) => { document.documentElement.style.zoom = z; }, "1.6");
              await page.waitForTimeout(900);
              await page.evaluate(() => { const e = document.querySelector('[data-zyj-hit]'); if (e) e.scrollIntoView({ block: "center" }); });
              await page.waitForTimeout(700);
              await page.evaluate(() => {
                const e = document.querySelector('[data-zyj-hit]');
                if (e) { e.style.setProperty("outline", "4px solid #FF0000", "important"); }
              });
              const m = await shootHit(key).catch(() => null);
              if (m) { out.has_hit_img = true; out.hit_file = `${key}_hit.png`; }
              await page.evaluate(() => { document.documentElement.style.zoom = ""; }).catch(() => {});
              await clearMark();
            }
          }
        }
      }
    } catch (e) {
      const msg = String((e && e.message) || e);
      if (e && e.retry) {
        out.status = msg.replace("RETRY:", "");
      } else {
        out.status = msg.startsWith("WATCHDOG:") ? "timeout" : "navigation_error";
        out.reason = msg.slice(0, 200);
        await resetPage();
      }
    }
    await fs.appendFile(RESULTS_FILE, JSON.stringify(out) + "\n", "utf8");
    bad = out.status === "ok" || out.status === "deleted" ? 0 : bad + 1;
    console.log([key, it.platform, out.status, out.hit ? "HIT:" + out.hit_word : "nohit", ((Date.now() - t0) / 1000).toFixed(1) + "s", (out.page_title || "").slice(0, 22)].join("\t"));
  }
  console.log("BATCH DONE");
}
