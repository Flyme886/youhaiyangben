/**
 * 环境配置
 *
 * 设计目标：**忘记切环境不会导致线上事故**。
 *
 * 之前的做法是手动改 `ENV` 常量，这个类别的 bug 已经真实发生过一次
 * （联调时 ENV 还是 prod 的占位域名 / 打包时忘了切回 prod 指向 127.0.0.1）。
 * 现在改成三级判定：
 *
 *   1. FORCE_ENV / FORCE_BASE_URL   手动覆盖（最高优先级，用于特殊情况）
 *   2. 平台环境自动判定              微信开发者工具 develop / 体验版 trial / 正式版 release
 *   3. dev 兜底                     拿不到平台信息时按联调环境处理
 *
 * 各个环境对应的 `baseUrl` 能力要求：
 *   dev  ：可以是 http、可以是 127.0.0.1，但必须在开发者工具里勾选
 *          「详情 → 本地设置 → 不校验合法域名、web-view、TLS 版本以及 HTTPS 证书」。
 *          真机预览时这个开关**无效**，必须用 FORCE_BASE_URL 指向一个外网可达的 https 地址
 *          （如内网穿透域名），否则请求全部失败。
 *   prod ：必须 https，且域名已在
 *          「微信公众平台 → 开发管理 → 开发设置 → 服务器域名」
 *          中同时配置为 request / uploadFile / downloadFile 三类合法域名。
 *          三个缺一个，上传或下载就会整条挂掉。
 */

/* ────────── 手动覆盖（一般情况下不要动） ────────── */

/** '' = 自动判定；'dev' / 'prod' = 强制指定 */
const FORCE_ENV = '';

/**
 * 直接指定后端地址，绕过 profile。
 * 典型用法：域名还没备案时，把内网穿透得到的临时域名（如 https://xxxx.cpolar.cn）填这里，
 * 就能用真机预览跑完整流程。验证完记得清空。
 */
const FORCE_BASE_URL = '';

/* ────────── 各环境档案 ────────── */

const PROFILES = {
  dev: {
    // 后端只在云服务器上跑（/opt/harmful-sample-web，trendradar-ipv4），
    // 本机没有 8787 服务，所以联调环境同样打线上地址。
    // 若某天在本地起了后端，再改回 http://127.0.0.1:8787 并关掉域名校验。
    // 开发者工具若报「url not in domain list」，去
    // 「详情 → 本地设置」勾选「不校验合法域名、web-view、TLS 版本以及 HTTPS 证书」。
    baseUrl: 'https://zhuiguang.click',
    debug: true
  },
  prod: {
    // 复用既有的 zhuiguang.click：它已经通过 Cloudflare Tunnel 指向这台服务器，
    // nginx 上 /api/wx/ 转发到本机 8787。结尾不要带斜杠。
    //
    // 走法说明：
    //   该域名已完成 ICP 备案（2026-10）。仍需在微信公众平台
    //   「开发管理 → 开发设置 → 服务器域名」把它**同时**填进
    //   request / uploadFile / downloadFile 三类白名单，
    //   三类缺一类，上传或下载就会整条挂掉。结尾不要带斜杠。
    //
    //   白名单还没配好的临时办法（例如刚换了域名）：
    //     · 开发者工具模拟器：勾选「不校验合法域名、web-view、TLS 版本以及 HTTPS 证书」
    //     · 真机 / 体验版：胶囊「···」→ 打开调试
    //   体验版「正常模式」仍会被微信拦下来（报 url not in domain list），
    //   这是白名单限制，不是代码问题。白名单配好后无需再改代码。
    baseUrl: 'https://zhuiguang.click',
    debug: false
  }
};

/* ────────── 环境判定 ────────── */

/**
 * 读取微信运行环境。三者对应关系：
 *   develop  → 开发者工具 / 真机预览 / 真机调试
 *   trial    → 体验版
 *   release  → 正式版
 * 只有 trial / release 视为生产，因为它们对普通用户可见。
 */
function detectEnv() {
  try {
    const info = wx.getAccountInfoSync();
    const version = info && info.miniProgram && info.miniProgram.envVersion;
    if (version === 'release' || version === 'trial') return 'prod';
    if (version === 'develop') return 'dev';
  } catch (err) {
    // 基础库过低或非小程序容器，落到 dev 兜底
    console.warn('[config] 无法判定运行环境，按 dev 处理：', err && err.message);
  }
  return 'dev';
}

const ENV = FORCE_ENV || detectEnv();
const PROFILE = PROFILES[ENV] || PROFILES.dev;
const BASE_URL = FORCE_BASE_URL || PROFILE.baseUrl;

/* ────────── 配置自检 ────────── */

/**
 * 生产环境的硬性约束，违反任意一条小程序都不可能跑通。
 * 返回问题列表，空数组表示配置合规。
 */
function validate() {
  const problems = [];

  if (!BASE_URL || BASE_URL.indexOf('REPLACE-WITH-YOUR-DOMAIN') !== -1) {
    problems.push('后端地址还是占位域名，请在 config/index.js 里填入你的备案域名');
  }
  if (ENV === 'prod') {
    if (BASE_URL.indexOf('https://') !== 0) {
      problems.push('生产环境必须使用 https（微信强制要求，体验版和正式版一样）');
    }
    if (/\/$/.test(BASE_URL)) {
      problems.push('baseUrl 结尾不要带斜杠');
    }
  }
  if (FORCE_BASE_URL && ENV === 'prod') {
    problems.push('正式环境不要使用 FORCE_BASE_URL 临时覆盖，请改 PROFILE 里的正式域名');
  }

  return problems;
}

// 注意顺序：PROFILE 在前，后面的显式字段才能正确覆盖它。
// 反过来写会让 PROFILE.baseUrl 盖掉计算好的 BASE_URL（曾经踩过）。
module.exports = Object.assign(
  {},
  PROFILE,
  {
    env: ENV,
    baseUrl: BASE_URL,
    /** true 表示当前地址是临时覆盖的（内网穿透等），发版前应清空 */
    usingOverride: !!FORCE_BASE_URL,
    /** 后端小程序专用接口前缀，对应 server-patch/app/api_wx.py 的路由 */
    apiPrefix: '/api/wx',
    /** 单次请求超时（毫秒）。任务详情轮询走短超时，上传走长超时 */
    timeout: 20000,
    uploadTimeout: 120000,
    /** 列表分页大小 */
    pageSize: 20,
    /** 运行中任务的轮询间隔（毫秒） */
    pollInterval: 5000,
    /** 配置自检，启动时调用 */
    validate
  }
);
