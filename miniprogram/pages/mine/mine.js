const taskApi = require('../../services/task');
const { ensureLogin, logout } = require('../../utils/auth');
const { getOpenid } = require('../../utils/request');
const config = require('../../config/index');
const { formatFull } = require('../../utils/util');

const FAQ = [
  {
    q: '选择文件时找不到我的 Excel',
    a: '小程序只能从聊天记录里取文件，不能浏览手机本地目录。请先把 .xlsx 发送到任意聊天（推荐「文件传输助手」），再回来从这里面选。'
  },
  {
    q: '任务一直停在「截图采集」',
    a: '链接数量多时属正常，每条通常需要数秒。单个任务默认 2 小时超时，超时后可在详情页点「继续任务」接着跑。'
  },
  {
    q: '提示「N/N 条命中平台验证码」是不是没做完',
    a: '不是。平台的验证码挡住了部分链接的截图，但文字部分（标题、原文、链接、违规要素）已全部处理完成，Word 已按文字版正常交付，只是图片位置留空。不需要人工介入，也不影响本次交付。'
  },
  {
    q: '「已完成」但说图片留空，正常吗',
    a: '正常。这是抓不到图时的降级交付：文档照常生成并交付，缺图的位置留白，判定理由和正文都不受影响。想要补齐图片，可在详情页点「补齐图片」重跑一次。'
  },
  {
    q: '任务显示「待重跑」怎么办',
    a: '只有「命中验证码且一个 Word 都没产出」时才会出现。通常是后台登录态失效，刷新登录态文件后回详情页点「重跑」即可。'
  },
  {
    q: '下载的 zip 打不开 / 看不到',
    a: 'zip 无法在小程序内预览。点文件右侧的「发送」把它转发到聊天，再从聊天里用电脑打开。'
  },
  {
    q: '图标上显示的数字是什么',
    a: '「Word N」表示已生成 N 份 Word 文档（每 50 条样本一份）；「未附图 N」表示有 N 条没取到截图，图片位置是留白的。'
  }
];

Page({
  data: {
    openidMasked: '',
    envLabel: '生产环境',
    baseUrl: '',
    version: '1.0.0',

    /** null = 未检测，true / false = 检测结果 */
    health: null,
    healthText: '未检测',

    statCards: [],
    detailsOpen: false,

    faq: FAQ,
    faqOpenIndex: -1
  },

  onLoad() {
    this.setData({
      envLabel: config.env === 'dev' ? '联调环境' : '生产环境',
      baseUrl: config.baseUrl
    });
  },

  onShow() {
    ensureLogin().then((ok) => {
      if (!ok) return;
      this.setData({ openidMasked: this.maskOpenid(getOpenid()) });
      this.loadStats();
      this.checkHealth();
    });
  },

  onPullDownRefresh() {
    this.loadStats();
    this.checkHealth().then(() => wx.stopPullDownRefresh());
  },

  maskOpenid(openid) {
    if (!openid) return '未绑定';
    if (openid.length <= 12) return openid;
    return openid.slice(0, 6) + '••••' + openid.slice(-4);
  },

  loadStats() {
    taskApi
      .fetchStats()
      .then((s) => {
        const n = (k) => Number((s && s[k]) || 0);
        this.setData({
          statCards: [
            { label: '全部任务', value: n('total') },
            { label: '进行中', value: n('queued') + n('running') },
            { label: '已完成', value: n('completed') + n('partial_completed') },
            { label: '异常', value: n('failed') + n('paused_captcha') + n('cancelled') }
          ]
        });
      })
      .catch(() => {});
  },

  checkHealth() {
    this.setData({ health: null, healthText: '检测中…' });
    return taskApi
      .checkHealth()
      .then((res) => {
        const ok = !res || res.status === 'ok' || res.ok !== false;
        this.setData({
          health: ok,
          healthText: ok ? '服务端正常' : '服务端异常',
          serverTime: res && res.server_time ? formatFull(res.server_time) : ''
        });
      })
      .catch(() => {
        this.setData({ health: false, healthText: '服务端不可达' });
      });
  },

  toggleDetails() {
    this.setData({ detailsOpen: !this.data.detailsOpen });
  },

  onFaqTap(e) {
    const idx = Number(e.currentTarget.dataset.index);
    this.setData({ faqOpenIndex: this.data.faqOpenIndex === idx ? -1 : idx });
  },

  onLogout() {
    wx.showModal({
      title: '退出登录',
      content: '退出后需要重新登录才能查看任务。',
      confirmText: '退出',
      confirmColor: '#9a2f2f',
      success: (r) => {
        if (r.confirm) logout();
      }
    });
  },

  onCopyBaseUrl() {
    wx.setClipboardData({
      data: this.data.baseUrl,
      success: () => wx.showToast({ title: '已复制', icon: 'none' })
    });
  }
});
