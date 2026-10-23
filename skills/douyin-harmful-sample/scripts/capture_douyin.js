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
 *   1. globalThis.__CAPTURE_ENV.CONFIG（run_capture.sh 注入的 prelude；【推荐】）
 *   2. 环境变量 CAPTURE_CONFIG 指向的 JSON 文件路径
 *   3. 与本脚本同目录下的 capture_config.json（仅在 import.meta.url 有效时）
 *   4. 单键覆盖：CAPTURE_MANIFEST / CAPTURE_SHOTS / CAPTURE_RESULTS
 *      / CAPTURE_TAG / CAPTURE_LIMIT / CAPTURE_SPACE / CAPTURE_FRESH
 *
 * ⚠️ 重要：Ego 的 node 运行时在 **app 侧**执行脚本，调用方的环境变量与环境变量前缀
 *    都不会透传进来（实测 process.env 里没有调用方设置的变量，cwd 恒为 "/"）。
 *    所以 `CAPTURE_CONFIG=xxx ego-browser nodejs < script` 这种写法**不生效**。
 *    必须由 run_capture.sh 在脚本前面拼一段 prelude：
 *        globalThis.__CAPTURE_ENV = {CONFIG:'…', LIMIT:'12', TAG:'c0', FRESH:'true'};
 *    本脚本优先读 prelude，其次才是 process.env（仅当在别处直跑时才有意义）。
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

// ── 配置取值：prelude（globalThis.__CAPTURE_ENV）优先，其次 process.env ──────
// 见文件头「配置格式」一节的说明：ego 的 node 在 app 侧跑，调用方 env 不透传。
const EGO_ENV = globalThis.__CAPTURE_ENV || {};
function envOf(key, fallback) {
  const v = EGO_ENV[key];
  if (v !== undefined && v !== null && v !== "") return String(v);
  const p = process.env["CAPTURE_" + key];
  if (p !== undefined && p !== null && p !== "") return String(p);
  return fallback;
}

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
  extra_settle: 900,
  max_attempts: 2,
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
  // SCRIPT_DIR 在 stdin 执行时恒为 "/"，此时不能拿它拼 capture_config.json
  const scriptDirOk = SCRIPT_DIR && SCRIPT_DIR !== "/" && SCRIPT_DIR !== ".";
  const configPath =
    envOf("CONFIG", null) ||
    (scriptDirOk ? path.join(SCRIPT_DIR, "capture_config.json") : null);
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
  // 单键覆盖（prelude 优先，其次 process.env）
  cfg.manifest = envOf("MANIFEST", cfg.manifest);
  cfg.shots_dir = envOf("SHOTS", cfg.shots_dir);
  cfg.results_dir = envOf("RESULTS", cfg.results_dir);
  cfg.tag = envOf("TAG", cfg.tag);
  const lim = envOf("LIMIT", null);
  if (lim !== null) cfg.limit = parseInt(lim, 10) || 0;
  const sp = envOf("SPACE", null);
  if (sp !== null) cfg.space_id = parseInt(sp, 10) || cfg.space_id;
  const fresh = envOf("FRESH", null);
  if (fresh !== null) cfg.fresh = fresh !== "0" && fresh !== "false";
  if (!cfg.manifest || !cfg.shots_dir || !cfg.results_dir) {
    throw new Error(
      "manifest / shots_dir / results_dir 必须配置（globalThis.__CAPTURE_ENV.CONFIG 或 CAPTURE_* 环境变量）"
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

// ── 断点续跑：统计已 ok 的 item_id，以及每个 item 的尝试次数 ────────────────
// 只跳 ok 会导致「永久 blocked_or_blank 的条目每片都被重抓 → pending 永不归零 → 死循环」，
// 所以额外记录尝试次数，达到 cfg.max_attempts 即视为已处置（降级路径，不是失败）。
const settledIds = new Set();
const attempts = new Map();
try {
  const files = (await fs.readdir(RESULTS_DIR)).filter((x) => /^results.*\.jsonl$/.test(x));
  for (const f of files) {
    const text = await fs.readFile(path.join(RESULTS_DIR, f), "utf8");
    for (const line of text.split("\n")) {
      if (!line.trim()) continue;
      try {
        const r = JSON.parse(line);
        const id = String(r.item_id);
        attempts.set(id, (attempts.get(id) || 0) + 1);
        if (r.status === "ok") settledIds.add(id);
      } catch {}
    }
  }
} catch {}

const maxAttempts = cfg.max_attempts || 2;

const items = JSON.parse(await fs.readFile(MANIFEST, "utf8")).items;
const remaining = items.filter((it) => {
  const id = String(it.item_id);
  if (settledIds.has(id)) return false;
  return (attempts.get(id) || 0) < maxAttempts;
});
let pending = remaining;
if (cfg.limit > 0) pending = remaining.slice(0, cfg.limit);
console.log("already_ok", settledIds.size, "remaining", remaining.length, "pending", pending.length);

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
    const olds = await task.pages();
    for (const p of olds) {
      await withTimeout(p.close(), 6000, "close").catch(() => {});
    }
    // 关闭后 task.pages() 可能还短暂返回刚关掉的“僵尸页”；
    // 直接取第一个会拿到已关闭的页 → 后续每条都报 `page pNN was closed`。
    await new Promise((r) => setTimeout(r, 600));
  }
  let page = null;
  for (const p of await task.pages()) {
    try {
      await withTimeout(p.url(), 3000, "probe-url");
      page = p;
      break;
    } catch {
      // 已关闭的僵尸页，跳过
    }
  }
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
      // 抖音详情页是客户端渲染：domcontentloaded 之后播放器/描述区还要再渲染一拍。
      // 不等就会截到「作者卡已出、播放器一片空白」的半成品（实测约 1/4 条目如此）。
      // best-effort：等不到 video/描述节点也不致命，超时后照常走下面的轮询。
      await withTimeout(
        page.waitForSelector(
          'video, [data-e2e="detail-video-desc"], [data-e2e="video-desc"], [data-e2e="detail-video-player"]',
          { timeout: 5000 }
        ),
        6000,
        "waitdetail"
      ).catch(() => {});
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
      // ── 等正文真正渲染出来 ──────────────────────────────────────────────
      // 抖音 SPA 常停在整页「加载中」的骨架态（截图只有转圈图标，正文 0 字）。
      // 轮询正文；若持续卡在加载态，重载一次再等。这是 blocked_or_blank 的主因。
      const readBodyText = () =>
        page.evaluate(() =>
          (document.body ? document.body.innerText : "").replace(/\s+/g, " ").trim()
        );
      const stillLoading = (t) =>
        !t || t.length < 20 || /加载中|加载失败|网络异常|请稍后重试/.test(t.slice(-40));
      let bodyText = "";
      for (let round = 0; round < 3; round++) {
        const started = Date.now();
        const budget = round === 0 ? 5000 : 4000;
        while (Date.now() - started < budget) {
          bodyText = await withTimeout(readBodyText(), WD.extract, "extract").catch(() => "");
          if (!stillLoading(bodyText)) break;
          await page.waitForTimeout(500);
        }
        if (!stillLoading(bodyText)) break;
        if (round === 2) break;
        // 卡在「加载中」：重载一次（仍用 domcontentloaded，别改回 load）
        await withTimeout(
          page.reload({ timeout: WD.nav, waitUntil: "domcontentloaded" }),
          WD.goto,
          "reload"
        ).catch(() => {});
        await page.waitForTimeout(WD.settle);
      }
      await page.waitForTimeout(cfg.extra_settle || 900);
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
