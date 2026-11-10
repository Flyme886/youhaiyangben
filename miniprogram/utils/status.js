/**
 * 后端状态 / 步骤 / 错误 的展示层映射（唯一口径）
 * 取值来源：app/db.py 的 status 字段、app/task_runner.py 的 current_step 与 step_failures
 */

/* ---------------- 任务状态 ---------------- */

const STATUS_MAP = {
  queued: {
    label: '排队中',
    tone: 'info',
    hint: '已进入队列，轮到它时会自动开始'
  },
  running: {
    label: '处理中',
    tone: 'active',
    hint: '服务器正在逐条打开链接并截图'
  },
  completed: {
    label: '已完成',
    tone: 'ok',
    hint: '全部条目都附上了截图'
  },
  partial_completed: {
    label: '已完成',
    tone: 'ok',
    hint: 'Word 已交付，部分条目没取到截图，图片位置留空'
  },
  failed: {
    label: '失败',
    tone: 'bad',
    hint: '没有产出 Word，可以重跑一次'
  },
  cancelled: {
    label: '已取消',
    tone: 'muted',
    hint: '任务已手动停止，已生成的部分保留'
  },
  paused_captcha: {
    // 兜底态：只有「命中验证码且一个 Word 都没产出」才会出现。
    label: '待重跑',
    tone: 'warn',
    hint: '平台验证拦住了全部链接，刷新登录态后可重跑'
  }
};

const FALLBACK_STATUS = { label: '未知', tone: 'muted', hint: '' };

function describeStatus(status) {
  return STATUS_MAP[status] || Object.assign({}, FALLBACK_STATUS, { label: status || '未知' });
}

/** 是否处于「还会自己变」的状态 —— 决定要不要开轮询 */
function isLive(status) {
  return status === 'queued' || status === 'running';
}

/** 是否可以「重跑 / 补齐图片」（对齐后端 _RESUMABLE） */
function canResume(status) {
  return status === 'paused_captcha' || status === 'partial_completed' || status === 'failed';
}

/** 是否可以取消（对齐 task_runner.cancel_task） */
function canCancel(status) {
  return status === 'queued' || status === 'running';
}

/**
 * 交付印章：任务结束后在详情页盖下的那枚章。
 * 只有「结束了」的任务才有章；进行中返回 null。
 */
const SEAL_MAP = {
  completed: { text: '已交付', tone: 'seal' },
  partial_completed: { text: '缺图交付', tone: 'seal' },
  failed: { text: '未交付', tone: 'void' },
  paused_captcha: { text: '待重跑', tone: 'void' },
  cancelled: { text: '已撤回', tone: 'void' }
};

function sealOf(status) {
  return SEAL_MAP[status] || null;
}

/* ---------------- 步骤流 ---------------- */

const STEPS = [
  { key: 'precheck', label: '核对表格', hint: '校验表头，按链接去重' },
  { key: 'capture', label: '打开链接并截图', hint: '逐条访问，条数多时最耗时' },
  { key: 'quality', label: '检查截图', hint: '剔除空白页、验证页' },
  { key: 'word', label: '生成 Word', hint: '每 50 条装订成一册' }
];

const STEP_INDEX = {
  等待执行: 0,
  服务重启后重新排队: 0,
  重新排队: 0,
  小程序端重新排队: 0,
  'Bot 触发重新排队': 0,
  '预检 Excel': 0,
  截图采集: 1,
  截图质检: 2,
  '生成 Word': 3,
  完成: 4,
  失败: 4,
  已取消: 4,
  等待人工处理: 4
};

/** 返回当前应高亮的步骤下标；4 表示全部走完 */
function currentStepIndex(currentStep) {
  const idx = STEP_INDEX[currentStep];
  return typeof idx === 'number' ? idx : 0;
}

/**
 * 后端 current_step 是直接落库的字符串，统一换成用户能看懂的话。
 * 历史任务里的「等待人工处理」只代表「未产出文档、需要重跑」。
 */
const STEP_TEXT = {
  等待人工处理: '待重跑',
  等待执行: '排队等待',
  '预检 Excel': '核对表格',
  截图采集: '打开链接并截图',
  截图质检: '检查截图',
  小程序端重新排队: '重新排队',
  服务重启后重新排队: '重新排队'
};

function describeStep(step) {
  if (!step) return '排队等待';
  return STEP_TEXT[step] || step;
}

/** 给步骤流渲染用的状态数组：done / doing / todo */
function buildSteps(task) {
  const status = task.status;
  const idx = currentStepIndex(task.current_step);
  const finished = !isLive(status);

  return STEPS.map((step, i) => {
    let state = 'todo';
    if (finished) {
      state = status === 'cancelled' && i >= idx ? 'todo' : 'done';
    } else if (i < idx) {
      state = 'done';
    } else if (i === idx) {
      state = status === 'queued' ? 'todo' : 'doing';
    }
    return Object.assign({}, step, { state, no: i + 1 });
  });
}

/** 进度百分比：优先按步骤，无步骤信息时按状态兜底 */
function progressOf(task) {
  const status = task.status;
  if (!isLive(status)) return 100;
  if (status === 'queued') return 4;
  const idx = currentStepIndex(task.current_step);
  const perStep = [10, 62, 80, 94];
  return perStep[Math.min(idx, perStep.length - 1)] || 6;
}

/* ---------------- 截图状态 ---------------- */

/**
 * 截图结果分三组：
 *   got     拿到了图（含「已删除」提示页 —— 那本身就是有效证据）
 *   blocked 平台拦了（登录墙、验证、空白）
 *   broken  链路问题（超时、打不开）
 */
const CAPTURE_META = {
  ok: { label: '截图成功', group: 'got' },
  deleted: { label: '已删除（保留提示页）', group: 'got' },
  login_required: { label: '需要登录', group: 'blocked' },
  login_wall: { label: '需要登录', group: 'blocked' },
  captcha: { label: '平台验证', group: 'blocked' },
  captcha_detected: { label: '平台验证', group: 'blocked' },
  blocked_or_blank: { label: '被拦截或空白', group: 'blocked' },
  need_token: { label: '小红书缺少访问令牌', group: 'blocked' },
  platform_unreachable: { label: '网页端无法查看', group: 'blocked' },
  timeout: { label: '加载超时', group: 'broken' },
  navigation_error: { label: '页面打不开', group: 'broken' },
  playwright_unavailable: { label: '截图组件未就绪', group: 'broken' },
  unknown: { label: '未识别', group: 'broken' }
};

/** 兼容旧调用方 */
const CAPTURE_LABELS = Object.keys(CAPTURE_META).reduce((acc, k) => {
  acc[k] = CAPTURE_META[k].label;
  return acc;
}, {});

const QUALITY_LABELS = {
  usable: '可直接用',
  needs_review: '建议复核',
  unusable: '不可用',
  not_captured: '未截到'
};

/** 把 {"ok":12,"timeout":3} 变成 [{key,label,count}]，便于 wxml 直接渲染 */
function toCountList(counts, labelMap) {
  if (!counts) return [];
  return Object.keys(counts)
    .filter((k) => Number(counts[k]) > 0)
    .map((k) => ({
      key: k,
      label: (labelMap && labelMap[k]) || k,
      count: Number(counts[k])
    }))
    .sort((a, b) => b.count - a.count);
}

/**
 * 取证覆盖率：多少条拿到了截图。
 * 返回 null 表示 summary 里还没有截图统计（任务没跑到那一步）。
 *
 * @returns {{total:number, got:number, percent:number,
 *            segments:Array<{key,label,count,group,width}>}|null}
 */
function coverageOf(summary) {
  const counts = summary && summary.capture_status_counts;
  if (!counts) return null;

  const rows = toCountList(counts, CAPTURE_LABELS).map((r) => {
    const meta = CAPTURE_META[r.key] || CAPTURE_META.unknown;
    return Object.assign(r, { group: meta.group });
  });
  const total = rows.reduce((s, r) => s + r.count, 0);
  if (!total) return null;

  // 同组相邻、拿到的排最前，取证条读起来才是「绿 → 琥珀 → 灰」
  const order = { got: 0, blocked: 1, broken: 2 };
  rows.sort((a, b) => order[a.group] - order[b.group] || b.count - a.count);

  const got = rows.filter((r) => r.group === 'got').reduce((s, r) => s + r.count, 0);
  rows.forEach((r) => {
    r.width = Math.max(1.2, (r.count / total) * 100).toFixed(2);
  });

  return {
    total,
    got,
    missing: total - got,
    percent: Math.round((got / total) * 100),
    segments: rows
  };
}

/* ---------------- 错误文案 ---------------- */

const ERROR_RULES = [
  {
    test: /(\d+)\/(\d+)\s*条命中平台验证码，未取到截图/,
    render: (m) =>
      m[2] + ' 条里有 ' + m[1] + ' 条被平台验证拦下，没取到截图；Word 照常交付，图片位置留空。'
  },
  {
    test: /检测到验证码\s*(\d+)\s*条/,
    render: (m) => m[1] + ' 条被平台验证拦下，已按文字版交付。'
  },
  {
    test: /登录态文件已\s*(\d+)\s*天未更新/,
    render: (m) => '平台登录态已 ' + m[1] + ' 天没更新，截图成功率会下降。请联系管理员刷新。'
  },
  {
    test: /任务超时，停止于：(.*)/,
    render: (m) => '任务超时，停在「' + describeStep(m[1]) + '」。点「重跑」会从没完成的条目继续。'
  },
  { test: /截图采集失败/, render: () => '截图环节没能启动，本次按文字版交付。' },
  { test: /截图质检失败/, render: () => '截图检查环节失败，Word 里用的是未经检查的原图。' },
  { test: /生成 Word 失败/, render: () => 'Word 生成失败，通常是模板或字体缺失。请联系管理员。' },
  { test: /未生成 screenshots_manifest\.json/, render: () => '没有产出截图清单，本次按文字版交付。' },
  { test: /未生成质检manifest/, render: () => '缺少截图检查结果，Word 按文字版交付。' },
  { test: /No Word files were generated\./, render: () => '没有生成任何 Word 文件。' },
  { test: /^worker error: (.*)/, render: (m) => '服务器内部异常：' + m[1] }
];

/**
 * 把后端拼接的原始错误串转成人话。
 * 后端会把多个 step_failures 用 "; " 拼起来，这里逐段翻译。
 */
function describeError(raw) {
  if (!raw) return [];
  return String(raw)
    .split(';')
    .map((s) => s.trim())
    .filter(Boolean)
    .map((part) => {
      for (let i = 0; i < ERROR_RULES.length; i += 1) {
        const rule = ERROR_RULES[i];
        const m = part.match(rule.test);
        if (m) return rule.render(m);
      }
      return part.replace(/^⚠️\s*/, '');
    });
}

/* ---------------- 汇总 ---------------- */

/** 从 summary_json 里抽出截图统计（兼容旧调用方） */
function captureSummary(summary) {
  if (!summary) return { capture: [], quality: [] };
  return {
    capture: toCountList(summary.capture_status_counts, CAPTURE_LABELS),
    quality: toCountList(summary.screenshot_status_counts, QUALITY_LABELS)
  };
}

module.exports = {
  STEPS,
  describeStatus,
  isLive,
  canResume,
  canCancel,
  sealOf,
  currentStepIndex,
  describeStep,
  buildSteps,
  progressOf,
  describeError,
  captureSummary,
  coverageOf,
  toCountList,
  CAPTURE_LABELS,
  QUALITY_LABELS
};
