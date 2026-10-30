const taskApi = require('../../services/task');
const { ensureLogin } = require('../../utils/auth');
const { formatSize } = require('../../utils/util');

/** 与后端 app/main.py 的校验保持一致 */
const LOCK_PREFIX = '~$';
const ALLOWED = /\.xlsx$/i;

Page({
  data: {
    file: null,
    name: '',
    note: '',
    uploading: false,
    progress: 0,
    error: '',
    /** 上传流程说明，展开/收起 */
    stepsOpen: true
  },

  onLoad() {
    ensureLogin();
  },

  onShow() {
    // 从结果页返回后给一个干净的表单
    if (this.needReset) {
      this.needReset = false;
      this.setData({ file: null, name: '', note: '', progress: 0, error: '' });
    }
  },

  /* ---------------- 选文件 ---------------- */

  /**
   * 小程序没有「浏览本机文件」的通用接口，只能从聊天记录里取文件。
   * 交互上必须提前告诉用户，否则会卡在这一步。
   */
  onChooseFile() {
    if (this.data.uploading) return;
    wx.chooseMessageFile({
      count: 1,
      type: 'file',
      extension: ['xlsx'],
      success: (res) => {
        const picked = (res.tempFiles || [])[0];
        if (!picked) return;
        this.acceptFile(picked);
      },
      fail: (err) => {
        const msg = (err && err.errMsg) || '';
        if (msg.indexOf('cancel') !== -1) return;
        this.setData({ error: '选择文件失败，请重试' });
      }
    });
  },

  acceptFile(picked) {
    const filename = picked.name || '';
    if (!filename) {
      this.setData({ error: '无法读取文件名，请重新选择' });
      return;
    }
    if (filename.indexOf(LOCK_PREFIX) === 0) {
      this.setData({ error: '这是 Excel 打开时产生的临时锁文件，请选择正式文件' });
      return;
    }
    if (!ALLOWED.test(filename)) {
      this.setData({ error: '只支持 .xlsx；如果是 .xls，请先用 Excel 另存为 .xlsx' });
      return;
    }
    this.setData({
      file: {
        name: filename,
        path: picked.path,
        sizeText: formatSize(picked.size)
      },
      // 任务名默认取文件名（去扩展名），用户可改
      name: this.data.name || filename.replace(/\.xlsx$/i, ''),
      error: ''
    });
  },

  onRemoveFile() {
    if (this.data.uploading) return;
    this.setData({ file: null, name: '', progress: 0, error: '' });
  },

  onPreviewFile() {
    if (!this.data.file) return;
    // xlsx 不能被 openDocument 直接渲染，给个明确提示而不是报错
    wx.showModal({
      title: '文件已就绪',
      content: this.data.file.name + '\n' + this.data.file.sizeText + '\n\n小程序内无法预览 Excel，上传后可在服务端处理。',
      showCancel: false,
      confirmText: '知道了'
    });
  },

  /* ---------------- 表单 ---------------- */

  onNameInput(e) {
    this.setData({ name: e.detail.value });
  },

  onNoteInput(e) {
    this.setData({ note: e.detail.value });
  },

  toggleSteps() {
    this.setData({ stepsOpen: !this.data.stepsOpen });
  },

  /* ---------------- 提交 ---------------- */

  onSubmit() {
    if (this.data.uploading) return;
    if (!this.data.file) {
      this.setData({ error: '请先选择一份 .xlsx 文件' });
      return;
    }

    this.setData({ uploading: true, progress: 0, error: '' });

    const pending = taskApi.createTask({
      filePath: this.data.file.path,
      name: (this.data.name || '').trim(),
      note: (this.data.note || '').trim(),
      onProgress: (v) => {
        // 只在实际变化时 setData，避免高频渲染
        if (v !== this.data.progress) this.setData({ progress: v });
      }
    });

    this.pendingUpload = pending;

    pending
      .then((res) => {
        this.pendingUpload = null;
        this.needReset = true;
        this.setData({ uploading: false, progress: 100 });
        wx.showToast({ title: '已提交', icon: 'success', duration: 1200 });
        setTimeout(() => {
          wx.navigateTo({ url: '/pages/task-detail/task-detail?id=' + res.task_id });
        }, 700);
      })
      .catch((err) => {
        this.pendingUpload = null;
        const aborted = err && err.aborted;
        this.setData({
          uploading: false,
          progress: 0,
          error: aborted ? '' : (err && err.message) || '提交失败，请重试'
        });
      });
  },

  onCancelUpload() {
    if (!this.data.uploading) return;
    if (this.pendingUpload && typeof this.pendingUpload.abort === 'function') {
      this.pendingUpload.abort();
    }
    this.setData({ uploading: false, progress: 0 });
  }
});
