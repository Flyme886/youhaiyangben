const taskApi = require('../../services/task');
const { ensureLogin } = require('../../utils/auth');
const { describeStatus, describeStep, isLive, progressOf, currentStepIndex } = require('../../utils/status');
const { fromNow, durationText, envVersionText } = require('../../utils/util');
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
  const meta = describeStatus(task.status);
  return Object.assign({}, task, {
    statusTone: meta.tone,
    live: isLive(task.status),
    progress: progressOf(task),
    stepText: describeStep(task.current_step),
    stepNo: Math.min(4, currentStepIndex(task.current_step) + 1),
    createdText: fromNow(task.created_at),
    durationText: task.started_at ? durationText(task.started_at, task.finished_at) : '',
    nameText: task.name || '未命名任务'
  });
}

/** 列表页顶部那句话：有进行中就说进度，没有就说结果 */
function headlineOf(stats) {
  if (!stats) return '查看处理进度，取回每一册结果';
  const n = (k) => Number(stats[k] || 0);
  const live = n('queued') + n('running');
  if (live > 0) return live + ' 个任务正在处理，结束后可直接下载 Word';
  const total = n('total');
  if (total === 0) return '上传一份 Excel 开始第一个任务';
  return '共 ' + total + ' 个任务，全部处理完毕';
}

Page({
  data: {
    filters: FILTERS.map((f) => Object.assign({ count: null }, f)),
    activeFilter: 'all',
    keyword: '',
    headline: '',

    list: [],
    liveList: [],
    restList: [],
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
    this.setData({ headline: headlineOf(null) });
    this.boot();
  },

  onShow() {
    // 「我的」页点统计数字跳过来时，带着目标筛选
    const app = getApp();
    const pending = app && app.globalData && app.globalData.pendingFilter;
    if (pending) {
      app.globalData.pendingFilter = '';
      if (FILTERS.some((f) => f.key === pending) && pending !== this.data.activeFilter) {
        this.setData({ activeFilter: pending, keyword: '' });
        if (this.booted) {
          this.resetAndLoad();
          return;
        }
      }
    }
    // 从新建 / 详情页回来时静默刷新，同时恢复轮询
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

  /** 「全部」视图里把进行中的任务拆出来置顶；其它筛选下保持单列 */
  applyList(list) {
    const split = this.data.activeFilter === 'all' || this.data.activeFilter === 'active';
    this.setData({
      list,
      liveList: split ? list.filter((t) => t.live) : [],
      restList: split ? list.filter((t) => !t.live) : list
    });
  },

  /**
   * @param {boolean} reset  true = 从第一页重新拉
   * @param {boolean} silent true = 静默刷新（轮询 / 下拉），不显示骨架
   */
  loadList(reset, silent) {
    // 切筛选 / 搜索（reset 且非静默）必须能打断正在进行的请求；
    // 其余情况（轮询、翻页）遇到在途请求就跳过。旧请求的响应靠序号作废。
    const preempt = reset && !silent;
    if (this.data.loading && !preempt) return Promise.resolve();
    const seq = (this.seq = (this.seq || 0) + 1);
    const stale = () => seq !== this.seq;
    this.setData({ loading: true });

    // 静默刷新：用「已加载条数」当 size 一次覆盖前 N 条，避免轮询造成分页漂移。
    // 加载更多：严格按 pageSize 步进，否则 offset 会算错。
    const size = reset && silent ? Math.max(config.pageSize, this.data.list.length) : config.pageSize;
    const page = reset ? 1 : this.data.page + 1;

    return taskApi
      .listMyTasks({ page, size, status: this.currentStatus(), keyword: this.data.keyword.trim() })
      .then((res) => {
        if (stale()) return;
        const items = (res.items || []).map(enrich);
        const merged = reset ? items : this.data.list.concat(items);
        const nextPage = reset && silent ? Math.max(1, Math.ceil(merged.length / config.pageSize)) : page;

        this.applyList(merged);
        this.setData({
          page: nextPage,
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
        // 静默刷新失败不打断已显示的列表
        if (silent && this.data.list.length) {
          this.setData({ loading: false });
          return;
        }
        // 真机上「域名没白名单」「地址配错」「网络不通」长得一样，
        // 所以把运行版本、请求地址、微信原始错误一并显示，一张截图就能定位。
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

  /* ---------------- 轮询 ---------------- */

  /** 只要列表里有排队中 / 处理中的任务就保持轮询 */
  syncPoll() {
    if (this.data.list.some((t) => t.live)) this.startPoll();
    else this.stopPoll();
  },

  startPoll() {
    if (this.timer) return;
    this.timer = setInterval(() => {
      const before = this.data.liveList.length;
      this.loadList(true, true).then(() => {
        // 有任务刚结束，顺手刷新筛选上的数字
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

  resetAndLoad() {
    this.stopPoll();
    this.setData({ list: [], liveList: [], restList: [], page: 1, hasMore: true, firstLoading: true });
    this.loadList(true);
  },

  onFilterTap(e) {
    const key = e.currentTarget.dataset.key;
    if (key === this.data.activeFilter) return;
    this.setData({ activeFilter: key });
    this.resetAndLoad();
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
    this.setData({ keyword: '' });
    this.resetAndLoad();
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
    wx.switchTab({ url: '/pages/task-new/task-new' });
  },

  onRetry() {
    this.setData({ error: '', errorDetail: '' });
    this.resetAndLoad();
  },

  onShareAppMessage() {
    return { title: '样本任务台', path: '/pages/tasks/tasks' };
  }
});
