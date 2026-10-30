/**
 * 后端状态 / 步骤 / 错误 的展示层映射
 * 取值来源：app/db.py 的 status 字段、app/task_runner.py 的 current_step 与 step_failures
 */

/* ---------------- 任务状态 ---------------- */

const STATUS_MAP = {
  queued: {
    label: '排队中',
    tone: 'info',
    hint: '已进入队列，等待 worker 取任务'
  },
  running: {
    label: '处理中',
    tone: 'active',
    hint: 'worker 正在执行该任务'
  },
  completed: {
    label: '已完成',
    tone: 'ok',
    hint: '全部产物已生成'
  },
  partial_completed: {
    label: '已完成',
    tone: 'ok',
    hint: '文档已交付；部分条目未取到截图，图片位置留空'
  },
  failed: {
    label: '失败',
    tone: 'bad',
    hint: '没有产出任何 Word，需要排查后重试'
  },
  cancelled: {
    label: '已取消',
    tone: 'muted',
    hint: '任务被手动取消'
  },
  paused_captcha: {
    // 兜底态：只有「命中验证码且一个 Word 都没产出」才会出现。
    // 正常命中验证码的任务会直接按「已完成」交付，不会再卡在这里。
    label: '待重跑',
    tone: 'warn',
    hint: '未产出文档，刷新登录态后可重跑'
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

/** 是否可以「继续任务」（对齐 web 端 /tasks/{id}/resume 的判断） */
function canResume(status) {
  return status === 'paused_captcha' || status === 'partial_completed' || status === 'failed';
}

/** 是否可以取消（对齐 task_runner.cancel_task） */
function canCancel(status) {
  return status === 'queued' || status === 'running';
}

/* ---------------- 步骤流 ---------------- */

const STEPS = [
  { key: '预检 Excel', label: '预检 Excel', hint: '校验表头、按链接去重' },
  { key: '截图采集', label: '截图采集', hint: '逐条打开链接并截图' },
  { key: '截图质检', label: '截图质检', hint: '标记可用 / 待确认 / 不可用' },
  { key: '生成 Word', label: '生成 Word', hint: '每 50 条一份并打包' }
];

const STEP_INDEX = {
  等待执行: 0,
  服务重启后重新排队: 0,
  重新排队: 0,
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
 * 后端 current_step 是直接落库的字符串，历史任务里还留着「等待人工处理」这类旧口径。
 * 交付优先改造后该状态只代表「未产出文档、需要重跑」，这里统一换成人话再上屏，
 * 避免小程序又提示「人工」二字。
 */
const STEP_TEXT = {
  等待人工处理: '待重跑',
  等待执行: '等待执行'
};

function describeStep(step) {
  if (!step) return '等待执行';
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
      state = 'doing';
    }
    return Object.assign({}, step, { state });
  });
}

/** 进度百分比：优先按步骤，无步骤信息时按状态兜底 */
function progressOf(task) {
  const status = task.status;
  if (status === 'completed') return 100;
  if (status === 'partial_completed' || status === 'failed' || status === 'cancelled' || status === 'paused_captcha') {
    return 100;
  }
  const idx = currentStepIndex(task.current_step);
  const perStep = [10, 62, 78, 94];
  if (status === 'queued') return 6;
  return perStep[Math.min(idx, perStep.length - 1)] || 6;
}

/* ---------------- 截图状态 ---------------- */

const CAPTURE_LABELS = {
  ok: '截图成功',
  login_required: '需要登录态',
  blocked_or_blank: '被拦截或空白',
  timeout: '加载超时',
  navigation_error: '页面打不开',
  captcha: '命中验证码',
  unknown: '未识别状态'
};

const QUALITY_LABELS = {
  usable: '可直接用',
  needs_review: '需人工复核',
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

/* ---------------- 错误文案 ---------------- */

const ERROR_RULES = [
  {
    // 新文案（task_runner 交付优先）：验证码只留一句简短说明，不阻塞交付
    test: /(\d+)\/(\d+)\s*条命中平台验证码，未取到截图/,
    render: (m) =>
      '本次 ' + m[2] + ' 条中有 ' + m[1] + ' 条命中平台验证码，未取到截图；' +
      'Word 已按文字版正常交付，图片位置留空。'
  },
  {
    // 兼容历史任务里的旧文案
    test: /检测到验证码\s*(\d+)\s*条/,
    render: (m) => '命中 ' + m[1] + ' 条平台验证码，未取到截图，已按文字版交付。'
  },
  {
    test: /登录态文件已\s*(\d+)\s*天未更新/,
    render: (m) => '登录态已 ' + m[1] + ' 天未更新，截图成功率会下降，建议先刷新登录态。'
  },
  {
    test: /任务超时，停止于：(.*)/,
    render: (m) => '任务超时，卡在「' + m[1] + '」这一步。可在后台调大 TASK_TIMEOUT_SECONDS 后重试。'
  },
  { test: /截图采集失败/, render: () => '截图采集整体没跑起来，本次按文字版交付。' },
  { test: /截图质检失败/, render: () => '截图质检环节失败，Word 会退化为使用原始截图。' },
  { test: /生成 Word 失败/, render: () => 'Word 生成失败，通常是模板或字体缺失导致。' },
  {
    test: /未生成 screenshots_manifest\.json/,
    render: () => '没有产出截图清单，本次按文字版交付。'
  },
  {
    test: /未生成质检manifest/,
    render: () => '缺少质检清单，Word 按文字版交付。'
  },
  { test: /No Word files were generated\./, render: () => '没有生成任何 Word 文件。' },
  { test: /^worker error: (.*)/, render: (m) => 'worker 内部异常：' + m[1] }
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

/** 从 summary_json 里抽出截图统计，供详情页展示 */
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
  currentStepIndex,
  describeStep,
  buildSteps,
  progressOf,
  describeError,
  captureSummary,
  toCountList,
  CAPTURE_LABELS
};
