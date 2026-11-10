const taskApi = require('../../services/task');
const { ensureLogin } = require('../../utils/auth');
const { describeStatus, describeStep, isLive, progressOf, currentStepIndex } = require('../../utils/status');
const { durationText, envVersionText, formatClock, dayLabel } = require('../../utils/util');
const config = require('../../config/index');

/** 筛选分组。status 支持逗号分隔多状态，后端按 in 查询 */
const FILTERS = [
  { key: 'all', label: '全部', status: '' },
  { key: 'active', label: '进行中', status: 'queued,running' },
  { key: 'done', label: '已完成', status: 'completed,partial_completed' },
  { key: 'issue', label: '异常', status: 'failed,paused_captcha,cancelled' }
];

const SEARCH_DEBOUNCE = 400;

/** 把后端裸数据加工成模板能直接渲染的形状（WXML 里不能调 JS 函数） */
function enrich(task) {
  const day = dayLabel(task.created_at);
  return Object.assign({}, task, {
    statusTone: describeStatus(task.status).tone,
    live: isLive(task.status),
    progress: progressOf(task),
    stepText: describeStep(task.current_step),
    stepNo: Math.min(4, currentStepIndex(task.current_step) + 1),
    clock: formatClock(task.created_at),
    dayKey: day.key,
    dayLabel: day.label,
    durationText: task.started_at ? durationText(task.started_at, task.finished_at) : '',
    nameText: task.name || '未命名任务'
  });
}

/** 按创建日期分组（后端已按创建时间倒序） */
function groupByDay(items) {
  const groups = [];
  const index = {};
  items.forEach((t) => {
    if (!(t.dayKey in index)) {
      index[t.dayKey] = groups.length;
      groups.push({ key: t.dayKey, label: t.dayLabel, items: [] });
    }
    groups[index[t.dayKey]].items.push(t);
  });
  return groups;
}

/** 开场那句话：有在跑的就说在跑，否则说结果，再否则引导上传 */
function headlineOf(stats) {
  if (!stats) return { title: '任务', sub: '' };
  const n = (k) => Number(stats[k] || 0);
  const live = n('queued') + n('running');
  const total = n('total');
  const done = n('completed') + n('partial_completed');
  const issue = n('failed') + n('paused_captcha');

  if (total === 0) {
    return { title: '交一份表格开始', sub: '服务器会逐条打开链接截图，每 50 条装订成一册 Word。' };
  }
  if (live > 0) {
    return {
      title: live + ' 个任务正在处理',
      sub: '可以离开小程序，任务在服务器上继续。已完成 ' + done + ' 个。'
    };
  }
  if (issue > 0) {
    return { title: issue + ' 个任务需要重跑', sub: '其余 ' + done + ' 个已完成，Word 都可以直接下载。' };
  }
  return { title: '全部处理完毕', sub: done + ' 个任务的 Word 都可以直接下载。' };
}

Page({
  data: {
    nav: { top: 24, height: 32, reserve: 100, bottom: 64 },
    filters: FILTERS.map((f) => Object.assign({ count: null }, f)),
    activeFilter: 'all',
    keyword: '',
    searchOpen: false,
    headline: { title: '任务', sub: '' },

    list: [],
    liveList: [],
    groups: [],
    total: 0,

    page: 1,
    hasMore: true,
    loading: false,
    firstLoading: true,
    error: '',
    errorDetail: ''
  },

  onLoad() {
    this.timer = null;
    this.searchTimer = null;
    const app = getApp();
    if (app && app.navMetrics) this.setData({ nav: app.navMetrics() });
    this.boot();
  },

  onShow() {
    // 设置页点统计数字跳回来时，带着目标筛选
    const app = getApp();
    const pending = app && app.globalData && app.globalData.pendingFilter;
    if (pending) {
      app.globalData.pendingFilter = '';
      if (FILTERS.some((f) => f.key === pending) && pending !== this.data.activeFilter) {
        this.setData({ activeFilter: pending, keyword: '', searchOpen: false });
        if (this.booted) {
          this.resetAndLoad();
          this.loadStats();
          return;
        }
      }
    }
    // 从新建 / 详情页回来：静默刷新，恢复轮询
    if (this.booted) this.reload();
  },

  onHide() {
    this.stopPoll();
  },

  onUnload() {
    this.stopPoll();
    clearTimeout(this.searchTimer);
  },

  boot() {
    ensureLogin().then((ok) => {
      if (!ok) return;
      this.booted = true;
      this.loadStats();
      this.loadList(true);
    });
  },

  /* ---------------- 数据 ---------------- */

  currentStatus() {
    const f = FILTERS.filter((x) => x.key === this.data.activeFilter)[0];
    return f ? f.status : '';
  },

  loadStats() {
    return taskApi
      .fetchStats()
      .then((s) => {
        const n = (k) => Number((s && s[k]) || 0);
        const counts = {
          all: n('total'),
          active: n('queued') + n('running'),
          done: n('completed') + n('partial_completed'),
          issue: n('failed') + n('paused_captcha') + n('cancelled')
        };
        this.setData({
          filters: this.data.filters.map((f) => Object.assign({}, f, { count: counts[f.key] })),
          headline: headlineOf(s)
        });
      })
      .catch(() => {});
  },

  /** 进行中的任务拆出来置顶，其余按日期分组 */
  applyList(list) {
    const live = list.filter((t) => t.live);
    const rest = list.filter((t) => !t.live);
    this.setData({ list, liveList: live, groups: groupByDay(rest) });
  },

  /**
   * @param {boolean} reset  true = 从第一页重新拉
   * @param {boolean} silent true = 静默刷新（轮询 / 下拉），不显示骨架
   */
  loadList(reset, silent) {
    // 切筛选 / 搜索（reset 且非静默）必须能打断在途请求；
    // 轮询和翻页遇到在途请求就跳过。旧请求的响应靠序号作废。
    const preempt = reset && !silent;
    if (this.data.loading && !preempt) return Promise.resolve();
    const seq = (this.seq = (this.seq || 0) + 1);
    const stale = () => seq !== this.seq;
    this.setData({ loading: true });

    // 静默刷新用「已加载条数」当 size 一次覆盖前 N 条，避免轮询造成分页漂移
    const size = reset && silent ? Math.max(config.pageSize, this.data.list.length) : config.pageSize;
    const page = reset ? 1 : this.data.page + 1;

    return taskApi
      .listMyTasks({ page, size, status: this.currentStatus(), keyword: this.data.keyword.trim() })
      .then((res) => {
        if (stale()) return;
        const items = (res.items || []).map(enrich);
        const merged = reset ? items : this.data.list.concat(items);
        this.applyList(merged);
        this.setData({
          page: reset && silent ? Math.max(1, Math.ceil(merged.length / config.pageSize)) : page,
          total: Number(res.total || merged.length),
          hasMore: reset && silent ? this.data.hasMore : !!res.has_more,
          loading: false,
          firstLoading: false,
          error: '',
          errorDetail: ''
        });
      })
      .catch((err) => {
        if (stale()) return;
        // 静默刷新失败不打断已显示的内容
        if (silent && this.data.list.length) {
          this.setData({ loading: false });
          return;
        }
        // 真机上「域名没进白名单」「地址配错」「网络不通」长得一样，
        // 把运行版本、请求地址、微信原始错误一起列出，一张截图就能定位
        const lines = ['运行版本：' + envVersionText()];
        if (err && err.url) lines.push('请求地址：' + err.url);
        if (err && err.errMsg) lines.push('原始错误：' + err.errMsg);
        this.setData({
          loading: false,
          firstLoading: false,
          error: (err && err.message) || '任务列表没有加载出来',
          errorDetail: lines.join('\n')
        });
      })
      .then(() => {
        if (!stale()) this.syncPoll();
      });
  },

  reload() {
    if (this.data.firstLoading) return Promise.resolve();
    return Promise.all([this.loadList(true, true), this.loadStats()]);
  },

  resetAndLoad() {
    this.stopPoll();
    this.setData({ list: [], liveList: [], groups: [], page: 1, hasMore: true, firstLoading: true });
    this.loadList(true);
  },

  /* ---------------- 轮询 ---------------- */

  /** 只要有排队中 / 处理中的任务就保持轮询 */
  syncPoll() {
    if (this.data.liveList.length > 0) this.startPoll();
    else this.stopPoll();
  },

  startPoll() {
    if (this.timer) return;
    this.timer = setInterval(() => {
      const before = this.data.liveList.length;
      this.loadList(true, true).then(() => {
        // 有任务刚结束：刷新开场那句话和筛选上的数字
        if (this.data.liveList.length !== before) this.loadStats();
      });
    }, config.pollInterval);
  },

  stopPoll() {
    if (this.timer) {
      clearInterval(this.timer);
      this.timer = null;
    }
  },

  /* ---------------- 交互 ---------------- */

  onFilterTap(e) {
    const key = e.currentTarget.dataset.key;
    if (key === this.data.activeFilter) return;
    this.setData({ activeFilter: key });
    this.resetAndLoad();
  },

  onToggleSearch() {
    if (this.data.searchOpen && !this.data.keyword) {
      this.setData({ searchOpen: false });
      return;
    }
    this.setData({ searchOpen: true });
  },

  onKeywordInput(e) {
    this.setData({ keyword: e.detail.value });
    clearTimeout(this.searchTimer);
    this.searchTimer = setTimeout(() => this.resetAndLoad(), SEARCH_DEBOUNCE);
  },

  onKeywordConfirm() {
    clearTimeout(this.searchTimer);
    this.resetAndLoad();
  },

  onKeywordClear() {
    clearTimeout(this.searchTimer);
    const had = !!this.data.keyword;
    this.setData({ keyword: '', searchOpen: false });
    if (had) this.resetAndLoad();
  },

  onPullDownRefresh() {
    Promise.all([this.loadStats(), this.loadList(true, true)]).then(() => wx.stopPullDownRefresh());
  },

  onReachBottom() {
    if (!this.data.hasMore || this.data.loading) return;
    this.loadList(false);
  },

  onTaskTap(e) {
    wx.navigateTo({ url: '/pages/task-detail/task-detail?id=' + e.currentTarget.dataset.id });
  },

  onGoNew() {
    wx.navigateTo({ url: '/pages/task-new/task-new' });
  },

  onGoMine() {
    wx.navigateTo({ url: '/pages/mine/mine' });
  },

  onRetry() {
    this.setData({ error: '', errorDetail: '' });
    this.resetAndLoad();
    this.loadStats();
  },

  onShareAppMessage() {
    return { title: '样本任务台', path: '/pages/tasks/tasks' };
  }
});
