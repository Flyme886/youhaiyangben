const taskApi = require('../../services/task');
const { ensureLogin } = require('../../utils/auth');
const {
  describeStatus,
  describeStep,
  isLive,
  progressOf
} = require('../../utils/status');
const { fromNow, durationText, envVersionText } = require('../../utils/util');
const config = require('../../config/index');

/** 筛选分组。status 支持逗号分隔多状态，后端 /tasks 接口按 in 查询 */
const FILTERS = [
  { key: 'all', label: '全部', status: '' },
  { key: 'active', label: '进行中', status: 'queued,running' },
  { key: 'done', label: '已完成', status: 'completed,partial_completed' },
  // 交付优先：命中验证码的任务已并入 partial_completed（落到「已完成」），
  // 这里只剩真正非正常结束、确实需要你操作的状态，故改称「异常」而非「需处理」。
  { key: 'issue', label: '异常', status: 'failed,paused_captcha,cancelled' }
];

/** 把后端裸数据加工成模板能直接渲染的形状（WXML 里不能调 JS 函数） */
function enrich(task) {
  const meta = describeStatus(task.status);
  const live = isLive(task.status);
  return Object.assign({}, task, {
    statusLabel: meta.label,
    statusTone: meta.tone,
    statusHint: meta.hint,
    live,
    progress: progressOf(task),
    stepText: describeStep(task.current_step),
    createdText: fromNow(task.created_at),
    createdFull: task.created_at || '',
    durationText: task.started_at ? durationText(task.started_at, task.finished_at) : '',
    nameText: task.name || '(未命名任务)'
  });
}

Page({
  data: {
    filters: FILTERS,
    activeFilter: 'all',
    list: [],
    stats: null,
    statCards: [],

    page: 1,
    hasMore: true,
    loading: false,
    refreshing: false,
    firstLoading: true,
    error: '',
    /** 失败的具体原因（请求地址 + 微信原始错误），只有一个 error 时没法定位 */
    errorDetail: ''
  },

  onLoad() {
    this.timer = null;
    this.boot();
  },

  onShow() {
    // 从新建/详情页返回时需要刷新；同时恢复轮询
    if (this.booted) {
      this.reload(true);
    }
  },

  onHide() {
    this.stopPoll();
  },

  onUnload() {
    this.stopPoll();
  },

  /** 首次进入：先确保登录态，再拉数据 */
  boot() {
    ensureLogin().then((ok) => {
      if (!ok) return;
      this.booted = true;
      this.loadStats();
      this.loadList(true).then(() => this.syncPoll());
    });
  },

  /* ---------------- 数据加载 ---------------- */

  currentStatus() {
    const f = FILTERS.filter((x) => x.key === this.data.activeFilter)[0];
    return f ? f.status : '';
  },

  loadStats() {
    taskApi
      .fetchStats()
      .then((res) => {
        const s = res || {};
        const n = (k) => Number(s[k] || 0);
        this.setData({
          stats: s,
          statCards: [
            { key: 'all', label: '全部', value: n('total') },
            { key: 'active', label: '进行中', value: n('queued') + n('running'), tone: 'active' },
            { key: 'done', label: '已完成', value: n('completed') + n('partial_completed'), tone: 'ok' },
            {
              key: 'issue',
              label: '异常',
              value: n('failed') + n('paused_captcha') + n('cancelled'),
              tone: n('failed') + n('paused_captcha') + n('cancelled') > 0 ? 'bad' : 'muted'
            }
          ]
        });
      })
      .catch(() => {});
  },

  /**
   * @param {boolean} reset  true = 从第一页重新拉
   * @param {boolean} silent true = 静默刷新（轮询/下拉），不显示骨架屏
   */
  loadList(reset, silent) {
    if (this.data.loading) return Promise.resolve();
    this.setData({ loading: true, error: '', errorDetail: '' });

    // 静默刷新：用「已加载条数」当 size 一次性覆盖前 N 条，让轮询不产生分页漂移。
    // 加载更多：必须严格按 pageSize 步进，否则 offset 会算错、数据会跳条。
    const size =
      reset && silent ? Math.max(config.pageSize, this.data.list.length) : config.pageSize;
    const page = reset ? 1 : this.data.page;

    return taskApi
      .listMyTasks({ page, size, status: this.currentStatus() })
      .then((res) => {
        const items = (res.items || []).map(enrich);
        const merged = reset ? items : this.data.list.concat(items);

        // 静默刷新覆盖了前 N 条后，游标要回落到「已加载条数」对应的页，
        // 否则下一次「加载更多」会从第 2 页开始，与已展示的数据重叠。
        const nextPage =
          reset && silent ? Math.max(1, Math.ceil(merged.length / config.pageSize)) : page;
        // 静默刷新时 has_more 沿用原状态，不被这次覆盖式请求的返回值带偏
        const hasMore = reset && silent ? this.data.hasMore : !!res.has_more;

        this.setData({
          list: merged,
          page: nextPage,
          hasMore: hasMore,
          loading: false,
          firstLoading: false,
          refreshing: false
        });
      })
      .catch((err) => {
        // 真机上「域名没白名单」「地址配错」「网络不通」长得一模一样，
        // 所以把运行版本、失败地址、微信原始错误一并显示，一张截图就能定位。
        const lines = [(err && err.message) || '加载失败'];
        lines.push('运行版本：' + envVersionText());
        if (err && err.url) lines.push('请求地址：' + err.url);
        if (err && err.errMsg) lines.push('原始错误：' + err.errMsg);
        this.setData({
          loading: false,
          firstLoading: false,
          refreshing: false,
          error: lines[0],
          errorDetail: lines.slice(1).join('\n')
        });
      })
      .then(() => {
        this.syncPoll();
      });
  },

  /** 静默刷新已加载的前 N 条，不打断用户浏览 */
  reload(silent) {
    if (this.data.firstLoading) return;
    return this.loadList(true, true).then(() => this.loadStats());
  },

  /* ---------------- 轮询 ---------------- */

  /** 只要列表里有「排队中/处理中」的任务，就保持轮询 */
  syncPoll() {
    const hasLive = this.data.list.some((t) => t.live);
    if (hasLive) this.startPoll();
    else this.stopPoll();
  },

  startPoll() {
    if (this.timer) return;
    this.timer = setInterval(() => {
      this.loadList(true, true);
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
    this.stopPoll();
    this.setData({ activeFilter: key, list: [], page: 1, hasMore: true, firstLoading: true });
    this.loadList(true);
  },

  onPullDownRefresh() {
    this.setData({ refreshing: true });
    this.loadStats();
    this.loadList(true, true).then(() => {
      wx.stopPullDownRefresh();
      wx.showToast({ title: '已刷新', icon: 'none', duration: 1200 });
    });
  },

  onReachBottom() {
    if (!this.data.hasMore || this.data.loading) return;
    this.setData({ page: this.data.page + 1 });
    this.loadList(false);
  },

  onTaskTap(e) {
    const id = e.currentTarget.dataset.id;
    wx.navigateTo({ url: '/pages/task-detail/task-detail?id=' + id });
  },

  onGoNew() {
    wx.switchTab({ url: '/pages/task-new/task-new' });
  },

  onRetry() {
    this.setData({ firstLoading: true, error: '', errorDetail: '' });
    this.loadList(true);
  },

  /** 列表页也支持一次下拉刷新 */
  onShareAppMessage() {
    return { title: '样本任务台', path: '/pages/tasks/tasks' };
  }
});
