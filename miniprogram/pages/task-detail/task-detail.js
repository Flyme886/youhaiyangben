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
  sealOf,
  coverageOf,
  currentStepIndex,
  describeError,
  captureSummary
} = require('../../utils/status');
const { formatFull, fromNow, durationText, formatSize, safeFileName, formatDateDot } = require('../../utils/util');
const config = require('../../config/index');

const PREVIEWABLE = ['doc', 'docx', 'xls', 'xlsx', 'ppt', 'pptx', 'pdf'];

/** 底栏：每个状态只给最该做的一两件事 */
function barOf(task, files) {
  const status = task.status;
  const firstDoc = files.filter((f) => f.ext === 'docx')[0];
  const bar = {
    cancel: canCancel(status),
    resume: canResume(status),
    resumeText: status === 'partial_completed' ? '补齐图片' : '重跑任务',
    // 只有一册时才把「打开」放进底栏；多册在列表里逐个点更清楚
    open: !isLive(status) && !!firstDoc && files.filter((f) => f.ext === 'docx').length === 1,
    openName: firstDoc ? firstDoc.name : ''
  };
  bar.show = bar.cancel || bar.resume || bar.open;
  return bar;
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

    seal: null,
    sealDate: '',
    stampNow: false,
    coverage: null,
    quality: [],

    createdText: '',
    createdFull: '',
    finishedText: '',
    durationText: '',

    errorList: [],
    conclusion: '',
    files: [],
    logTail: '',

    stepsOpen: false,
    logOpen: false,
    bar: { show: false },

    loading: true,
    busy: false,
    loadError: ''
  },

  onLoad(options) {
    this.pollTimer = null;
    this.wasLive = null;
    this.setData({ taskId: (options && options.id) || '' });
    ensureLogin().then((ok) => {
      if (ok) this.fetchDetail(true);
    });
  },

  onShow() {
    // 从后台切回来时继续轮询
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
    const meta = describeStatus(res.status);
    const summary = res.summary || {};
    const live = isLive(res.status);

    // 页面开着时任务刚好结束 → 落章 + 轻震，这是全页唯一一次主动动效
    const justFinished = this.wasLive === true && !live;
    this.wasLive = live;

    const files = (res.files || []).map((f) => {
      const ext = String(f.name || '').split('.').pop().toLowerCase();
      return Object.assign({}, f, {
        ext,
        extLabel: ext.toUpperCase(),
        kind: ext === 'docx' || ext === 'doc' ? 'doc' : 'pack',
        sizeText: formatSize(f.size),
        previewable: PREVIEWABLE.indexOf(ext) !== -1
      });
    });

    this.setData({
      task: res,
      live,
      steps: buildSteps(res),
      stepText: describeStep(res.current_step),
      stepNo: Math.min(4, currentStepIndex(res.current_step) + 1),
      progress: progressOf(res),
      statusHint: meta.hint,

      seal: sealOf(res.status),
      sealDate: formatDateDot(res.finished_at),
      stampNow: justFinished,
      coverage: coverageOf(summary),
      quality: captureSummary(summary).quality,

      createdText: fromNow(res.created_at),
      createdFull: formatFull(res.created_at),
      finishedText: res.finished_at ? formatFull(res.finished_at) : '',
      durationText: res.started_at ? durationText(res.started_at, res.finished_at) : '',

      errorList: describeError(res.error_message),
      conclusion: String(summary.conclusion || '').trim(),
      files,
      logTail: res.log_tail || '',
      bar: barOf(res, files),

      loading: false,
      loadError: ''
    });

    if (justFinished && wx.vibrateShort) wx.vibrateShort({ type: 'medium' });
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
        ? '只重新截取缺图的条目，已有的截图保留。完成后会生成新的 Word。'
        : '任务会重新排队，从没完成的条目继续。',
      confirmText: partial ? '开始补图' : '重跑',
      confirmColor: '#1d5c4e',
      success: (r) => {
        if (!r.confirm) return;
        this.setData({ busy: true });
        taskApi
          .resumeTask(this.data.taskId)
          .then((res) => {
            const resumed = !(res && res.resumed === false);
            wx.showToast({ title: resumed ? '已重新排队' : '当前状态不需要重跑', icon: 'none' });
            this.wasLive = null; // 新一轮开始，结束时再落一次章
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
      confirmColor: '#a83a30',
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
    if (this.data.bar.openName) this.openFile(this.data.bar.openName);
  },

  onDownload(e) {
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

  toggleLog() {
    this.setData({ logOpen: !this.data.logOpen });
  },

  onCopyLog() {
    if (!this.data.logTail) return;
    wx.setClipboardData({ data: this.data.logTail });
  },

  onCopyId() {
    wx.setClipboardData({ data: this.data.taskId });
  },

  onBackToTasks() {
    wx.switchTab({ url: '/pages/tasks/tasks' });
  }
});
