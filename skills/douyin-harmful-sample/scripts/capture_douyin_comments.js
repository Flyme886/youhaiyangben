/*
 * capture_douyin_comments.js  —  Ego Lite 抖音「评论区」截图抓取（stdin 执行）
 *
 * 与 capture_douyin.js 的区别：本脚本按【单条样本】产出【多张】截图——
 *   有恶意评论 -> 从第一条恶意评论起逐张对准恶意评论截，最多 max_shots 张（业务口径：2–3 张）
 *   无恶意评论 -> 退回普通评论（前 plain_per_sample 条，最多 plain_max_shots 张）
 *   无评论     -> 退回截视频页一张（用户口径：视频无评论就只截视频）
 *   清单标记 capture_comments=false 的条目 -> 跳过评论区，只截视频页一张
 *   （按批次只抓前 N 条评论截图时，由 prepare_manifest.py 给清单打这个标记）
 *
 * 运行方式：
 *   ego-browser nodejs < capture_douyin_comments.js
 *   （由 run_capture_comments.sh 拼 prelude 注入 globalThis.__CAPTURE_ENV）
 *
 * ⚠️ ego 的 node 运行时在 app 侧执行脚本，调用方的环境变量**不透传**（cwd 恒为 "/"）。
 *    因此必须由 run_capture_comments.sh 在脚本前拼：
 *      globalThis.__CAPTURE_ENV = {CONFIG:'…', LIMIT:'3', TAG:'c0', FRESH:'true', SPACE:'4'};
 *
 * ───────────────────────── 评论区实测结论（2026-09-22，勿删） ─────────────────────────
 * 抖音 PC 视频详情页有两种布局，评论面板可能**默认折叠**：
 *   A) 侧栏布局：视频在左，评论区是右侧固定侧栏（实测 x≈845, w=301, 高≈502）。
 *      评论面板默认不展开，页面上有个文字节点 `评论(N)`（点击其可点击祖先才展开）。
 *   B) 通栏布局：视频在上、评论在下方，滚动容器是 `.route-scroll-container`，
 *      评论列表 `[data-e2e=comment-list]` 宽≈796。
 * 两种布局下**评论条目节点都是 `[data-e2e="comment-item"]`**，但 DOM 里同时存在
 * 一份「不可见的预渲染副本」（getBoundingClientRect() 全 0）——必须按
 * `rect.width > 50` 过滤，否则会解析到隐藏副本（其 rect 全 0，裁不出图）。
 * 单条评论内部结构：
 *   正文   → `.FduGc_lz`（退化时用整条 innerText 剔除作者/时间/统计）
 *   作者   → `.vlTNLkdG`
 *   时间地域 → `.VAQA49VP`
 *   点赞/分享/回复 → `.Ctt1baq8`
 * 评论滚动容器 = 从可见条目向上找第一个 overflowY ∈ {auto,scroll} 且
 *   scrollHeight > clientHeight 的祖先（不一定是同一个元素，别写死）。
 * 视频已被删除 → 正文含「你要观看的视频不存在」，无 comment-list。
 *
 * ───────────────────────── 配置 ─────────────────────────
 * 复用 capture_douyin.js 的配置读取（prelude > process.env > 同目录 capture_config.json），
 * 额外支持 comments 段：
 * {
 *   "comments": {
 *     "enabled": true,
 *     "max_per_sample": 20,   // 扫描窗口：前 N 条评论内找恶意评论
 *     "max_scan": 60,         // 前 N 条里没恶意评论时，最多往后扫到第几条
 *     "min_malice_score": 1,  // 判定「带恶意」的阈值
 *     "keywords_file": "",    // 恶意词表 JSON；默认取与本脚本同目录的 comment_malice_keywords.json
 *     "dwell": 1200,          // 每次滚动后等待
 *     "load_rounds": 8,       // 最多滚动加载多少轮
 *     "open_wait": 2600,      // 点击「评论(N)」后等待面板展开
 *     "max_shots": 3,         // ★ 单条样本最多产出多少张评论截图（业务口径：2–3 张）
 *     "plain_max_shots": 2,   // 一条恶意评论都没有时，普通评论最多截几张
 *     "plain_per_sample": 6,  // 一条恶意评论都没有时，普通评论取前几条
 *     "malice_context": 2,    // 恶意评论前后各留几条上下文（截图要有语境）
 *     "malice_min_cover": 6,  // 有恶意评论时，窗口至少覆盖几条评论
 *     "pad": 4                // 裁剪时四周留白(px)
 *   }
 * }
 *
 * ───────────────────────── 输出（results_<tag>.jsonl 每行一条） ─────────────────────────
 * 在 capture_douyin.js 字段基础上新增：
 *   capture_kind   : "comments" | "no_comments" | "video_gone"
 *   comment_total  : 页面显示的评论总数（评论(N) 里的 N，取不到为 null）
 *   capture_comments : 清单是否要求本条抓评论（false 时只截视频页）
 *   comments_scanned / comments_malicious / window_end / shot_from / shot_cap
 *   comments       : [{i, author, time, text, score, malicious}]（最多 60 条，供质检与判定）
 *   screenshots    : [{file, kind:"comment"|"video", clip:[x,y,w,h], covers:[a,b],
 *                      malicious_in_shot:N}]  ← 多图；file 是相对文件名
 *   screenshot_path: 第一张（兼容旧字段与旧版引擎）
 */

const fs = await import("node:fs/promises");
const path = await import("node:path");

const SCRIPT_DIR = path.dirname(new URL(import.meta.url).pathname);

const EGO_ENV = globalThis.__CAPTURE_ENV || {};
function envOf(key, fallback) {
  const v = EGO_ENV[key];
  if (v !== undefined && v !== null && v !== "") return String(v);
  const p = process.env["CAPTURE_" + key];
  if (p !== undefined && p !== null && p !== "") return String(p);
  return fallback;
}

const DEFAULTS = {
  manifest: "",
  shots_dir: "",
  results_dir: "",
  space_id: 4,
  limit: 0,
  tag: "run",
  fresh: true,
  recycle: 99,
  extra_settle: 900,
  max_attempts: 2,
  comments: {
    enabled: true,
    max_per_sample: 20,
    max_scan: 60,
    min_malice_score: 1,
    keywords_file: "",
    dwell: 1200,
    load_rounds: 8,
    open_wait: 2600,
    max_shots: 3,
    plain_max_shots: 2,
    plain_per_sample: 6,
    malice_context: 2,
    malice_min_cover: 6,
    pad: 4,
  },
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
  let cfg = {
    ...DEFAULTS,
    comments: { ...DEFAULTS.comments },
    watchdog: { ...DEFAULTS.watchdog },
  };
  const scriptDirOk = SCRIPT_DIR && SCRIPT_DIR !== "/" && SCRIPT_DIR !== ".";
  const configPath =
    envOf("CONFIG", null) ||
    (scriptDirOk ? path.join(SCRIPT_DIR, "capture_config.json") : null);
  cfg.__configPath = configPath || "";
  if (configPath) {
    try {
      const raw = JSON.parse(await fs.readFile(configPath, "utf8"));
      cfg = {
        ...cfg,
        ...raw,
        comments: { ...cfg.comments, ...(raw.comments || {}) },
        watchdog: { ...cfg.watchdog, ...(raw.watchdog || {}) },
      };
    } catch (e) {
      console.log("config_load_warn", String(e.message || e));
    }
  }
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

// ── 恶意评论词表 ─────────────────────────────────────────────────────────
// ⚠️ stdin 执行时 SCRIPT_DIR 恒为 "/"，不能只靠它推词表路径——否则词表静默为空、
//    所有评论 score 全 0（踩过）。解析顺序：config 显式指定 > 配置文件同目录 >
//    脚本同目录 > 内置兜底最小词表。
const FALLBACK_MALICE = {
  strong: ["精神病", "疯子", "骗子", "造假", "学术造假", "窃取", "剽窃", "人渣", "无耻", "缺德", "恶毒", "迫害", "打压", "只手遮天", "天理难容", "活该"],
  medium: ["黑暗", "黑幕", "腐败", "学阀", "利益集团", "掩盖", "洗白", "甩锅", "水军", "带节奏", "炒作", "煽动", "冤", "沉冤", "平反", "问责", "追责", "体制", "官僚", "不公", "愤怒", "气愤", "心寒", "意难平", "痛心", "可悲", "荒谬", "讽刺", "魔幻", "抢功", "摘桃子", "埋没", "毁掉"],
  weak: ["怒", "恨", "可恶", "可恨", "可怜", "可惜", "唉", "泪", "凭什么", "不应该", "太过分", "离谱", "真相", "查清", "给个说法", "交代", "心疼", "惋惜", "遗憾", "唏嘘"],
};

async function loadMalice() {
  const candidates = [];
  if (cfg.comments.keywords_file) candidates.push(cfg.comments.keywords_file);
  if (cfg.__configPath) {
    candidates.push(path.join(path.dirname(cfg.__configPath), "comment_malice_keywords.json"));
  }
  if (SCRIPT_DIR && SCRIPT_DIR !== "/" && SCRIPT_DIR !== ".") {
    candidates.push(path.join(SCRIPT_DIR, "comment_malice_keywords.json"));
  }
  for (const f of candidates) {
    try {
      const raw = JSON.parse(await fs.readFile(f, "utf8"));
      const out = { min_score: 1, strong: [], medium: [], weak: [] };
      for (const k of ["strong", "medium", "weak"]) {
        if (Array.isArray(raw[k])) out[k] = raw[k].filter((x) => typeof x === "string" && x);
      }
      if (typeof raw.min_score === "number") out.min_score = raw.min_score;
      if (out.strong.length || out.medium.length || out.weak.length) {
        console.log("malice_keywords_loaded", f, "strong=" + out.strong.length,
          "medium=" + out.medium.length, "weak=" + out.weak.length, "min_score=" + out.min_score);
        return out;
      }
      console.log("malice_keywords_empty", f);
    } catch (e) {
      console.log("malice_keywords_warn", f, String(e.message || e).slice(0, 60));
    }
  }
  console.log("malice_keywords_FALLBACK (未找到词表文件，使用内置最小词表)");
  return { min_score: 1, ...FALLBACK_MALICE };
}

const cfg = await loadConfig();
const WD = cfg.watchdog;
const C = cfg.comments;
const SHOTS = cfg.shots_dir;
const RESULTS_DIR = cfg.results_dir;
const MANIFEST = cfg.manifest;
const RESULTS_FILE = path.join(RESULTS_DIR, "results_" + cfg.tag + ".jsonl");

await fs.mkdir(SHOTS, { recursive: true });
await fs.mkdir(RESULTS_DIR, { recursive: true });

const MALICE = await loadMalice();
const minScore = C.min_malice_score != null ? C.min_malice_score : MALICE.min_score;

function scoreComment(text) {
  const t = String(text || "");
  if (!t) return 0;
  let s = 0;
  for (const w of MALICE.strong) if (t.includes(w)) s += 3;
  for (const w of MALICE.medium) if (t.includes(w)) s += 2;
  for (const w of MALICE.weak) if (t.includes(w)) s += 1;
  return s;
}

// ── 断点续跑 ─────────────────────────────────────────────────────────────
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

  if (cfg.fresh) {
    const olds = await task.pages();
    for (const p of olds) await withTimeout(p.close(), 6000, "close").catch(() => {});
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

  async function resetPage() {
    await withTimeout(
      page.goto("about:blank", { timeout: 8000, waitUntil: "commit" }),
      WD.reset,
      "reset"
    ).catch(() => {});
  }

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
        if (cs.position === "fixed" && r.width >= innerWidth * 0.8 && r.height >= innerHeight * 0.8) {
          target = t;
          break;
        }
        t = t.parentElement;
      }
      target.style.setProperty("display", "none", "important");
      return 1;
    });

  // ── 展开评论面板 ────────────────────────────────────────────────────────
  // 三种手段按可靠性排序：① 官方评论图标 [data-e2e=feed-comment-icon]（真实鼠标事件）
  // ② 「评论(N)」文字节点的可点击祖先（evaluate 内 click）③ 向下滚一屏让入口现身。
  const openComments = async () => {
    const visible = await page
      .evaluate(
        () =>
          [...document.querySelectorAll('[data-e2e="comment-item"]')].filter(
            (n) => n.getBoundingClientRect().width > 50
          ).length
      )
      .catch(() => 0);
    if (visible > 0) return { act: "already", total: null };
    // ① 官方评论图标
    try {
      await page.click('[data-e2e="feed-comment-icon"]', { timeout: 3000 });
      return { act: "clicked-icon", total: null };
    } catch {}
    // ② 退化：点「评论(N)」文字节点的可点击祖先
    const r = await page
      .evaluate(() => {
        let total = null;
        for (const n of document.querySelectorAll("div,span")) {
          const t = (n.innerText || "").trim();
          if (!/^评论\s*[（(]?\d*[)）]?$/.test(t)) continue;
          if (n.getBoundingClientRect().width < 10) continue;
          const m = t.match(/\d+/);
          if (m) total = parseInt(m[0], 10);
          let p = n,
            target = n;
          for (let i = 0; i < 4 && p; i++) {
            if (p.getAttribute("role") === "button" || p.tagName === "BUTTON") {
              target = p;
              break;
            }
            p = p.parentElement;
          }
          target.click();
          return { act: "clicked", total };
        }
        return { act: "not_found", total };
      })
      .catch(() => ({ act: "err", total: null }));
    if (r.act === "clicked") return r;
    // ③ 都失败：向下滚一屏（部分布局入口在首屏下方），主流程会再试一轮
    await page.evaluate(() => window.scrollBy(0, 700)).catch(() => {});
    return { act: "scrolled", total: r.total };
  };

  // ── 读取当前可见评论条目 + 滚动容器几何 ────────────────────────────────
  const readComments = () =>
    page.evaluate(() => {
      const items = [...document.querySelectorAll('[data-e2e="comment-item"]')].filter(
        (n) => n.getBoundingClientRect().width > 50
      );
      if (!items.length) return { count: 0, items: [], root: null };
      let root = null,
        n = items[0];
      while (n && n !== document.documentElement) {
        const cs = getComputedStyle(n);
        if (/(auto|scroll)/.test(cs.overflowY) && n.scrollHeight > n.clientHeight + 10) {
          root = n;
          break;
        }
        n = n.parentElement;
      }
      const rb = root ? root.getBoundingClientRect() : null;
      const out = items.map((el, i) => {
        const b = el.getBoundingClientRect();
        const bodyEl = el.querySelector(".FduGc_lz");
        const authorEl = el.querySelector(".vlTNLkdG");
        const timeEl = el.querySelector(".VAQA49VP");
        let text = "";
        if (bodyEl) {
          text = (bodyEl.innerText || "").replace(/\s+/g, " ").trim();
        } else {
          // 退化：整条文本剔除作者/时间/统计噪声
          let t = (el.innerText || "").replace(/\s+/g, " ").trim();
          const au = authorEl ? (authorEl.innerText || "").trim() : "";
          if (au && t.startsWith(au)) t = t.slice(au.length).trim();
          t = t.replace(/^\s*作者\s*/, "").replace(/\s*分享\s*回复\s*$/, "");
          t = t.replace(/\d+小时前|\d+分钟前|\d+天前|刚刚|[·•]?\s*[\u4e00-\u9fa5]{2,6}$/g, "");
          text = t.trim();
        }
        return {
          i,
          x: Math.round(b.x),
          y: Math.round(b.y),
          w: Math.round(b.width),
          h: Math.round(b.height),
          author: authorEl ? (authorEl.innerText || "").replace(/\s+/g, " ").trim().slice(0, 40) : "",
          time: timeEl ? (timeEl.innerText || "").replace(/\s+/g, " ").trim().slice(0, 40) : "",
          text: text.slice(0, 800),
        };
      });
      // 找不到可滚动祖先（例如只有 1–2 条评论、容器不满屏）时，
      // 退回「以视口为容器」——否则 screen 循环会因为 root 为空直接 break，
      // 单条样本一张图都产不出来（踩过：评论数=1 的条目全部 comment_screenshot_failed）。
      const useViewport = !root;
      const doc = document.documentElement;
      return {
        count: items.length,
        items: out,
        root: useViewport
          ? {
              scrollTop: Math.round(window.scrollY),
              scrollHeight: Math.max(doc.scrollHeight, window.innerHeight),
              clientHeight: window.innerHeight,
              top: 0,
              bottom: window.innerHeight,
              x: 0,
              w: window.innerWidth,
              viewport: true,
            }
          : {
              scrollTop: Math.round(root.scrollTop),
              scrollHeight: root.scrollHeight,
              clientHeight: root.clientHeight,
              top: Math.round(rb.top),
              bottom: Math.round(rb.bottom),
              x: Math.round(rb.x),
              w: Math.round(rb.width),
              viewport: false,
            },
      };
    });

  // 让第 idx 条评论顶端对齐滚动容器顶端（正向滚动）
  const scrollItemToTop = (idx) =>
    page.evaluate((targetIdx) => {
      const items = [...document.querySelectorAll('[data-e2e="comment-item"]')].filter(
        (n) => n.getBoundingClientRect().width > 50
      );
      if (!items.length || !items[targetIdx]) return null;
      let root = null,
        n = items[0];
      while (n && n !== document.documentElement) {
        const cs = getComputedStyle(n);
        if (/(auto|scroll)/.test(cs.overflowY) && n.scrollHeight > n.clientHeight + 10) {
          root = n;
          break;
        }
        n = n.parentElement;
      }
      if (!root) {
        const b = items[targetIdx].getBoundingClientRect();
        const before = window.scrollY;
        window.scrollBy(0, b.top - 60);
        return { mode: "window", delta: Math.round(window.scrollY - before) };
      }
      const rTop = root.getBoundingClientRect().top;
      const b = items[targetIdx].getBoundingClientRect();
      const delta = b.top - rTop;
      root.scrollTop = Math.max(0, root.scrollTop + delta);
      return { mode: "root", delta: Math.round(delta) };
    }, idx);

  const scrollDownBy = (delta) =>
    page.evaluate((d) => {
      const items = [...document.querySelectorAll('[data-e2e="comment-item"]')].filter(
        (n) => n.getBoundingClientRect().width > 50
      );
      let root = null;
      if (items.length) {
        let n = items[0];
        while (n && n !== document.documentElement) {
          const cs = getComputedStyle(n);
          if (/(auto|scroll)/.test(cs.overflowY) && n.scrollHeight > n.clientHeight + 10) {
            root = n;
            break;
          }
          n = n.parentElement;
        }
      }
      if (root) {
        root.scrollTop = root.scrollTop + d;
        return { mode: "root", st: Math.round(root.scrollTop), sh: root.scrollHeight };
      }
      window.scrollBy(0, d);
      return { mode: "window", sy: Math.round(window.scrollY) };
    }, delta);

  const commentTotalOnPage = () =>
    page.evaluate(() => {
      for (const n of document.querySelectorAll("div,span")) {
        const t = (n.innerText || "").trim();
        if (!/^评论\s*[（(]?\d*[)）]?$/.test(t)) continue;
        if (n.getBoundingClientRect().width < 10) continue;
        const m = t.match(/\d+/);
        if (m) return parseInt(m[0], 10);
      }
      // 兜底：正文里找「评论（N）」「N 条评论」；注意别把“评论”当标题误取
      const txt = (document.body ? document.body.innerText : "").replace(/\s+/g, " ");
      const m =
        txt.match(/评论\s*[（(]\s*(\d{1,6})\s*[)）]/) || txt.match(/(\d{1,6})\s*条评论/);
      return m ? parseInt(m[1], 10) : null;
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

    const stem = String(it.item_id).padStart(5, "0");
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
      engine: "ego-browser-comments",
      status: "ok",
      reason: "",
      captcha_signals: [],
      needs_manual_action: false,
      capture_kind: "",
      comment_total: null,
      comments_scanned: 0,
      comments_malicious: 0,
      window_end: 0,
      comments: [],
      screenshots: [],
      screenshot_path: "",
      page_title: "",
      page_text: "",
    };
    const t0 = Date.now();
    let stuck = false;
    try {
      const navUrl = String(it.link).replace("/share/video/", "/video/");
      await withTimeout(
        page.goto(navUrl, { timeout: WD.nav, waitUntil: "domcontentloaded" }),
        WD.goto,
        "goto"
      );
      await page.waitForTimeout(WD.settle);
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
      // 等正文渲染（抖音 SPA 常停在整页「加载中」骨架态）
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
            .slice(0, 6000),
        })),
        WD.extract,
        "extract"
      );
      out.page_title = ev.title;
      out.page_text = ev.text;
      const sig = ev.title + " " + ev.text;

      if (/验证码中间页|请完成下列验证|拖动完成上方拼图|人机验证|安全验证/.test(sig)) {
        out.status = "captcha_detected";
        out.reason = "captcha or risk challenge detected";
      } else if (ev.text.trim().length < 20) {
        out.status = "blocked_or_blank";
        out.reason = "page body is blank or too short";
      } else {
        // 视频详情页是否真的渲染出来了？
        // 踩过：SPA 偶发停在首页/推荐流（正文是「精选 推荐 AI抖音 关注…视频数据加载中」），
        // 此时正文长度正常、会被误判为 ok，整条样本的评论与截图全部报废且不会重试。
        const hasDetail = await withTimeout(
          page.evaluate(
            () =>
              !!document.querySelector(
                'video, [data-e2e="detail-video-desc"], [data-e2e="video-desc"], [data-e2e="detail-video-player"], [data-e2e="feed-comment-icon"]'
              )
          ),
          WD.extract,
          "detail-probe"
        ).catch(() => true);
        if (!hasDetail) {
          out.status = "detail_not_loaded";
          out.reason = "视频详情页未渲染（疑似停在首页/骨架态），等待重试";
        }
      }

      const shotVideoPage = async (kind) => {
        const file = stem + ".png";
        await withTimeout(
          page.screenshot({ path: path.join(SHOTS, file) }),
          WD.shot,
          "shot"
        );
        out.capture_kind = kind;
        out.screenshots.push({ file, kind: "video", clip: null });
        out.screenshot_path = file;
      };

      const videoGone = /你要观看的视频不存在|视频不存在/.test(sig);
      if (out.status === "ok" && videoGone) {
        out.reason = "video_unavailable";
        await shotVideoPage("video_gone");
      } else if (out.status === "ok" && it.capture_comments === false) {
        // 清单未要求本条抓评论（例如「每批只抓前 N 条评论截图」的降级模式）——只留视频页
        out.capture_kind = "video_only";
        out.reason = "comments_skipped_by_manifest";
        await shotVideoPage("video_only");
      } else if (out.status === "ok") {
        // ── 评论区 ─────────────────────────────────────────────────────
        // 先等评论入口/条目出现（详情页是异步渲染的，入口常比正文晚一步）
        await withTimeout(
          page.waitForSelector('[data-e2e="comment-item"], [data-e2e="feed-comment-icon"]', {
            timeout: 5000,
          }),
          6000,
          "wait-comment-entry"
        ).catch(() => {});
        // 打开评论面板：最多 3 轮「点击 → 等 → 读」。
        // 踩过：偶发点一次没展开（网络慢/入口未就绪），只重试一次仍会落到 no_comments，
        // 整条样本就只剩视频页图。多轮 + 递增等待能把它救回来。
        let state = { count: 0, items: [], root: null };
        for (let attempt = 0; attempt < 3; attempt++) {
          const r = await withTimeout(openComments(), WD.hide, "openComments" + attempt).catch(
            () => null
          );
          if (r && typeof r.act === "string" && r.act.startsWith("clicked")) {
            await page.waitForTimeout(C.open_wait + attempt * 1200);
          } else {
            // already（已有可见评论条目）/ scrolled（已向下滚一屏让入口现身）/ err
            await page.waitForTimeout(C.dwell);
          }
          state = await withTimeout(readComments(), WD.extract, "readComments").catch(() => ({
            count: 0,
            items: [],
            root: null,
          }));
          if (state.count) break;
        }
        out.comment_total = await withTimeout(commentTotalOnPage(), WD.extract, "total").catch(
          () => null
        );

        if (!state.count) {
          out.capture_kind = "no_comments";
          out.reason = "no_comments";
          await shotVideoPage("no_comments");
        } else {
          out.capture_kind = "comments";
          // 滚动加载，收集评论
          const seen = new Map();
          const collect = (st) => {
            for (const c of st.items) {
              const key = c.author + "\u0000" + c.text.slice(0, 120);
              if (!seen.has(key)) seen.set(key, c);
            }
          };
          collect(state);
          const capScan = Math.min(C.max_scan, Math.max(C.max_per_sample, 20));
          let needMore = seen.size < C.max_per_sample;
          let stall = 0;
          for (let r = 0; r < C.load_rounds && (needMore || seen.size < capScan); r++) {
            const before = seen.size;
            await scrollDownBy(500).catch(() => {});
            await page.waitForTimeout(C.dwell);
            const st = await withTimeout(readComments(), WD.extract, "readComments").catch(() => null);
            if (!st) break;
            collect(st);
            if (!st.root) break;
            const atBottom =
              st.root.scrollTop + st.root.clientHeight >= st.root.scrollHeight - 8;
            if (seen.size >= C.max_per_sample && atBottom) break;
            // 连续 2 轮没有新评论才收手 —— 只判 1 轮会在「面板刚展开、评论还在陆续
            // 渲染」时提前退出（实测同一条视频：一次扫到 23 条、一次只扫到 5 条）。
            if (seen.size === before) {
              stall += 1;
              if (stall >= 2) break;
            } else {
              stall = 0;
            }
            needMore = seen.size < C.max_per_sample;
          }
          const comments = [...seen.values()].map((c, i) => {
            const score = scoreComment(c.text);
            return {
              i,
              author: c.author,
              time: c.time,
              text: c.text,
              score,
              malicious: score >= minScore,
            };
          });
          const scanCap = Math.min(comments.length, capScan);
          out.comments = comments.slice(0, scanCap);
          out.comments_scanned = comments.length;
          out.comments_malicious = comments.filter((c) => c.malicious).length;

          // ── 选截图窗口：有恶意评论就从恶意评论截起 ────────────────────
          // 业务口径（2026-09-22）：评论区只出 2–3 张恶意评论图（max_shots）。
          //   有恶意评论 → 从第一条恶意评论起、覆盖到最后一条恶意评论，最多 max_shots 张
          //   一条恶意都没有 → 退回普通评论：取前 plain_per_sample 条，最多 plain_max_shots 张
          const malIdx = comments.filter((c) => c.malicious).map((c) => c.i);
          let shotFrom, shotUntil, shotCap;
          if (malIdx.length) {
            // 恶意评论前后各留 malice_context 条上下文（截图要有语境，别只孤零零一条），
            // 并保证窗口至少覆盖 malice_min_cover 条 —— 否则「只有 1 条恶意评论」时
            // 窗口只有 1 条、只出 1 张小图，与「出 2–3 张」的口径不符。
            const ctx = C.malice_context != null ? C.malice_context : 2;
            shotFrom = Math.max(0, malIdx[0] - ctx);
            shotUntil = Math.min(comments.length, malIdx[malIdx.length - 1] + 1 + ctx);
            const minCover = C.malice_min_cover != null ? C.malice_min_cover : 6;
            if (shotUntil - shotFrom < minCover) {
              shotUntil = Math.min(comments.length, shotFrom + minCover);
              shotFrom = Math.max(0, shotUntil - minCover);
            }
            shotCap = C.max_shots;
          } else {
            shotFrom = 0;
            shotUntil = Math.min(comments.length, C.plain_per_sample);
            shotCap = C.plain_max_shots;
          }
          if (!shotUntil || shotUntil <= shotFrom) {
            shotFrom = 0;
            shotUntil = comments.length;
            shotCap = C.plain_max_shots;
          }
          out.window_end = shotUntil;
          out.shot_from = shotFrom;
          out.shot_cap = shotCap;
          out.page_text = (
            out.page_text + " ‖评论：" + comments.slice(0, 40).map((c) => c.text).join(" | ")
          ).slice(0, 12000);

          // ── 按屏截图：顺序向下滚，只截「含恶意评论」的那几屏 ────────────
          // ⚠️ 不要「跨屏跳转」到目标评论（2026-09-22 实测踩坑）：跳到第 N 条时该条
          //    往往还没渲染（虚拟列表），或滚动量算不准导致它落在容器视口外，
          //    裁切区变成无效矩形 → 整条样本一张评论图都出不来（3 条测试里 2 条复现）。
          //    正确做法是顺序推进 + 择屏：跟着滚动一屏一屏走，该屏含恶意评论才截图。
          // 有恶意评论：最多 shotCap(max_shots) 张，每张必含恶意评论；
          // 无恶意评论：退化为前 plain_per_sample 条连续截，最多 plain_max_shots 张。
          const wantMalice = malIdx.length > 0;
          let covered = 0;
          let guard = 0;
          const maxScreens = shotCap * 6 + 12; // 最多扫多少屏（恶意评论很靠底时止损）
          while (covered < shotUntil && out.screenshots.length < shotCap && guard < maxScreens) {
            guard += 1;
            let st = await withTimeout(readComments(), WD.extract, "readComments").catch(() => null);
            if (!st || !st.root || !st.items.length) break;
            const cont = st.root;
            // 让 covered 条对齐容器顶端；滚不动（已在底/被夹紧）就按当前可见裁
            const fit = st.items[covered];
            if (fit && fit.y > cont.top + 2) {
              const moved = await scrollItemToTop(covered).catch(() => null);
              if (moved && moved.delta) {
                await page.waitForTimeout(C.dwell);
                continue;
              }
            }
            st = await withTimeout(readComments(), WD.extract, "readComments").catch(() => null);
            if (!st || !st.root || !st.items.length) break;
            const first = st.items[covered] || st.items[st.items.length - 1];
            if (!first) break;
            let last = -1;
            for (let i = covered; i < shotUntil && i < st.items.length; i++) {
              const c = st.items[i];
              if (c.y >= cont.top - 2 && c.y + c.h <= cont.bottom + 2) last = i;
              else break;
            }
            if (last < covered) last = covered;
            const malInShot = comments.slice(covered, last + 1).filter((c) => c.malicious).length;
            // 有恶意评论时只截含恶意评论的屏；没有恶意评论时（wantMalice=false）每屏都截
            if (!wantMalice || malInShot > 0) {
              const lastIt = st.items[Math.min(last, st.items.length - 1)];
              const xs = st.items.slice(covered, last + 1).map((c) => c.x);
              const rs = st.items.slice(covered, last + 1).map((c) => c.x + c.w);
              const pad = C.pad || 0;
              const clipX = Math.max(cont.x, Math.min(...xs) - pad);
              const clipR = Math.min(cont.x + cont.w, Math.max(...rs) + pad);
              const clipY = Math.max(cont.top, first.y - pad);
              const clipB = Math.min(cont.bottom, lastIt.y + lastIt.h + pad);
              const clipW = Math.round(clipR - clipX);
              const clipH = Math.round(clipB - clipY);
              if (clipW >= 40 && clipH >= 40) {
                const file = stem + "_k" + (out.screenshots.length + 1) + ".png";
                await withTimeout(
                  page.screenshot({
                    path: path.join(SHOTS, file),
                    clip: { x: Math.round(clipX), y: Math.round(clipY), width: clipW, height: clipH },
                  }),
                  WD.shot,
                  "shot"
                );
                out.screenshots.push({
                  file,
                  kind: "comment",
                  clip: [Math.round(clipX), Math.round(clipY), clipW, clipH],
                  covers: [covered, last],
                  malicious_in_shot: malInShot,
                });
                if (!out.screenshot_path) out.screenshot_path = file;
              }
            }
            covered = last + 1;
            if (covered >= shotUntil) break;
            await scrollItemToTop(covered).catch(() => {});
            await page.waitForTimeout(C.dwell);
          }
          if (!out.screenshots.length) {
            out.capture_kind = "video_page";
            out.reason = "comment_screenshot_failed";
            await shotVideoPage("video_page");
          }
        }
      }
    } catch (e) {
      const msg = String((e && e.message) || e);
      stuck = msg.startsWith("WATCHDOG:");
      out.status = stuck ? "timeout" : "navigation_error";
      out.reason = (stuck ? "watchdog timeout on " : "") + msg.slice(0, 250);
    }
    await fs.appendFile(RESULTS_FILE, JSON.stringify(out) + "\n", "utf8");
    consecutiveBad = out.status === "ok" ? 0 : consecutiveBad + 1;
    console.log(
      [
        it.item_id,
        out.status,
        out.capture_kind || "-",
        "评论" + (out.comments_scanned || 0) + "/恶" + (out.comments_malicious || 0),
        "图" + (out.screenshots.length || 0),
        ((Date.now() - t0) / 1000).toFixed(1) + "s",
      ].join("\t")
    );
    if (stuck) await resetPage();
  }
  console.log("run complete");
}
