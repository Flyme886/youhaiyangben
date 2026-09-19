/*
 * capture_douyin.js  —  Ego Lite 抖音视频页单条截图抓取（stdin 执行）
 *
 * 运行方式：
 *   ego-browser nodejs < capture_douyin.js
 *
 * 本脚本运行在 Ego Lite 注入的环境里，全局已存在：
 *   taskSpace(id)                              -> 取得/复用浏览器 space
 *   task.pages() / task.newPage() / task.page("p1") / task.handOff()
 *   page.goto / page.waitForTimeout / page.evaluate / page.screenshot / page.close / page.label
 * 使用【顶层 await】，不要 require/import 任何 Ego SDK，也不要用 Playwright 的
 * locator() 之类本环境未文档化的 API。
 *
 * ───────────────────────── 配置格式（JSON） ─────────────────────────
 * 配置读取顺序：
 *   1. 环境变量 CAPTURE_CONFIG 指向的 JSON 文件路径
 *   2. 与本脚本同目录下的 capture_config.json
 *   3. 环境变量可单独覆盖：CAPTURE_MANIFEST / CAPTURE_SHOTS / CAPTURE_RESULTS
 *      / CAPTURE_TAG / CAPTURE_LIMIT / CAPTURE_SPACE / CAPTURE_FRESH
 * 字段（均可选，给出默认值）：
 * {
 *   "manifest":   "/abs/path/to/links_manifest.json",   // 顺序基准，必填
 *   "shots_dir":  "/abs/path/to/screenshots",           // 截图输出目录，必填
 *   "results_dir":"/abs/path/to/_ego",                  // results*.jsonl 所在目录
 *   "space_id":   3,                                    // taskSpace() 的 space 编号
 *   "limit":      0,                                    // 本轮最多抓多少条(0=全部 pending)
 *   "tag":        "run",                                // 结果文件名 results_<tag>.jsonl
 *   "fresh":      true,                                 // 每片开头是否重建标签页
 *   "recycle":    99,                                   // 单次会话内每 N 条重置页面(防劣化)
 *   "watchdog": {                                       // 各类超时(毫秒)
 *     "goto":   24000,   // page.goto 的竞赛看门狗超时
 *     "nav":    18000,   // page.goto 的导航 timeout 选项(等 domcontentloaded)
 *     "settle": 1300,    // goto 后稳定等待
 *     "hide":   5000,    // 关闭登录弹窗 evaluate 超时
 *     "probe":  5000,    // 弹窗是否消失探测超时
 *     "extract":10000,   // 提取 title/text 超时
 *     "shot":   20000,   // 截图超时
 *     "reset":  10000    // 重置到 about:blank 超时
 *   }
 * }
 *
 * ───────────────────────── 实战结论（务必保留） ─────────────────────────
 * 1. page.goto(url, { waitUntil: "domcontentloaded" })：抖音视频页永远等不到
 *    load（视频流持续加载），用默认值每条都会等到超时。
 * 2. 单线程，绝不并发：并发会触发风控，连累整个会话限流。
 * 3. URL 改写：/share/video/{id} -> /video/{id}（前者返回「验证码中间页」）；
 *    但记录里保留原始链接。
 * 4. 关闭登录弹窗：页面里有两个 .douyin_login_new_class，取【面积最大】那个，
 *    再向上找全屏 position:fixed 祖先设 display:none（用 parentElement 逐级尝试）。
 * 5. 验证码判定【只看标题】（验证码中间页）或正文特定词（请完成下列验证 / 拖动完成上方拼图 /
 *    人机验证 / 安全验证）；不要用 /验证码/ 匹配全文——登录弹窗里有「验证码登录」会误报。
 * 6. 每条加超时看门狗；超时后必须重置页面，否则污染后续条目。
 * 7. 可断点续跑：结果逐条 append 到 results*.jsonl，启动时把 status==="ok" 的
 *    item_id 全部跳过。
 * 8. 复用 space 内已有页面（task.pages() 取第一个），不要每次 newPage()——
 *    连续 close 再 newPage 会触发页面账本冲突（报 "is already page pNN"）。
 * 9. 输出字段与仓库 quality_check_screenshots.py 兼容：
 *    item_id, source_row, original_no, sheet_id, source, title, author, link,
 *    summary, engine, status, reason, captcha_signals, needs_manual_action,
 *    screenshot_path, page_title, page_text。screenshot_path 是相对文件名 00001.png 形式。
 */

const fs = await import("node:fs/promises");
const path = await import("node:path");

const SCRIPT_DIR = path.dirname(new URL(import.meta.url).pathname);

// ── 读配置 ───────────────────────────────────────────────────────────────
const DEFAULTS = {
  manifest: "",
  shots_dir: "",
  results_dir: "",
  space_id: 3,
  limit: 0,
  tag: "run",
  fresh: true,
  recycle: 99,
  watchdog: {
    goto: 24000,
    nav: 18000,
    settle: 1300,
    hide: 5000,
    probe: 5000,
    extract: 10000,
    shot: 20000,
    reset: 10000,
  },
};

async function loadConfig() {
  let cfg = { ...DEFAULTS, watchdog: { ...DEFAULTS.watchdog } };
  const configPath =
    process.env.CAPTURE_CONFIG ||
    (SCRIPT_DIR ? path.join(SCRIPT_DIR, "capture_config.json") : null);
  if (configPath) {
    try {
      const raw = JSON.parse(await fs.readFile(configPath, "utf8"));
      cfg = {
        ...cfg,
        ...raw,
        watchdog: { ...cfg.watchdog, ...(raw.watchdog || {}) },
      };
    } catch (e) {
      // 配置缺失/损坏不致命，仅用默认
      console.log("config_load_warn", String(e.message || e));
    }
  }
  // 环境变量单键覆盖
  if (process.env.CAPTURE_MANIFEST) cfg.manifest = process.env.CAPTURE_MANIFEST;
  if (process.env.CAPTURE_SHOTS) cfg.shots_dir = process.env.CAPTURE_SHOTS;
  if (process.env.CAPTURE_RESULTS) cfg.results_dir = process.env.CAPTURE_RESULTS;
  if (process.env.CAPTURE_TAG) cfg.tag = process.env.CAPTURE_TAG;
  if (process.env.CAPTURE_LIMIT) cfg.limit = parseInt(process.env.CAPTURE_LIMIT, 10) || 0;
  if (process.env.CAPTURE_SPACE) cfg.space_id = parseInt(process.env.CAPTURE_SPACE, 10) || cfg.space_id;
  if (process.env.CAPTURE_FRESH) cfg.fresh = process.env.CAPTURE_FRESH !== "0" && process.env.CAPTURE_FRESH !== "false";
  if (!cfg.manifest || !cfg.shots_dir || !cfg.results_dir) {
    throw new Error(
      "manifest / shots_dir / results_dir 必须配置（CAPTURE_CONFIG 或环境变量）"
    );
  }
  return cfg;
}

const cfg = await loadConfig();
const WD = cfg.watchdog;
const SHOTS = cfg.shots_dir;
const RESULTS_DIR = cfg.results_dir;
const MANIFEST = cfg.manifest;
const RESULTS_FILE = path.join(RESULTS_DIR, "results_" + cfg.tag + ".jsonl");

await fs.mkdir(SHOTS, { recursive: true });
await fs.mkdir(RESULTS_DIR, { recursive: true });

// ── 断点续跑：统计已 ok 的 item_id ─────────────────────────────────────────
const okIds = new Set();
try {
  const files = (await fs.readdir(RESULTS_DIR)).filter((x) => /^results.*\.jsonl$/.test(x));
  for (const f of files) {
    const text = await fs.readFile(path.join(RESULTS_DIR, f), "utf8");
    for (const line of text.split("\n")) {
      if (!line.trim()) continue;
      try {
        const r = JSON.parse(line);
        if (r.status === "ok") okIds.add(String(r.item_id));
      } catch {}
    }
  }
} catch {}

const items = JSON.parse(await fs.readFile(MANIFEST, "utf8")).items;
let pending = items.filter((it) => !okIds.has(String(it.item_id)));
if (cfg.limit > 0) pending = pending.slice(0, cfg.limit);
console.log("already_ok", okIds.size, "pending", pending.length);

if (pending.length === 0) {
  console.log("ALL DONE");
} else {
  const task = await taskSpace(cfg.space_id);

  function withTimeout(promise, ms, label) {
    let timer;
    const timeout = new Promise((_, rej) => {
      timer = setTimeout(() => rej(new Error("WATCHDOG:" + label)), ms);
    });
    return Promise.race([promise, timeout]).finally(() => clearTimeout(timer));
  }

  // 渲染进程会在同一次会话里跑十几条后明显劣化，分片时重建页面（同一 space 内）
  if (cfg.fresh) {
    for (const p of await task.pages()) {
      await withTimeout(p.close(), 6000, "close").catch(() => {});
    }
  }
  let page = null;
  for (const p of await task.pages()) { page = p; break; }
  if (!page) page = await task.newPage();
  console.log("page", page.label);

  async function resetPage() {
    await withTimeout(
      page.goto("about:blank", { timeout: 8000, waitUntil: "commit" }),
      WD.reset,
      "reset"
    ).catch(() => {});
  }

  // 关闭登录弹窗：取面积最大的 .douyin_login_new_class，向上找全屏 fixed 祖先设 display:none
  const hideLogin = () =>
    page.evaluate(() => {
      const cands = [...document.querySelectorAll(".douyin_login_new_class")];
      let best = null,
        bestArea = 0;
      for (const c of cands) {
        const r = c.getBoundingClientRect();
        const a = r.width * r.height;
        if (a > bestArea) {
          bestArea = a;
          best = c;
        }
      }
      if (!best) return 0;
      let t = best,
        target = best;
      while (t && t !== document.body) {
        const cs = getComputedStyle(t);
        const r = t.getBoundingClientRect();
        if (
          cs.position === "fixed" &&
          r.width >= innerWidth * 0.8 &&
          r.height >= innerHeight * 0.8
        ) {
          target = t;
          break;
        }
        t = t.parentElement;
      }
      target.style.setProperty("display", "none", "important");
      return 1;
    });

  let n = 0,
    consecutiveBad = 0;
  for (const it of pending) {
    if (consecutiveBad >= 12) {
      console.log("CIRCUIT_BREAKER after", consecutiveBad);
      break;
    }
    if (n > 0 && n % cfg.recycle === 0) {
      await resetPage();
      console.log("RESET");
    }
    n += 1;

    const name = String(it.item_id).padStart(5, "0") + ".png";
    const out = {
      item_id: it.item_id,
      source_row: it.source_row,
      original_no: it.original_no,
      sheet_id: it.sheet_id,
      source: it.source,
      title: it.title,
      author: it.author,
      link: it.link,
      summary: it.summary,
      engine: "ego-browser",
      status: "ok",
      reason: "",
      captcha_signals: [],
      needs_manual_action: false,
      screenshot_path: name,
      page_title: "",
      page_text: "",
    };
    const t0 = Date.now();
    let stuck = false;
    try {
      const navUrl = String(it.link).replace("/share/video/", "/video/");
      // 关键：抖音视频页永远等不到 load 事件（直播/视频流持续加载），必须用 domcontentloaded
      await withTimeout(
        page.goto(navUrl, { timeout: WD.nav, waitUntil: "domcontentloaded" }),
        WD.goto,
        "goto"
      );
      await page.waitForTimeout(WD.settle);
      for (let k = 0; k < 2; k++) {
        const hid = await withTimeout(hideLogin(), WD.hide, "hide").catch(() => 0);
        if (!hid) break;
        await page.waitForTimeout(300);
        const gone = await withTimeout(
          page.evaluate(() => !document.body.innerText.includes("扫码登录")),
          WD.probe,
          "probe"
        );
        if (gone) break;
      }
      const ev = await withTimeout(
        page.evaluate(() => ({
          title: (document.title || "").trim().slice(0, 500),
          text: (document.body ? document.body.innerText : "")
            .replace(/\s+/g, " ")
            .trim()
            .slice(0, 4000),
        })),
        WD.extract,
        "extract"
      );
      out.page_title = ev.title;
      out.page_text = ev.text;
      const sig = ev.title + " " + ev.text;
      // 验证码判定只看标题 / 正文特定词；不要用 /验证码/ 匹配全文（登录弹窗有「验证码登录」会误报）
      if (/验证码中间页|请完成下列验证|拖动完成上方拼图|人机验证|安全验证/.test(sig)) {
        out.status = "captcha_detected";
        out.reason = "captcha or risk challenge detected";
      } else if (ev.text.trim().length < 20) {
        out.status = "blocked_or_blank";
        out.reason = "page body is blank or too short";
      }
      await withTimeout(page.screenshot({ path: path.join(SHOTS, name) }), WD.shot, "shot");
    } catch (e) {
      const msg = String((e && e.message) || e);
      stuck = msg.startsWith("WATCHDOG:");
      out.status = stuck ? "timeout" : "navigation_error";
      out.reason = (stuck ? "watchdog timeout on " : "") + msg.slice(0, 250);
    }
    await fs.appendFile(RESULTS_FILE, JSON.stringify(out) + "\n", "utf8");
    consecutiveBad = out.status === "ok" ? 0 : consecutiveBad + 1;
    console.log(
      [it.item_id, out.status, ((Date.now() - t0) / 1000).toFixed(1) + "s", out.page_title.slice(0, 30)].join("\t")
    );
    if (stuck) await resetPage();
  }
  console.log("run complete");
}
