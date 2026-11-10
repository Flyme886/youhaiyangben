const taskApi = require('../../services/task');
const { ensureLogin } = require('../../utils/auth');
const {
  describeStatus,
  describeStep,
  buildSteps,
  progressOf,
  canResume,
  canCancel,
  isLive,
  coverageOf,
  gridOf,
  currentStepIndex,
  describeError,
  captureSummary
} = require('../../utils/status');
const { formatFull, durationText, formatSize, safeFileName, formatClock, dayLabel } = require('../../utils/util');
const config = require('../../config/index');

const PREVIEWABLE = ['doc', 'docx', 'xls', 'xlsx', 'ppt', 'pptx', 'pdf'];

/** 底栏：每个状态只给最该做的一两件事 */
function dockOf(task, files) {
  const status = task.status;
  const docs = files.filter((f) => f.ext === 'docx');
  const dock = {
    cancel: canCancel(status),
    resume: canResume(status),
    resumeText: status === 'partial_completed' ? '补齐图片' : '重跑任务',
    // 只有一册时把「打开」放进底栏；多册在列表里逐个点更清楚
    open: !isLive(status) && docs.length === 1,
    openName: docs.length === 1 ? docs[0].name : ''
  };
  dock.show = dock.cancel || dock.resume || dock.open;
  return dock;
}

Page({
  data: {
    taskId: '',
    task: null,
    live: false,

    steps: [],
    stepText: '',
    stepNo: 1,
    progress: 0,
    statusHint: '',

    coverage: null,
    grid: null,
    gridAnimate: false,
    quality: [],

    createdText: '',
    createdFull: '',
    finishedFull: '',
    durationText: '',

    errorList: [],
    conclusion: '',
    files: [],
    logTail: '',

    stepsOpen: false,
    techOpen: false,
    dock: { show: false },

    loading: true,
    busy: false,
    loadError: ''
  },

  onLoad(options) {
    this.pollTimer = null;
    this.revealed = false;
    this.setData({ taskId: (options && options.id) || '' });
    ensureLogin().then((ok) => {
      if (ok) this.fetchDetail(true);
    });
  },

  onShow() {
    if (this.data.task && this.data.live) this.startPoll();
  },

  onHide() {
    this.stopPoll();
  },

  onUnload() {
    this.stopPoll();
  },

  onPullDownRefresh() {
    this.fetchDetail(false).then(() => wx.stopPullDownRefresh());
  },

  /* ---------------- 数据 ---------------- */

  fetchDetail(showLoading) {
    if (!this.data.taskId) {
      this.setData({ loading: false, loadError: '链接里缺少任务编号，请从任务列表进入' });
      return Promise.resolve();
    }
    if (showLoading) this.setData({ loading: true, loadError: '' });

    return taskApi
      .getTask(this.data.taskId)
      .then((res) => this.applyTask(res))
      .catch((err) => {
        // 轮询中的偶发失败不清空页面
        if (!showLoading && this.data.task) return;
        this.setData({ loading: false, loadError: (err && err.message) || '任务详情没有加载出来' });
      })
      .then(() => this.syncPoll());
  },

  applyTask(res) {
    const summary = res.summary || {};
    const live = isLive(res.status);
    const coverage = live ? null : coverageOf(summary);

    // 样本格第一次出现时点亮一次（首次打开已结束的任务，或在看时任务刚结束）
    const animate = !!coverage && !this.revealed;
    if (coverage) this.revealed = true;

    const files = (res.files || []).map((f) => {
      const ext = String(f.name || '').split('.').pop().toLowerCase();
      return Object.assign({}, f, {
        ext,
        extLabel: ext.toUpperCase(),
        kind: ext === 'docx' || ext === 'doc' ? 'word' : 'pack',
        sizeText: formatSize(f.size),
        previewable: PREVIEWABLE.indexOf(ext) !== -1
      });
    });

    const day = dayLabel(res.created_at);

    this.setData({
      task: res,
      live,
      steps: buildSteps(res),
      stepText: describeStep(res.current_step),
      stepNo: Math.min(4, currentStepIndex(res.current_step) + 1),
      progress: progressOf(res),
      statusHint: describeStatus(res.status).hint,

      coverage,
      grid: gridOf(coverage),
      gridAnimate: animate,
      quality: captureSummary(summary).quality,

      createdText: day.label + ' ' + formatClock(res.created_at),
      createdFull: formatFull(res.created_at),
      finishedFull: res.finished_at ? formatFull(res.finished_at) : '',
      durationText: res.started_at ? durationText(res.started_at, res.finished_at) : '',

      errorList: describeError(res.error_message),
      conclusion: String(summary.conclusion || '').trim(),
      files,
      logTail: res.log_tail || '',
      dock: dockOf(res, files),

      loading: false,
      loadError: ''
    });

    // 在看着的时候任务结束：轻震一下提示结果到了
    if (this.wasLive && !live && wx.vibrateShort) wx.vibrateShort({ type: 'light' });
    this.wasLive = live;
  },

  /* ---------------- 轮询 ---------------- */

  syncPoll() {
    if (this.data.live) this.startPoll();
    else this.stopPoll();
  },

  startPoll() {
    if (this.pollTimer) return;
    this.pollTimer = setInterval(() => this.fetchDetail(false), config.pollInterval);
  },

  stopPoll() {
    if (this.pollTimer) {
      clearInterval(this.pollTimer);
      this.pollTimer = null;
    }
  },

  /* ---------------- 操作 ---------------- */

  onResume() {
    if (this.data.busy) return;
    const partial = this.data.task && this.data.task.status === 'partial_completed';
    wx.showModal({
      title: partial ? '补齐图片' : '重跑任务',
      content: partial
        ? '只重新截取缺图的条目，已有截图保留，完成后生成新的 Word。'
        : '任务会重新排队，从没完成的条目继续。',
      confirmText: partial ? '开始补图' : '重跑',
      confirmColor: '#1f5a4c',
      success: (r) => {
        if (!r.confirm) return;
        this.setData({ busy: true });
        taskApi
          .resumeTask(this.data.taskId)
          .then((res) => {
            const resumed = !(res && res.resumed === false);
            wx.showToast({ title: resumed ? '已重新排队' : '当前状态不需要重跑', icon: 'none' });
            this.revealed = false; // 新一轮结束时再点亮一次
            return this.fetchDetail(false);
          })
          .catch(() => {})
          .then(() => this.setData({ busy: false }));
      }
    });
  },

  onCancel() {
    if (this.data.busy) return;
    wx.showModal({
      title: '取消任务',
      content: '任务会立即停止，已经生成的部分会保留。',
      confirmText: '取消任务',
      cancelText: '继续运行',
      confirmColor: '#b4433a',
      success: (r) => {
        if (!r.confirm) return;
        this.setData({ busy: true });
        taskApi
          .cancelTask(this.data.taskId)
          .then(() => {
            wx.showToast({ title: '任务已取消', icon: 'none' });
            return this.fetchDetail(false);
          })
          .catch(() => {})
          .then(() => this.setData({ busy: false }));
      }
    });
  },

  /* ---------------- 文件 ---------------- */

  onOpenPrimary() {
    if (this.data.dock.openName) this.openFile(this.data.dock.openName);
  },

  onFileTap(e) {
    this.openFile(e.currentTarget.dataset.name);
  },

  openFile(name) {
    wx.showLoading({ title: '正在取文件', mask: true });
    taskApi
      .downloadFile(this.data.taskId, name)
      .then((res) => {
        wx.hideLoading();
        const ext = String(name).split('.').pop().toLowerCase();
        if (PREVIEWABLE.indexOf(ext) !== -1) {
          this.openDocument(res.tempFilePath, ext, name);
        } else {
          this.shareFile(res.tempFilePath, name);
        }
      })
      .catch(() => wx.hideLoading());
  },

  /** docx / pdf 走系统预览，预览页右上角自带「转发 / 用其他应用打开」 */
  openDocument(filePath, ext, name) {
    wx.openDocument({
      filePath,
      fileType: ext,
      showMenu: true,
      fail: () => this.shareFile(filePath, name)
    });
  },

  /** zip 等不能预览的文件：转发到聊天，在电脑上打开 */
  shareFile(filePath, name) {
    if (!wx.shareFileMessage) {
      wx.showModal({
        title: '无法转发',
        content: '当前微信版本不支持转发文件，请升级微信后重试。',
        showCancel: false
      });
      return;
    }
    wx.shareFileMessage({
      filePath,
      fileName: safeFileName(name),
      fail: (err) => {
        if (String((err && err.errMsg) || '').indexOf('cancel') !== -1) return;
        wx.showToast({ title: '转发没有完成，请重试', icon: 'none' });
      }
    });
  },

  /* ---------------- 其它 ---------------- */

  toggleSteps() {
    this.setData({ stepsOpen: !this.data.stepsOpen });
  },

  toggleTech() {
    this.setData({ techOpen: !this.data.techOpen });
  },

  onCopyLog() {
    if (this.data.logTail) wx.setClipboardData({ data: this.data.logTail });
  },

  onCopyId() {
    wx.setClipboardData({ data: this.data.taskId });
  },

  /** 从分享或新建页 redirect 进来时可能没有上一页，回首页兜底 */
  onBack() {
    if (getCurrentPages().length > 1) wx.navigateBack();
    else wx.reLaunch({ url: '/pages/tasks/tasks' });
  }
});
