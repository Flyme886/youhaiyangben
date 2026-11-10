const taskApi = require('../../services/task');
const { ensureLogin, logout } = require('../../utils/auth');
const { getOpenid, getToken } = require('../../utils/request');
const config = require('../../config/index');
const { formatFull, envVersionText } = require('../../utils/util');

const FAQ = [
  {
    q: '选文件时找不到我的 Excel',
    a: '小程序只能从聊天记录里取文件，不能浏览手机目录。先把 .xlsx 发到任意聊天（推荐「文件传输助手」），再回来从聊天记录里选。'
  },
  {
    q: '任务一直停在「打开链接并截图」',
    a: '链接多时这一步最慢，每条通常要几秒到十几秒。单个任务最长跑 2 小时，超时后在详情页点「重跑任务」会从没完成的条目接着跑。'
  },
  {
    q: '「缺图交付」是没做完吗',
    a: '不是。Word 已经完整交付，只是有些链接被平台拦下（需要登录、平台验证等），那几条的图片位置留空。详情页的取证条会告诉你每一类各有多少条。'
  },
  {
    q: '怎么把缺的图补上',
    a: '在详情页底部点「补齐图片」。服务器只会重新截取缺图的条目，已有的截图保留，完成后生成新的 Word。'
  },
  {
    q: '任务显示「待重跑」怎么办',
    a: '说明平台验证拦住了全部链接，一册 Word 都没产出。通常是服务器上的平台登录态过期了，请联系管理员刷新后，回详情页点「重跑任务」。'
  },
  {
    q: '下载的 zip 怎么打开',
    a: 'zip 不能在手机里预览。点文件会转发到聊天，在电脑上打开那条聊天记录即可。'
  }
];

Page({
  data: {
    openidMasked: '',
    isAdmin: false,
    envLabel: '',
    envVersion: '',
    baseUrl: '',
    version: '1.1.0',
    serverTime: '',

    /** null = 检测中，true / false = 检测结果 */
    health: null,
    healthText: '正在检测服务器',

    statCards: [
      { key: 'all', label: '全部', value: '–' },
      { key: 'active', label: '进行中', value: '–' },
      { key: 'done', label: '已完成', value: '–' },
      { key: 'issue', label: '异常', value: '–' }
    ],
    detailsOpen: false,

    faq: FAQ,
    faqOpenIndex: -1
  },

  onLoad() {
    this.setData({
      envLabel: config.env === 'dev' ? '联调环境' : '生产环境',
      envVersion: envVersionText(),
      baseUrl: config.baseUrl
    });
  },

  onShow() {
    ensureLogin().then((ok) => {
      if (!ok) return;
      const openid = getOpenid();
      this.setData({
        openidMasked: this.maskOpenid(openid),
        // 密码登录不下发 openid，后端把它当管理员
        isAdmin: !openid && !!getToken()
      });
      this.loadStats();
      this.checkHealth();
    });
  },

  onPullDownRefresh() {
    Promise.all([this.loadStats(), this.checkHealth()]).then(() => wx.stopPullDownRefresh());
  },

  maskOpenid(openid) {
    if (!openid) return '访问密码登录';
    if (openid.length <= 12) return openid;
    return openid.slice(0, 6) + '••••' + openid.slice(-4);
  },

  loadStats() {
    return taskApi
      .fetchStats()
      .then((s) => {
        const n = (k) => Number((s && s[k]) || 0);
        const values = {
          all: n('total'),
          active: n('queued') + n('running'),
          done: n('completed') + n('partial_completed'),
          issue: n('failed') + n('paused_captcha') + n('cancelled')
        };
        this.setData({
          statCards: this.data.statCards.map((c) => Object.assign({}, c, { value: values[c.key] }))
        });
      })
      .catch(() => {});
  },

  checkHealth() {
    this.setData({ health: null, healthText: '正在检测服务器' });
    return taskApi
      .checkHealth()
      .then((res) => {
        const ok = !res || res.status === 'ok';
        this.setData({
          health: ok,
          healthText: ok ? '服务器连接正常' : '服务器返回异常',
          serverTime: res && res.server_time ? formatFull(res.server_time) : ''
        });
      })
      .catch(() => {
        this.setData({ health: false, healthText: '连不上服务器' });
      });
  },

  /** 点统计数字：带着筛选跳到任务页（switchTab 不能带参数，经 globalData 传递） */
  onStatTap(e) {
    const app = getApp();
    if (app && app.globalData) app.globalData.pendingFilter = e.currentTarget.dataset.key;
    wx.switchTab({ url: '/pages/tasks/tasks' });
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
      confirmText: '退出登录',
      confirmColor: '#a83a30',
      success: (r) => {
        if (r.confirm) logout();
      }
    });
  },

  onCopyBaseUrl() {
    wx.setClipboardData({ data: this.data.baseUrl });
  }
});
