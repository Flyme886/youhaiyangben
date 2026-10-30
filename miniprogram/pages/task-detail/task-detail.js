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
  describeError,
  captureSummary
} = require('../../utils/status');
const { formatFull, fromNow, durationText, formatSize, safeFileName } = require('../../utils/util');
const config = require('../../config/index');

const PREVIEWABLE = ['doc', 'docx', 'xls', 'xlsx', 'ppt', 'pptx', 'pdf'];

Page({
  data: {
    taskId: '',
    task: null,
    steps: [],
    progress: 0,
    statusLabel: '',
    statusTone: 'muted',
    statusHint: '',
    stepText: '等待执行',

    createdFull: '',
    createdText: '',
    finishedText: '',
    durationText: '',

    errorList: [],
    capture: [],
    quality: [],
    files: [],
    logTail: '',
    logOpen: false,

    actions: { resume: false, cancel: false },
    live: false,

    loading: true,
    busy: false,
    loadError: ''
  },

  onLoad(options) {
    const id = (options && options.id) || '';
    this.setData({ taskId: id });
    this.pollTimer = null;

    ensureLogin().then((ok) => {
      if (!ok) return;
      this.fetchDetail(true);
    });
  },

  onHide() {
    this.stopPoll();
  },

  onUnload() {
    this.stopPoll();
  },

  onPullDownRefresh() {
    this.fetchDetail(true).then(() => wx.stopPullDownRefresh());
  },

  /* ---------------- 数据 ---------------- */

  fetchDetail(showLoading) {
    if (!this.data.taskId) {
      this.setData({ loading: false, loadError: '缺少任务 ID' });
      return Promise.resolve();
    }
    if (showLoading) this.setData({ loading: true, loadError: '' });

    return taskApi
      .getTask(this.data.taskId)
      .then((res) => this.applyTask(res))
      .catch((err) => {
        this.setData({
          loading: false,
          loadError: (err && err.message) || '加载失败'
        });
      })
      .then(() => this.syncPoll());
  },

  applyTask(res) {
    const meta = describeStatus(res.status);
    const summary = res.summary || {};
    const counts = captureSummary(summary);

    const files = (res.files || []).map((f) => {
      const ext = String(f.name || '').split('.').pop().toLowerCase();
      return Object.assign({}, f, {
        ext: ext,
        extLabel: ext.toUpperCase(),
        sizeText: formatSize(f.size),
        previewable: PREVIEWABLE.indexOf(ext) !== -1
      });
    });

    this.setData({
      task: res,
      steps: buildSteps(res),
      progress: progressOf(res),
      statusLabel: meta.label,
      statusTone: meta.tone,
      statusHint: meta.hint,
      stepText: describeStep(res.current_step),

      createdFull: formatFull(res.created_at),
      createdText: fromNow(res.created_at),
      finishedText: res.finished_at ? formatFull(res.finished_at) : '',
      durationText: res.started_at ? durationText(res.started_at, res.finished_at) : '',

      errorList: describeError(res.error_message),
      capture: counts.capture,
      quality: counts.quality,
      files: files,
      logTail: res.log_tail || '',
      live: isLive(res.status),

      actions: {
        resume: canResume(res.status),
        cancel: canCancel(res.status)
      },

      loading: false,
      loadError: ''
    });
  },

  /* ---------------- 轮询 ---------------- */

  syncPoll() {
    if (this.data.live) this.startPoll();
    else this.stopPoll();
  },

  startPoll() {
    if (this.pollTimer) return;
    this.pollTimer = setInterval(() => {
      this.fetchDetail(false);
    }, config.pollInterval);
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
    this.setData({ busy: true });
    taskApi
      .resumeTask(this.data.taskId)
      .then((res) => {
        wx.showToast({
          title: res && res.resumed === false ? '当前状态无需重试' : '已重新排队',
          icon: 'none'
        });
        this.fetchDetail(false);
      })
      .catch(() => {})
      .then(() => this.setData({ busy: false }));
  },

  onCancel() {
    if (this.data.busy) return;
    wx.showModal({
      title: '取消任务',
      content: '任务会立即停止，已生成的中间产物会保留。确定取消吗？',
      confirmText: '确定取消',
      confirmColor: '#9a2f2f',
      success: (r) => {
        if (!r.confirm) return;
        this.setData({ busy: true });
        taskApi
          .cancelTask(this.data.taskId)
          .then(() => {
            wx.showToast({ title: '已取消', icon: 'none' });
            this.fetchDetail(false);
          })
          .catch(() => {})
          .then(() => this.setData({ busy: false }));
      }
    });
  },

  /* ---------------- 文件 ---------------- */

  onDownload(e) {
    const name = e.currentTarget.dataset.name;
    wx.showLoading({ title: '下载中', mask: true });

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
      .catch(() => {
        wx.hideLoading();
      });
  },

  /** docx / pdf 等走系统预览，预览页自带「用其他应用打开」入口 */
  openDocument(filePath, ext, name) {
    wx.openDocument({
      filePath: filePath,
      fileType: ext,
      showMenu: true,
      success: () => {},
      fail: () => {
        // 预览失败（缺少办公类 App）时退化为「发送到聊天」
        this.shareFile(filePath, name);
      }
    });
  },

  /** zip 等无法预览的文件：转发到聊天，用户可在微信内保存 */
  shareFile(filePath, name) {
    if (!wx.shareFileMessage) {
      wx.showModal({
        title: '文件已下载',
        content: '当前微信版本不支持转发文件。文件名：' + name,
        showCancel: false
      });
      return;
    }
    wx.shareFileMessage({
      filePath: filePath,
      fileName: safeFileName(name),
      fail: (err) => {
        const msg = (err && err.errMsg) || '';
        if (msg.indexOf('cancel') !== -1) return;
        wx.showModal({
          title: '文件已下载',
          content: '转发未完成。可在微信中重新打开小程序再试。',
          showCancel: false
        });
      }
    });
  },

  /* ---------------- 其它 ---------------- */

  toggleLog() {
    this.setData({ logOpen: !this.data.logOpen });
  },

  onCopyLog() {
    if (!this.data.logTail) return;
    wx.setClipboardData({
      data: this.data.logTail,
      success: () => wx.showToast({ title: '日志已复制', icon: 'none' })
    });
  },

  onCopyId() {
    wx.setClipboardData({
      data: this.data.taskId,
      success: () => wx.showToast({ title: '任务 ID 已复制', icon: 'none' })
    });
  },

  onBackToTasks() {
    wx.switchTab({ url: '/pages/tasks/tasks' });
  }
});
