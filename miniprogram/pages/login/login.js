const { silentLogin, loginWithPassword } = require('../../utils/auth');
const taskApi = require('../../services/task');
const { envVersionText } = require('../../utils/util');
const config = require('../../config/index');

/** 品牌标识的 16 格：g 拿到截图、b 被平台拦下。固定排布，不随机 */
const MARK_CELLS = ['g', 'g', 'g', 'g', 'g', 'g', 'b', 'g', 'g', 'g', 'g', 'g', 'g', 'b', 'g', 'g'];

Page({
  data: {
    markCells: MARK_CELLS.map((g, i) => ({ k: i, g })),
    loading: false,
    /** wechat | password —— 哪个按钮在转圈 */
    mode: '',
    passwordOpen: false,
    password: '',
    error: '',
    envLabel: '',
    envVersion: '',
    baseUrl: '',
    /** null = 检测中 */
    serverOk: null,
    serverMsg: '正在检测服务器',
    serverDetail: ''
  },

  onLoad() {
    this.setData({
      envLabel: config.env === 'dev' ? '联调环境' : '生产环境',
      envVersion: envVersionText(),
      baseUrl: config.baseUrl
    });
    this.probeServer();
  },

  /**
   * 先探一次健康检查，把「服务器连不上」和「登录失败」分开。
   * 失败时把请求地址和微信原始错误一起列出：域名没进白名单、超时、证书问题
   * 在真机上长得一模一样，不看这几行没法定位。
   */
  probeServer() {
    taskApi
      .checkHealth()
      .then(() => {
        this.setData({ serverOk: true, serverMsg: '服务器连接正常', serverDetail: '' });
      })
      .catch((err) => {
        const lines = [(err && err.message) || '原因未知'];
        if (err && err.url) lines.push('请求地址：' + err.url);
        if (err && err.errMsg) lines.push('原始错误：' + err.errMsg);
        this.setData({ serverOk: false, serverMsg: '连不上服务器', serverDetail: lines.join('\n') });
      });
  },

  onRetryProbe() {
    this.setData({ serverOk: null, serverMsg: '正在检测服务器', serverDetail: '' });
    this.probeServer();
  },

  onOpenPassword() {
    this.setData({ passwordOpen: true, error: '' });
  },

  onPasswordInput(e) {
    this.setData({ password: e.detail.value, error: '' });
  },

  /** 主通道：微信静默登录 */
  onWechatLogin() {
    if (this.data.loading) return;
    this.setData({ loading: true, mode: 'wechat', error: '' });

    silentLogin(false).then((res) => {
      this.setData({ loading: false, mode: '' });
      if (res && res.token) {
        wx.reLaunch({ url: '/pages/tasks/tasks' });
      } else {
        this.setData({
          error: '微信登录没有成功。如果服务器还没配置 AppID / AppSecret，请改用访问密码登录。',
          passwordOpen: true
        });
      }
    });
  },

  /** 兜底通道：访问密码 */
  onPasswordLogin() {
    const pwd = (this.data.password || '').trim();
    if (!pwd) {
      this.setData({ error: '请输入访问密码' });
      return;
    }
    if (this.data.loading) return;
    this.setData({ loading: true, mode: 'password', error: '' });

    loginWithPassword(pwd)
      .then(() => {
        this.setData({ loading: false, mode: '', password: '' });
        wx.reLaunch({ url: '/pages/tasks/tasks' });
      })
      .catch((err) => {
        this.setData({
          loading: false,
          mode: '',
          error: err && err.code === 401 ? '访问密码不对' : (err && err.message) || '登录没有成功，请重试'
        });
      });
  }
});
