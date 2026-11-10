const taskApi = require('../../services/task');
const { ensureLogin } = require('../../utils/auth');
const { formatSize } = require('../../utils/util');

/** 与后端 app/api_wx.py 的校验保持一致 */
const LOCK_PREFIX = '~$';
const ALLOWED = /\.xlsx$/i;

/**
 * 体积上限。监测表通常几十 KB 到几 MB；超过 10MB 基本是误选了带大量图片的表，
 * 前端先拦下，免得用户在弱网下等很久才看到失败。
 */
const MAX_BYTES = 10 * 1024 * 1024;

const FLOW = [
  '核对表头，按链接去掉重复条目',
  '逐条打开链接，截下整页',
  '剔除空白页和平台验证页',
  '每 50 条装订成一册 Word'
];

/**
 * 部分安卓机型上 chooseMessageFile 返回的临时路径可能已失效，
 * 上传前先确认文件真的读得到，失败时让用户重新选，而不是传到一半报错。
 */
function verifyTempFile(path) {
  return new Promise((resolve) => {
    let fs = null;
    try {
      fs = wx.getFileSystemManager();
    } catch (e) {
      resolve(true);
      return;
    }
    if (!fs || typeof fs.getFileInfo !== 'function') {
      resolve(true);
      return;
    }
    fs.getFileInfo({
      filePath: path,
      success: (res) => resolve(!!(res && res.size > 0)),
      fail: () => resolve(false)
    });
  });
}

Page({
  data: {
    file: null,
    name: '',
    note: '',
    uploading: false,
    checking: false,
    progress: 0,
    error: '',
    stepsOpen: false,
    flow: FLOW
  },

  onLoad() {
    ensureLogin();
  },

  onShow() {
    // 提交成功后再回到这个 tab，给一张干净的表单
    if (this.needReset) {
      this.needReset = false;
      this.setData({ file: null, name: '', note: '', progress: 0, error: '' });
    }
  },

  /* ---------------- 选文件 ---------------- */

  /** 小程序没有「浏览本机文件」的通用接口，只能从聊天记录里取文件 */
  onChooseFile() {
    if (this.data.uploading) return;
    wx.chooseMessageFile({
      count: 1,
      type: 'file',
      extension: ['xlsx'],
      success: (res) => {
        const picked = (res.tempFiles || [])[0];
        if (picked) this.acceptFile(picked);
      },
      fail: (err) => {
        if (String((err && err.errMsg) || '').indexOf('cancel') !== -1) return;
        this.setData({ error: '没能打开聊天记录，请再试一次' });
      }
    });
  },

  acceptFile(picked) {
    const filename = picked.name || '';
    if (!filename) {
      this.setData({ error: '读不到文件名，请重新选择' });
      return;
    }
    if (filename.indexOf(LOCK_PREFIX) === 0) {
      this.setData({ error: '这是 Excel 打开时产生的临时文件，请选择正式的表格' });
      return;
    }
    if (!ALLOWED.test(filename)) {
      this.setData({ error: '只支持 .xlsx。如果是 .xls，请先在 Excel 里另存为 .xlsx' });
      return;
    }
    if (picked.size > MAX_BYTES) {
      this.setData({
        error: '这个文件有 ' + formatSize(picked.size) + '，超过 10 MB 上限。请删掉表里的图片或拆成几份再传'
      });
      return;
    }

    // 换文件时，如果任务名还是上一个文件名，就跟着换
    const prevAuto = this.data.file ? this.data.file.name.replace(ALLOWED, '') : '';
    const keepName = this.data.name && this.data.name !== prevAuto;

    this.setData({
      file: { name: filename, path: picked.path, size: picked.size, sizeText: formatSize(picked.size) },
      name: keepName ? this.data.name : filename.replace(ALLOWED, ''),
      error: ''
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
    if (this.data.uploading || this.data.checking) return;
    if (!this.data.file) {
      this.setData({ error: '先选一份 .xlsx' });
      return;
    }

    this.setData({ checking: true, error: '' });
    verifyTempFile(this.data.file.path).then((ok) => {
      this.setData({ checking: false });
      if (!ok) {
        this.setData({
          file: null,
          error: '微信给的临时文件已经失效，请重新从聊天记录选一次'
        });
        return;
      }
      this.startUpload();
    });
  },

  startUpload() {
    this.setData({ uploading: true, progress: 0 });

    const pending = taskApi.createTask({
      filePath: this.data.file.path,
      name: (this.data.name || '').trim(),
      note: (this.data.note || '').trim(),
      onProgress: (v) => {
        if (v !== this.data.progress) this.setData({ progress: v });
      }
    });
    this.pendingUpload = pending;

    pending
      .then((res) => {
        this.pendingUpload = null;
        this.needReset = true;
        this.setData({ uploading: false, progress: 100 });
        wx.showToast({ title: '任务已提交', icon: 'success', duration: 900 });
        setTimeout(() => {
          wx.navigateTo({ url: '/pages/task-detail/task-detail?id=' + res.task_id });
        }, 600);
      })
      .catch((err) => {
        this.pendingUpload = null;
        const aborted = err && err.aborted;
        this.setData({
          uploading: false,
          progress: 0,
          error: aborted ? '' : (err && err.message) || '上传没有成功，请重试'
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
